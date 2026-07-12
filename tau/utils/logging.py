from __future__ import annotations

import logging


def attach_session_log_file(session_id: str) -> logging.Handler | None:
    """Attach a FileHandler for this run's log file to the root logger.

    One log file per run, named by session id, under the global logs dir
    (``tau.settings.paths.get_log_file_path``) — the same path advertised to
    the model in the system prompt's Log File section. Best-effort: returns
    None and leaves logging untouched if the file can't be opened, rather
    than failing the run over a logging concern.

    The interactive TUI (``tau.modes.interactive.app``) manages its own
    FileHandler instead, since it also has to strip terminal-writing
    handlers and restore everything on exit — call this only for modes that
    don't already do that (print/json/rpc), to avoid attaching two handlers
    to the same file.
    """
    from tau.settings.paths import get_log_file_path, get_logs_dir

    try:
        get_logs_dir().mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(get_log_file_path(session_id))
    except OSError:
        return None
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > logging.WARNING:
        root.setLevel(logging.WARNING)
    return handler
