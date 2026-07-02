from __future__ import annotations

from pathlib import Path

from .types import RootFinder, ServerDefinition


def _nearest_root(markers: list[str], exclude_markers: list[str] | None = None) -> RootFinder:
    """
    Walk up from the file's directory looking for any of `markers`.
    Returns the directory containing the first marker found, or the
    file's own directory if none found. Returns None if an exclude
    marker is hit first (mirrors opencode's NearestRoot logic).
    Supports glob patterns (e.g. "*.sln") via Path.glob().
    """

    async def find(file: str) -> str | None:
        # Resolve to absolute: the returned root becomes an LSP rootUri via
        # Path(root).as_uri(), which rejects relative paths.
        start = Path(file).resolve().parent
        current = start

        while True:
            if exclude_markers:
                for ex in exclude_markers:
                    if (current / ex).exists():
                        return None

            for marker in markers:
                if "*" in marker:
                    if any(current.glob(marker)):
                        return str(current)
                elif (current / marker).exists():
                    return str(current)

            parent = current.parent
            if parent == current:
                return str(start)
            current = parent

    return find


def _file_dir(file: str) -> str:
    return str(Path(file).parent)


# ── Root finders ──────────────────────────────────────────────────────────────


async def _pyright_root(file: str) -> str | None:
    return await _nearest_root(["pyrightconfig.json", "pyproject.toml", "setup.py", "setup.cfg"])(
        file
    )


async def _ruff_root(file: str) -> str | None:
    return await _nearest_root(["ruff.toml", ".ruff.toml", "pyproject.toml"])(file)


async def _ts_root(file: str) -> str | None:
    return await _nearest_root(["tsconfig.json", "package.json"])(file)


async def _deno_root(file: str) -> str | None:
    return await _nearest_root(["deno.json", "deno.jsonc"])(file)


async def _go_root(file: str) -> str | None:
    return await _nearest_root(["go.mod"])(file)


async def _rust_root(file: str) -> str | None:
    return await _nearest_root(["Cargo.toml"])(file)


async def _clangd_root(file: str) -> str | None:
    return await _nearest_root(["compile_commands.json", "CMakeLists.txt", ".clangd"])(file)


async def _java_root(file: str) -> str | None:
    return await _nearest_root(["pom.xml", "build.gradle", "build.gradle.kts", ".project"])(file)


async def _ruby_root(file: str) -> str | None:
    return await _nearest_root(["Gemfile", ".ruby-version"])(file)


async def _lua_root(file: str) -> str | None:
    return await _nearest_root([".luarc.json", ".luarc.jsonc", ".luacheckrc"])(file)


async def _zig_root(file: str) -> str | None:
    return await _nearest_root(["build.zig", "build.zig.zon"])(file)


async def _swift_root(file: str) -> str | None:
    return await _nearest_root(["Package.swift"])(file)


async def _elixir_root(file: str) -> str | None:
    return await _nearest_root(["mix.exs"])(file)


async def _kotlin_root(file: str) -> str | None:
    return await _nearest_root(
        ["build.gradle.kts", "build.gradle", "pom.xml", "settings.gradle.kts"]
    )(file)


async def _terraform_root(file: str) -> str | None:
    return await _nearest_root([".terraform", "terraform.tf", "main.tf"])(file)


async def _csharp_root(file: str) -> str | None:
    return await _nearest_root(["*.sln", "*.csproj"])(file)


async def _php_root(file: str) -> str | None:
    return await _nearest_root(["composer.json"])(file)


async def _erlang_root(file: str) -> str | None:
    return await _nearest_root(["rebar.config", "erlang.mk", "mix.exs"])(file)


async def _haskell_root(file: str) -> str | None:
    return await _nearest_root(["*.cabal", "stack.yaml", "cabal.project"])(file)


async def _ocaml_root(file: str) -> str | None:
    return await _nearest_root(["dune-project", "*.opam"])(file)


async def _svelte_root(file: str) -> str | None:
    return await _nearest_root(["svelte.config.js", "svelte.config.ts", "package.json"])(file)


async def _vue_root(file: str) -> str | None:
    return await _nearest_root(["vue.config.js", "vite.config.ts", "package.json"])(file)


async def _yaml_root(file: str) -> str | None:
    return _file_dir(file)


async def _bash_root(file: str) -> str | None:
    return _file_dir(file)


async def _astro_root(file: str) -> str | None:
    return await _nearest_root(
        ["astro.config.js", "astro.config.mjs", "astro.config.ts", "package.json"]
    )(file)


async def _clojure_root(file: str) -> str | None:
    return await _nearest_root(["deps.edn", "project.clj", "build.clj", ".clj-kondo"])(file)


async def _dart_root(file: str) -> str | None:
    return await _nearest_root(["pubspec.yaml"])(file)


async def _eslint_root(file: str) -> str | None:
    return await _nearest_root(
        [
            "eslint.config.js",
            "eslint.config.mjs",
            "eslint.config.cjs",
            ".eslintrc.js",
            ".eslintrc.cjs",
            ".eslintrc.json",
            ".eslintrc.yaml",
            ".eslintrc.yml",
            ".eslintrc",
            "package.json",
        ]
    )(file)


