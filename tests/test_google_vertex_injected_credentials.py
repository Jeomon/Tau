"""pi #6862 — google-vertex must accept run-local, in-memory credentials.

A multi-tenant caller can pass a google.auth Credentials object via
LLMOptions.credentials to select a per-run service account without mutating
GOOGLE_APPLICATION_CREDENTIALS process-wide. Injected creds take precedence
over the env var.
"""

from __future__ import annotations

import tau.inference.api.text.google_vertex as gv
from tau.inference.types import LLMOptions


class _FakeClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def _capture(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    def fake_client(**kwargs):
        calls.append(kwargs)
        return _FakeClient(**kwargs)

    monkeypatch.setattr(gv.genai, "Client", fake_client)
    return calls


def test_injected_credentials_are_forwarded(monkeypatch):
    calls = _capture(monkeypatch)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    sentinel = object()  # stands in for a google.auth.credentials.Credentials
    opts = LLMOptions(
        credentials=sentinel,
        extra_params={"project": "proj-1", "location": "us-central1"},
    )

    gv._build_client(opts)

    assert len(calls) == 1
    assert calls[0]["credentials"] is sentinel
    assert calls[0]["project"] == "proj-1"
    assert calls[0]["location"] == "us-central1"
    assert calls[0].get("vertexai") is True


def test_injected_credentials_take_precedence_over_env_file(monkeypatch):
    calls = _capture(monkeypatch)
    # Env points at a service-account file; the injected credential must win and
    # the file must NOT be read (from_service_account_file would otherwise raise).
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/nonexistent/key.json")
    sentinel = object()
    opts = LLMOptions(
        credentials=sentinel,
        extra_params={"project": "proj-2", "location": "europe-west1"},
    )

    gv._build_client(opts)

    assert calls[0]["credentials"] is sentinel


def test_falls_back_to_adc_when_nothing_supplied(monkeypatch):
    calls = _capture(monkeypatch)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    opts = LLMOptions(extra_params={"project": "proj-3", "location": "us-central1"})

    gv._build_client(opts)

    # ADC path: no credentials kwarg, just project/location
    assert "credentials" not in calls[0]
    assert calls[0]["project"] == "proj-3"


def test_api_key_still_wins_first(monkeypatch):
    calls = _capture(monkeypatch)
    opts = LLMOptions(api_key="real-key", credentials=object())

    gv._build_client(opts)

    # api_key path returns early with no project/location and no credentials kwarg
    assert calls[0]["api_key"] == "real-key"
    assert "credentials" not in calls[0]
