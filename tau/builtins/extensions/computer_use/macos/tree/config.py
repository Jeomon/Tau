"""AX role taxonomy used to classify accessibility-tree nodes during traversal."""

# Interactive roles - elements that users can interact with
INTERACTIVE_ROLES = {
    "AXButton",
    "AXCheckBox",
    "AXRadioButton",
    "AXTextField",
    "AXTextArea",
    "AXComboBox",
    "AXPopUpButton",
    "AXSlider",
    "AXIncrementor",
    "AXLink",
    "AXMenuItem",
    "AXMenuButton",
    "AXMenuBarItem",
    "AXTab",
    "AXDockItem",
    "AXCell",
    "AXToggle",
    "AXSwitch",
    "AXDisclosureTriangle",
    "AXColorWell",
    "AXLevelIndicator",
    "AXValueIndicator",
}

# Container roles - elements that hold other elements
CONTAINER_ROLES = {
    "AXWindow",
    "AXToolbar",
    "AXGroup",
    "AXScrollArea",
    "AXSplitGroup",
    "AXList",
    "AXTabGroup",
    "AXWebArea",
    "AXPopover",
    "AXSheet",
    "AXLayoutArea",
    "AXLayoutItem",
}

# Non-interactive roles - informational elements
NON_INTERACTIVE_ROLES = {
    "AXList",
    "AXMenuBar",
    "AXMenu",
    "AXGroup",
    "AXScrollArea",
    "AXStaticText",
    "AXRadioGroup",
    "AXGrid",
    "AXApplication",
    "AXWindow",
    "AXToolbar",
    "AXSplitGroup",
    "AXTabGroup",
    "AXWebArea",
}

# Scrollable roles - elements that can be scrolled
SCROLLABLE_ROLES = {
    "AXScrollArea",
    "AXScrollView",
    "AXWebArea",
    "AXBrowser",
}

# Actions that indicate an element is interactive
INTERACTIVE_ACTIONS = {
    "AXPress",
    "AXConfirm",
    "AXCancel",
    "AXIncrement",
    "AXDecrement",
    "AXShowMenu",
    "AXPick",
    "AXRaise",
}

# Window control subroles with friendly names
WINDOW_CONTROL_SUBROLES = {
    "AXCloseButton": "Close Button",
    "AXMinimizeButton": "Minimize Button",
    "AXZoomButton": "Zoom Button",
    "AXFullScreenButton": "Full Screen Button",
}

# Roles skipped during traversal: decorative/structural elements with no
# interactive children, or subtrees that never yield actionable nodes.
PRUNABLE_ROLES = {
    "AXScrollBar",
    "AXGrowArea",
    "AXUnknown",
    "AXValueIndicator",
    "AXLevelIndicator",
    "AXProgressIndicator",
    "AXSeparator",
    "AXSplitter",
    "AXHandle",
    "AXRuler",
    "AXRulerMarker",
    "AXBusyIndicator",
    "AXRelevanceIndicator",
    "AXSizeHandle",
    "AXResizeIndicator",
}
