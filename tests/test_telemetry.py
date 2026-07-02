from __future__ import annotations

import asyncio

import httpx

from tau.utils.telemetry import report_install


def test_reports_only_version_once(tmp_path, monkeypatch):
    requests: list[httpx.Request] = []
    async_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: async_client(
            transport=httpx.MockTransport(handler),
            **kwargs,
        ),
    )
    marker = tmp_path / "telemetry-version"

    asyncio.run(
        report_install(
            "1.2.3", endpoint="https://example.test/report", reported_version_path=marker
        )
    )
    asyncio.run(
        report_install(
            "1.2.3", endpoint="https://example.test/report", reported_version_path=marker
        )
    )

    assert len(requests) == 1
    assert requests[0].content == b'{"version":"1.2.3"}'
    assert marker.read_text(encoding="utf-8") == "1.2.3"


def test_failed_report_is_retried(tmp_path, monkeypatch):
    calls = 0
    async_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: async_client(
            transport=httpx.MockTransport(handler),
            **kwargs,
        ),
    )
    marker = tmp_path / "telemetry-version"

    asyncio.run(
        report_install(
            "1.2.3", endpoint="https://example.test/report", reported_version_path=marker
        )
    )
    asyncio.run(
        report_install(
            "1.2.3", endpoint="https://example.test/report", reported_version_path=marker
        )
    )

    assert calls == 2
    assert not marker.exists()
