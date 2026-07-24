"""watch extension — let the agent read any video via yt-dlp.

Commands:
    /watch <url> [question]   fetch transcript and metadata, then ask a question
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tau.extensions.api import ExtensionAPI
    from tau.extensions.context import ExtensionContext


# ── VTT parser ─────────────────────────────────────────────────────────────────


def _parse_vtt(content: str) -> str:
    """Convert a WebVTT caption file to plain timestamped text."""
    result: list[str] = []
    seen: set[str] = set()
    current_time = ""
    current_text: list[str] = []

    for line in content.splitlines():
        line = line.strip()

        if not line or line.startswith(("WEBVTT", "NOTE", "Kind:", "Language:", "X-TIMESTAMP")):
            if current_text and current_time:
                text = re.sub(r"<[^>]+>", "", " ".join(current_text)).strip()
                if text and text not in seen:
                    result.append(f"[{current_time}] {text}")
                    seen.add(text)
            current_text = []
            continue

        if "-->" in line:
            if current_text and current_time:
                text = re.sub(r"<[^>]+>", "", " ".join(current_text)).strip()
                if text and text not in seen:
                    result.append(f"[{current_time}] {text}")
                    seen.add(text)
            current_text = []
            raw_start = line.split("-->")[0].strip()
            parts = raw_start.replace(",", ".").split(":")
            try:
                if len(parts) == 3:
                    h, m, s = int(parts[0]), int(parts[1]), int(float(parts[2]))
                    current_time = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
                elif len(parts) == 2:
                    m, s = int(parts[0]), int(float(parts[1]))
                    current_time = f"{m}:{s:02d}"
                else:
                    current_time = raw_start
            except (ValueError, IndexError):
                current_time = raw_start
            continue

        # Skip bare cue numbers
        if line.isdigit():
            continue

        current_text.append(line)

    # Flush last block
    if current_text and current_time:
        text = re.sub(r"<[^>]+>", "", " ".join(current_text)).strip()
        if text and text not in seen:
            result.append(f"[{current_time}] {text}")

    return "\n".join(result)


# ── Core fetch logic ───────────────────────────────────────────────────────────


#: yt-dlp is a network fetch — generous, but a hung download must not block
#: the session forever (before this cap there was no timeout at all).
FETCH_TIMEOUT_S = 120


class _FetchCancelled(Exception):
    """Esc/Ctrl+C fired the command signal while yt-dlp was running."""


async def _fetch(url: str, signal: asyncio.Event | None = None) -> dict[str, str]:
    """Run yt-dlp and return {title, channel, duration, description, transcript}.

    ``signal`` is the per-command abort signal (ctx.command_signal): when it
    fires, yt-dlp is killed and ``_FetchCancelled`` is raised so the command
    can report the cancellation instead of a yt-dlp error.
    """
    with tempfile.TemporaryDirectory(prefix="tau-watch-") as tmpdir:
        out_tmpl = str(Path(tmpdir) / "%(id)s")

        proc = await asyncio.create_subprocess_exec(
            "yt-dlp",
            "--skip-download",
            "--write-info-json",
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs",
            "en.*,en",
            "--sub-format",
            "vtt",
            "--no-playlist",
            "-o",
            out_tmpl,
            url,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        comm = asyncio.ensure_future(proc.communicate())
        waiters: set[asyncio.Future] = {comm}
        abort_waiter: asyncio.Task | None = None
        if signal is not None:
            abort_waiter = asyncio.ensure_future(signal.wait())
            waiters.add(abort_waiter)
        try:
            done, _ = await asyncio.wait(
                waiters, timeout=FETCH_TIMEOUT_S, return_when=asyncio.FIRST_COMPLETED
            )
            if comm in done:
                _stdout, stderr = comm.result()
            else:
                # Cancelled by Esc, or timed out: kill and stop waiting.
                proc.kill()
                _stdout, stderr = await comm
                if signal is not None and signal.is_set():
                    raise _FetchCancelled
                raise RuntimeError(f"yt-dlp timed out after {FETCH_TIMEOUT_S}s")
        finally:
            if abort_waiter is not None:
                abort_waiter.cancel()

        if proc.returncode != 0:
            err = stderr.decode(errors="replace").strip()
            short_err = err[-300:] if len(err) > 300 else err
            raise RuntimeError(f"yt-dlp error:\n{short_err}")

        tmp = Path(tmpdir)

        # ── Metadata ──────────────────────────────────────────────────────────
        info_files = list(tmp.glob("*.info.json"))
        meta: dict[str, str] = {}
        if info_files:
            with open(info_files[0], encoding="utf-8") as f:
                data = json.load(f)
            meta["title"] = data.get("title", "")
            meta["channel"] = data.get("uploader") or data.get("channel", "")
            meta["duration"] = data.get("duration_string", "")
            desc = (data.get("description") or "").strip()
            meta["description"] = desc[:600] + ("…" if len(desc) > 600 else "")

        # ── Transcript ────────────────────────────────────────────────────────
        # Prefer manual captions over auto-generated captions.
        vtt_files = sorted(tmp.glob("*.vtt"))
        manual = [
            f for f in vtt_files if ".auto." not in f.name and ".autogenerated." not in f.name
        ]
        chosen = next(iter(manual or vtt_files), None)

        transcript = ""
        if chosen:
            transcript = _parse_vtt(chosen.read_text(encoding="utf-8"))

        meta["transcript"] = transcript
        return meta


def _build_context(url: str, meta: dict[str, str]) -> str:
    parts: list[str] = [f"URL: {url}"]
    if meta.get("title"):
        parts.append(f"Title: {meta['title']}")
    if meta.get("channel"):
        parts.append(f"Channel: {meta['channel']}")
    if meta.get("duration"):
        parts.append(f"Duration: {meta['duration']}")
    if meta.get("description"):
        parts.append(f"Description: {meta['description']}")

    transcript = meta.get("transcript", "")
    if transcript:
        parts.append(f"\nTranscript:\n{transcript}")
    else:
        parts.append("\n(No transcript available — video may have no captions.)")

    return "\n".join(parts)


# ── Extension entry point ──────────────────────────────────────────────────────


def register(tau: ExtensionAPI) -> None:

    async def cmd_watch(ctx: ExtensionContext, args: list[str]) -> None:
        if not shutil.which("yt-dlp"):
            ui = ctx.ui
            if ui is not None:
                ui.notify(
                    "yt-dlp not found. Install with:  brew install yt-dlp  or  pip install yt-dlp",
                    type="error",
                )
            return

        if not args:
            ui = ctx.ui
            if ui is not None:
                ui.notify("Usage: /watch <url> [question]")
            return

        url = args[0]
        question = " ".join(args[1:]).strip() if len(args) > 1 else ""

        ui = ctx.ui
        if ui is not None:
            ui.notify(f"Fetching {url} …")

        try:
            meta = await _fetch(url, signal=ctx.command_signal)
        except _FetchCancelled:
            if ui is not None:
                ui.notify("Fetch cancelled.", type="warning")
            return
        except Exception as exc:
            if ui is not None:
                ui.notify(str(exc), type="error")
            return

        context = _build_context(url, meta)
        title = meta.get("title") or url

        if question:
            message = f"[watch: {title}]\n\n{context}\n\nQuestion: {question}"
        else:
            message = f"[watch: {title}]\n\n{context}"

        await ctx.send_user_message(message, deliver_as="follow_up", trigger_turn=True)

        if ui is not None:
            snippet = title[:60] + ("…" if len(title) > 60 else "")
            has_transcript = bool(meta.get("transcript"))
            suffix = "transcript loaded" if has_transcript else "no transcript"
            ui.notify(f"Loaded: {snippet} ({suffix})")

    tau.register_command(
        "watch",
        "Fetch a video's transcript and metadata via yt-dlp",
        cmd_watch,
        argument_hint="<video-url> [question]",
    )