async def _fsharp_root(file: str) -> str | None:
    return await _nearest_root(["*.sln", "*.fsproj"])(file)


async def _gleam_root(file: str) -> str | None:
    return await _nearest_root(["gleam.toml"])(file)


async def _julia_root(file: str) -> str | None:
    return await _nearest_root(["Project.toml", "JuliaProject.toml"])(file)


async def _nix_root(file: str) -> str | None:
    return await _nearest_root(["flake.nix", "default.nix", "shell.nix"])(file)


async def _prisma_root(file: str) -> str | None:
    return await _nearest_root(["package.json", "prisma"])(file)


async def _typst_root(file: str) -> str | None:
    return await _nearest_root(["typst.toml"])(file) or _file_dir(file)


# ── Built-in server definitions ───────────────────────────────────────────────

BUILTIN_SERVERS: list[ServerDefinition] = [
    # ── Python ────────────────────────────────────────────────────────────────
    ServerDefinition(
        id="pyright",
        extensions=[".py", ".pyi"],
        command=["pyright-langserver", "--stdio"],
        root_finder=_pyright_root,
    ),
    ServerDefinition(
        id="ruff",
        extensions=[".py", ".pyi"],
        command=["ruff", "server"],
        root_finder=_ruff_root,
    ),
    # ── JavaScript / TypeScript ───────────────────────────────────────────────
    ServerDefinition(
        id="typescript-language-server",
        extensions=[".ts", ".tsx", ".js", ".jsx", ".mts", ".cts", ".mjs", ".cjs"],
        command=["typescript-language-server", "--stdio"],
        root_finder=_ts_root,
        initialization={"preferences": {}},
    ),
    ServerDefinition(
        id="deno",
        extensions=[".ts", ".tsx", ".js", ".jsx", ".mjs"],
        command=["deno", "lsp"],
        root_finder=_deno_root,
    ),
    ServerDefinition(
        id="eslint",
        extensions=[".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".vue"],
        command=["vscode-eslint-language-server", "--stdio"],
        root_finder=_eslint_root,
    ),
    ServerDefinition(
        id="oxlint",
        extensions=[
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
            ".mjs",
            ".cjs",
            ".mts",
            ".cts",
            ".vue",
            ".astro",
            ".svelte",
        ],
        command=["oxlint-language-server"],
        root_finder=_eslint_root,
    ),
    # ── Svelte / Vue / Astro ──────────────────────────────────────────────────
    ServerDefinition(
        id="svelte-language-server",
        extensions=[".svelte"],
        command=["svelteserver", "--stdio"],
        root_finder=_svelte_root,
    ),
    ServerDefinition(
        id="vue-language-server",
        extensions=[".vue"],
        command=["vue-language-server", "--stdio"],
        root_finder=_vue_root,
    ),
    ServerDefinition(
        id="astro",
        extensions=[".astro"],
        command=["astro-ls", "--stdio"],
        root_finder=_astro_root,
    ),
    # ── Go ────────────────────────────────────────────────────────────────────
    ServerDefinition(
        id="gopls",
        extensions=[".go"],
        command=["gopls"],
        root_finder=_go_root,
    ),
    # ── Rust ─────────────────────────────────────────────────────────────────
    ServerDefinition(
        id="rust-analyzer",
        extensions=[".rs"],
        command=["rust-analyzer"],
        root_finder=_rust_root,
    ),
    # ── C / C++ ───────────────────────────────────────────────────────────────
    ServerDefinition(
        id="clangd",
        extensions=[".c", ".cpp", ".cxx", ".cc", ".c++", ".h", ".hpp", ".hxx", ".hh", ".h++"],
        command=["clangd"],
        root_finder=_clangd_root,
    ),
    # ── Java ─────────────────────────────────────────────────────────────────
    ServerDefinition(
        id="jdtls",
        extensions=[".java"],
        command=["jdtls"],
        root_finder=_java_root,
    ),
    # ── Kotlin ───────────────────────────────────────────────────────────────
    ServerDefinition(
        id="kotlin-language-server",
        extensions=[".kt", ".kts"],
        command=["kotlin-language-server"],
        root_finder=_kotlin_root,
    ),
    # ── C# / F# / Razor ──────────────────────────────────────────────────────
    ServerDefinition(
        id="omnisharp",
        extensions=[".cs", ".csx"],
        command=["omnisharp", "--languageserver", "--hostPID", str(__import__("os").getpid())],
        root_finder=_csharp_root,
    ),
    ServerDefinition(
        id="fsautocomplete",
        extensions=[".fs", ".fsi", ".fsx", ".fsscript"],
        command=["fsautocomplete"],
        root_finder=_fsharp_root,
    ),
    ServerDefinition(
        id="razor",
        extensions=[".razor", ".cshtml"],
        command=["rzls"],
        root_finder=_csharp_root,
    ),
    # ── Ruby ─────────────────────────────────────────────────────────────────
    ServerDefinition(
        id="ruby-lsp",
        extensions=[".rb", ".rake", ".gemspec", ".ru"],
        command=["ruby-lsp"],
        root_finder=_ruby_root,
    ),
    ServerDefinition(
        id="solargraph",
        extensions=[".rb"],
        command=["solargraph", "stdio"],
        root_finder=_ruby_root,
        enabled=False,  # prefer ruby-lsp; enable via settings if needed
    ),
    # ── PHP ──────────────────────────────────────────────────────────────────
    ServerDefinition(
        id="intelephense",
        extensions=[".php"],
        command=["intelephense", "--stdio"],
        root_finder=_php_root,
    ),
    ServerDefinition(
        id="phpactor",
        extensions=[".php"],
        command=["phpactor", "language-server"],
        root_finder=_php_root,
        enabled=False,  # prefer intelephense; enable via settings if needed
    ),
    # ── Lua ──────────────────────────────────────────────────────────────────
    ServerDefinition(
        id="lua-language-server",
        extensions=[".lua"],
        command=["lua-language-server"],
        root_finder=_lua_root,
    ),
    # ── Zig ──────────────────────────────────────────────────────────────────
    ServerDefinition(
        id="zls",
        extensions=[".zig", ".zon"],
        command=["zls"],
        root_finder=_zig_root,
    ),
    # ── Swift / Objective-C ───────────────────────────────────────────────────
    ServerDefinition(
        id="sourcekit-lsp",
        extensions=[".swift", ".m", ".mm"],
        command=["sourcekit-lsp"],
        root_finder=_swift_root,
    ),
    # ── Elixir ───────────────────────────────────────────────────────────────
    ServerDefinition(
        id="elixir-ls",
        extensions=[".ex", ".exs"],
        command=["elixir-ls"],
        root_finder=_elixir_root,
    ),
    # ── Erlang ───────────────────────────────────────────────────────────────
    ServerDefinition(
        id="erlang-ls",
        extensions=[".erl", ".hrl"],
        command=["erlang_ls"],
        root_finder=_erlang_root,
    ),
    # ── Haskell ──────────────────────────────────────────────────────────────
    ServerDefinition(
        id="haskell-language-server",
        extensions=[".hs", ".lhs"],
        command=["haskell-language-server-wrapper", "--lsp"],
        root_finder=_haskell_root,
    ),
    # ── OCaml ────────────────────────────────────────────────────────────────
    ServerDefinition(
        id="ocamllsp",
        extensions=[".ml", ".mli"],
        command=["ocamllsp"],
        root_finder=_ocaml_root,
    ),
    # ── Clojure ──────────────────────────────────────────────────────────────
    ServerDefinition(
        id="clojure-lsp",
        extensions=[".clj", ".cljs", ".cljc", ".edn"],
        command=["clojure-lsp"],
        root_finder=_clojure_root,
    ),
    # ── Dart ─────────────────────────────────────────────────────────────────
    ServerDefinition(
        id="dart",
        extensions=[".dart"],
        command=["dart", "language-server", "--client-id=tau"],
        root_finder=_dart_root,
    ),
    # ── Gleam ────────────────────────────────────────────────────────────────
    ServerDefinition(
        id="gleam",
        extensions=[".gleam"],
        command=["gleam", "lsp"],
        root_finder=_gleam_root,
    ),
    # ── Julia ────────────────────────────────────────────────────────────────
    ServerDefinition(
        id="julials",
        extensions=[".jl"],
        command=[
            "julia",
            "--startup-file=no",
            "--history-file=no",
            "-e",
            "using LanguageServer; runserver()",
        ],
        root_finder=_julia_root,
    ),
    # ── Nix ──────────────────────────────────────────────────────────────────
    ServerDefinition(
        id="nixd",
        extensions=[".nix"],
        command=["nixd"],
        root_finder=_nix_root,
    ),
    # ── Prisma ───────────────────────────────────────────────────────────────
    ServerDefinition(
        id="prisma",
        extensions=[".prisma"],
        command=["prisma-language-server", "--stdio"],
        root_finder=_prisma_root,
    ),
    # ── Typst ────────────────────────────────────────────────────────────────
    ServerDefinition(
        id="tinymist",
        extensions=[".typ", ".typc"],
        command=["tinymist", "lsp"],
        root_finder=_typst_root,
    ),
    # ── Terraform ────────────────────────────────────────────────────────────
    ServerDefinition(
        id="terraform-ls",
        extensions=[".tf", ".tfvars"],
        command=["terraform-ls", "serve"],
        root_finder=_terraform_root,
    ),
    # ── YAML ─────────────────────────────────────────────────────────────────
    ServerDefinition(
        id="yaml-language-server",
        extensions=[".yaml", ".yml"],
        command=["yaml-language-server", "--stdio"],
        root_finder=_yaml_root,
        initialization={"yaml": {"schemas": {}}},
    ),
    # ── Bash / Shell ─────────────────────────────────────────────────────────
    ServerDefinition(
        id="bash-language-server",
        extensions=[".sh", ".bash", ".zsh", ".ksh", ".fish"],
        command=["bash-language-server", "start"],
        root_finder=_bash_root,
    ),
]
