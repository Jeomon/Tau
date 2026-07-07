"""Shared pytest fixtures for the Tau test suite."""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session", autouse=True)
def _load_compaction_tokenizer() -> None:
    """Force the compaction tokenizer's background load to finish before tests run.

    tau.session.compaction loads its real tokenizer (tiktoken) in a background
    thread so a live session's first turn never blocks on it (see
    _start_loading_encoding) — but that means whether a given test observes the
    real tokenizer or the chars/4 fallback would otherwise be a race. Waiting
    once here, before any test runs, makes token-count assertions deterministic
    across the whole suite.
    """
    from tau.session import compaction

    compaction._start_loading_encoding()
    compaction._encoding_ready.wait(timeout=15.0)
