"""Tests for ExtensionLoader._resolve_source_priority (tau/extensions/loader.py).

_resolve_source_priority is a pure function over (Path, source) pairs — no
filesystem access — so these use fabricated paths rather than real extension
directories.
"""

from __future__ import annotations

from pathlib import Path

from tau.extensions.loader import ExtensionLoader


def _init(root: str) -> Path:
    """A folder-based extension entry point, e.g. tau/builtins/extensions/web/__init__.py."""
    return Path(root) / "extensions" / "web" / "__init__.py"


def _other_init(root: str, name: str) -> Path:
    return Path(root) / "extensions" / name / "__init__.py"


class TestRankedSourcesUnaffected:
    """Existing project > global > builtin behavior must survive untouched."""

    def test_project_beats_global_and_builtin(self):
        found = [
            (_init("/builtins"), "builtin"),
            (_init("/global"), "global"),
            (_init("/project"), "project"),
        ]
        result = ExtensionLoader._resolve_source_priority(found)
        assert result == [(_init("/project"), "project")]

    def test_global_beats_builtin_when_no_project(self):
        found = [
            (_init("/builtins"), "builtin"),
            (_init("/global"), "global"),
        ]
        result = ExtensionLoader._resolve_source_priority(found)
        assert result == [(_init("/global"), "global")]

    def test_distinct_identities_all_pass_through(self):
        found = [
            (_init("/builtins"), "builtin"),
            (_other_init("/builtins", "todo"), "builtin"),
        ]
        result = ExtensionLoader._resolve_source_priority(found)
        assert set(result) == set(found)


class TestExplicitEntryCollidingWithBuiltin:
    """An extensions.list ('explicit') entry pointing at what is really the
    same extension as an already-loaded builtin/global/project entry — e.g.
    a settings.json entry pointing straight at tau's own bundled `web`
    builtin, possibly from a different tau install than the one currently
    running — must configure that entry, not double-register it.
    """

    def test_explicit_entry_sharing_identity_with_builtin_is_dropped(self):
        builtin_entry = (_init("/dev-checkout/tau/builtins"), "builtin")
        explicit_entry = (_init("/other-install/site-packages/tau/builtins"), "explicit")

        result = ExtensionLoader._resolve_source_priority([builtin_entry, explicit_entry])

        assert result == [builtin_entry]

    def test_explicit_entry_sharing_identity_with_global_is_dropped(self):
        global_entry = (_init("/home/.tau/extensions"), "global")
        explicit_entry = (_init("/somewhere/else"), "explicit")

        result = ExtensionLoader._resolve_source_priority([global_entry, explicit_entry])

        assert result == [global_entry]

    def test_explicit_entry_with_no_matching_identity_passes_through(self):
        builtin_entry = (_other_init("/builtins", "todo"), "builtin")
        explicit_entry = (_init("/some/third-party/path"), "explicit")

        result = ExtensionLoader._resolve_source_priority([builtin_entry, explicit_entry])

        assert set(result) == {builtin_entry, explicit_entry}

    def test_two_explicit_entries_never_dedup_against_each_other(self):
        # Unranked-vs-unranked collisions are out of scope for this scheme —
        # only unranked-vs-ranked collisions get dropped.
        a = (_init("/path/a"), "explicit")
        b = (_init("/path/b"), "explicit")

        result = ExtensionLoader._resolve_source_priority([a, b])

        assert set(result) == {a, b}
