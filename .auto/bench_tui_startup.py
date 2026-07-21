"""Headless benchmark: how long it takes to get a ready-to-type TUI.

Mirrors the real `tau` entrypoint's interactive path (tau/console/cli.py
`_start` -> `_run_interactive` -> `App.create`) closely enough to be a
faithful proxy, but stops right after `App.create()` returns instead of
entering the render/input loop (which blocks on a real terminal and would
never exit on its own).

Run as a fresh process each time (see measure.sh) so process + import
overhead — often the dominant cost for a CLI's perceived startup — is
included, not just the async setup work.

Deliberately does *not* use ``asyncio.run()``: that helper's cleanup path
(``shutdown_default_executor``) blocks until every ``asyncio.to_thread``
background job still running has finished — including things like the
project-local LSP extension's `runtime_ready`-triggered eager server
warm-up, which walks the whole project tree and is fired with
``asyncio.ensure_future`` (fire-and-forget) precisely so it does *not*
block the TUI becoming interactive. In the real app that thread keeps
running quietly after the user is already typing; measuring it here would
count work that never actually delays perceived startup. Using a bare
event loop + immediate ``os._exit`` avoids waiting on it, matching what a
user actually experiences. (Measured impact: ~120-150ms of the original
~850ms baseline — see .auto/prompt.md.)
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
    loop = asyncio.new_event_loop()
    loop.run_until_complete(_main())
    # The app is ready to render/accept input at this point — that's the
    # moment being timed. Skip teardown (background threads/tasks, log
    # flushing, asyncio.run()'s executor-shutdown wait) so it isn't counted
    # against startup: it isn't part of what the user perceives as "the TUI
    # is up".
    sys.stdout.flush()
    os._exit(0)
