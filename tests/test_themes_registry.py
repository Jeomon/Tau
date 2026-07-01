"""Tests for tau/themes/registry.py — ThemeRegistry."""

from __future__ import annotations

import pytest

from tau.themes.registry import ThemeRegistry


def _registry() -> ThemeRegistry:
    r = ThemeRegistry()
    r._builtins_loaded = True
    return r


class TestThemeRegistryRegister:
    def test_register_instance(self):
        from tau.tui.theme import LayoutTheme

        r = _registry()
        t = LayoutTheme()
        r.register("custom", t)
        result = r.get("custom")
        assert result is not None

    def test_register_factory(self):
        from tau.tui.theme import LayoutTheme

        r = _registry()
        r.register("factory_theme", lambda: LayoutTheme())
        result = r.get("factory_theme")
        assert result is not None

    def test_register_instance_has_runtime_source(self):
        from tau.tui.theme import LayoutTheme

        r = _registry()
        r.register("rt", LayoutTheme())
        assert r.source("rt") == "runtime"

    def test_register_factory_has_runtime_source(self):
        from tau.tui.theme import LayoutTheme

        r = _registry()
        r.register("rt_factory", lambda: LayoutTheme())
        assert r.source("rt_factory") == "runtime"

    def test_register_case_insensitive_lookup(self):
        from tau.tui.theme import LayoutTheme

        r = _registry()
        r.register("MyTheme", LayoutTheme())
        assert r.get("mytheme") is not None
        assert r.get("MYTHEME") is not None

    def test_register_overwrite(self):
        from tau.tui.theme import LayoutTheme

        r = _registry()
        t1 = LayoutTheme()
        t2 = LayoutTheme()
        r.register("same", t1)
        r.register("same", t2)
        # second registration wins; just verify no error and name still resolves
        assert r.get("same") is not None


class TestThemeRegistryGet:
    def test_get_unknown_raises_value_error(self):
        r = _registry()
        with pytest.raises(ValueError):
            r.get("nonexistent_theme_xyz")

    def test_get_returns_theme(self):
        from tau.tui.theme import LayoutTheme

        r = _registry()
        r.register("mythem", LayoutTheme())
        result = r.get("mythem")
        assert isinstance(result, LayoutTheme)


class TestThemeRegistryUnregister:
    def test_unregister_removes_theme(self):
        from tau.tui.theme import LayoutTheme

        r = _registry()
        r.register("to_remove", LayoutTheme())
        r.unregister("to_remove")
        with pytest.raises(ValueError):
            r.get("to_remove")

    def test_unregister_unknown_raises_value_error(self):
        r = _registry()
        with pytest.raises(ValueError):
            r.unregister("does_not_exist")

    def test_unregister_removes_source(self):
        from tau.tui.theme import LayoutTheme

        r = _registry()
        r.register("bye", LayoutTheme())
        r.unregister("bye")
        assert r.source("bye") == "unknown"


class TestThemeRegistryList:
    def test_list_empty(self):
        r = _registry()
        assert r.list() == []

    def test_list_returns_names(self):
        from tau.tui.theme import LayoutTheme

        r = _registry()
        r.register("alpha", LayoutTheme())
        r.register("beta", LayoutTheme())
        names = r.list()
        assert "alpha" in names
        assert "beta" in names

    def test_list_lowercase(self):
        from tau.tui.theme import LayoutTheme

        r = _registry()
        r.register("MyMixedCase", LayoutTheme())
        assert "mymixedcase" in r.list()


class TestThemeRegistryGetDefault:
    def test_get_default_returns_something(self):
        r = ThemeRegistry()
        result = r.get_default()
        assert result is not None

    def test_get_default_with_dark_builtin(self):
        r = ThemeRegistry()
        from tau.tui.theme import LayoutTheme

        result = r.get_default()
        assert isinstance(result, LayoutTheme)

    def test_get_default_fallback_when_empty(self):
        from tau.tui.theme import LayoutTheme

        r = _registry()
        result = r.get_default()
        assert isinstance(result, LayoutTheme)


class TestThemeRegistrySource:
    def test_source_unknown_for_nonexistent(self):
        r = _registry()
        assert r.source("nope") == "unknown"

    def test_source_builtin_from_real_registry(self):
        r = ThemeRegistry()
        r._ensure_builtins()
        names = r.list()
        if names:
            assert r.source(names[0]) == "builtin"


class TestThemeRegistryEnsureBuiltins:
    def test_builtins_loaded_on_first_access(self):
        r = ThemeRegistry()
        assert not r._builtins_loaded
        r._ensure_builtins()
        assert r._builtins_loaded

    def test_builtin_themes_present(self):
        r = ThemeRegistry()
        r._ensure_builtins()
        assert len(r._registry) > 0

    def test_ensure_builtins_idempotent(self):
        r = ThemeRegistry()
        r._ensure_builtins()
        count = len(r._registry)
        r._ensure_builtins()
        assert len(r._registry) == count
