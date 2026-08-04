"""Accessibility-tree traversal: extracts interactive/scrollable elements from
the running apps' AX trees into a compact, LLM-facing TreeState snapshot."""

from __future__ import annotations

import logging
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed

from AppKit import NSWorkspace

from ...types import Window
from .. import ax
from ..desktop.config import BROWSER_BUNDLE_IDS, SYSTEM_UI_BUNDLE_IDS
from .config import (
    INTERACTIVE_ROLES,
    INTERACTIVE_SUBROLES,
    PRUNABLE_ROLES,
    SCROLLABLE_ROLES,
    STATE_VALUED_ROLES,
    TEXT_INPUT_ROLES,
    WINDOW_CONTROL_SUBROLES,
)
from .views import BoundingBox, ScrollElementNode, TextElementNode, TreeElementNode, TreeState

logger = logging.getLogger(__name__)

_THREAD_MAX_RETRIES = 3
_FINDER_BUNDLE_ID = "com.apple.finder"

# Upper bound on word nodes emitted per text area. Each word costs an
# AXBoundsForRange round-trip, so an uncapped document can cost more than the
# entire rest of the snapshot. Raise it for deeper coverage of a single field;
# set it to 0 to turn word nodes off entirely.
MAX_WORD_NODES_PER_ELEMENT = 200


