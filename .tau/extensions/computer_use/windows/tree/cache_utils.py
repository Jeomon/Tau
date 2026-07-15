"""UIA property/pattern caching utilities: batches COM calls during tree traversal
so each node's commonly-needed properties are fetched in one cross-process round
trip instead of one call per property."""

from __future__ import annotations

import logging

from ..uia import CacheRequest, Control, PatternId, PropertyId, TreeScope

logger = logging.getLogger(__name__)


class CacheRequestFactory:
    """Builds cache requests optimized for tree traversal."""

    @staticmethod
    def create_tree_traversal_cache() -> CacheRequest:
        cache_request = CacheRequest()
        cache_request.TreeScope = TreeScope.TreeScope_Element | TreeScope.TreeScope_Children

        cache_request.AddProperty(PropertyId.NameProperty)
        cache_request.AddProperty(PropertyId.AutomationIdProperty)
        cache_request.AddProperty(PropertyId.LocalizedControlTypeProperty)
        cache_request.AddProperty(PropertyId.AcceleratorKeyProperty)
        cache_request.AddProperty(PropertyId.ClassNameProperty)
        cache_request.AddProperty(PropertyId.ControlTypeProperty)

        cache_request.AddProperty(PropertyId.IsEnabledProperty)
        cache_request.AddProperty(PropertyId.IsOffscreenProperty)
        cache_request.AddProperty(PropertyId.IsControlElementProperty)
        cache_request.AddProperty(PropertyId.HasKeyboardFocusProperty)
        cache_request.AddProperty(PropertyId.IsKeyboardFocusableProperty)
        cache_request.AddProperty(PropertyId.IsPasswordProperty)

        cache_request.AddProperty(PropertyId.BoundingRectangleProperty)
        cache_request.AddProperty(PropertyId.HelpTextProperty)

        cache_request.AddPattern(PatternId.LegacyIAccessiblePattern)
        cache_request.AddPattern(PatternId.ScrollPattern)
        cache_request.AddPattern(PatternId.WindowPattern)

        cache_request.AddProperty(PropertyId.LegacyIAccessibleRoleProperty)
        cache_request.AddProperty(PropertyId.LegacyIAccessibleValueProperty)
        cache_request.AddProperty(PropertyId.LegacyIAccessibleDefaultActionProperty)
        cache_request.AddProperty(PropertyId.LegacyIAccessibleStateProperty)

        cache_request.AddProperty(PropertyId.ScrollHorizontallyScrollableProperty)
        cache_request.AddProperty(PropertyId.ScrollVerticallyScrollableProperty)
        cache_request.AddProperty(PropertyId.ScrollHorizontalScrollPercentProperty)
        cache_request.AddProperty(PropertyId.ScrollVerticalScrollPercentProperty)

        cache_request.AddProperty(PropertyId.ExpandCollapseExpandCollapseStateProperty)

        cache_request.AddProperty(PropertyId.SelectionCanSelectMultipleProperty)
        cache_request.AddProperty(PropertyId.SelectionIsSelectionRequiredProperty)
        cache_request.AddProperty(PropertyId.SelectionSelectionProperty)

        cache_request.AddProperty(PropertyId.SelectionItemIsSelectedProperty)
        cache_request.AddProperty(PropertyId.SelectionItemSelectionContainerProperty)

        cache_request.AddProperty(PropertyId.WindowIsModalProperty)

        cache_request.AddProperty(PropertyId.ToggleToggleStateProperty)

        cache_request.AddProperty(PropertyId.RangeValueValueProperty)
        cache_request.AddProperty(PropertyId.RangeValueMinimumProperty)
        cache_request.AddProperty(PropertyId.RangeValueMaximumProperty)

        return cache_request


class CachedControlHelper:
    """Helper for building/reading UIA controls with pre-cached properties."""

    @staticmethod
    def build_cached_control(node: Control, cache_request: CacheRequest | None = None) -> Control:
        if cache_request is None:
            cache_request = CacheRequestFactory.create_tree_traversal_cache()
        try:
            cached_node = node.BuildUpdatedCache(cache_request)
            cached_node._is_cached = True  # type: ignore[attr-defined]
            return cached_node
        except Exception as exc:
            logger.debug("Failed to build cached control: %s", exc)
            return node

    @staticmethod
    def get_cached_children(node: Control, cache_request: CacheRequest | None = None) -> list[Control]:
        """Fetch node's children with properties already cached, eliminating
        per-child property round trips."""
        if cache_request is None:
            cache_request = CacheRequestFactory.create_tree_traversal_cache()

        # Only the Children scope is needed here — omitting TreeScope_Element
        # avoids redundant property fetches for the parent node itself.
        req_clone = cache_request.Clone()
        req_clone.TreeScope = TreeScope.TreeScope_Children

        try:
            # Bypass Control.CreateControlFromElement when building the cache:
            # with TreeScope_Element omitted, BuildUpdatedCache returns a dummy
            # element with no valid properties (e.g. CurrentControlType), which
            # would crash our wrapper. Use the raw IUIAutomationElement instead.
            updated_element = node.Element.BuildUpdatedCache(req_clone.check_request)  # type: ignore[union-attr]
            element_array = updated_element.GetCachedChildren()

            children: list[Control] = []
            if element_array:
                for i in range(element_array.Length):
                    child_elem = element_array.GetElement(i)
                    child_control = Control.CreateControlFromElement(child_elem)
                    if child_control:
                        child_control._is_cached = True  # type: ignore[attr-defined]
                        children.append(child_control)

            logger.debug("Retrieved %d cached children (newly built)", len(children))
            return children
        except Exception as exc:
            logger.debug("Failed to get cached children, falling back to regular access: %s", exc)
            return node.GetChildren()
