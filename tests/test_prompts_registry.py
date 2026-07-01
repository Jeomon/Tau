"""Tests for tau/prompts/registry.py — PromptRegistry."""

from __future__ import annotations

from tau.prompts.registry import PromptRegistry
from tau.prompts.types import PromptTemplate


def _template(
    name: str, content: str = "Hello $ARGUMENTS", description: str = "A prompt"
) -> PromptTemplate:
    return PromptTemplate(
        name=name, description=description, content=content, file_path="/fake/prompt.md"
    )


def _registry(*templates: PromptTemplate) -> PromptRegistry:
    r = PromptRegistry()
    r._registry.clear()
    r._builtins_loaded = True
    for t in templates:
        r._registry[t.name.lower()] = t
    return r


class TestPromptRegistryRegister:
    def test_register_and_get(self):
        r = _registry()
        t = _template("greet")
        r._registry["greet"] = t
        assert r.get("greet") is t

    def test_get_unknown_returns_none(self):
        r = _registry()
        assert r.get("nonexistent") is None

    def test_get_case_insensitive(self):
        r = _registry(_template("Greet"))
        assert r.get("greet") is not None
        assert r.get("GREET") is not None


class TestPromptRegistryExpand:
    def test_expand_known_template(self):
        r = _registry(_template("greet", content="Hello $ARGUMENTS"))
        result = r.expand("greet", "World")
        assert result == "Hello World"

    def test_expand_unknown_returns_none(self):
        r = _registry()
        result = r.expand("nonexistent", "args")
        assert result is None

    def test_expand_no_args(self):
        r = _registry(_template("plain", content="No args here"))
        result = r.expand("plain", "")
        assert result == "No args here"

    def test_expand_positional_arg(self):
        r = _registry(_template("pos", content="$1 is cool"))
        result = r.expand("pos", "Python")
        assert result == "Python is cool"

    def test_expand_multiple_args(self):
        r = _registry(_template("multi", content="$1 and $2"))
        result = r.expand("multi", "foo bar")
        assert result == "foo and bar"

    def test_expand_case_insensitive_lookup(self):
        r = _registry(_template("Greet", content="Hello $ARGUMENTS"))
        result = r.expand("GREET", "World")
        assert result == "Hello World"


class TestPromptRegistryList:
    def test_list_empty(self):
        r = _registry()
        assert r.list() == []

    def test_list_returns_templates(self):
        r = _registry(_template("a"), _template("b"), _template("c"))
        templates = r.list()
        names = [t.name for t in templates]
        assert "a" in names
        assert "b" in names

    def test_list_count(self):
        r = _registry(_template("x"), _template("y"))
        assert len(r.list()) == 2
