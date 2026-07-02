# Contributing to Tau

Thanks for your interest in contributing. This document covers the practical steps for setting up, making, and submitting changes. For project-specific rules (code style, doc-update policy, module organization), see [AGENTS.md](AGENTS.md).

## Prerequisites

- Python 3.12 or higher
- git
- pip or uv

## Setup

```bash
git clone https://github.com/Jeomon/Tau.git
cd Tau
pip install -e .
```

Or with uv:

```bash
uv sync
```

Verify the install:

```bash
tau --print "Say hello"
```

## Making Changes

1. Create a feature branch: `git checkout -b feature-name`
2. Read `docs/architecture.md` and `docs/project-structure.md` to understand where your change belongs
3. Follow the code style: type hints everywhere, PEP 8, docstrings on public APIs
4. If your change is meaningful (new setting, message type, hook, tool source, theme, slash command, or Python API change), update the relevant file in `docs/` in the same commit — see the table in [AGENTS.md](AGENTS.md)
5. Write or update tests in `tests/`, matching the module name being changed

## Before Submitting

Run, in order, and fix all errors and warnings:

```bash
ruff check tau/
ruff format tau/
mypy tau/
pyright tau/
python -m pytest
```

Manually verify behavior where relevant:

```bash
tau --print "test prompt"
```

## Commit Guidelines

- Stage explicit paths (`git add path1.py path2.py`); never `git add -A` or `git add .`
- Only commit files you actually changed
- Message format: `{feat,fix,docs,refactor}: <description>`
- Never use `git reset --hard`, `git checkout .`, `git clean -fd`, or `git commit --no-verify`

## Pull Requests

1. Push your branch: `git push origin feature-name`
2. Open a pull request describing what changed and why
3. Ensure CI (tests, type checks) passes

## Reporting Security Issues

Do not open a public issue for security vulnerabilities. Email jeogeoalukka@gmail.com instead — see [SECURITY.md](SECURITY.md) for details.

## Questions

Refer to [docs/](docs/index.md) first. If documentation is missing or unclear for something you needed, that's a bug — please fix it as part of your PR.
