# Creating Tools

A tool is a typed asynchronous operation the model can request. Every tool declares a name, a description, a Pydantic input schema, an execution policy, and how its result is rendered.

Use this guide to implement, register, and test a tool. See [Tools](tools.md) for the built-in tool reference and the execution model the engine applies.

## Table of Contents

- [Anatomy of a Tool](#anatomy-of-a-tool)
- [Implement a Tool](#implement-a-tool)
- [Constructor Reference](#constructor-reference)
- [Return Results](#return-results)
- [Use Runtime Context](#use-runtime-context)
- [Streaming and Cancellation](#streaming-and-cancellation)
- [Register the Tool](#register-the-tool)
- [Standalone Usage](#standalone-usage)
- [Test the Tool](#test-the-tool)
- [Checklist](#checklist)

## Anatomy of a Tool

Subclass `tau.tool.types.Tool`, pass metadata to `super().__init__()`, and implement one abstract method:

```python
async def execute(
    self,
    invocation: ToolInvocation,
    tool_execution_update_callback: ToolExecutionUpdateCallback | None = None,
    signal: AbortSignal | None = None,
    context: ToolContext | None = None,
) -> ToolResult: ...
```

There is no decorator-based registration API — `Tool` subclassing is the only way to define a tool. The base class supplies `validate()` (schema check returning `(ok, errors)`) and `to_json()` (provider-facing `{name, description, input_schema}`); you do not override them.

| Object | Role |
|--------|------|
| `ToolInvocation` | The call: `id`, `name`, `cwd`, `params` |
| `ToolContext` | Runtime services: `cwd`, `llm`, `settings` |
| `AbortSignal` | An `asyncio.Event` set when the user cancels |
| `ToolResult` | The outcome: `content`, `metadata`, `is_error`, optional media |

## Implement a Tool

This read-only word-count tool is complete and runnable:

```python
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from tau.tool.types import (
    AbortSignal,
    Tool,
    ToolContext,
    ToolExecutionMode,
    ToolExecutionUpdateCallback,
    ToolInvocation,
    ToolKind,
    ToolResult,
)


class WordCountParams(BaseModel):
    """Validated parameters for the word-count tool."""

    path: str = Field(description="Text file path, relative to the working directory.")


class WordCountTool(Tool):
    """Count words in a UTF-8 text file."""

    def __init__(self) -> None:
        super().__init__(
            name="word_count",
            description="Count the words in a UTF-8 text file.",
            schema=WordCountParams,
            kind=ToolKind.Read,
            execution_mode=ToolExecutionMode.Parallel,
        )

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback: ToolExecutionUpdateCallback | None = None,
        signal: AbortSignal | None = None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        params = WordCountParams.model_validate(invocation.params)

        if signal is not None and signal.is_set():
            return ToolResult.error(invocation.id, "Word count cancelled.")

        base = context.cwd if context is not None and context.cwd is not None else Path.cwd()
        path = Path(params.path)
        if not path.is_absolute():
            path = base / path

        try:
            content = path.read_text(encoding="utf-8")
        except OSError as error:
            return ToolResult.error(invocation.id, f"Cannot read {path}: {error}")

        count = len(content.split())
        return ToolResult.ok(
            invocation.id,
            f"{path}: {count} words",
            metadata={"path": str(path), "word_count": count},
        )
```

The engine validates `invocation.params` against `schema` before calling `execute()`. Validating again inside `execute()` gives you a typed parameter object and keeps the tool correct when it is called directly from tests.

## Constructor Reference

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | `str` | — | Stable lowercase identifier, underscores for spaces |
| `description` | `str` | — | What the tool does and when the model should call it |
| `schema` | `type[BaseModel]` | — | Pydantic model describing the parameters |
| `kind` | `ToolKind` | — | `Read`, `Edit`, `Write`, `Execute`, or `Web` |
| `execution_mode` | `ToolExecutionMode` | `Sequential` | `Sequential`, `Parallel`, or `Batch` |
| `render_call` | callable \| `None` | `None` | Renders the invocation line in the TUI |
| `render_result` | callable \| `None` | `None` | Renders the result body in the TUI |
| `render_shell` | `str` | `"self"` | `"self"` uses renderer output as-is; `"default"` applies the standard shell |
| `result_expandable` | `bool` | `True` | `False` disables central collapsing |
| `result_preview_lines` | `int` \| `None` | `None` | Overrides the global preview threshold |
| `prompt_snippet` | `str` \| `None` | `None` | Appended to the tool's line in the system prompt's tool list |
| `prompt_guidelines` | `str` \| `None` | `None` | Adds an entry to the system prompt's "Tool Guidelines" section |
| `prepare_arguments` | callable \| `None` | `None` | Transforms the raw argument dict before validation |

Choose `kind` and `execution_mode` deliberately:

- Tools that mutate files, launch processes, or change external state should use `ToolExecutionMode.Sequential`. A sequential tool acts as an ordering barrier for its whole batch.
- Read-only operations may use `Parallel`, but only when they share no mutable state.
- `kind` is descriptive metadata for rendering, telemetry, and hooks. It does not gate execution — Tau has no tool approval prompt.

## Return Results

Build results with the `ToolResult` constructors rather than instantiating it directly:

```python
return ToolResult.ok(invocation.id, "Completed", metadata={"items": 3})
return ToolResult.error(invocation.id, "The requested file does not exist")
```

Keep `content` concise and actionable — it goes to the model. Use `metadata` for structured facts that hooks and renderers need, not to duplicate the text result.

To render a successful result as Markdown in the TUI:

```python
return ToolResult.ok(
    invocation.id,
    "## Results\n\n- First\n- Second",
    metadata={"_render_format": "markdown"},
)
```

A tool can also hand media back to the model:

| Constructor | Attaches |
|-------------|----------|
| `ToolResult.with_images(id, content, images)` | PIL Images, raw bytes, or image URLs |
| `ToolResult.with_audio(id, content, audio)` | Bytes, base64 strings, or `file:` paths |
| `ToolResult.with_video(id, content, video)` | Bytes, base64 strings, or `file:` paths |
| `ToolResult.with_media(id, content, images=…, audio=…, video=…)` | Any combination |

Only providers with native tool-result media support (Anthropic, Gemini, OpenAI Responses) deliver these to the model; others ignore them silently.

Set `terminate=True` (with an optional `terminate_message`) on a result to stop the agent loop after the call completes.

## Use Runtime Context

`ToolContext` carries optional runtime services:

| Attribute | Value |
|-----------|-------|
| `cwd` | Current engine working directory |
| `llm` | Active text inference client, for tools that call a model |
| `settings` | Active settings manager, when available |

Treat every attribute as optional. Tools are unit-tested and invoked outside the full runtime, so always guard before use and fall back sensibly — as the example does with `Path.cwd()`.

## Streaming and Cancellation

Long-running tools should check `signal.is_set()` at safe cancellation points and return early with `ToolResult.error`.

Tools that produce incremental output call `tool_execution_update_callback` with partial `ToolResult` values. Emit an initial update when work begins and a final update matching the returned result. Throttle high-frequency producers — the built-in `terminal` tool caps updates at one per 100 milliseconds — so they do not flood engine events or terminal renders.

## Register the Tool

Tau supports three registration paths. All three converge on the same `ToolRegistry`, tagged with a different source.

### Project or Global Extension

Create `.tau/extensions/word_count.py` for one project, or `~/.tau/extensions/word_count.py` for every project:

```python
from tau.extensions import ExtensionAPI


def register(tau: ExtensionAPI) -> None:
    tau.register_tool(WordCountTool())
```

Run `/reload` after editing an extension — tools sync to the live engine without restarting the session. Project extensions load only after the project is trusted; see [Project Context Files](project-context.md#trust-and-security).

### Python Runtime

Pass instances through `RuntimeConfig.tools`. They register under the `runtime` source:

```python
from pathlib import Path

from tau.runtime.types import RuntimeConfig

config = RuntimeConfig(
    cwd=Path.cwd(),
    tools=[WordCountTool()],
)
```

### Standalone Engine

Supply tools to both the `Engine` constructor and the `EngineContext` for a run:

```python
from pathlib import Path

from tau.engine import Engine, EngineContext
from tau.message.types import UserMessage

tools = [WordCountTool()]
engine = Engine(cwd=Path.cwd(), llm=llm, tools=tools)

await engine.run(
    EngineContext(
        system_prompt="Use tools when they answer the request.",
        messages=[UserMessage.from_text("Count the words in README.md")],
        tools=tools,
    )
)
```

## Standalone Usage

A tool is an ordinary object. You can execute one with no model, no engine, and no session — useful for scripting and for driving a tool from your own code.

```python
import asyncio
from pathlib import Path

from tau.tool.types import ToolContext, ToolInvocation


async def main() -> None:
    tool = WordCountTool()
    cwd = Path.cwd()

    result = await tool.execute(
        ToolInvocation(
            id="call-1",
            name=tool.name,
            cwd=cwd,
            params={"path": "README.md"},
        ),
        context=ToolContext(cwd=cwd),
    )

    print(result.content)          # /repo/README.md: 412 words
    print(result.metadata)         # {'path': '...', 'word_count': 412}
    print(tool.to_json())          # Provider-facing schema
    print(tool.validate({}))       # (False, ['path: Field required'])


asyncio.run(main())
```

Executing a tool directly performs **no** schema validation, applies no execution-mode scheduling, and emits no engine events — those are the engine's responsibilities. Call `tool.validate(params)` yourself if you need the check. For the full loop, see [Engine](engine.md).

## Test the Tool

Test execution without making an inference request:

```python
from pathlib import Path

import pytest

from tau.tool.types import ToolContext, ToolInvocation


@pytest.mark.asyncio
async def test_word_count(tmp_path: Path) -> None:
    source = tmp_path / "sample.txt"
    source.write_text("one two three", encoding="utf-8")
    tool = WordCountTool()

    result = await tool.execute(
        ToolInvocation(
            id="call-1",
            name=tool.name,
            cwd=tmp_path,
            params={"path": "sample.txt"},
        ),
        context=ToolContext(cwd=tmp_path),
    )

    assert not result.is_error
    assert result.metadata["word_count"] == 3
```

Also cover invalid parameters (`tool.validate`), missing resources, cancellation via a set `AbortSignal`, and concurrent execution when the tool declares `Parallel`.

## Checklist

- Parameter types, defaults, and constraints live in the Pydantic schema, each with a `description`.
- The `description` explains *when* the model should call the tool, not just what it does.
- `kind` describes the side effect; `execution_mode` is `Parallel` only when concurrent calls are genuinely safe.
- Failures return `ToolResult.error` — raw tracebacks never leak to the model.
- Relative paths resolve against `ToolContext.cwd`, with a fallback when context is absent.
- Long operations check `signal.is_set()` and stream through the update callback.
- Unit tests cover the success path, the failure path, and cancellation.

## Next Steps

- [Tools](tools.md) — Built-in tool reference and execution model
- [Extensions](extensions.md) — Package tools with commands, hooks, and UI
- [Engine](engine.md) — Standalone lifecycle and event handling
