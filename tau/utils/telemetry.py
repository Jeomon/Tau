from __future__ import annotations

from pathlib import Path

from tau.settings.paths import CONFIG_DIR_PATH

TELEMETRY_ENDPOINT = "https://tau.jeogeoalukka.workers.dev/api/report-install"
_REPORTED_VERSION_PATH = CONFIG_DIR_PATH / "telemetry-version"
_TIMEOUT = 5.0


async def report_install(
    version: str,
    *,
    endpoint: str = TELEMETRY_ENDPOINT,
    reported_version_path: Path = _REPORTED_VERSION_PATH,
) -> None:
    """Report one anonymous install/update count for each installed Tau version.

    The request contains only ``{"version": "..."}``. Failures are ignored and
    retried at the next startup; the local marker is written only after a
    successful response.
    """
    try:
        if reported_version_path.read_text(encoding="utf-8").strip() == version:
            return
    except OSError:
        pass

    try:
        import httpx

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(endpoint, json={"version": version})
            if response.status_code != 204:
                return

        reported_version_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        reported_version_path.write_text(version, encoding="utf-8")
        reported_version_path.chmod(0o600)
    except Exception:
        return