class Tree:
    """Extracts a TreeState snapshot of interactive/scrollable elements across
    the active window plus a whitelist of system UI apps."""

    # (pids seen, bundle ids that had extras) -- recomputed when the set of
    # running processes changes.
    _extras_cache: tuple[frozenset, list[str]] | None = None

    @classmethod
    def _bundles_with_menu_bar_extras(cls) -> list[str]:
        """Bundle ids of running apps that contribute a menu bar extra.

        Status icons belong to the application that owns them, not to the
        system UI, so an app like Docker or Ollama is never scanned by the
        normal rules -- it is neither frontmost nor system UI -- and its icon
        never reaches a snapshot even though it is plainly visible.

        Only the extras menu bar of these apps is scanned afterwards; walking
        their full window tree would cost far more than the single node they
        contribute.
        """
        try:
            running = [
                app
                for app in NSWorkspace.sharedWorkspace().runningApplications()
                # Includes accessory apps (policy 1), which is what most
                # menu-bar-only tools are.
                if app.activationPolicy() in (0, 1) and app.bundleIdentifier()
            ]
        except Exception as exc:
            logger.debug("could not enumerate running applications: %s", exc)
            return []

        key = frozenset(app.processIdentifier() for app in running)
        cached = cls._extras_cache
        if cached is not None and cached[0] == key:
            return cached[1]

        candidates = [app for app in running if app.bundleIdentifier() not in SYSTEM_UI_BUNDLE_IDS]

        def _has_extras(app):
            try:
                extras = ax.Control(pid=app.processIdentifier()).ExtrasMenuBar
                if extras is not None and extras.GetChildren():
                    return app.bundleIdentifier()
            except Exception:
                pass
            return None

        # Probed in parallel. The first accessibility call to a process is far
        # more expensive than later ones -- roughly 8ms against microseconds --
        # because the connection has to be established, and there are ~50
        # running processes to ask. Serially that is over a second on a cold
        # start; concurrently it is about 200ms.
        bundle_ids: list[str] = []
        if candidates:
            with ThreadPoolExecutor(
                max_workers=min(12, len(candidates)),
                thread_name_prefix="ax-extras-probe",
            ) as pool:
                bundle_ids = [b for b in pool.map(_has_extras, candidates) if b]

        cls._extras_cache = (key, bundle_ids)
        return bundle_ids

    def on_focus_changed(self, element, notification: str, pid: int) -> None:
        """Callback hook for WatchDog focus-change notifications (FocusedUIElementChanged,
        FocusedWindowChanged, MainWindowChanged) — useful for invalidating caches."""
        logger.debug("Focus changed: notification=%s pid=%d", notification, pid)

    def get_state(self, active_window: Window | None) -> TreeState:
        bundle_ids: list[str] = []
        system_bundle_ids: list[str] = []
        desktop_only_bundle_ids: list[str] = []
        for bundle_id in SYSTEM_UI_BUNDLE_IDS:
            if (app := ax.GetRunningApplicationByBundleId(bundle_id)) and app.BundleIdentifier:
                system_bundle_ids.append(app.BundleIdentifier)
                bundle_ids.append(app.BundleIdentifier)
        if active_window is not None and active_window.bundle_id:
            if app := ax.GetRunningApplicationByBundleId(active_window.bundle_id):
                ax.SetAttribute(app.Element, "AXEnhancedUserInterface", True)
            bundle_ids.append(active_window.bundle_id)
            is_windowless = (
                active_window.bundle_id != _FINDER_BUNDLE_ID
                and active_window.width == 0
                and active_window.height == 0
            )
            if is_windowless:
                desktop_only_bundle_ids.append(_FINDER_BUNDLE_ID)

        interactive_nodes, scrollable_nodes, dom_informative_nodes = self.get_window_wise_nodes(
            bundle_ids=bundle_ids,
            system_bundle_ids=system_bundle_ids,
            desktop_only_bundle_ids=desktop_only_bundle_ids,
            extras_only_bundle_ids=self._bundles_with_menu_bar_extras(),
        )

        return TreeState(
            status=True,
            interactive_nodes=interactive_nodes,
            scrollable_nodes=scrollable_nodes,
            dom_informative_nodes=dom_informative_nodes,
        )

    def get_window_wise_nodes(
        self,
        bundle_ids: list[str],
        system_bundle_ids: list[str] | None = None,
        desktop_only_bundle_ids: list[str] | None = None,
        extras_only_bundle_ids: list[str] | None = None,
    ) -> tuple[list[TreeElementNode], list[ScrollElementNode], list[TextElementNode]]:
        interactive_nodes: list[TreeElementNode] = []
        scrollable_nodes: list[ScrollElementNode] = []
        dom_informative_nodes: list[TextElementNode] = []

        system_bundle_ids = system_bundle_ids or []
        desktop_only_bundle_ids = desktop_only_bundle_ids or []
        extras_only_bundle_ids = extras_only_bundle_ids or []

        task_inputs: list[tuple[str, bool, bool, bool]] = []
        for bundle_id in bundle_ids:
            task_inputs.append((bundle_id, bundle_id in BROWSER_BUNDLE_IDS, False, False))
        for bundle_id in desktop_only_bundle_ids:
            if bundle_id not in bundle_ids:
                task_inputs.append((bundle_id, bundle_id in BROWSER_BUNDLE_IDS, True, False))
        # Apps contributing only a status icon: scan the extras menu bar alone.
        for bundle_id in extras_only_bundle_ids:
            if bundle_id not in bundle_ids and bundle_id not in desktop_only_bundle_ids:
                task_inputs.append((bundle_id, False, False, True))

        with ThreadPoolExecutor() as executor:
            retry_counts: dict[str, int] = {bid: 0 for bid, _, __, ___ in task_inputs}
            future_to_bundle_id: dict = {
                executor.submit(self.get_nodes, bid, is_browser, desktop_only, extras_only): bid
                for bid, is_browser, desktop_only, extras_only in task_inputs
            }
            while future_to_bundle_id:
                for future in as_completed(list(future_to_bundle_id)):
                    bundle_id = future_to_bundle_id.pop(future)
                    try:
                        element_nodes, scroll_nodes, info_nodes = future.result()
                        interactive_nodes.extend(element_nodes)
                        scrollable_nodes.extend(scroll_nodes)
                        dom_informative_nodes.extend(info_nodes)
                    except Exception as exc:
                        retry_counts[bundle_id] = retry_counts.get(bundle_id, 0) + 1
                        logger.debug(
                            "Error processing bundle %s, retry %d: %s",
                            bundle_id,
                            retry_counts[bundle_id],
                            exc,
                        )
                        if retry_counts[bundle_id] < _THREAD_MAX_RETRIES:
                            is_browser = next((ib for b, ib, _, __ in task_inputs if b == bundle_id), False)
                            desktop_only = next((do for b, _, do, __ in task_inputs if b == bundle_id), False)
                            extras_only = next((eo for b, _, __, eo in task_inputs if b == bundle_id), False)
                            new_future = executor.submit(
                                self.get_nodes, bundle_id, is_browser, desktop_only, extras_only
                            )
                            future_to_bundle_id[new_future] = bundle_id
                        else:
                            logger.error(
                                "Tree traversal failed for bundle %s after %d retries: %s",
                                bundle_id,
                                _THREAD_MAX_RETRIES,
                                exc,
                                exc_info=True,
                            )
        return interactive_nodes, scrollable_nodes, dom_informative_nodes

    def get_nodes(
        self,
        bundle_id: str,
        is_browser: bool,
        desktop_only: bool = False,
        extras_only: bool = False,
    ) -> tuple[list[TreeElementNode], list[ScrollElementNode], list[TextElementNode]]:
        """Traverse one app's AX tree, starting from each of its windows.

        extras_only: scan only the app's menu bar extras and skip its windows --
        for background apps that contribute a status icon and nothing else.
        """
        app = ax.GetRunningApplicationByBundleId(bundle_id)
        if not app:
            return [], [], []
        ax.SetMessagingTimeout(app.Element, 0.5)

        app_name = app.Name or bundle_id
        interactive_nodes: list[TreeElementNode] = []
        scrollable_nodes: list[ScrollElementNode] = []
        dom_informative_nodes: list[TextElementNode] = []

        if extras_only:
            if extras := app.ExtrasMenuBar:
                self.tree_traversal(
                    extras, app_name, interactive_nodes, scrollable_nodes, [], is_browser=is_browser
                )
            return interactive_nodes, scrollable_nodes, dom_informative_nodes

        if not desktop_only:
            if menubar := app.MenuBar:
                self.tree_traversal(menubar, app_name, interactive_nodes, scrollable_nodes, [], is_browser=is_browser)
            if extras_menubar := app.ExtrasMenuBar:
                self.tree_traversal(
                    extras_menubar, app_name, interactive_nodes, scrollable_nodes, [], is_browser=is_browser
                )

        if main_window := app.MainWindow:
            if main_window_rect := main_window.BoundingRectangle:
                main_window_bounding_box = BoundingBox.from_bounding_rectangle(main_window_rect)
                self.tree_traversal(
                    main_window,
                    app_name,
                    interactive_nodes,
                    scrollable_nodes,
                    dom_informative_nodes,
                    main_window_bounding_box=main_window_bounding_box,
                    is_browser=is_browser,
                )
        else:
            all_windows = app.Windows
            visible_windows = [w for w in all_windows if not ax.GetAttribute(w.Element, "AXMinimized")]
            if visible_windows:
                for window in visible_windows:
                    window_rect = window.BoundingRectangle
                    window_bbox = BoundingBox.from_bounding_rectangle(window_rect) if window_rect else None
                    self.tree_traversal(
                        window,
                        app_name,
                        interactive_nodes,
                        scrollable_nodes,
                        dom_informative_nodes,
                        main_window_bounding_box=window_bbox,
                        is_browser=is_browser,
                    )
            elif not all_windows:
                for child in app.GetChildren():
                    self.tree_traversal(
                        child, app_name, interactive_nodes, scrollable_nodes, dom_informative_nodes, is_browser=is_browser
                    )
        return interactive_nodes, scrollable_nodes, dom_informative_nodes

    def iou_bounding_box(self, window_box: BoundingBox, element_box: BoundingBox) -> BoundingBox:
        """Clip element_box to its intersection with window_box."""
        left = max(window_box.left, element_box.left)
        top = max(window_box.top, element_box.top)
        right = min(window_box.right, element_box.right)
        bottom = min(window_box.bottom, element_box.bottom)
        if right > left and bottom > top:
            return BoundingBox(left=left, top=top, right=right, bottom=bottom, width=right - left, height=bottom - top)
        return BoundingBox(left=0, top=0, right=0, bottom=0, width=0, height=0)

    def _append_word_nodes(
        self,
        element,
        interactive_nodes: list[TreeElementNode],
        window_name: str,
        main_window_bounding_box: BoundingBox | None = None,
    ) -> None:
        """Emit one node per word in a text area, each individually addressable.

        Capped by MAX_WORD_NODES_PER_ELEMENT. Every word costs an
        AXBoundsForRange round-trip, so this is linear in word count and by far
        the most expensive thing the traversal can do: a 400-word text area
        measures ~250ms on its own, against ~220ms for a whole snapshot. The cap
        keeps one large document from dominating the capture.

        A word that soft-wraps yields one box per line, so each of those becomes
        its own node -- the box is what gets clicked, not the word.
        """
        if MAX_WORD_NODES_PER_ELEMENT <= 0:
            return
        try:
            words = ax.Control(element=element).WordBoundingBoxes()
        except Exception:
            return
        if not words:
            # None when the app exposes no range geometry (AXBoundsForRange
            # absent), which is common outside native Cocoa text views.
            return

        emitted = 0
        for word, boxes in words:
            if emitted >= MAX_WORD_NODES_PER_ELEMENT:
                logger.debug(
                    "word node cap (%d) reached for a text area in %s; %d words not emitted",
                    MAX_WORD_NODES_PER_ELEMENT,
                    window_name,
                    len(words) - emitted,
                )
                break
            for rect in boxes:
                bounding_box = BoundingBox.from_bounding_rectangle(rect)
                if main_window_bounding_box:
                    bounding_box = self.iou_bounding_box(main_window_bounding_box, bounding_box)
                if bounding_box.width <= 0 or bounding_box.height <= 0:
                    continue
                interactive_nodes.append(
                    TreeElementNode(
                        bounding_box=bounding_box,
                        center=bounding_box.get_center(),
                        name=word,
                        control_type="Word",
                        window_name=window_name,
                        metadata={},
                    )
                )
            emitted += 1

    def _dom_correction(
        self,
        attrs: dict,
        interactive_nodes: list[TreeElementNode],
        window_name: str,
        main_window_bounding_box: BoundingBox | None = None,
    ) -> None:
        """Browser DOM quirk: an AXLink wrapping an AXHeading should report the
        heading's own geometry/label rather than the link's."""
        if attrs["role"] != "AXLink":
            return
        children = attrs.get("children", [])
        if not children:
            return
        child_attrs = ax.GetTraversalBatch(children[0])
        if child_attrs["role"] != "AXHeading":
            return
        interactive_nodes.pop()
        if not child_attrs["rect"]:
            return
        bounding_box = BoundingBox.from_bounding_rectangle(child_attrs["rect"])
        if main_window_bounding_box:
            bounding_box = self.iou_bounding_box(main_window_bounding_box, bounding_box)
        metadata = {}
        if child_attrs["identifier"]:
            metadata["axidentifier"] = child_attrs["identifier"]
        interactive_nodes.append(
            TreeElementNode(
                bounding_box=bounding_box,
                center=bounding_box.get_center(),
                name=child_attrs["label"] or "",
                control_type=child_attrs["role"] or "",
                window_name=window_name,
                metadata=metadata,
            )
        )

    def _desktop_correction(
        self,
        attrs: dict,
        interactive_nodes: list[TreeElementNode],
        window_name: str,
        main_window_bounding_box: BoundingBox | None = None,
    ) -> None:
        """Native-app quirks: a cell/group wrapping a static-text label should
        report that text as its name; a window-control button should report a
        friendly name instead of its raw title."""
        role = attrs["role"]
        rect = attrs["rect"]
        if role in ("AXCell", "AXGroup"):
            children = attrs.get("children", [])
            current_element = children[0] if children else None
            found_static_text_value = None
            while current_element:
                batch = ax.GetMultipleAttributeValues(
                    current_element, [ax.Attribute.Role, ax.Attribute.Value, ax.Attribute.Children]
                )
                if batch.get(ax.Attribute.Role) == "AXStaticText":
                    found_static_text_value = batch.get(ax.Attribute.Value) or ""
                    break
                next_children = batch.get(ax.Attribute.Children)
                current_element = next_children[0] if next_children else None

            if found_static_text_value is not None:
                node = interactive_nodes.pop()
                bounding_box = BoundingBox.from_bounding_rectangle(rect)
                if main_window_bounding_box:
                    bounding_box = self.iou_bounding_box(main_window_bounding_box, bounding_box)
                interactive_nodes.append(
                    TreeElementNode(
                        bounding_box=bounding_box,
                        center=bounding_box.get_center(),
                        name=found_static_text_value,
                        control_type=role,
                        window_name=window_name,
                        metadata=node.metadata,
                    )
                )
        elif role == "AXButton":
            subrole = attrs["subrole"]
            if subrole in WINDOW_CONTROL_SUBROLES:
                node = interactive_nodes.pop()
                bounding_box = BoundingBox.from_bounding_rectangle(rect)
                if main_window_bounding_box:
                    bounding_box = self.iou_bounding_box(main_window_bounding_box, bounding_box)
                interactive_nodes.append(
                    TreeElementNode(
                        bounding_box=bounding_box,
                        center=bounding_box.get_center(),
                        name=WINDOW_CONTROL_SUBROLES[subrole] or "",
                        control_type=role,
                        window_name=window_name,
                        metadata=node.metadata,
                    )
                )

    def tree_traversal(
        self,
        root_control,
        window_name: str,
        interactive_nodes: list[TreeElementNode],
        scrollable_nodes: list[ScrollElementNode],
        dom_informative_nodes: list[TextElementNode],
        main_window_bounding_box: BoundingBox | None = None,
        is_browser: bool = False,
    ) -> None:
        """Iterative (stack-based) traversal: fetches a minimal attribute batch per
        node to decide interactivity, then a fuller batch only for the small
        minority that turn out interactive."""
        stack = deque([(root_control.Element, is_browser, True)])

        while stack:
            element, current_is_browser, parent_box_ok = stack.pop()
            early = ax.GetEarlyTraversalBatch(element)

            role = early["role"]
            rect = early["rect"]
            children = early["children"]

            if early["hidden"] or role in PRUNABLE_ROLES:
                continue

            if rect is None:
                for child_element in reversed(children):
                    stack.append((child_element, current_is_browser, parent_box_ok))
                continue

            is_visible = rect.width > 1 and rect.height > 1

            if role == "AXMenu" and not is_visible:
                # A closed submenu reports a degenerate (0-size) rect for its
                # entire subtree, so no descendant can pass the is_visible check
                # below. Skip walking the (often large) hidden submenu instead of
                # paying one AX round-trip per item.
                continue
            has_roles = role in INTERACTIVE_ROLES or role == "AXImage"
            has_title_ui_element = bool(early["title_ui_element"])
            # Some elements are clickable without adopting an interactive role.
            # A Notification Centre banner is an AXGroup carrying the subrole
            # AXNotificationCenterBanner.
            #
            # Matched on subrole rather than "advertises an AXPress action":
            # Electron applications attach press actions to wrapper elements
            # liberally, and using actions as the signal added 118 nodes to a
            # 95-node capture -- mostly AXGroup/AXStaticText containers.
            has_interactive_subrole = early["subrole"] in INTERACTIVE_SUBROLES

            is_interactive = (
                (has_roles and early["enabled"])
                or bool(early["help"])
                or early["has_popup"]
                or bool(early["index"])
                or has_title_ui_element
                or has_interactive_subrole
            ) and is_visible

            bounding_box = BoundingBox.from_bounding_rectangle(rect)
            if main_window_bounding_box:
                bounding_box = self.iou_bounding_box(main_window_bounding_box, bounding_box)
                if bounding_box.width == 0 or bounding_box.height == 0:
                    # The element itself has no clickable area, so it is not
                    # emitted -- but a collapsed container says nothing about
                    # its descendants. Instagram positions a floating button
                    # inside a zero-height wrapper at the page bottom, and
                    # pruning here removed the whole subtree even though the
                    # button had a perfectly good rect inside the window.
                    # Mirrors how a None rect is already handled above.
                    if parent_box_ok:
                        for child_element in reversed(children):
                            stack.append((child_element, current_is_browser, False))
                    continue

            if is_interactive:
                late = ax.GetLateTraversalBatch(element, role)

                title_ui_element_text = None
                if early["title_ui_element"] is not None:
                    ref_raw = ax.GetMultipleAttributeValues(
                        early["title_ui_element"], [ax.Attribute.Title, ax.Attribute.Value, ax.Attribute.Description]
                    )
                    title_ui_element_text = (
                        ref_raw.get(ax.Attribute.Title)
                        or ref_raw.get(ax.Attribute.Value)
                        or ref_raw.get(ax.Attribute.Description)
                        or None
                    )

                linked_title = str(title_ui_element_text) if title_ui_element_text else ""
                if role in STATE_VALUED_ROLES:
                    # AXValue here is the state, not a name, so it is skipped:
                    # a switch reporting 1 would otherwise be labelled "1".
                    label = late["title"] or late["description"] or linked_title or late["identifier"]
                else:
                    label = late["label"] or linked_title
                attrs = {**early, **late, "title_ui_element": title_ui_element_text}

                metadata: dict = {}
                if role == "AXTextField":
                    if placeholder := late["placeholder"]:
                        metadata["placeholder"] = placeholder
                    if value := late["value"]:
                        metadata["value"] = value
                elif role in ("AXComboBox", "AXTextArea", "AXRow"):
                    if placeholder := late["placeholder"]:
                        metadata["placeholder"] = placeholder
                    if value := late["value"]:
                        metadata["value"] = value
                    if late["expanded"] is not None:
                        metadata["expanded"] = late["expanded"]
                    if early["has_popup"]:
                        metadata["has_popup"] = early["has_popup"]
                elif role == "AXRadioButton":
                    if value := late["value"]:
                        metadata["selected"] = value
                elif role in ("AXMenuItem", "AXMenuBarItem"):
                    if shortcut := late["shortcut"]:
                        metadata["shortcut"] = shortcut
                elif role == "AXCheckBox":
                    # 0 off, 1 on, 2 mixed -- the third state is what a "select
                    # all" box shows when only some of its items are selected.
                    if (value := late["value"]) is not None:
                        try:
                            state = int(value)
                        except (TypeError, ValueError):
                            state = None
                        if state is not None:
                            metadata["checked"] = "mixed" if state == 2 else bool(state)
                elif role == "AXPopUpButton":
                    if title_ui_element_text:
                        metadata["title"] = title_ui_element_text
                elif role == "AXLink":
                    if (url := late["url"]) and url.startswith(("file://", "http://", "https://")):
                        metadata["url"] = url
                elif role == "AXImage":
                    if filename := late["filename"]:
                        metadata["filename"] = filename
                    if (url := late["url"]) and url.startswith(("file://", "http://", "https://")):
                        metadata["url"] = url

                # Selection state for text-entry roles, kept out of the
                # role-specific chain above so it applies uniformly. A caret
                # (zero-length) is the resting state of every focused field and
                # would be noise on every node, so only real selections are
                # reported -- what matters is whether typing would replace
                # existing content.
                if role in TEXT_INPUT_ROLES or early["subrole"] == "AXSearchField":
                    selection = late.get("selected_range")
                    if selection is not None and selection[1] > 0:
                        metadata["selected_text"] = late.get("selected_text") or ""
                        total = len(str(late["value"] or ""))
                        if selection[0] == 0 and total and selection[1] == total:
                            metadata["all_selected"] = True

                if late.get("identifier"):
                    metadata["axidentifier"] = late["identifier"]

                # Close/minimise/zoom/full-screen buttons expose no title,
                # description, value or identifier, so they are nameless and
                # any name-based filtering drops them. _desktop_correction
                # already names them for native windows, but browsers take the
                # _dom_correction path and would otherwise lose them entirely.
                if not label and (window_subrole := WINDOW_CONTROL_SUBROLES.get(early["subrole"])):
                    label = window_subrole

                # Third-party menu bar extras are pure icons: Claude, Docker,
                # Ollama and LM Studio each expose one AXMenuBarItem with no
                # title, description, value or identifier, so name-based
                # filtering drops them despite being visible with real
                # geometry. The owning application's name is the only
                # meaningful thing to call them.
                #
                # Only unnamed items are affected, so an app's real menus
                # (File, Edit, ...) keep their own titles, and Control Centre's
                # disabled extras stay out because they are already filtered on
                # geometry -- they sit at (0, 900) with zero size.
                if not label and role == "AXMenuBarItem":
                    label = window_name

                interactive_nodes.append(
                    TreeElementNode(
                        bounding_box=bounding_box,
                        center=bounding_box.get_center(),
                        name=label,
                        control_type=role,
                        window_name=window_name,
                        metadata=metadata,
                    )
                )
                if current_is_browser:
                    self._dom_correction(attrs, interactive_nodes, window_name, main_window_bounding_box)
                else:
                    self._desktop_correction(attrs, interactive_nodes, window_name, main_window_bounding_box)

                # Word-level boxes for text areas. Emitted as their own nodes
                # rather than metadata so each word is individually clickable,
                # matching how Windows-MCP surfaces them.
                if role == "AXTextArea":
                    self._append_word_nodes(
                        element, interactive_nodes, window_name, main_window_bounding_box
                    )

            if role in SCROLLABLE_ROLES and is_visible:
                first_child = children[0] if children else None
                scroll_label = ""
                if first_child is not None:
                    scroll_label = ax.GetLateTraversalBatch(first_child)["label"]
                scrollable_nodes.append(
                    ScrollElementNode(
                        name=scroll_label,
                        control_type=role,
                        window_name=window_name,
                        bounding_box=bounding_box,
                        center=bounding_box.get_center(),
                    )
                )

            for child_element in reversed(children):
                stack.append((child_element, current_is_browser, True))
