# Tau Documentation

Tau is a Python CLI framework for building interactive agent applications. It provides a terminal chat interface backed by any supported LLM provider, extensible tool execution, persistent session management, and a comprehensive plugin system.

## What is Tau?

Tau allows you to:
- Chat with LLMs (Claude, GPT, Gemini, Mistral, Ollama) in a terminal UI
- Execute tools (terminal commands, file operations, search) within conversations
- Save and resume sessions with branching and forking
- Extend Tau with custom tools, commands, themes, and skills
- Embed Tau in Python applications as a library
- Integrate with IDEs via JSON-RPC

## Getting Started

**New to Tau?** Start here:

1. [Quickstart](quickstart.md) - Get started in 5 minutes
2. [Installation](installation.md) - Set up providers and credentials
3. [Usage Guide](usage.md) - Learn interactive mode and slash commands
4. [Architecture](architecture.md) - Understand how components fit together

## Core Concepts

- [Inference](inference.md) - Standalone inference clients, types, and architecture
- [Engine](engine.md) - Standalone inference and tool-execution loop
- [Terminal UI](tui.md) - Standalone terminal rendering and component framework
- [Messages & Context](messages.md) - How messages flow and context is managed
- [Sessions](sessions.md) - Session persistence, branching, compaction, JSONL format
- [Inference Providers](inference-providers.md) - Supported LLMs and provider setup
- [Tools](tools.md) - Tool registry, built-in tools, execution model
- [Creating Tools](creating-tools.md) - Implement, register, and test custom tools
- [Project Context Files](project-context.md) - Repository-specific agent instructions
- [CLI Reference](cli-reference.md) - All command-line options and run modes

## Configuration & Customization

- [Settings](settings.md) - Configuration files, all available settings
- [Authentication](auth.md) - Credential storage and resolution
- [Themes](themes.md) - Terminal color themes (YAML format)
- [Skills](skills.md) - Reusable instruction sets with frontmatter
- [Prompts](prompts.md) - Prompt templates with argument substitution
- [Keybindings](keybindings.md) - Keyboard shortcuts and customization

## Building Extensions

- [Extensions Guide](extensions.md) - Complete guide to extending Tau
  - Custom tools
  - Slash commands
  - Hooks and events
  - UI dialogs and overlays
  - Hot-reload for development
- [Python API](python-api.md) - Programmatic usage, `RuntimeConfig`, `Runtime` class

## For Contributors

- [Project Structure](project-structure.md) - Codebase organization and module breakdown
- [Development Setup](development.md) - Local development environment and testing

## Reference

- [CLI Reference](cli-reference.md) - All flags, modes (interactive/print/json/rpc)
- [Settings Reference](settings.md) - Complete settings schema
- [Session Format](sessions.md) - JSONL file format and structure
- [Hooks & Events](extensions.md#event-hooks) - All available hooks and events

---

**Can't find what you're looking for?** Check [the full documentation index](index.md) or the [project structure guide](project-structure.md) for module-by-module documentation.
