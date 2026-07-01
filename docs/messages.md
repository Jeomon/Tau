# Messages & Context

This page explains how tau handles messages, context, and conversation history.

## Message Types

Tau's message system supports several types of messages:

### User Message

A message from the user. Contains the prompt text and optionally referenced files. Tau supports text, image, audio, and video files.

```python
{
  "role": "user",
  "contents": [
    {"type": "text", "content": "Transcribe this audio"},
    {"type": "audio", "audio": ["...base64..."]}
  ]
}
```

Referenced files are automatically processed based on type:
- **Text files** — content included directly
- **Images** — sent as base64-encoded visual data
- **Audio files** — sent for transcription or analysis
- **Video files** — sent for scene analysis or transcription

### Assistant Message

A response from the LLM. Can include text and tool calls.

Assistant messages also store provider usage metadata: input, output,
cache-read, and cache-write token counts. The
`input_tokens_include_cache_read` flag records whether cached tokens are
already included in the provider's input count, preventing context usage from
counting the same tokens twice.

```python
{
  "role": "assistant",
  "contents": [
    {"type": "text", "content": "Here's a summary..."},
    {
      "type": "tool_call",
      "id": "call_123",
      "name": "read",
      "args": {"path": "src/main.py"}
    }
  ]
}
```

### Tool Result

Output from executing a tool.

```python
{
  "role": "tool",
  "contents": [{
    "type": "tool_result",
    "id": "call_123",
    "tool_name": "read",
    "content": "File contents here",
    "is_error": false
  }]
}
```

Assistant text and thinking content are rendered as Markdown in the interactive
TUI, including while content is streaming. Tool results remain plain text unless
the tool explicitly marks a successful result for Markdown rendering.

### System Message

Context and instructions for the agent. Tau constructs this separately from
persisted session messages and may include trusted project context files.

```python
{
  "type": "system",
  "content": "You are an agent that helps with code tasks..."
}
```

## Context Window

The context window is the total number of tokens sent to the LLM in each request.

### Token Counting

Check your current token usage with `/session`:

```text
Tokens: 2,456 input | 543 output | ~3,000 total
Cost: ~$0.012 (estimated)
```

### Token Limits

Context and output limits come from the active model metadata. Use `/model`
and `/session` rather than relying on a static table.

### Context Compression

When messages get too long, tau automatically compresses older messages to stay within limits. This happens transparently and maintains conversation continuity.

## Message History

Messages are stored in your session file, saved to disk automatically.

### View History

Run `/session` to see:
- All messages in the current branch
- Token counts per message
- Timestamps

### Clear History

Start a new session inside the TUI:

```text
/new
```

Resume from a specific point:

```bash
tau --resume
```

## File References

Reference files in your prompt to add their contents to the message:

```python
@src/main.py "What does this function do?"
@src/app.ts @src/app.css "Review these together"
```

Tau automatically:
1. Locates the files
2. Reads their contents
3. Adds them to the message context

Use `@` in the editor to fuzzy-search files.

## System Instructions

Tau loads project instructions from context files:

**Within a Git repository:**

- `AGENTS.md` or `CLAUDE.md`, matched case-insensitively
- One file per directory from the repository root through the current directory
- Files closer to the current directory take precedence

Outside a Git repository, only the current directory is checked. Context files
are loaded only for trusted projects.

Example `AGENTS.md`:

```markdown
# Project Instructions

- Always run tests after code changes
- Focus on performance and security
- Keep responses concise
- Use type hints in Python code
```

Instructions are automatically injected into every turn sent to the LLM.
See [Project Context Files](project-context.md) for complete behavior.

## Message Delivery

Configure how steering and follow-up messages are delivered in [Settings](settings.md):

| Mode | Behavior |
|------|----------|
| `one-at-a-time` | Each message queued and sent separately |
| `all` | All queued messages sent together |

## Next Steps

- [Sessions](sessions.md) - Session management and persistence
- [Settings](settings.md) - Configure message behavior
- [Usage Guide](usage.md) - Interactive mode features
