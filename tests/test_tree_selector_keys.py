"""Every key the tree picker advertises must reach a method that exists.

`/tree` crashed on each printable keystroke:

    internal error: AttributeError: 'TreeSelectList' object has no attribute
    'append_search'  in <Handle TUI._on_stdin_ready()>

The filtering was already built and already advertised — `_help_lines` lists
"type to search" and `render` draws the query back with a cursor — but the two
methods `SelectorController` calls to feed it were never defined.

The same defect was fixed on `SelectList` earlier and not here, so the contract
test below checks *every* method the controller invokes on a tree selector
rather than the two that happened to be reported.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tau.modes.interactive.components.tree_selector import TreeRow, TreeSelectList
from tau.tui.theme import Style

_CONTROLLER = Path("tau/modes/interactive/components/selector_controller.py")


def _tree(*labels: str) -> TreeSelectList[str]:
    rows = [
        TreeRow(prefix="", role="user", text=label, value=label, search_text=label)
        for label in labels
    ]
    return TreeSelectList(
        rows,
        role_style=lambda _role, _text: Style(),
        accent_style=Style(),
        dim_style=Style(),
    )


def test_every_method_the_controller_calls_exists() -> None:
    """The contract, not the two methods that happened to be reported.

    Scoped to the shared fallback. The kind-specific handlers (`_handle_model`
    and friends) also call methods by name, but each is reached only for the
    one selector kind that defines them; `_handle_generic` is the one that
    serves several kinds, which is why a gap there reaches a user.
    """
    import inspect

    from tau.modes.interactive.components.selector_controller import SelectorController

    source = inspect.getsource(SelectorController._handle_generic)
    called = sorted(set(re.findall(r"\btree\.(\w+)\(", source)))

    assert called, "expected the fallback handler to drive a tree selector by name"
    missing = [name for name in called if not hasattr(TreeSelectList, name)]
    assert missing == [], f"TreeSelectList is missing {missing}"


def test_the_plain_selector_path_is_guarded_or_satisfied() -> None:
    """The other half of the same fallback, which took the first bug report."""
    import inspect

    from tau.modes.interactive.components.selector_controller import SelectorController
    from tau.tui.components.select_list import SelectList

    source = "".join(
        inspect.getsource(fn)
        for fn in (
            SelectorController._handle_generic,
            SelectorController._feed_search,
            SelectorController._feed_paging,
        )
    )
    called = set(re.findall(r"\bselector\.(\w+)\(", source))
    guarded = set(re.findall(r'getattr\(\s*selector,\s*"(\w+)"', source))

    missing = [
        name for name in sorted(called) if not hasattr(SelectList, name) and name not in guarded
    ]
    assert missing == [], f"unguarded and undefined on SelectList: {missing}"


def test_typing_filters_the_tree() -> None:
    tree = _tree("alpha", "beta", "gamma")

    tree.append_search("bet")

    assert [row.value for row in tree._filtered] == ["beta"]


def test_backspace_widens_the_filter() -> None:
    tree = _tree("alpha", "beta", "gamma")
    for char in "bet":
        tree.append_search(char)

    for _ in range(3):
        tree.backspace_search()

    assert [row.value for row in tree._filtered] == ["alpha", "beta", "gamma"]


def test_backspace_on_an_empty_query_is_harmless() -> None:
    tree = _tree("alpha")

    tree.backspace_search()

    assert [row.value for row in tree._filtered] == ["alpha"]


def test_the_query_is_shown_back_to_the_user() -> None:
    """render() draws the query, so it has to be what append_search built."""
    tree = _tree("alpha", "beta")

    tree.append_search("al")

    assert tree._query == "al"


@pytest.mark.parametrize("keys", ["a", "zzz", "beta"])
def test_no_printable_sequence_raises(keys: str) -> None:
    tree = _tree("alpha", "beta", "gamma")

    for char in keys:
        tree.append_search(char)  # must not raise
