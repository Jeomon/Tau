from __future__ import annotations

import asyncio

import posthog.client as posthog_client
from posthog.request import APIError

from tau.telemetry import report_install


def test_reports_only_version_once(tmp_path, monkeypatch):
    calls = []

    def fake_batch_post(api_key, host=None, **kwargs):
        calls.append(kwargs["batch"][0])

    monkeypatch.setattr(posthog_client, "batch_post", fake_batch_post)
    marker = tmp_path / "telemetry-version"

    asyncio.run(
        report_install(
            "1.2.3",
            host="https://example.test",
            api_key="phc_test",
            reported_version_path=marker,
        )
    )
    asyncio.run(
        report_install(
            "1.2.3",
            host="https://example.test",
            api_key="phc_test",
            reported_version_path=marker,
        )
    )

    assert len(calls) == 1
    assert calls[0]["event"] == "tau"
    assert calls[0]["distinct_id"] == "anonymous"
    assert calls[0]["properties"]["version"] == "1.2.3"
    assert marker.read_text(encoding="utf-8") == "1.2.3"


def test_failed_report_is_retried(tmp_path, monkeypatch):
    calls = 0

    def fake_batch_post(api_key, host=None, **kwargs):
        nonlocal calls
        calls += 1
        raise APIError(503, "unavailable")

    monkeypatch.setattr(posthog_client, "batch_post", fake_batch_post)
    marker = tmp_path / "telemetry-version"

    asyncio.run(
        report_install(
            "1.2.3",
            host="https://example.test",
            api_key="phc_test",
            reported_version_path=marker,
        )
    )
    asyncio.run(
        report_install(
            "1.2.3",
            host="https://example.test",
            api_key="phc_test",
            reported_version_path=marker,
        )
    )

    assert calls == 2
    assert not marker.exists()
