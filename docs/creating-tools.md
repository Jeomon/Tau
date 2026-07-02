# Creating Tools

Tools are typed asynchronous operations that a model can request. A tool
defines its name, description, Pydantic input schema, execution policy, and
result handling.

Use this guide to implement and test a tool. See [Tools](tools.md) for the
built-in tool reference and execution model.

## Implement a Tool

This example creates a read-only word-count tool:

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

The engine validates `invocation.params` against `schema` before execution.
Validating again inside `execute()` produces a typed parameter object and keeps
the tool safe when called directly in tests.

## Define Clear Metadata

Constructor fields affect both model behavior and execution:

| Field | Guidance |
|-------|----------|
| `name` | Use a stable lowercase identifier with underscores |
| `description` | State what the tool does and when it should be used |
| `schema` | Use Pydantic fields with precise descriptions and constraints |
| `kind` | Classify the side effect as read, edit, write, execute, or web |
| `execution_mode` | Use parallel only when concurrent calls are safe |

Tools that mutate files, launch processes, or modify external state should
normally use `ToolExecutionMode.Sequential`. Read-only operations may use
`Parallel` when they do not share mutable state.

## Return Results

Return model-facing text with one of the result constructors:

```python
return ToolResult.ok(invocation.id, "Completed", metadata={"items": 3})
return ToolResult.error(invocation.id, "The requested file does not exist")
```

Keep `content` concise and actionable. Use `metadata` for structured facts
needed by hooks or renderers, not for duplicating the complete text result.

For Markdown rendering of a successful result:

```python
return ToolResult.ok(
    invocation.id,
    "## Results\n\n- First\n- Second",
    metadata={"_render_format": "markdown"},
)
```

## Use Runtime Context

`ToolContext` provides optional runtime services:

| Attribute | Value |
|-----------|-------|
| `cwd` | Current engine working directory |
| `llm` | Active text inference client |
| `settings` | Active settings manager, when available |

Treat every attribute as optional because tools can be unit-tested or called
outside the complete runtime.

Long-running tools should check `signal.is_set()` at safe cancellation points.
Tools that produce incremental output can call
`tool_execution_update_callback` with partial `ToolResult` values.

## Register the Tool

### Project or Global Extension

Create `.tau/extensions/word_count.py` for one project, or
`~/.tau/extensions/word_count.py` for all projects:

```python
from tau.extensions import ExtensionAPI


def register(tau: ExtensionAPI) -> None:
    tau.register_tool(WordCountTool())
```

Run `/reload` after changing an extension.

### Python Runtime

Pass tool instances through `RuntimeConfig`:

```python
from pathlib import Path

from tau.runtime.types import RuntimeConfig

config = RuntimeConfig(
    cwd=Path.cwd(),
    tools=[WordCountTool()],
)
```

### Standalone Engine

Supply tools to both `Engine` and `EngineContext`:

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

See [Engine](engine.md) for standalone lifecycle and event handling.

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

Also test invalid parameters, missing resources, cancellation, and concurrent
execution when the tool declares parallel safety.

## Checklist

- Parameter types and constraints are expressed in the Pydantic schema.
- The description explains when the model should call the tool.
- Side effects match `ToolKind` and `ToolExecutionMode`.
- Errors are returned as `ToolResult.error`, not leaked as raw tracebacks.
- Paths are resolved against `ToolContext.cwd`.
- Cancellation is checked during long operations.
- Unit tests cover success and failure paths.
