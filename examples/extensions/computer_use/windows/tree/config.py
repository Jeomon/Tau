"""UIA control-type/role taxonomy used to classify accessibility-tree nodes."""

INTERACTIVE_CONTROL_TYPE_NAMES = {
    "ButtonControl",
    "ListItemControl",
    "MenuItemControl",
    "EditControl",
    "CheckBoxControl",
    "RadioButtonControl",
    "DocumentControl",
    "ComboBoxControl",
    "HyperlinkControl",
    "SplitButtonControl",
    "TabItemControl",
    "TreeItemControl",
    "DataItemControl",
    "HeaderItemControl",
    "TextBoxControl",
    "SpinnerControl",
    "ScrollBarControl",
    "SliderControl",
}

INTERACTIVE_ROLES = {
    # Buttons
    "PushButton",
    "SplitButton",
    "ButtonDropDown",
    "ButtonMenu",
    "ButtonDropDownGrid",
    "OutlineButton",
    # Links
    "Link",
    # Inputs & Selection
    "Text",
    "IpAddress",
    "HotkeyField",
    "ComboBox",
    "DropList",
    "CheckButton",
    "RadioButton",
    # Menus & Tabs
    "MenuItem",
    "ListItem",
    "PageTab",
    # Trees
    "OutlineItem",
    # Values
    "Slider",
    "SpinButton",
    "Dial",
    "ScrollBar",
    "Grip",
    # Grids
    "ColumnHeader",
    "RowHeader",
    "Cell",
    # Document
    "Document",
}

DOCUMENT_CONTROL_TYPE_NAMES = {"DocumentControl"}

STRUCTURAL_CONTROL_TYPE_NAMES = {"PaneControl", "GroupControl", "CustomControl"}

INFORMATIVE_CONTROL_TYPE_NAMES = {
    "TextControl",
    "ImageControl",
    "StatusBarControl",
}

DEFAULT_ACTIONS = {"Click", "Press", "Jump", "Check", "Uncheck", "Double Click", "Expand", "Collapse"}

THREAD_MAX_RETRIES = 3
