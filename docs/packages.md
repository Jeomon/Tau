# Packages

A Tau package is an ordinary Python distribution that ships Tau resources — extensions, skills, prompt templates, and themes — alongside its code. Packages install into a Tau-managed virtual environment, are recorded in `settings.json`, and are discovered on every startup. Use a package when you want to share resources through PyPI, git, or a wheel; use a plain file in `.tau/extensions/` when the code is project-specific (see [Extensions](extensions.md)).

## Table of Contents

- [What a Package Is](#what-a-package-is)
- [Install and Manage](#install-and-manage)
- [Source Formats](#source-formats)
- [Where Packages Are Installed](#where-packages-are-installed)
- [Package Structure](#package-structure)
- [Resource Discovery](#resource-discovery)
- [The Settings Entry](#the-settings-entry)
- [Resource Filtering](#resource-filtering)
- [Validation with `tau doctor`](#validation-with-tau-doctor)
- [Worked Example: Authoring a Package](#worked-example-authoring-a-package)
- [Next Steps](#next-steps)

## What a Package Is

> **Security:** Packages run with full user permissions. Extension code is imported and executed at startup, and bundled skills can instruct the model to run anything. Review the source before installing a third-party package.

Tau does not define its own archive format. A package is whatever `pip`/`uv` can install — a PyPI distribution, a git repository, a local directory, a wheel, or a source archive. What makes it a *Tau* package is that its installed import directory declares Tau resources, either through a `manifest.json` file, a `[tool.tau]` table in `pyproject.toml`, or conventional resource directories.

A package can bundle four resource types:

| Resource | Loaded as | Doc |
|----------|-----------|-----|
| `extensions` | Python files providing `register(tau)` | [Extensions](extensions.md) |
| `skills` | Skill directories | [Skills](skills.md) |
| `prompts` | Prompt template files | [Prompts](prompts.md) |
| `themes` | Theme definitions | [Themes](themes.md) |

## Install and Manage

```bash
tau install pypi:tau-tools               # Latest from PyPI
tau install pypi:tau-tools==1.2.3        # Pinned version
tau install git+https://github.com/example/tau-tools.git@v1
tau install ./dist/tau_tools-1.2.3-py3-none-any.whl
tau install ./packages/tau-tools         # Local directory
tau install --local pypi:tau-tools       # Project scope instead of global

tau list                                 # Global packages
tau list --local                         # Project packages
tau list --all                           # Both scopes

tau update tau-tools                     # Upgrade one package
tau update --all                         # Upgrade Tau itself and every package
tau update                               # Upgrade Tau itself only

tau remove tau-tools                     # Uninstall and drop the settings entry
```

| Command | Options | Description |
|---------|---------|-------------|
| `tau install SOURCE` | `--local`, `--index-url URL`, `--extra-index-url URL` | Install into the managed venv and record a package entry |
| `tau remove NAME` | `--local` | Uninstall from the venv and remove the settings entry |
| `tau list` | `--local`, `--all` | Show recorded packages with version, disabled state, and redacted source |
| `tau update [NAME]` | `--all`, `--local` | Upgrade one package, or Tau plus all packages with `--all` |
| `tau doctor` | `--json`, `--fix` | Report drift between settings entries and the venv |

`--index-url` and `--extra-index-url` are stored on the package entry and reused automatically by later `tau update` runs, so a package from a private index does not need the flag repeated. `--extra-index-url` may be repeated.

`tau list` passes each source through a redactor that strips URL userinfo and any query string or fragment before printing, so credentials embedded in a git or wheel URL are not echoed to the terminal.

Install is transactional in one direction: if the package installs but the settings write fails, Tau uninstalls it again and reports an error rather than leaving the venv and settings out of sync.

## Source Formats

`tau install` and the `source` field of a settings entry accept the same strings.

| Format | Example | Parsed as |
|--------|---------|-----------|
| `pypi:NAME` | `pypi:tau-tools` | PyPI, latest |
| `pypi:NAME==VERSION` | `pypi:tau-tools==1.2.3` | PyPI, pinned |
| `NAME` / `NAME==VERSION` | `tau-tools==1.2.3` | Bare names are treated as PyPI |
| `git+URL` | `git+https://github.com/example/tau-tools.git@v1` | Git |
| `https://…` / `http://…` | `https://example.com/tau_tools-1.2.3-py3-none-any.whl` | Wheel or source-archive URL |
| `/abs/path`, `./rel/path`, `~/path` | `./dist/tau_tools-1.2.3.tar.gz` | Local path (expanded and resolved) |

Details that matter when writing sources by hand:

- Git revisions may contain slashes. Tau splits the revision at `.git@` when the URL ends in `.git`, so `git+https://host/user/repo.git@release/2.0` keeps `release/2.0` as the ref and `repo` as the name.
- For URL and local-path sources, the distribution name and version are parsed from the wheel or sdist filename. A local directory that is not a wheel or archive falls back to the directory name.
- PyPI names are validated against `[a-zA-Z0-9_.-]+` and then canonicalized, so `Tau_Tools` and `tau-tools` resolve to the same recorded name.

## Where Packages Are Installed

Tau keeps package installs out of its own runtime environment by using a dedicated venv per scope.

| Scope | Venv | Settings file |
|-------|------|---------------|
| Global (default) | `~/.tau/venv/` | `~/.tau/settings.json` |
| Project (`--local`) | `.tau/venv/` | `.tau/settings.json` |

The venv is created on first install. If `uv` is on `PATH`, Tau runs `uv venv --python <the interpreter running Tau>` — pinning the interpreter explicitly, because an unpinned `uv` picks its own default toolchain and can produce a venv whose native extensions will not import. Without `uv`, Tau falls back to `python -m venv`. Installs likewise prefer `uv pip install --python <venv python>` and fall back to the venv's `pip`.

At startup the resource loader appends each active venv's `site-packages` to the end of `sys.path`. Appending rather than prepending is deliberate: a package cannot shadow Tau's own dependencies with incompatible versions.

Subprocess calls are bounded. Install, uninstall, and venv creation time out after 120 seconds; local introspection calls (reading the site-packages location, querying an installed version) time out after 15 seconds and degrade to "unknown" rather than raising.

## Package Structure

A package declares its resources in a `manifest.json` placed inside the installed import directory, under a top-level `tau` key:

```json
{
  "tau": {
    "extensions": ["extensions/main.py"],
    "skills": ["skills"],
    "prompts": ["prompts"],
    "themes": ["themes"]
  }
}
```

All declared paths are resolved relative to the package's import directory and re-checked afterwards: a path that resolves outside that directory (via `..` or a symlink) is logged and ignored. Extension declarations must resolve to files; the other three resource types may be files or directories.

Because the manifest is read from the *installed* directory, it must be shipped as package data. With setuptools that means including it in `MANIFEST.in` and enabling `include-package-data`; other build backends have equivalents.

If there is no manifest, Tau falls back to conventional directories with the same names — `extensions/`, `skills/`, `prompts/`, `themes/` — inside the package directory.

```text
tau_tools/                 # The installed import directory
├── __init__.py            # Optional: a register() here is the last-resort entry point
├── manifest.json          # {"tau": {...}} — declares resources
├── extensions/
│   └── main.py            # def register(tau): ...
├── skills/
│   └── web-search/
│       └── SKILL.md
├── prompts/
│   └── review.md
└── themes/
    └── midnight.json
```

## Resource Discovery

Non-extension resources (`skills`, `prompts`, `themes`) are resolved in two steps: read the manifest declaration for that key, and if it is absent or empty, use the conventional directory of the same name when it exists.

Extensions get an additional fallback chain. If the manifest declares nothing for `extensions` and no filter is configured, Tau tries, in order:

1. `manifest.json` — `{"tau": {"extensions": [...]}}`, taking the first list that resolves to at least one existing file.
2. `pyproject.toml` — an `extensions` list under `[tool.tau]`, checked in the package directory and then in its parent. Declarations that escape the file's own directory are dropped.
3. `__init__.py` — used as the entry point if its source contains `def register(` or `async def register(`.

```toml
# pyproject.toml — the step 2 fallback
[tool.tau]
extensions = ["extensions/main.py"]
```

The import directory itself is located from distribution metadata rather than guessed, because distribution and import names frequently differ. Tau queries the managed interpreter's `importlib.metadata.packages_distributions()` for the import names belonging to the requested distribution, then falls back to trying the raw name and its underscore/lower/upper variants inside `site-packages`.

## The Settings Entry

Each install writes one entry to the `packages.list` array of the target settings file. Entries can be hand-edited; see [Settings](settings.md) for the surrounding file format.

| Field | Type | Description |
|-------|------|-------------|
| `source` | string | The original source string, reused by `tau update` |
| `name` | string | Canonicalized distribution name; the key for `tau remove` and `tau update` |
| `version` | string \| null | Version recorded at install time, when it could be determined |
| `installed_path` | string \| null | Absolute path to the import directory inside the venv |
| `enabled` | bool | Default `true`. `false` skips the package entirely at load time |
| `extensions` | list \| null | Path filter; `null` means load everything declared |
| `skills` | list \| null | Path filter |
| `prompts` | list \| null | Path filter |
| `themes` | list \| null | Path filter |
| `index_url` | string \| null | Base index URL, replayed on update |
| `extra_index_urls` | list \| null | Additional index URLs, replayed on update |

```json
{
  "packages": {
    "list": [
      {
        "source": "pypi:tau-tools==1.2.3",
        "name": "tau-tools",
        "version": "1.2.3",
        "installed_path": "/home/user/.tau/venv/lib/python3.13/site-packages/tau_tools",
        "enabled": true,
        "skills": [],
        "index_url": "https://packages.example.com/simple"
      }
    ]
  }
}
```

Global and project entries are both loaded at runtime; the global list is processed first, then the project list. Adding a package to a scope replaces any existing entry with the same name in that scope.

## Resource Filtering

The four resource keys on a package entry narrow what a package contributes without editing the package itself.

| Value | Effect |
|-------|--------|
| omitted / `null` | Load everything the package declares for that resource |
| `[]` | Load nothing of that resource type |
| `["skills", "themes/midnight.json"]` | Load only the declared paths that match |

Matching is by exact path, not by glob. A declared path is selected when its path relative to the package root, its bare filename, or that relative path prefixed with `./` appears in the filter list. Filters narrow the manifest — they cannot add a path the manifest never declared.

Setting `extensions` to `[]` also disables the extension fallback chain: the `pyproject.toml` and `__init__.py` steps only run when the filter is `null` and the manifest yielded nothing.

## Validation with `tau doctor`

`tau doctor` runs eight diagnostic sections — Settings, Auth, Models, Extensions, Sessions, Logs, Environment, and Packages — and exits `1` if any check fails. Three of them bear on packages.

| Section | Check | Status on problem |
|---------|-------|-------------------|
| Packages | Every enabled entry's `installed_path` exists, or the name is importable in the scope's venv | `fail` — "recorded as installed but missing from the venv" |
| Environment | `~/.tau/venv` was built for the Python currently running Tau | `warn` |
| Extensions | Each `manifest.json` is valid JSON and has a top-level `tau` key | `fail` on invalid JSON, `warn` on the missing key |
| Extensions | Manifest-declared extension entries exist as files; declared skill paths exist as directories | `fail` / `warn` |
| Extensions | Manifest-declared `dependencies` are installed for the current dependency digest | `warn`, or `fail` if a previous install errored |

```bash
tau doctor                # Human-readable report
tau doctor --json         # Machine-readable sections/results
tau doctor --fix          # Apply safe, reversible repairs
```

The Packages section deliberately has no `--fix`. A stale entry's `source` string is the only record of how to reinstall it, so removing the entry would destroy that information, and reinstalling automatically would mean running a network install without asking. Fix drift by re-running the install the report prints:

```text
Packages
  ✗ tau-tools (global) — recorded as installed but missing from the venv
    (/home/user/.tau/venv) — run `tau install pypi:tau-tools==1.2.3` again
```

Extension checks are static. `tau doctor` never calls the extension loader, so it does not import extension code or trigger dependency installs.

## Worked Example: Authoring a Package

This builds a package that ships one extension, one skill, and one prompt template, then installs it locally.

**1. Lay out the project.** Everything Tau reads must live inside the import directory, because that is what ends up in `site-packages`.

```text
tau-tools/
├── pyproject.toml
├── MANIFEST.in
└── tau_tools/
    ├── __init__.py
    ├── manifest.json
    ├── extensions/
    │   └── main.py
    ├── skills/
    │   └── changelog/
    │       └── SKILL.md
    └── prompts/
        └── review.md
```

**2. Write `pyproject.toml`.** The `[tool.tau]` table is optional here — the manifest takes precedence — but it keeps the declaration working for editable installs.

```toml
[project]
name = "tau-tools"
version = "1.2.3"
requires-python = ">=3.12"

[tool.setuptools]
include-package-data = true

[tool.setuptools.packages.find]
include = ["tau_tools*"]

[tool.tau]
extensions = ["extensions/main.py"]
```

**3. Ship the resources as package data.**

```text
# MANIFEST.in
include tau_tools/manifest.json
recursive-include tau_tools/extensions *.py
recursive-include tau_tools/skills *
recursive-include tau_tools/prompts *
```

**4. Declare the resources in `tau_tools/manifest.json`.**

```json
{
  "tau": {
    "extensions": ["extensions/main.py"],
    "skills": ["skills"],
    "prompts": ["prompts"]
  }
}
```

**5. Write the extension.** Every extension entry file exports `register(tau)`.

```python
# tau_tools/extensions/main.py

from pydantic import BaseModel, Field
from tau.tool.types import Tool, ToolInvocation, ToolKind, ToolResult


class _Schema(BaseModel):
    path: str = Field(..., description="File to count lines in")


class CountLinesTool(Tool):
    def __init__(self) -> None:
        super().__init__(
            name="count_lines",
            description="Count the lines in a file.",
            schema=_Schema,
            kind=ToolKind.Read,
        )

    async def execute(self, invocation: ToolInvocation, *args, **kwargs) -> ToolResult:
        path = invocation.params["path"]
        try:
            with open(path, encoding="utf-8") as handle:
                count = sum(1 for _ in handle)
        except OSError as exc:
            return ToolResult.error(invocation.id, str(exc))
        return ToolResult.ok(invocation.id, f"{path}: {count} lines")


def register(tau):
    tau.register_tool(CountLinesTool())
```

**6. Add the skill.**

```markdown
---
name: changelog
description: Draft a changelog entry from the current git diff.
---

Read the staged diff with `git diff --cached`, then write one entry
in Keep a Changelog format. Do not invent changes that are not in the diff.
```

**7. Build and install it.**

```bash
python -m build                                   # Produces dist/tau_tools-1.2.3-*.whl
tau install ./dist/tau_tools-1.2.3-py3-none-any.whl
```

```text
Installing ./dist/tau_tools-1.2.3-py3-none-any.whl…
✓ Installed tau-tools@1.2.3 (global)
```

**8. Verify.**

```bash
tau list                # tau-tools  1.2.3  (./dist/tau_tools-1.2.3-py3-none-any.whl)
tau doctor              # Packages: ✓ no drift detected
```

**9. Iterate.** During development, install the source directory instead of a wheel and reinstall after changes:

```bash
tau install ./tau-tools     # Recorded source is the resolved absolute path
tau update tau-tools        # Upgrade in place later
```

To ship it, publish to PyPI and have consumers run `tau install pypi:tau-tools==1.2.3`, or point them at the git URL with a pinned tag.

## Next Steps

- [Extensions](extensions.md) — writing `register(tau)`, extension manifests, and per-extension dependencies
- [Settings](settings.md) — the `packages` block and scope precedence
- [CLI Reference](cli-reference.md) — full flag list for `install`, `remove`, `list`, `update`, and `doctor`
- [Security](security.md) — project trust, which gates project-scoped package installs
- [Development](development.md) — working on Tau itself
