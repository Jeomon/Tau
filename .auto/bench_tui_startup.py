"""Headless benchmark: how long it takes to get a ready-to-type TUI.

Mirrors the real `tau` entrypoint's interactive path (tau/console/cli.py
`_start` -> `_run_interactive` -> `App.create`) closely enough to be a
faithful proxy, but stops right after `App.create()` returns instead of
entering the render/input loop (which blocks on a real terminal and would
never exit on its own).

Run as a fresh process each time (see measure.sh) so process + import
overhead — often the dominant cost for a CLI's perceived startup — is
included, not just the async setup work.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


async def _main() -> None:
    from tau.modes.interactive.app import App
    from tau.runtime.service import Runtime
    from tau.runtime.types import RuntimeConfig

    config = RuntimeConfig(
        cwd=REPO_ROOT,
        persist_session=False,  # no session file to clean up between runs
        project_trusted=True,  # skip the interactive trust prompt
        mode="interactive",
    )
    runtime = await Runtime.create(config)
    await App.create(runtime)


if __name__ == "__main__":
    sys.path.insert(0, str(REPO_ROOT))
    asyncio.run(_main())
    # The app is ready to render/accept input at this point — that's the
    # moment being timed. Skip teardown (background threads/tasks, log
    # flushing) so it isn't counted against startup: it isn't part of what
    # the user perceives as "the TUI is up".
    sys.stdout.flush()
    os._exit(0)
