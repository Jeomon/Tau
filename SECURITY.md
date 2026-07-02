# Security & Supply Chain

## Supply Chain Hardening

We implement multiple layers of defense to prevent dependency-related attacks:

### 1. Exact Version Pinning

- All direct dependencies are pinned to exact versions in `pyproject.toml`
- Python 3.12+ is required (see `requires-python` in `pyproject.toml`)
- `uv.lock` serves as the source of truth for all dependencies and transitive versions

### 2. Dependency Auditing

Check for known vulnerabilities before installation:

```bash
# Using pip-audit (requires: pip install pip-audit)
pip-audit --desc

# Using safety (requires: pip install safety)
safety check
```

### 3. Installation Security

To prevent malicious scripts from running during installation:

```bash
# Recommended: Use --no-deps to install without running lifecycle scripts
pip install --no-deps -e .

# Or use uv (built-in protection)
uv sync
```

### 4. Lockfile Integrity

The `uv.lock` file should never change without explicit review:

- Pre-commit hooks (when enabled) block lockfile commits
- Dependency updates require explicit review and testing
- Use `uv add` or `uv upgrade` with careful review

### 5. Release Process

Before publishing releases:

```bash
# Test installation in isolated environment
python -m venv /tmp/test-env
source /tmp/test-env/bin/activate
pip install --no-deps .
tau --help
```

## Reporting Security Issues

If you discover a security vulnerability, please email jeogeoalukka@gmail.com instead of using the issue tracker.

Include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

## Dependencies

Current direct dependencies are explicitly pinned. See `pyproject.toml` for the full list.

Run `pip-audit` or `safety check` against `uv.lock` before release to catch known vulnerabilities in the dependency tree (see [Dependency Auditing](#2-dependency-auditing) above).

Key provider libraries:
- `anthropic` — Anthropic Claude API client
- `openai` — OpenAI GPT API client
- `google-genai` — Google Gemini API client
- `mistralai` — Mistral AI API client
- `ollama` — Ollama local models
