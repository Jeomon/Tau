"""`--append-system-prompt` must survive both prompt paths.

The generated prompt goes through `build_prompt`, which has always honoured
`PromptOptions.append_prompt`. A prompt supplied with `--system` (or by an
extension) skips `build_prompt` entirely, so an append applied only there
would silently vanish whenever both flags were passed — the exact combination
someone reaches for when they want a custom prompt *plus* a house rule.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tau.hooks.service import Hooks
from tau.runtime.dependencies import RuntimeDependencies
from tau.runtime.types import RuntimeConfig, RuntimeContext
from tau.settings.manager import SettingsManager
from tau.tool.registry import ToolRegistry


class _Options:
    timeout = None
    max_retries = 0
    retry_base_delay_ms = 0


class _FakeLLM:
    def __init__(self) -> None:
        self.model = SimpleNamespace(thinking=False, input_limit=100_000)
        self.api = SimpleNamespace(options=_Options())
        self.provider_id = "fake"


async def _prompt_for(tmp_path: Path, **overrides) -> str:
    config = RuntimeConfig(
        cwd=tmp_path,
        config_dir=tmp_path / "config",
        persist_session=False,
        project_trusted=True,
        disable_context_files=True,
        dependencies=RuntimeDependencies(
            settings=lambda ctx: SettingsManager.create(
                ctx.cwd, config_dir=ctx.config_dir, project_trusted=ctx.project_trusted
            ),
            llm=lambda ctx: _FakeLLM(),  # type: ignore[arg-type]
            hooks=lambda: Hooks(),
            tool_registry=lambda: ToolRegistry(),
        ),
        **overrides,
    )
    context = await RuntimeContext.create(config)
    return context.agent.get_system_prompt()


@pytest.mark.asyncio
async def test_append_reaches_a_generated_prompt(tmp_path: Path) -> None:
    prompt = await _prompt_for(tmp_path, append_system_prompt="HOUSE RULE")

    assert "HOUSE RULE" in prompt
    # Still the generated prompt, not just the append on its own.
    assert len(prompt) > len("HOUSE RULE") * 2


@pytest.mark.asyncio
async def test_append_reaches_a_replaced_prompt(tmp_path: Path) -> None:
    """The regression this guards: --system skips build_prompt."""
    prompt = await _prompt_for(
        tmp_path, system_prompt="REPLACED PROMPT", append_system_prompt="HOUSE RULE"
    )

    assert prompt == "REPLACED PROMPT\n\nHOUSE RULE"


@pytest.mark.asyncio
async def test_replacement_alone_is_left_exactly_as_given(tmp_path: Path) -> None:
    prompt = await _prompt_for(tmp_path, system_prompt="REPLACED PROMPT")

    assert prompt == "REPLACED PROMPT"


@pytest.mark.asyncio
async def test_no_append_leaves_the_generated_prompt_untouched(tmp_path: Path) -> None:
    plain = await _prompt_for(tmp_path)
    appended = await _prompt_for(tmp_path, append_system_prompt="HOUSE RULE")

    assert "HOUSE RULE" not in plain
    assert len(appended) > len(plain)
