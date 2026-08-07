"""`/trust` — inspect and change the current project's trust decision.

Trust decides whether Tau loads a project's own `.tau/` settings, extensions
and context files. It was asked once, at startup, and then never surfaced
again: no way to see what the answer had been, to grant trust to a project you
had declined, or to withdraw it — short of editing `~/.tau/trust.json` by hand.

A decision has two independent parts and both are reported: what is in effect
for this process, and what is stored on disk for next time. A session-only
answer makes those differ on purpose.
"""

from __future__ import annotations

from pathlib import Path

from tau.modes.interactive.commands.context import CommandContext

#: Argument → (trusted, remember). ``None`` means "forget the stored answer".
ACTIONS: dict[str, tuple[bool, bool] | None] = {
    "yes": (True, True),
    "always": (True, True),
    "session": (True, False),
    "no": (False, True),
    "never": (False, True),
    "forget": None,
}


def _status_lines(
    cwd: Path, active: bool, stored: bool | None, stored_path: str | None
) -> list[str]:
    from tau.tui.utils import BOLD, DIM, RESET

    lines = [f"{BOLD}Project Trust{RESET}", ""]
    lines.append(f"{DIM}{'Directory':<12}{RESET} {cwd}")
    lines.append(f"{DIM}{'In effect':<12}{RESET} {'trusted' if active else 'not trusted'}")
    if stored is None:
        lines.append(f"{DIM}{'Remembered':<12}{RESET} no — you will be asked again next time")
    else:
        inherited = "" if stored_path == str(cwd) else f" (inherited from {stored_path})"
        lines.append(
            f"{DIM}{'Remembered':<12}{RESET} {'trusted' if stored else 'not trusted'}{inherited}"
        )
    if stored is not None and stored != active:
        lines.append("")
        lines.append(
            f"{DIM}This session overrides what is stored; the stored answer wins next time.{RESET}"
        )
    lines.append("")
    lines.append(
        f"{DIM}Trusted projects load their own .tau/ settings, extensions and context files.{RESET}"
    )
    lines.append(f"{DIM}Change with: /trust yes | session | no | forget{RESET}")
    return lines


async def cmd_trust(ctx: CommandContext, args: list[str] | None = None) -> None:
    """Report the current decision, or apply the one named in ``args``."""
    from tau.trust.manager import trust_store

    settings = ctx.runtime.settings_manager
    session = ctx.runtime.session_manager
    if settings is None or session is None:
        ctx.notify("No active session.")
        return

    cwd = Path(session.cwd)
    active = bool(settings.is_project_trusted())
    stored = trust_store.get(cwd)
    stored_path = trust_store.get_stored_path(cwd)

    argument = (args[0].lower() if args else "").strip()
    if not argument:
        ctx.notify("\n".join(_status_lines(cwd, active, stored, stored_path)))
        return

    if argument not in ACTIONS:
        ctx.notify(f"Unknown option {argument!r}. Use: /trust yes | session | no | forget")
        return

    action = ACTIONS[argument]
    if action is None:
        trust_store.set(cwd, None)
        state = "trusted" if active else "untrusted"
        ctx.notify(f"Forgot the remembered answer for {cwd}. Still {state} for this session.")
        return

    trusted, remember = action
    settings.set_project_trusted(trusted)
    if remember:
        trust_store.set(cwd, trusted)

    # Granting trust mid-session loads the project settings that were skipped
    # at startup; extensions and context files are read while building the
    # session, so they need a reload before they take effect.
    if trusted and not active:
        await ctx.runtime.reload_extensions()

    scope = "remembered" if remember else "this session only"
    reloaded = " Project settings and extensions reloaded." if trusted and not active else ""
    ctx.notify(f"{'Trusted' if trusted else 'Untrusted'} {cwd} ({scope}).{reloaded}")
