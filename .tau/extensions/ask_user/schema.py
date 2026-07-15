from __future__ import annotations

from pydantic import BaseModel, Field

MAX_QUESTIONS = 4
MIN_OPTIONS = 2
MAX_OPTIONS = 4
MAX_LABEL_LENGTH = 60

FREEFORM_LABEL = "Type something…"


class AskUserOption(BaseModel):
    title: str
    description: str | None = None
    preview: str | None = Field(
        default=None,
        description=(
            "Optional preview content shown when this option is focused — an ASCII "
            "mockup, code snippet, diagram, or config example the user needs to see "
            "to compare choices. Markdown/plain text, multi-line supported. Only "
            "supported on single-select questions (allow_multiple=False)."
        ),
    )


class AskUserQuestion(BaseModel):
    question: str = Field(..., description="The question to ask the user")
    context: str | None = Field(
        default=None, description="Relevant context summary shown before the question"
    )
    options: list[str | AskUserOption] | None = Field(
        default=None,
        description=(
            f"Multiple-choice options ({MIN_OPTIONS}-{MAX_OPTIONS} when provided). "
            "Each needs a concise title (1-5 words); a description explaining the "
            "trade-off is recommended. Do not author a 'Type something…' option "
            "yourself — it is appended automatically when allow_freeform is true. "
            "If you recommend a specific option, list it first and append "
            '"(Recommended)" to its title.'
        ),
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


class AskUserParams(BaseModel):
    questions: list[AskUserQuestion] = Field(
        ...,
        min_length=1,
        max_length=MAX_QUESTIONS,
        description=(
            f"One or more questions to ask, in order (max {MAX_QUESTIONS} per call — "
            "group all clarifying questions into one invocation instead of stacking "
            "several ask_user calls back-to-back). Each is shown and answered before "
            "the next appears — like a short interview. Use a single-item list for a "
            "standalone question."
        ),
    )
    timeout: int | None = Field(
        default=None,
        description=(
            "Auto-dismiss after N ms of inactivity on the current question and "
            "cancel the rest of the sequence if it times out"
        ),
    )


AskUserQuestion.model_rebuild()
AskUserParams.model_rebuild()


class QuestionValidationError(Exception):
    """Raised when a question sequence fails validation before any UI is shown."""


def normalize_options(raw: list[str | AskUserOption] | None) -> list[AskUserOption]:
    return [AskUserOption(title=o) if isinstance(o, str) else o for o in (raw or [])]


def validate_questions(questions: list[AskUserQuestion]) -> None:
    """Reject malformed questionnaires up front, before rendering any UI.

    Mirrors the guardrails of the reference ask-user-question implementation:
    bounded option counts, no reserved/duplicate labels, no duplicate questions,
    and previews restricted to single-select.
    """
    if len(questions) > MAX_QUESTIONS:
        raise QuestionValidationError(
            f"ask_user supports at most {MAX_QUESTIONS} questions per call; got "
            f"{len(questions)}. Group them into fewer, related questions."
        )

    seen_questions: set[str] = set()
    for q in questions:
        if q.question in seen_questions:
            raise QuestionValidationError(f"Duplicate question: {q.question!r}")
        seen_questions.add(q.question)

        if not q.options:
            continue

        if not (MIN_OPTIONS <= len(q.options) <= MAX_OPTIONS):
            raise QuestionValidationError(
                f"Question {q.question!r} must have between {MIN_OPTIONS} and "
                f"{MAX_OPTIONS} options when options are provided; got {len(q.options)}."
            )

        seen_titles: set[str] = set()
        for opt in q.options:
            title = opt if isinstance(opt, str) else opt.title
            preview = None if isinstance(opt, str) else opt.preview

            if len(title) > MAX_LABEL_LENGTH:
                raise QuestionValidationError(
                    f"Option title {title!r} exceeds {MAX_LABEL_LENGTH} characters."
                )
            if title == FREEFORM_LABEL:
                raise QuestionValidationError(
                    f"{FREEFORM_LABEL!r} is reserved for the auto-appended freeform "
                    "row — do not author it as an option."
                )
            if title in seen_titles:
                raise QuestionValidationError(f"Duplicate option title: {title!r}")
            seen_titles.add(title)

            if preview and q.allow_multiple:
                raise QuestionValidationError(
                    "Option preview is only supported on single-select questions "
                    f"(question {q.question!r} has allow_multiple=True)."
                )
