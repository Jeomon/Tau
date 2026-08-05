from __future__ import annotations

from pathlib import Path

from tau.prompts.types import LoadPromptsResult, PromptLoadError, PromptTemplate
from tau.utils.frontmatter import parse_frontmatter


def load_template_from_file(path: Path) -> tuple[PromptTemplate | None, str | None]:
    """Load a prompt template from a markdown file."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:
        return None, f"read error: {exc}"

    meta, body = parse_frontmatter(text)
    body = body.strip()
    if not body:
        return None, "template body is empty"

    name = path.stem.lower()
    description = meta.get("description", "")
    if not description:
        for line in body.splitlines():
            stripped = line.strip().lstrip("#").strip()
            if stripped:
                description = stripped[:120]
                break

    argument_hint = meta.get("argument-hint") or meta.get("argument_hint") or None

    return PromptTemplate(
        name=name,
        description=description,
        content=body,
        argument_hint=argument_hint,
        file_path=str(path),
    ), None


def load_templates_from_dir(directory: Path) -> LoadPromptsResult:
    """Load all prompt templates from a directory."""
    result = LoadPromptsResult()
    if not directory.is_dir():
        return result
    for path in sorted(directory.glob("*.md")):
        tmpl, err = load_template_from_file(path)
        if err or tmpl is None:
            result.errors.append(PromptLoadError(str(path), err or "unknown error"))
            continue
        result.templates[tmpl.name] = tmpl
    return result
