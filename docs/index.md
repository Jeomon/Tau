# Tau Documentation

Tau is a self-extensible agent CLI: a terminal chat interface backed by any supported LLM provider, with tool execution, branching session history, and a plugin system for tools, commands, themes, and skills. Its core layers — inference, engine, session, and TUI — are also usable standalone as Python libraries.

## Quick start

Install Tau with pip:

```bash
pip install tau-coding-agent      # installs the `tau` command
```

Tau requires Python `>=3.12,<3.14`. `uv tool install tau-coding-agent --python 3.13` also works; see [Installation](installation.md) for why the `--python` pin matters.

Then run it in a project directory:

```bash
tau
```

Authenticate with `/login` for subscription providers, or set an API key such as `ANTHROPIC_API_KEY` before starting. For the full first-run flow, see [Quickstart](quickstart.md).

## Start here

- [Quickstart](quickstart.md) - install, authenticate, and run a first session.
- [Installation](installation.md) - Python requirements, providers, credentials, and troubleshooting.
- [Usage](usage.md) - interactive mode, slash commands, editor features, and shortcuts.
- [CLI Reference](cli-reference.md) - every flag, subcommand, and run mode.

## Core concepts

- [Architecture](architecture.md) - how runtime, agent, engine, and inference layer together.
- [Engine](engine.md) - the standalone inference and tool-execution loop.
- [Inference](inference.md) - `TextLLM`, streaming events, retries, and thinking support.
- [Inference Providers](inference-providers.md) - all supported providers and their setup.
- [Messages](messages.md) - message types, content blocks, and context projection.
- [Sessions](sessions.md) - storage, branching with `/tree`, compaction, and the JSONL format.
- [Tools](tools.md) - built-in tools, their schemas, kinds, and execution modes.
- [Project Context Files](project-context.md) - repository-specific agent instructions.
- [Authentication](auth.md) - credential storage, resolution order, and OAuth.

## Configuration and customization

- [Settings](settings.md) - every setting key, type, and default; precedence and merging.
- [Themes](themes.md) - the 17 bundled themes and the YAML theme format.
- [Keybindings](keybindings.md) - default shortcuts per context and how to rebind them.
- [Skills](skills.md) - reusable on-demand instruction sets.
- [Prompts](prompts.md) - prompt templates with argument substitution.
- [Packages](packages.md) - install and share extensions, skills, prompts, and themes.

## Extending Tau

- [Extensions](extensions.md) - tools, commands, hooks, custom UI, and hot reload.
- [Extension Settings](extension-settings.md) - typed settings schemas for extensions.
- [Creating Tools](creating-tools.md) - implement, register, and test a custom tool.

## Programmatic usage

- [Python API](python-api.md) - embed the full runtime via `Runtime` and `RuntimeConfig`.
- [RPC Mode](rpc.md) - drive Tau over stdio JSON for IDE integrations.
- [TUI Components](tui.md) - build terminal UI components and widgets.

Several modules run standalone, without the rest of Tau. Each of these docs has a
**Standalone Usage** section with a self-contained script:
[Inference](inference.md#standalone-usage), [Engine](engine.md#standalone-usage),
[Sessions](sessions.md#standalone-usage), [TUI](tui.md#standalone-usage), and
[Creating Tools](creating-tools.md#standalone-usage).

## Reference

- [Project Structure](project-structure.md) - module-by-module codebase map.
- [Security](security.md) - project trust, what it does and does not gate, and telemetry.
- [HTTP Proxy](http-proxy.md) - proxy configuration and what is currently wired.
- [Development](development.md) - local setup, tests, linting, `tau doctor`, and profiling.
