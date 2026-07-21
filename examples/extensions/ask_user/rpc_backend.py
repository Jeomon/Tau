"""Ask the questions through an RPC client instead of the TUI component.

An RPC client understands a fixed set of dialog shapes (`select`, `multi_select`,
`confirm`, `input`, `editor`) and nothing else — there is no grid to render the
tabbed component onto. This backend expresses the questionnaire in those shapes:

* single-select → one ``select``
* multi-select  → one ``multi_select``
* free text     → ``editor`` for multiline questions, ``input`` otherwise

What is lost versus the TUI: the tab bar, the review-and-revise step, and
option previews. Answers come back in exactly the same shape either way, so
the model cannot tell which backend ran.
"""

from __future__ import annotations

from typing import Any

from .schema import FREEFORM_LABEL, AskUserOption, AskUserQuestion


def _prompt_text(question: AskUserQuestion) -> str:
    """Dialog title: the question, with its context above when there is one."""
    if question.context:
        return f"{question.context}\n\n{question.question}"
    return question.question


def _option_label(option: AskUserOption) -> str:
    """Fold the description into the label — a client only renders the label."""
    if option.description:
        return f"{option.title} — {option.description}"
    return option.title


async def _ask_free_text(
    ui: Any, question: AskUserQuestion, seed: str, timeout: float | None
) -> str | None:
    """Open whichever text dialog suits the question. ``None`` if dismissed."""
    if question.multiline:
        return await ui.editor(_prompt_text(question), seed, timeout)
    return await ui.input(_prompt_text(question), "", timeout)


async def _ask_single(
    ui: Any, question: AskUserQuestion, options: list[AskUserOption], timeout: float | None
) -> dict | None:
    labels = [_option_label(o) for o in options]
    if question.allow_freeform:
        labels.append(FREEFORM_LABEL)

    choice = await ui.select(_prompt_text(question), labels, timeout)
    if choice is None:
        return None

    if choice == FREEFORM_LABEL:
        text = await _ask_free_text(ui, question, "", timeout)
        if text is None:
            return None
        return {"kind": "freeform", "text": text}

    for option, label in zip(options, labels, strict=False):
        if label == choice:
            return {"kind": "selection", "selections": [option.title]}
    # A client that echoed something we never offered — treat it as free text
    # rather than dropping the user's answer on the floor.
    return {"kind": "freeform", "text": choice}


async def _ask_multi(
    ui: Any, question: AskUserQuestion, options: list[AskUserOption], timeout: float | None
) -> dict | None:
    """One ``multi_select`` dialog, plus a text dialog if the user asks for it.

    Picking the "Type something…" row alongside real options mirrors the TUI,
    where free text rides along with the ticked boxes rather than replacing them.
    """
    labels = [_option_label(o) for o in options]
    if question.allow_freeform:
        labels.append(FREEFORM_LABEL)

    chosen = await ui.multi_select(_prompt_text(question), labels, timeout)
    if chosen is None:
        return None

    selections: list[str] = []
    wants_text = False
    for label in chosen:
        if label == FREEFORM_LABEL:
            wants_text = True
            continue
        for option, option_label in zip(options, labels, strict=False):
            if option_label == label:
                selections.append(option.title)
                break
        else:
            selections.append(label)  # unknown echo — keep the user's answer

    payload: dict[str, Any] = {"kind": "selection", "selections": list(selections)}
    if wants_text:
        text = await _ask_free_text(ui, question, "", timeout)
        if text is None:
            return None
        text = text.strip()
        if text:
            payload["text"] = text
            payload["selections"] = [*selections, text]
    return payload


async def ask_over_bridge(
    ui: Any,
    questions: list[AskUserQuestion],
    options_per_question: list[list[AskUserOption]],
    timeout_ms: int | None,
) -> list[dict | None] | None:
    """Run the whole questionnaire over the protocol's dialogs.

    Returns one response per question, or ``None`` if the user dismissed any of
    them — cancelling one question cancels the set, matching the TUI, where
    there is no half-submitted questionnaire.
    """
    timeout = timeout_ms / 1000 if timeout_ms else None
    responses: list[dict | None] = []

    for question, options in zip(questions, options_per_question, strict=True):
        if not options:
            text = await _ask_free_text(ui, question, "", timeout)
            if text is None:
                return None
            responses.append({"kind": "freeform", "text": text})
            continue

        answer = (
            await _ask_multi(ui, question, options, timeout)
            if question.allow_multiple
            else await _ask_single(ui, question, options, timeout)
        )
        if answer is None:
            return None
        responses.append(answer)

    return responses
