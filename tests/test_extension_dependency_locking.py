"""Cross-thread regression tests for extension dependency cache transactions."""

import concurrent.futures
import json
import threading
import time
from pathlib import Path

from tau.extensions.loader import ExtensionLoader


def test_dependency_resolution_serializes_install_and_cache_write(tmp_path, monkeypatch) -> None:
    venv = tmp_path / "venv"
    subdir = tmp_path / "extension"
    subdir.mkdir()
    installs: list[list[str]] = []
    install_started = threading.Event()

    class FakePackageManager:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def install_requirements(self, deps: list[str]) -> None:
            installs.append(deps)
            install_started.set()
            time.sleep(0.05)  # give the other resolver time to contend for the lock

        def site_packages(self) -> Path:
            return venv / "site-packages"

    monkeypatch.setattr("tau.packages.manager.PackageManager", FakePackageManager)
    monkeypatch.setattr("tau.extensions.loader.add_site_packages_path", lambda _path: None)
    loader = ExtensionLoader(cwd=tmp_path)
    monkeypatch.setattr(loader, "_resolve_venv_dir", lambda _source: venv)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(loader._ensure_dependencies, subdir, ["demo==1"], "global")
            for _ in range(2)
        ]
        for future in futures:
            future.result(timeout=3)

    assert install_started.is_set()
    assert installs == [["demo==1"]]
    cache = json.loads((venv / ".tau_ext_deps.json").read_text(encoding="utf-8"))
    entry = cache[str(subdir.resolve())]
    assert entry["ok"] is True
