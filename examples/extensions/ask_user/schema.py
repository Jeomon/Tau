from __future__ import annotations

from pydantic import BaseModel, Field


class AskUserOption(BaseModel):
    title: str
    description: str | None = None


class AskUserParams(BaseModel):
    question: str = Field(..., description="The question to ask the user")
    context: str | None = Field(
        default=None, description="Relevant context summary shown before the question"
    )
    options: list[str | AskUserOption] | None = Field(
        default=None, description="Multiple-choice options"
    )
    allow_multiple: bool = Field(default=False, description="Allow selecting more than one option")
    allow_freeform: bool = Field(
        default=True, description="Offer a 'Type something' freeform option"
    )
    multiline: bool = Field(
        default=False,
        description=(
            "Use a multi-line text editor for the freeform answer instead of a single "
            "line — set this for open-ended, long-form answers (e.g. 'write your bio', "
            "'describe the requirements'). Supports arrow-key navigation between lines, "
            "Enter for a newline, Ctrl+S/Ctrl+Enter to submit."
        ),
    )
    timeout: int | None = Field(
        default=None, description="Auto-dismiss after N ms and cancel if the prompt times out"
    )


AskUserParams.model_rebuild()


def normalize_options(raw: list[str | AskUserOption] | None) -> list[AskUserOption]:
    return [AskUserOption(title=o) if isinstance(o, str) else o for o in (raw or [])]
