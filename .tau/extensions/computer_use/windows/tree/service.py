"""UI Automation tree traversal: extracts interactive/scrollable elements from
the active window plus other top-level windows into a compact, LLM-facing
TreeState snapshot. Uses UIA property/pattern caching (cache_utils.py) to keep
COM round trips down during traversal."""

from __future__ import annotations

import ctypes
import logging
import weakref
from _ctypes import COMError
from time import perf_counter, sleep
from typing import TYPE_CHECKING, Any

from ..uia import (
    AccessibleRoleNames,
    ButtonControl,
    CheckBoxControl,
    ComboBoxControl,
    Control,
    ControlFromHandle,
    EditControl,
    ExpandCollapseState,
    PatternId,
    PropertyId,
    Rect,
    ScrollPattern,
    SliderControl,
    ToggleState,
    TreeScope,
    UIADeadElementError,
    UIAException,
    WindowControl,
    from_com_error,
)
from .cache_utils import CachedControlHelper, CacheRequestFactory
from .config import (
    DEFAULT_ACTIONS,
    DOCUMENT_CONTROL_TYPE_NAMES,
    INFORMATIVE_CONTROL_TYPE_NAMES,
    INTERACTIVE_CONTROL_TYPE_NAMES,
    INTERACTIVE_ROLES,
    THREAD_MAX_RETRIES,
)
from .utils import random_point_within_bounding_box
from .views import BoundingBox, Center, ScrollElementNode, TextElementNode, TreeElementNode, TreeState

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..desktop.service import Desktop

_KEYBOARD_FOCUSABLE_CONTROL_TYPES = {
    "EditControl",
    "ButtonControl",
    "CheckBoxControl",
    "RadioButtonControl",
    "TabItemControl",
}


def _controls_from_raw_element_array(raw: Any) -> list[Control]:
    """Convert a raw IUIAutomationElementArray into a list of Control wrappers."""
    if not raw:
        return []
    controls: list[Control] = []
    for i in range(raw.Length):
        elem = raw.GetElement(i)
        control = Control.CreateControlFromElement(elem)
        if control:
            controls.append(control)
    return controls


