"""A rejected manifest settings field is reported, not silently dropped.

`build_manifest_panel` drops any field it cannot understand. The symptom is a
setting that is simply absent from `/settings` — and the only trace was a
warning on a logger `_redirect_logging_off_terminal` deliberately routes to a
file, so the author sees a missing knob and no reason for it.

The case that prompted this: permissions' manifest declared
``"type": "number"`` for its prompt timeout. ``number`` is the JSON Schema
spelling and is not in ``_LEAF_TYPES``, so the one knob controlling how long a
permission prompt waits before expiring to *denied* never appeared in the UI.
"""

from __future__ import annotations

from typing import Any

import pytest

from tau.modes.interactive.components.settings_selector import (
    _LEAF_TYPES,
    build_manifest_panel,
)


def _schema(*fields: dict) -> dict:
    return {"title": "Demo", "fields": list(fields)}


def _build(schema: dict) -> tuple[Any, list[str]]:
    problems: list[str] = []
    panel = build_manifest_panel(
        schema,
        {},
        default_title="demo",
        apply=lambda key, value: None,
        on_problem=problems.append,
    )
    return panel, problems


_GOOD = {"key": "keep", "label": "Keep", "type": "bool", "default": True}


def test_an_unknown_type_is_reported() -> None:
    _, problems = _build(_schema({"key": "timeout", "type": "number", "default": 600}))

    assert len(problems) == 1
    assert "number" in problems[0]
    assert "timeout" in problems[0]


def test_the_message_names_the_types_that_would_have_worked() -> None:
    """ "unknown type" without the valid set leaves the author guessing."""
    _, problems = _build(_schema({"key": "timeout", "type": "number"}))

    for leaf in _LEAF_TYPES:
        assert leaf in problems[0], f"{leaf} missing from the guidance"


def test_a_field_with_no_key_is_reported() -> None:
    _, problems = _build(_schema({"label": "Nameless", "type": "bool"}))

    assert len(problems) == 1
    assert "key" in problems[0]


def test_a_non_object_field_is_reported() -> None:
    _, problems = _build(_schema("not a field"))  # type: ignore[arg-type]

    assert len(problems) == 1


def test_an_enum_without_values_is_reported() -> None:
    _, problems = _build(_schema({"key": "mode", "type": "enum"}))

    assert len(problems) == 1
    assert "values" in problems[0]


def test_a_valid_schema_reports_nothing() -> None:
    panel, problems = _build(_schema(_GOOD))

    assert problems == []
    assert [item.id for item in panel.items] == ["keep"]


def test_the_good_fields_still_build_alongside_a_bad_one() -> None:
    """One malformed field must not cost the whole panel."""
    panel, problems = _build(_schema(_GOOD, {"key": "bad", "type": "number"}))

    assert [item.id for item in panel.items] == ["keep"]
    assert len(problems) == 1


def test_reporting_is_optional() -> None:
    """Callers outside the loader (tests, --print) pass no reporter."""
    panel = build_manifest_panel(
        _schema(_GOOD, {"key": "bad", "type": "number"}),
        {},
        default_title="demo",
        apply=lambda key, value: None,
    )

    assert [item.id for item in panel.items] == ["keep"]


def test_nested_group_problems_carry_the_full_path() -> None:
    """Without the prefix, two groups with the same key are indistinguishable."""
    _, problems = _build(
        _schema(
            {
                "key": "advanced",
                "type": "group",
                "fields": [{"key": "timeout", "type": "number"}],
            }
        )
    )

    assert "advanced.timeout" in problems[0]


class TestLoaderReporting:
    """The loader turns a problem into an ExtensionError the UI already shows."""

    def _loader(self, reported: list[Any]) -> Any:
        from tau.extensions.loader import ExtensionLoader

        class _Runtime:
            @staticmethod
            def report_extension_error(error: Any) -> None:
                reported.append(error)

        class _Ref:
            runtime = _Runtime()

        loader = ExtensionLoader.__new__(ExtensionLoader)
        loader._runtime_ref = _Ref()  # type: ignore[attr-defined]
        return loader

    def _ext(self) -> Any:
        from tau.extensions.loader import Extension

        ext = Extension.__new__(Extension)
        ext.path = "/x/permissions/__init__.py"
        return ext

    def test_a_problem_becomes_an_extension_error(self) -> None:
        reported: list[Any] = []

        self._loader(reported)._record_settings_error(self._ext(), "unknown field type 'number'")

        assert len(reported) == 1
        assert reported[0].event == "settings_schema"
        assert reported[0].extension_path == "/x/permissions/__init__.py"
        assert "number" in reported[0].error

    def test_a_runtime_that_cannot_report_is_tolerated(self) -> None:
        from tau.extensions.loader import ExtensionLoader

        class _Ref:
            runtime = object()

        loader = ExtensionLoader.__new__(ExtensionLoader)
        loader._runtime_ref = _Ref()  # type: ignore[attr-defined]

        loader._record_settings_error(self._ext(), "boom")  # must not raise

    def test_a_raising_reporter_does_not_break_the_panel(self) -> None:
        """This runs mid-load; escaping would cost the extension its whole panel."""
        from tau.extensions.loader import ExtensionLoader

        class _Runtime:
            @staticmethod
            def report_extension_error(error: Any) -> None:
                raise RuntimeError("reporter is broken")

        class _Ref:
            runtime = _Runtime()

        loader = ExtensionLoader.__new__(ExtensionLoader)
        loader._runtime_ref = _Ref()  # type: ignore[attr-defined]

        loader._record_settings_error(self._ext(), "boom")  # must not raise


@pytest.mark.parametrize("leaf", sorted(_LEAF_TYPES - {"enum", "select"}))
def test_every_documented_leaf_type_is_accepted(leaf: str) -> None:
    panel, problems = _build(_schema({"key": "f", "type": leaf, "default": ""}))

    assert problems == []
    assert [item.id for item in panel.items] == ["f"]
