"""Load a bundled extension in tests the way tau's loader does."""

from __future__ import annotations


def load_extension(name: str, *, builtin: bool = False):
    """Import a bundled extension the way tau's loader does — as a package.

    The loader gives each extension directory a unique package name
    (``_tau_ext_<hash of path>``) whose ``__init__.py`` makes it a package, so
    siblings are reached with relative imports and never occupy a global name
    like ``state`` or ``agents``. Tests must load it the same way: importing a
    sibling by bare name both fails (relative import with no parent) and
    reintroduces the collision the packaging exists to prevent.

    Returns the package module; reach submodules via
    ``importlib.import_module(f"{pkg.__name__}.state")``.
    """
    import hashlib
    import importlib.util
    import sys
    from pathlib import Path

    root = Path(__file__).parent.parent
    base = root / "tau" / "builtins" / "extensions" if builtin else root / ".tau" / "extensions"
    directory = base / name
    module_name = f"_tau_ext_{hashlib.sha1(str(directory.resolve()).encode()).hexdigest()[:16]}"

    if module_name in sys.modules:
        return sys.modules[module_name]

    spec = importlib.util.spec_from_file_location(module_name, directory / "__init__.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module
