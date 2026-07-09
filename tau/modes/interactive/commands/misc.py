from __future__ import annotations

import subprocess
import sys

from tau.modes.interactive.commands.context import CommandContext


def cmd_copy(ctx: CommandContext) -> None:
    from tau.message.types import AssistantMessage
    from tau.session.types import MessageEntry as SessionMessageEntry

    sm = ctx.runtime.session_manager
    if sm is None:
        ctx.notify("No active session.")
        return

    text = ""
    for entry in reversed(sm.get_branch()):
        if isinstance(entry, SessionMessageEntry):
            msg = entry.message
            if isinstance(msg, AssistantMessage):
                # An aborted turn is saved with empty content — skip it and
                # keep looking for the last assistant message that actually
                # has text, instead of reporting "nothing to copy".
                candidate = msg.text_content()
                if candidate:
                    text = candidate
                    break

    if not text:
        ctx.notify("No assistant messages to copy.")
        return

    try:
        copy_to_clipboard(text)
        ctx.notify("Copied last assistant message to clipboard.")
    except Exception as exc:
        ctx.notify(f"Copy failed: {exc}")


def copy_to_clipboard(text: str) -> None:
    if sys.platform == "darwin":
        subprocess.run(["pbcopy"], input=text.encode(), check=True)
    else:
        for cmd in [
            ["wl-copy"],
            ["xclip", "-selection", "clipboard"],
            ["xsel", "--clipboard", "--input"],
        ]:
            try:
                subprocess.run(cmd, input=text.encode(), check=True, capture_output=True)
                return
            except (FileNotFoundError, subprocess.CalledProcessError):
                continue
        raise RuntimeError("No clipboard tool found. Install xclip, xsel, or wl-copy.")


def show_help(ctx: CommandContext) -> None:
    from tau.prompts.registry import prompt_registry

    cmds = sorted(ctx.runtime.commands.list(), key=lambda c: c.name)
    cmd_lines = "\n".join(f"  /{c.name:<14} {c.description}" for c in cmds)

    tmpls = sorted(prompt_registry.list(), key=lambda t: t.name)
    tmpl_lines = "\n".join(
        f"  /{t.name:<14} {t.description}" + (f"  {t.argument_hint}" if t.argument_hint else "")
        for t in tmpls
    )

    shortcuts = (
        "  Enter          Submit / steer mid-task when agent is busy\n"
        "  Alt+Enter      Queue as follow-up (waits for agent to finish)\n"
        "  Ctrl+↑         Restore queued messages into editor\n"
        "  Esc            Abort running agent (restores queued messages)\n"
        "  Ctrl+C         Abort running agent / quit when idle\n"
        "  Page Up        Enter scroll mode  (Esc / End to exit)\n"
        "  @<path>        Attach a file — browse with ↑↓, Tab to select\n"
        "  /<command>     Run a slash command — executes immediately\n"
        "  /name [args]   Expand a prompt template and send to agent\n"
        "  !<cmd>         Run a shell command — executes immediately"
    )

    ext_shortcuts = ctx.runtime.extension_shortcuts
    ext_lines = ""
    if ext_shortcuts:
        rows = "\n".join(f"  {s.key:<14} {s.description or ''}" for s in ext_shortcuts)
        ext_lines = f"\n\nExtension shortcuts:\n{rows}"

    tmpl_section = f"\n\nPrompt templates:\n{tmpl_lines}" if tmpl_lines else ""
    ctx.notify(
        f"Commands:\n{cmd_lines}{tmpl_section}\n\nKeyboard shortcuts:\n{shortcuts}{ext_lines}"
    )
