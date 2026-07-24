# watch

A single `/watch` command that lets the agent read any video via
[yt-dlp](https://github.com/yt-dlp/yt-dlp): it fetches the video's metadata
and captions (no media download), folds them into a message, and hands that
to the agent — optionally with a question to answer.

Requires `yt-dlp` on PATH (`brew install yt-dlp` or `pip install yt-dlp`).

## Usage

```
/watch <video-url>              load transcript + metadata into the conversation
/watch <video-url> <question>   same, then ask the question about the video
```

The fetch grabs `--write-info-json` plus English subtitles (manual captions
preferred over auto-generated), converts the WebVTT captions to plain
timestamped text, and sends it all as a follow-up user message with
`trigger_turn=True`, so the agent immediately reads it (and answers the
question, if one was given).

## Cancellation

The fetch phase honors `ctx.command_signal` — the ambient per-command abort
signal the runtime manages around every slash-command dispatch. Esc (or
Ctrl+C) while yt-dlp is running kills the subprocess immediately and reports
`Fetch cancelled.`; a 120s timeout backstops a hung download. Once the fetch
completes and the agent turn starts, cancellation switches to the normal
turn-scoped abort (`ctx.signal` territory), so `/watch` is coverable
end-to-end:

```
/watch <url>
 ├─ fetch phase (command scope) → command_signal → yt-dlp killed
 └─ answer phase (turn scope)   → turn signal → stream aborted
```

## Files

| File | Responsibility |
|------|----------------|
| `__init__.py` | Everything — `register(tau)`, the `/watch` command handler, the yt-dlp fetch (signal-aware, timeout-bounded), and the WebVTT → timestamped-text parser |
