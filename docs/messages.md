> `tau.message` has no dependency on sessions, the runtime, or the TUI. Import it on its own to build request payloads.

# Messages

`tau.message` defines every message and content-block type Tau moves between the
user, the model, and tools. Messages are plain dataclasses: a `role` and a list
of typed `contents` blocks. Everything else in Tau (the [engine](engine.md), the
[session store](sessions.md), the TUI transcript) operates on these types.

## Table of Contents

- [Standalone Usage](#standalone-usage)
- [Content Blocks](#content-blocks)
- [Message Types](#message-types)
- [Roles](#roles)
- [Usage and Cost](#usage-and-cost)
- [Type Unions](#type-unions)
- [Message Flow](#message-flow)
- [Serialization](#serialization)
- [Context Projection](#context-projection)
- [Utilities](#utilities)
- [File References](#file-references)
- [Message Delivery](#message-delivery)

## Standalone Usage

Messages are constructible and serializable with nothing else running.

```python
import json
from dataclasses import asdict

from tau.message.types import (
    AssistantMessage,
    SystemMessage,
    TextContent,
    ToolCallContent,
    ToolMessage,
    ToolResultContent,
    UserMessage,
)


def build_history() -> list:
    return [
        SystemMessage.text("You are a terse assistant."),
        UserMessage.from_text("Read config.toml and summarize it."),
        AssistantMessage(
            contents=[
                TextContent(content="Reading the file."),
                ToolCallContent(id="call_1", name="read", args={"path": "config.toml"}),
            ]
        ),
        ToolMessage.from_result(
            ToolResultContent(id="call_1", tool_name="read", content="[server]\nport = 8080")
        ),
    ]


def main() -> None:
    history = build_history()

    # Inspect without any model call or network access.
    for message in history:
        print(message.role, [c.type for c in message.contents])

    # Messages are dataclasses, so asdict() gives JSON-ready output.
    print(json.dumps(asdict(history[-1]), indent=2))

    # Multimodal user message: text plus images, audio, video, and documents.
    multimodal = UserMessage.with_media(
        "Compare these.",
        images=["https://example.com/a.png"],
    )
    print([c.type for c in multimodal.contents])

    # Accessors on the assistant message.
    assistant = history[2]
    print(assistant.text_content())
    print([call.name for call in assistant.tool_calls()])


main()
```

This list is exactly what `Engine.run(EngineContext(messages=…))` consumes. See
[Engine](engine.md#standalone-usage) for the script that sends it to a model.

Standalone, `tau.message` does **not** persist anything, build a system prompt,
compact context, or resolve `@file` references; those belong to
[sessions](sessions.md), [project context](project-context.md), and the runtime.

## Content Blocks

Every block is a dataclass with a `Literal` `type` discriminator.

| Class | `type` | Key fields | Notes |
|-------|--------|-----------|-------|
| `TextContent` | `text` | `content` | Plain text |
| `ThinkingContent` | `thinking` | `content`, `signature` | Extended reasoning blocks |
| `ImageContent` | `image` | `images`, `dimension_note` | PIL images, bytes, URLs, or base64 |
| `AudioContent` | `audio` | `audios` | Bytes, base64, or `file:`-prefixed paths |
| `VideoContent` | `video` | `videos` | Bytes, base64, or `file:` paths |
| `FileContent` | `file` | `files` | Documents: PDF, DOCX, XLSX |
| `ToolCallContent` | `tool_call` | `id`, `name`, `kind`, `args`, `metadata` | Model's request to run a tool |
| `ToolResultContent` | `tool_result` | `id`, `tool_name`, `content`, `is_error`, `metadata`, `image`, `audio`, `video`, `terminate`, `terminate_message` | Paired to a call by `id` |
| `LinesContent` | `lines` | `lines`, `notify_type` | Pre-rendered TUI lines; not sent to models |

### Media Normalization

`ImageContent`, `AudioContent`, `VideoContent`, and `FileContent` normalize raw
`bytes` to base64 strings in `__post_init__`. This keeps every content block
JSON-serializable for session persistence: pydantic cannot encode non-UTF-8
bytes. Strings already present (base64 or URLs) pass through unchanged.

Each of those classes offers:

| Method | Description |
|--------|-------------|
| `to_base64()` | Returns `list[tuple[base64_data, mime_type]]` |
| `from_file(path)` | Load from disk |
| `ImageContent.from_url(url)` | Reference an image by URL |
| `AudioContent.from_base64(data, mime_type=None)` | Wrap pre-encoded audio |

### Tool Result Media

`ToolResultContent` can attach `image`, `audio`, or `video`. Providers with
native tool-result media support (Anthropic, Gemini, OpenAI Responses) embed it.
Providers without it (the Chat Completions family, Mistral, Ollama) silently
ignore the attachment. There is no text-placeholder or replay-turn fallback.

`terminate` / `terminate_message` let a tool request that the loop stop after its
result is recorded.

## Message Types

### LLM Messages

These four make up the `LLMMessage` union, the only messages sent to a model.
All inherit `BaseMessage`, which supplies `contents`, `id` (a UUID4), and
`timestamp`.

| Class | Role | Constructors | Allowed content |
|-------|------|--------------|-----------------|
| `SystemMessage` | `system` | `.text(content)` | `TextContent` |
| `UserMessage` | `user` | `.from_text()`, `.with_images()`, `.with_audio()`, `.with_video()`, `.with_file()`, `.with_media()` | text, image, audio, video, file, tool_result |
| `AssistantMessage` | `assistant` | `.from_text()` | text, thinking, tool_call |
| `ToolMessage` | `tool` | `.from_result()`, `.from_results()` | tool_result |

`UserMessage.with_media(content, images=…, audio=…, video=…, file=…)` appends one
block per supplied modality, so a single message can carry all of them. The
single-modality helpers all delegate to it.

`AssistantMessage` adds accessors and provider metadata:

| Member | Type | Description |
|--------|------|-------------|
| `usage` | `Usage` | Token counts and cost |
| `stop_reason` | `StopReason` | Why generation ended |
| `error` | `str` | Error text, when the turn failed |
| `error_kind` | `ErrorKind` | Classification driving recovery, e.g. compaction on overflow |
| `text_content()` | `str` | Concatenation of every `TextContent` |
| `tool_calls()` | `list[ToolCallContent]` | Every tool call |
| `thinking()` | `list[ThinkingContent]` | Every thinking block |

### Non-LLM Messages

These appear in a session and the TUI but are not `LLMMessage` members. They are
standalone dataclasses, not `BaseMessage` subclasses.

| Class | Role | Purpose |
|-------|------|---------|
| `CustomMessage` | `custom` | Application-defined entry: `custom_type`, `contents`, `details`; rebuilt via `.from_session(entry)` |
| `SkillInvocationMessage` | `skill_invocation` | A skill call: `name`, `args`, `content`, `expanded` |
| `TemplateInvocationMessage` | `template_invocation` | A prompt-template call: `name`, `args`, `expanded_content` |
| `TerminalExecutionMessage` | `terminal_execution` | A user-run `!` command: `command`, `output`, `exit_code`, `cancelled`, `exclude`; `.to_user_message()` projects it |
| `CompactionSummaryMessage` | `compaction_summary` | Summarized history after a compaction: `summary`, `tokens_before` |
| `BranchSummaryMessage` | `branch_summary` | Summary of an abandoned branch: `summary`, `from_id` |

> Note the mismatch: `Role.BASH_EXECUTION` is the enum member name, but its wire
> value is `"terminal_execution"`. Match on the value, not the member name.

## Roles

`Role` is a `StrEnum` in `tau/message/types.py`.

| Member | Value |
|--------|-------|
| `SYSTEM` | `system` |
| `USER` | `user` |
| `ASSISTANT` | `assistant` |
| `TOOL` | `tool` |
| `CUSTOM` | `custom` |
| `SKILL_INVOCATION` | `skill_invocation` |
| `TEMPLATE_INVOCATION` | `template_invocation` |
| `BASH_EXECUTION` | `terminal_execution` |
| `COMPACTION_SUMMARY` | `compaction_summary` |
| `BRANCH_SUMMARY` | `branch_summary` |

## Usage and Cost

`AssistantMessage.usage` is a `Usage` dataclass.

| Field | Type | Description |
|-------|------|-------------|
| `input_tokens` | `int` | Prompt tokens |
| `output_tokens` | `int` | Generated tokens |
| `cache_read_tokens` | `int` | Tokens served from cache |
| `cache_write_tokens` | `int` | Tokens written to cache |
| `cache_write_1h_tokens` | `int` | Tokens written to the 1-hour cache tier |
| `input_tokens_include_cache_read` | `bool` | Whether the provider already counted cache reads inside `input_tokens` |
| `cost` | `UsageCost` | `input`, `output`, `cache_read`, `cache_write`, `total` in USD |

`input_tokens_include_cache_read` exists so context accounting does not count the
same tokens twice across providers that report differently.

## Type Unions

| Union | Members |
|-------|---------|
| `Content` | text, image, audio, video, file, thinking, tool_call, tool_result |
| `SystemContent` | `TextContent` |
| `UserContent` | text, image, audio, video, file, tool_result |
| `AssistantContent` | text, thinking, tool_call |
| `ToolContent` | `ToolResultContent` |
| `LLMMessage` | `SystemMessage`, `UserMessage`, `AssistantMessage`, `ToolMessage` |
| `AgentMessage` | `LLMMessage` members plus `TerminalExecutionMessage`, `CustomMessage`, `CompactionSummaryMessage`, `BranchSummaryMessage` |

`AgentMessage` is an `Annotated` pydantic union discriminated on `role`, so each
persisted message deserializes back to its exact class and an unknown role fails
loudly rather than collapsing into the first structurally compatible member.

`SessionMessage` is an alias for `CustomMessage`.

## Message Flow

```text
user input
   │
   ▼
UserMessage ──────────────► session (MessageEntry, appended to JSONL)
   │
   ▼
to_llm_messages()  ── projects AgentMessage[] to LLMMessage[]
   │
   ▼
Engine.run(EngineContext(messages=…))
   │
   ├─ MessageStartEvent   ─► partial AssistantMessage
   ├─ MessageUpdateEvent  ─► streaming deltas
   └─ MessageEndEvent     ─► complete AssistantMessage ─► session
        │
        ▼  assistant.tool_calls()
   ToolCallContent ──► Tool.execute() ──► ToolResultContent
        │                                       │
        │                                       ▼
        │                              ToolMessage ─► session
        └──────── next turn, if tool calls were made
```

`ToolResultContent.id` always matches the `ToolCallContent.id` it answers. The
engine appends completed messages to `EngineState.messages` on `MessageEndEvent`,
and drops the last `count` on `MessageRollbackEvent` when an interrupted turn is
discarded. See [Engine](engine.md#events).

## Serialization

Messages persist inside session entries in `tau/session/types.py`:

```python
class MessageEntry(BaseSessionEntry):
    message: AgentMessage
```

Each session file is JSONL, one entry per line. Because `AgentMessage` is
discriminated on `role`, reload reconstructs the precise class. Content-block
unions are discriminated on `type`.

Two consequences follow from the media normalization described above:

1. Any `bytes` you construct a message with is already base64 by the time it is
   written, so a session file is always valid UTF-8 JSON.
2. `AudioContent.audios`, `VideoContent.videos`, and `FileContent.files` declare
   `str` before `bytes` in their unions. Pydantic tries union members in order, so
   a reloaded JSON string matches `str` first. Bytes-first would coerce it back to
   bytes on every reload and re-trigger the encode branch, double-encoding the
   content.

See [Sessions](sessions.md) for entry types beyond `MessageEntry`.

## Context Projection

`tau.session.utils.to_llm_messages(messages)` converts persisted
`AgentMessage` objects into the `LLMMessage` list sent to a provider.

| Input | Projection |
|-------|-----------|
| `UserMessage`, `ToolMessage` | Passed through |
| `AssistantMessage` | Kept only if it has text, thinking, or tool-call content |
| `CompactionSummaryMessage` | `UserMessage` with the summary wrapped in `<context-summary>` tags |
| `TerminalExecutionMessage` | `UserMessage` via `.to_user_message()`, unless `exclude` is set |
| `CustomMessage` and other non-LLM types | Skipped |

Empty assistant messages are visual-only markers: aborts, or persisted API and
credit errors. An assistant turn with neither content nor tool calls is invalid
to send back and triggers provider 400s, so it is dropped.

## Utilities

`tau/message/utils.py`:

| Function | Description |
|----------|-------------|
| `image_to_base64(img)` | Returns `(base64, mime_type)` for PIL images, bytes, URLs, or base64 |
| `audio_to_base64(item)` | Same, for audio |
| `video_to_base64(item)` | Same, for video |
| `file_to_base64(item)` | Same, for documents |
| `detect_image_mime(data)` | MIME type from magic bytes |
| `detect_audio_mime(data)` | MIME type from magic bytes |
| `detect_file_mime(data)` | MIME type from magic bytes |
| `filter_empty_assistant_messages(messages)` | Drop assistant messages with no usable content |
| `strip_unusable_trailing_assistant(messages, session_manager=None)` | Remove a trailing assistant message that cannot be continued from |

## File References

In the interactive TUI, prefix a path with `@` to attach it. Type `@` in the
editor to fuzzy-search files.

```text
@src/main.py What does this function do?
@src/app.ts @src/app.css Review these together.
```

Tau locates each file, reads it, and adds it to the outgoing `UserMessage` as the
content block matching its type: text inline, images as `ImageContent`, audio as
`AudioContent`, video as `VideoContent`, and documents as `FileContent`.

Rendering: assistant text and thinking content are rendered as Markdown in the
TUI, including while streaming. Tool results stay plain text unless the tool
explicitly opts a successful result into Markdown rendering.

## Message Delivery

Steering and follow-up messages queue while a turn is running. Drain behavior is
configured in [Settings](settings.md) via `steering_mode` and `follow_up_mode`.

| Mode | Behavior |
|------|----------|
| `one_at_a_time` | One queued message is delivered per boundary (default) |
| `all` | Every queued message is delivered together |

Steering messages are injected after the next tool-call round-trip; follow-up
messages after the loop stops naturally. In the TUI, Enter steers and Alt+Enter
queues a follow-up.

## Next Steps

- [Engine](engine.md) - the loop that produces and consumes these messages
- [Sessions](sessions.md) - persistence, branching, and compaction
- [Project Context Files](project-context.md) - `AGENTS.md` / `CLAUDE.md` loading
- [Settings](settings.md) - delivery modes and other configuration
