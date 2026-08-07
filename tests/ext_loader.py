"""Load a bundled extension in tests the way tau's loader does."""

from __future__ import annotations


def extension_dir(name: str, *, builtin: bool = False):
    """Where a bundled extension's source lives, for tests.

    Project extensions are looked up in ``.tau/extensions`` first — that is the
    copy actually loaded when tau runs here, so local edits are what gets
    tested — and fall back to the tracked mirror in ``examples/extensions``.

    The fallback is what makes the suite survive a fresh clone: ``.tau/`` is
    gitignored in its entirety, so on a clean checkout the project copy simply
    does not exist and every test loading one used to fail at import, with
    nothing to indicate the source was absent rather than broken.
    """
    from pathlib import Path

    root = Path(__file__).parent.parent
    if builtin:
        return root / "tau" / "builtins" / "extensions" / name
    local = root / ".tau" / "extensions" / name
    if (local / "__init__.py").exists():
        return local
    return root / "examples" / "extensions" / name


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

    directory = extension_dir(name, builtin=builtin)
    module_name = f"_tau_ext_{hashlib.sha1(str(directory.resolve()).encode()).hexdigest()[:16]}"

    if module_name in sys.modules:
        return sys.modules[module_name]

    spec = importlib.util.spec_from_file_location(module_name, directory / "__init__.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module