class Tree:
    """Extracts a TreeState snapshot of interactive/scrollable elements across
    the active window plus other top-level windows (taskbar, dialogs, ...)."""

    def __init__(self, desktop: Desktop) -> None:
        self.desktop = weakref.proxy(desktop)
        screen_size = desktop.get_screen_size()
        self.dom: Control | None = None
        self.dom_bounding_box: BoundingBox | None = None
        self.screen_box = BoundingBox(
            top=0,
            left=0,
            bottom=screen_size.height,
            right=screen_size.width,
            width=screen_size.width,
            height=screen_size.height,
        )

    def get_state(self, active_window_handle: int | None, other_windows_handles: list[int]) -> TreeState:
        self.dom = None
        self.dom_bounding_box = None
        start_time = perf_counter()

        if active_window_handle:
            windows_handles = [active_window_handle] + other_windows_handles
        else:
            windows_handles = other_windows_handles

        interactive_nodes, scrollable_nodes, dom_informative_nodes, failed_handles = self.get_window_wise_nodes(
            windows_handles=windows_handles, active_window_flag=bool(active_window_handle)
        )
        root_node = TreeElementNode(
            name="Desktop",
            control_type="PaneControl",
            bounding_box=self.screen_box,
            center=self.screen_box.get_center(),
            window_name="Desktop",
            metadata={},
        )

        dom_node = None
        if self.dom is not None and self.dom_bounding_box is not None:
            try:
                scroll_pattern: ScrollPattern = self.dom.GetCachedPattern(PatternId.ScrollPattern, True)
                metadata = {
                    "horizontal_scrollable": scroll_pattern.HorizontallyScrollable if scroll_pattern else False,
                    "horizontal_scroll_percent": (
                        round(scroll_pattern.HorizontalScrollPercent, 2)
                        if scroll_pattern and scroll_pattern.HorizontallyScrollable
                        else 0
                    ),
                    "vertical_scrollable": scroll_pattern.VerticallyScrollable if scroll_pattern else False,
                    "vertical_scroll_percent": (
                        round(scroll_pattern.VerticalScrollPercent, 2)
                        if scroll_pattern and scroll_pattern.VerticallyScrollable
                        else 0
                    ),
                }
                dom_node = ScrollElementNode(
                    name="DOM",
                    control_type="DocumentControl",
                    bounding_box=self.dom_bounding_box,
                    center=self.dom_bounding_box.get_center(),
                    window_name="DOM",
                    metadata=metadata,
                )
            except Exception as exc:
                logger.debug("Failed to get DOM scroll pattern: %s", exc)

        status = len(failed_handles) == 0
        if not status:
            logger.warning("[Tree] %d window(s) failed to capture — UI services may be loading", len(failed_handles))
        logger.debug("[Tree] Tree State capture took %.2f seconds", perf_counter() - start_time)

        return TreeState(
            status=status,
            root_node=root_node,
            dom_node=dom_node,
            interactive_nodes=interactive_nodes,
            scrollable_nodes=scrollable_nodes,
            dom_informative_nodes=dom_informative_nodes,
        )

    def get_window_wise_nodes(
        self, windows_handles: list[int], active_window_flag: bool
    ) -> tuple[list[TreeElementNode], list[ScrollElementNode], list[TextElementNode], list[int]]:
        """Process windows sequentially — UI Automation requires STA, and worker
        threads that each CoInitialize() would deadlock via cross-apartment
        marshaling against the main thread's own COM calls."""
        interactive_nodes: list[TreeElementNode] = []
        scrollable_nodes: list[ScrollElementNode] = []
        dom_informative_nodes: list[TextElementNode] = []
        failed_handles: list[int] = []

        task_inputs: list[tuple[int, bool]] = []
        for handle in windows_handles:
            is_browser = False
            try:
                temp_node = ControlFromHandle(handle)
                if active_window_flag and temp_node is not None and temp_node.ClassName == "Progman":
                    continue
                if temp_node is not None:
                    is_browser = self.desktop.is_window_browser(temp_node)
            except Exception:
                pass
            task_inputs.append((handle, is_browser))

        retry_counts = {handle: 0 for handle in windows_handles}
        for handle, is_browser in task_inputs:
            for attempt in range(THREAD_MAX_RETRIES + 1):
                try:
                    result = self.get_nodes(handle, is_browser, wait_time=0.5 * (2 ** (attempt - 1)) if attempt > 0 else 0)
                    if result:
                        element_nodes, scroll_nodes, info_nodes = result
                        interactive_nodes.extend(element_nodes)
                        scrollable_nodes.extend(scroll_nodes)
                        dom_informative_nodes.extend(info_nodes)
                    break
                except UIADeadElementError as exc:
                    logger.debug("Skipping destroyed window (handle %s): %s", handle, exc)
                    break
                except (UIAException, Exception) as exc:
                    retry_counts[handle] = attempt + 1
                    try:
                        window_name = ControlFromHandle(handle).Name  # type: ignore[union-attr]
                    except Exception:
                        window_name = "Unknown"
                    logger.warning(
                        "Error in processing window '%s' (handle %s), retry attempt %d/%d\nError: %s",
                        window_name,
                        handle,
                        retry_counts[handle],
                        THREAD_MAX_RETRIES,
                        exc,
                    )
                    if attempt < THREAD_MAX_RETRIES:
                        sleep(0.5 * (2**attempt))
                    else:
                        logger.error(
                            "Task failed completely for handle %s after %d retries", handle, THREAD_MAX_RETRIES
                        )
                        failed_handles.append(handle)
                        break

        return interactive_nodes, scrollable_nodes, dom_informative_nodes, failed_handles

    def iou_bounding_box(self, window_box: Rect, element_box: Rect) -> BoundingBox:
        clipped = element_box.intersect(window_box).intersect(
            Rect(self.screen_box.left, self.screen_box.top, self.screen_box.right, self.screen_box.bottom)
        )
        if clipped.right > clipped.left and clipped.bottom > clipped.top:
            return BoundingBox(
                left=clipped.left,
                top=clipped.top,
                right=clipped.right,
                bottom=clipped.bottom,
                width=clipped.width(),
                height=clipped.height(),
            )
        return BoundingBox(left=0, top=0, right=0, bottom=0, width=0, height=0)

    def element_has_child_element(self, node: Control, control_type: str, child_control_type: str) -> bool:
        if node.CachedLocalizedControlType == control_type:
            first_child = node.GetFirstChildControl()
            if first_child is None:
                return False
            return first_child.LocalizedControlType == child_control_type
        return False

    def _dom_correction(self, node: Control, dom_interactive_nodes: list[TreeElementNode], window_name: str) -> None:
        """Browser DOM quirks: unwrap list-item/option wrappers around a real
        link/button, and prefer a focusable group's inner text label."""
        if self.dom_bounding_box is None:
            return
        if (
            self.element_has_child_element(node, "list item", "link")
            or self.element_has_child_element(node, "item", "link")
            or self.element_has_child_element(node, "option", "button")
        ):
            dom_interactive_nodes.pop()
            return

        if node.CachedControlTypeName == "GroupControl":
            popped = dom_interactive_nodes.pop()
            if node.CachedControlTypeName in _KEYBOARD_FOCUSABLE_CONTROL_TYPES:
                is_kb_focusable = True
            else:
                is_kb_focusable = node.CachedIsKeyboardFocusable
            if not is_kb_focusable:
                return

            child: Control | None = node
            try:
                while True:
                    next_child = child.GetFirstChildControl() if child is not None else None
                    if next_child is None:
                        break
                    if child.ControlTypeName in INTERACTIVE_CONTROL_TYPE_NAMES:  # type: ignore[union-attr]
                        return
                    child = next_child
            except Exception:
                return
            if child is None or child.ControlTypeName != "TextControl":
                return

            metadata: dict[str, Any] = {}
            element_bounding_box = node.CachedBoundingRectangle
            bounding_box = self.iou_bounding_box(self.dom_bounding_box, element_bounding_box)
            metadata["has_focused"] = node.CachedHasKeyboardFocus
            if accelerator_key := node.CachedAcceleratorKey:
                metadata["shortcut"] = accelerator_key

            if isinstance(node, EditControl):
                try:
                    value = node.GetCachedPropertyValue(PropertyId.LegacyIAccessibleValueProperty)
                    metadata["value"] = value.strip() if value else "(empty)"
                except Exception:
                    pass
                try:
                    if help_text := node.CachedHelpText:
                        metadata["help_text"] = help_text.encode("ascii", "ignore").decode("ascii")
                except Exception:
                    pass

            dom_interactive_nodes.append(
                TreeElementNode(
                    name=(child.Name or "").strip(),
                    control_type=node.CachedLocalizedControlType,
                    bounding_box=bounding_box,
                    center=bounding_box.get_center(),
                    window_name=window_name,
                    hwnd=popped.hwnd,
                    control=node,
                    metadata=metadata,
                )
            )
        elif self.element_has_child_element(node, "link", "heading"):
            popped = dom_interactive_nodes.pop()
            heading = node.GetFirstChildControl()
            if heading is None:
                return
            value = heading.GetPropertyValue(PropertyId.LegacyIAccessibleValueProperty) or ""
            element_bounding_box = heading.BoundingRectangle
            bounding_box = self.iou_bounding_box(self.dom_bounding_box, element_bounding_box)
            dom_interactive_nodes.append(
                TreeElementNode(
                    name=(heading.Name or "").strip(),
                    control_type="link",
                    bounding_box=bounding_box,
                    center=bounding_box.get_center(),
                    window_name=window_name,
                    hwnd=popped.hwnd,
                    control=heading,
                    metadata={"has_focused": heading.HasKeyboardFocus},
                )
            )

    def tree_traversal(
        self,
        node: Control,
        window_bounding_box: Rect,
        window_name: str,
        is_browser: bool,
        interactive_nodes: list[TreeElementNode] | None = None,
        scrollable_nodes: list[ScrollElementNode] | None = None,
        dom_interactive_nodes: list[TreeElementNode] | None = None,
        dom_informative_nodes: list[TextElementNode] | None = None,
        is_dom: bool = False,
        is_dialog: bool = False,
        element_cache_req: Any | None = None,
        children_cache_req: Any | None = None,
        hwnd: int = 0,
    ) -> None:
        try:
            if not hasattr(node, "_is_cached") and element_cache_req:
                node = CachedControlHelper.build_cached_control(node, element_cache_req)

            is_offscreen = node.CachedIsOffscreen
            control_type_name = node.CachedControlTypeName

            if scrollable_nodes is not None:
                if (control_type_name not in (INTERACTIVE_CONTROL_TYPE_NAMES | INFORMATIVE_CONTROL_TYPE_NAMES)) and (
                    not is_offscreen
                ):
                    try:
                        scroll_pattern: ScrollPattern | None = node.GetCachedPattern(PatternId.ScrollPattern, True)
                        if scroll_pattern and scroll_pattern.VerticallyScrollable:
                            box = node.CachedBoundingRectangle
                            x, y = random_point_within_bounding_box(node=node, scale_factor=0.8)
                            localized_control_type = node.CachedLocalizedControlType
                            metadata: dict[str, Any] = {"has_focused": node.CachedHasKeyboardFocus}
                            scrollable_nodes.append(
                                ScrollElementNode(
                                    name=(node.CachedName or "").strip()
                                    or node.CachedAutomationId
                                    or localized_control_type.capitalize()
                                    or "''",
                                    control_type=localized_control_type.title(),
                                    bounding_box=BoundingBox(
                                        left=box.left,
                                        top=box.top,
                                        right=box.right,
                                        bottom=box.bottom,
                                        width=box.width(),
                                        height=box.height(),
                                    ),
                                    center=Center(x=x, y=y),
                                    window_name=window_name,
                                    hwnd=hwnd,
                                    control=node,
                                    metadata=metadata,
                                )
                            )
                    except Exception:
                        pass

            is_control_element = node.CachedIsControlElement
            element_bounding_box = node.CachedBoundingRectangle
            width = element_bounding_box.width()
            height = element_bounding_box.height()
            area = width * height

            is_visible = (
                (area > 0)
                and (
                    not is_offscreen
                    or control_type_name == "EditControl"
                    or (control_type_name == "ListItemControl" and is_browser)
                )
                and is_control_element
            )

            if is_visible and node.CachedIsEnabled:
                is_keyboard_focusable = (
                    True if control_type_name in (_KEYBOARD_FOCUSABLE_CONTROL_TYPES | {"ListItemControl"})
                    else node.CachedIsKeyboardFocusable
                )

                if interactive_nodes is not None:
                    is_interactive = False
                    if is_browser and control_type_name == "DataItemControl" and not is_keyboard_focusable:
                        is_interactive = False
                    elif not is_browser and control_type_name == "ImageControl" and is_keyboard_focusable:
                        is_interactive = True
                    elif control_type_name in (INTERACTIVE_CONTROL_TYPE_NAMES | DOCUMENT_CONTROL_TYPE_NAMES):
                        try:
                            role = node.GetCachedPropertyValue(PropertyId.LegacyIAccessibleRoleProperty)
                            is_role_interactive = AccessibleRoleNames.get(role, "Default") in INTERACTIVE_ROLES
                        except Exception:
                            is_role_interactive = False
                        is_image = False
                        if control_type_name == "ImageControl":
                            localized = node.CachedLocalizedControlType
                            if localized == "graphic" or not is_keyboard_focusable:
                                is_image = True
                        if is_role_interactive and (not is_image or is_keyboard_focusable):
                            is_interactive = True
                    elif control_type_name == "GroupControl" and is_browser:
                        try:
                            has_expand_collapse = node.GetCachedPropertyValue(
                                PropertyId.ExpandCollapseExpandCollapseStateProperty
                            )
                            if has_expand_collapse in ExpandCollapseState:
                                is_interactive = True
                        except Exception:
                            pass
                        try:
                            role = node.GetCachedPropertyValue(PropertyId.LegacyIAccessibleRoleProperty)
                            is_role_interactive = AccessibleRoleNames.get(role, "Default") in INTERACTIVE_ROLES
                        except Exception:
                            is_role_interactive = False
                        is_default_action = False
                        try:
                            default_action = node.GetCachedPropertyValue(
                                PropertyId.LegacyIAccessibleDefaultActionProperty
                            )
                            if default_action and default_action.title() in DEFAULT_ACTIONS:
                                is_default_action = True
                        except Exception:
                            pass
                        if is_role_interactive and (is_default_action or is_keyboard_focusable):
                            is_interactive = True

                    if is_interactive:
                        name = (node.CachedName or "").strip()
                        localized_control_type = node.CachedLocalizedControlType
                        metadata = {"has_focused": node.CachedHasKeyboardFocus}
                        if accelerator_key := node.CachedAcceleratorKey:
                            metadata["shortcut"] = accelerator_key
                        try:
                            if help_text := node.CachedHelpText:
                                metadata["help_text"] = help_text.encode("ascii", "ignore").decode("ascii")
                        except Exception:
                            pass

                        if isinstance(node, ButtonControl | CheckBoxControl):
                            try:
                                toggle_state = node.GetCachedPropertyValue(PropertyId.ToggleToggleStateProperty)
                                if toggle_state == ToggleState.On:
                                    metadata["toggle_state"] = "on"
                                elif toggle_state == ToggleState.Off:
                                    metadata["toggle_state"] = "off"
                            except Exception:
                                pass

                        if isinstance(node, EditControl):
                            try:
                                value = node.GetCachedPropertyValue(PropertyId.LegacyIAccessibleValueProperty)
                                metadata["value"] = value.strip() if value else "(empty)"
                            except Exception:
                                pass
                            try:
                                if node.CachedIsPassword:
                                    metadata["is_password"] = True
                            except Exception:
                                pass

                        if isinstance(node, ComboBoxControl):
                            try:
                                control_state = node.GetCachedPropertyValue(
                                    PropertyId.ExpandCollapseExpandCollapseStateProperty
                                )
                                if control_state == ExpandCollapseState.Expanded:
                                    metadata["expand_collapse_state"] = "expanded"
                                elif control_state == ExpandCollapseState.Collapsed:
                                    metadata["expand_collapse_state"] = "collapsed"
                                elif control_state == ExpandCollapseState.PartiallyExpanded:
                                    metadata["expand_collapse_state"] = "partially expanded"
                            except Exception:
                                pass
                            try:
                                metadata["is_selection_required"] = node.GetCachedPropertyValue(
                                    PropertyId.SelectionCanSelectMultipleProperty
                                )
                            except Exception:
                                pass
                            try:
                                metadata["is_selection_required"] = node.GetCachedPropertyValue(
                                    PropertyId.SelectionIsSelectionRequiredProperty
                                )
                            except Exception:
                                pass
                            try:
                                metadata["is_selected"] = node.GetCachedPropertyValue(
                                    PropertyId.SelectionItemIsSelectedProperty
                                )
                            except Exception:
                                pass
                            try:
                                selection_raw = node.GetCachedPropertyValue(PropertyId.SelectionSelectionProperty)
                                selected_items = _controls_from_raw_element_array(selection_raw)
                                selected_names = [item.Name for item in selected_items if item.Name]
                                if selected_names:
                                    metadata["selection"] = selected_names
                            except Exception:
                                pass

                        if isinstance(node, SliderControl):
                            try:
                                value = node.GetCachedPropertyValue(PropertyId.RangeValueValueProperty)
                                minimum = node.GetCachedPropertyValue(PropertyId.RangeValueMinimumProperty)
                                maximum = node.GetCachedPropertyValue(PropertyId.RangeValueMaximumProperty)
                                if value is not None:
                                    metadata["value"] = round(value, 2)
                                if minimum is not None:
                                    metadata["min"] = round(minimum, 2)
                                if maximum is not None:
                                    metadata["max"] = round(maximum, 2)
                            except Exception:
                                pass

                        if is_browser and is_dom and self.dom_bounding_box is not None and dom_interactive_nodes is not None:
                            bounding_box = self.iou_bounding_box(self.dom_bounding_box, element_bounding_box)
                            tree_node = TreeElementNode(
                                name=name,
                                control_type=localized_control_type.title(),
                                bounding_box=bounding_box,
                                center=bounding_box.get_center(),
                                window_name=window_name,
                                hwnd=hwnd,
                                control=node,
                                metadata=metadata,
                            )
                            dom_interactive_nodes.append(tree_node)
                            self._dom_correction(node, dom_interactive_nodes, window_name)
                        else:
                            bounding_box = self.iou_bounding_box(window_bounding_box, element_bounding_box)
                            interactive_nodes.append(
                                TreeElementNode(
                                    name=name,
                                    control_type=localized_control_type.title(),
                                    bounding_box=bounding_box,
                                    center=bounding_box.get_center(),
                                    window_name=window_name,
                                    hwnd=hwnd,
                                    control=node,
                                    metadata=metadata,
                                )
                            )

                if dom_informative_nodes is not None and control_type_name in INFORMATIVE_CONTROL_TYPE_NAMES:
                    is_image_check = False
                    if control_type_name == "ImageControl":
                        localized = node.CachedLocalizedControlType
                        if not is_keyboard_focusable or localized == "graphic":
                            is_image_check = True
                    is_text = not is_image_check
                    if is_text and is_browser and is_dom:
                        dom_informative_nodes.append(TextElementNode(text=(node.CachedName or "").strip()))

            children = CachedControlHelper.get_cached_children(node, children_cache_req)
            traversal = list(enumerate(children))
            if not is_dom:
                traversal = list(reversed(traversal))

            for _orig_idx, child in traversal:
                if is_browser and child.CachedAutomationId == "RootWebArea":
                    child_rect = child.CachedBoundingRectangle
                    self.dom_bounding_box = BoundingBox(
                        left=child_rect.left,
                        top=child_rect.top,
                        right=child_rect.right,
                        bottom=child_rect.bottom,
                        width=child_rect.width(),
                        height=child_rect.height(),
                    )
                    self.dom = child
                    self.tree_traversal(
                        child,
                        window_bounding_box,
                        window_name,
                        is_browser,
                        interactive_nodes,
                        scrollable_nodes,
                        dom_interactive_nodes,
                        dom_informative_nodes,
                        is_dom=True,
                        is_dialog=is_dialog,
                        element_cache_req=element_cache_req,
                        children_cache_req=children_cache_req,
                        hwnd=hwnd,
                    )
                elif isinstance(child, WindowControl):
                    if not child.CachedIsOffscreen:
                        if is_dom and self.dom_bounding_box is not None and dom_interactive_nodes is not None:
                            child_box = child.CachedBoundingRectangle
                            if child_box.width() > 0.8 * self.dom_bounding_box.width:
                                dom_interactive_nodes.clear()
                        elif not is_dom and interactive_nodes is not None:
                            try:
                                is_modal = child.GetCachedPropertyValue(PropertyId.WindowIsModalProperty)
                            except Exception:
                                is_modal = False
                            if is_modal:
                                interactive_nodes.clear()
                    self.tree_traversal(
                        child,
                        window_bounding_box,
                        window_name,
                        is_browser,
                        interactive_nodes,
                        scrollable_nodes,
                        dom_interactive_nodes,
                        dom_informative_nodes,
                        is_dom=is_dom,
                        is_dialog=True,
                        element_cache_req=element_cache_req,
                        children_cache_req=children_cache_req,
                        hwnd=hwnd,
                    )
                else:
                    self.tree_traversal(
                        child,
                        window_bounding_box,
                        window_name,
                        is_browser,
                        interactive_nodes,
                        scrollable_nodes,
                        dom_interactive_nodes,
                        dom_informative_nodes,
                        is_dom=is_dom,
                        is_dialog=is_dialog,
                        element_cache_req=element_cache_req,
                        children_cache_req=children_cache_req,
                        hwnd=hwnd,
                    )
        except UIAException:
            raise
        except COMError as exc:
            raise from_com_error(exc) from exc

    def window_name_correction(self, window_name: str) -> str:
        match window_name:
            case "Progman":
                return "Desktop"
            case "Shell_TrayWnd" | "Shell_SecondaryTrayWnd":
                return "Taskbar"
            case "Microsoft.UI.Content.PopupWindowSiteBridge":
                return "Context Menu"
            case _:
                return window_name

    def get_nodes(
        self, handle: int, is_browser: bool = False, wait_time: float = 0
    ) -> tuple[list[TreeElementNode], list[ScrollElementNode], list[TextElementNode]]:
        if wait_time > 0:
            sleep(wait_time)
        try:
            node = ControlFromHandle(handle)
            if not node:
                raise RuntimeError("Failed to create Control from handle")

            element_cache_req = CacheRequestFactory.create_tree_traversal_cache()
            element_cache_req.TreeScope = TreeScope.TreeScope_Element

            children_cache_req = CacheRequestFactory.create_tree_traversal_cache()
            children_cache_req.TreeScope = TreeScope.TreeScope_Element | TreeScope.TreeScope_Children

            window_bounding_box = node.BoundingRectangle
            interactive_nodes: list[TreeElementNode] = []
            dom_interactive_nodes: list[TreeElementNode] = []
            dom_informative_nodes: list[TextElementNode] = []
            scrollable_nodes: list[ScrollElementNode] = []
            window_name = self.window_name_correction((node.Name or "").strip())

            self.tree_traversal(
                node,
                window_bounding_box,
                window_name,
                is_browser,
                interactive_nodes,
                scrollable_nodes,
                dom_interactive_nodes,
                dom_informative_nodes,
                is_dom=False,
                is_dialog=False,
                element_cache_req=element_cache_req,
                children_cache_req=children_cache_req,
                hwnd=handle,
            )
            interactive_nodes.extend(dom_interactive_nodes)
            return (interactive_nodes, scrollable_nodes, dom_informative_nodes)
        except UIAException:
            raise
        except COMError as exc:
            raise from_com_error(exc) from exc

    def on_focus_change(self, sender: ctypes.POINTER) -> None:
        """WatchDog focus-change callback: logs the newly focused element,
        debouncing duplicate events fired within 1s of each other."""
        current_time = perf_counter()
        element = Control.CreateControlFromElement(sender)
        event_key = tuple(element.GetRuntimeId())
        last_event = getattr(self, "_last_focus_event", None)
        if last_event is not None:
            last_key, last_time = last_event
            if last_key == event_key and (current_time - last_time) < 1.0:
                return
        self._last_focus_event = (event_key, current_time)
        try:
            logger.debug("[WatchDog] Focus changed to: '%s' (%s)", element.Name, element.ControlTypeName)
        except Exception:
            pass
