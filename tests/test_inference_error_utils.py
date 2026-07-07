"""Tests for tau/inference/utils.py — API error classification."""

from __future__ import annotations

from tau.inference.utils import ErrorKind, classify_error


def _err(msg: str = "", status: int | None = None, type_name: str | None = None) -> Exception:
    """Build a minimal fake exception with controllable status_code and message."""
    exc = Exception(msg)
    if status is not None:
        exc.status_code = status  # type: ignore[attr-defined]
    if type_name is not None:
        exc.__class__ = type(type_name, (Exception,), {})
    return exc


class TestClassifyError:
    # ── Content policy ────────────────────────────────────────────────────────

    def test_content_blocked_by_message(self):
        e = _err("violates our usage policies")
        r = classify_error(e)
        assert r.kind == ErrorKind.CONTENT_BLOCKED
        assert r.retryable is False

    def test_content_blocked_content_filter(self):
        e = _err("content_filter triggered")
        r = classify_error(e)
        assert r.kind == ErrorKind.CONTENT_BLOCKED
        assert r.retryable is False

    # ── HTTP 401/403 ──────────────────────────────────────────────────────────

    def test_401_invalid_api_key(self):
        e = _err("invalid api key provided", status=401)
        r = classify_error(e)
        assert r.kind == ErrorKind.AUTH_PERMANENT
        assert r.retryable is False

    def test_401_generic_auth(self):
        e = _err("authentication required", status=401)
        r = classify_error(e)
        assert r.kind == ErrorKind.AUTH
        assert r.retryable is False

    def test_403_forbidden(self):
        e = _err("forbidden", status=403)
        r = classify_error(e)
        assert r.kind == ErrorKind.AUTH
        assert r.retryable is False

    # ── HTTP 402 ──────────────────────────────────────────────────────────────

    def test_402_billing(self):
        e = _err("payment required", status=402)
        r = classify_error(e)
        assert r.kind == ErrorKind.BILLING
        assert r.retryable is False

    # ── HTTP 429 ──────────────────────────────────────────────────────────────

    def test_429_rate_limit(self):
        e = _err("too many requests", status=429)
        r = classify_error(e)
        assert r.kind == ErrorKind.RATE_LIMIT
        assert r.retryable is True

    def test_429_billing_message_overrides(self):
        e = _err("insufficient credits, please top up", status=429)
        r = classify_error(e)
        assert r.kind == ErrorKind.BILLING
        assert r.retryable is False

    # ── HTTP 413 ──────────────────────────────────────────────────────────────

    def test_413_context_overflow(self):
        e = _err("payload too large", status=413)
        r = classify_error(e)
        assert r.kind == ErrorKind.CONTEXT_OVERFLOW
        assert r.should_compact is True

    # ── HTTP 400 ──────────────────────────────────────────────────────────────

    def test_400_context_overflow_message(self):
        e = _err("context length exceeded the limit", status=400)
        r = classify_error(e)
        assert r.kind == ErrorKind.CONTEXT_OVERFLOW
        assert r.should_compact is True

    def test_400_negative_max_tokens_is_context_overflow(self):
        e = _err("max_tokens must be at least 1, got -128", status=400)
        r = classify_error(e)
        assert r.kind == ErrorKind.CONTEXT_OVERFLOW
        assert r.should_compact is True

    def test_400_model_not_found(self):
        e = _err("model not found", status=400)
        r = classify_error(e)
        assert r.kind == ErrorKind.MODEL_NOT_FOUND
        assert r.retryable is False

    def test_400_generic_format_error(self):
        e = _err("bad request", status=400)
        r = classify_error(e)
        assert r.kind == ErrorKind.FORMAT_ERROR
        assert r.retryable is False

    # ── HTTP 404 ──────────────────────────────────────────────────────────────

    def test_404_model_not_found(self):
        e = _err("is not a valid model", status=404)
        r = classify_error(e)
        assert r.kind == ErrorKind.MODEL_NOT_FOUND

    def test_404_generic(self):
        e = _err("not found", status=404)
        r = classify_error(e)
        assert r.kind == ErrorKind.FORMAT_ERROR

    # ── HTTP 500/502 ──────────────────────────────────────────────────────────

    def test_500_server_error(self):
        e = _err("internal server error", status=500)
        r = classify_error(e)
        assert r.kind == ErrorKind.SERVER_ERROR
        assert r.retryable is True

    def test_502_server_error(self):
        e = _err("bad gateway", status=502)
        r = classify_error(e)
        assert r.kind == ErrorKind.SERVER_ERROR
        assert r.retryable is True

    # ── HTTP 503/529 ──────────────────────────────────────────────────────────

    def test_503_overloaded(self):
        e = _err("service unavailable", status=503)
        r = classify_error(e)
        assert r.kind == ErrorKind.OVERLOADED
        assert r.retryable is True

    def test_529_overloaded(self):
        e = _err("overloaded", status=529)
        r = classify_error(e)
        assert r.kind == ErrorKind.OVERLOADED

    # ── Pattern-only (no status code) ────────────────────────────────────────

    def test_billing_pattern_no_status(self):
        e = _err("insufficient credits to complete request")
        r = classify_error(e)
        assert r.kind == ErrorKind.BILLING

    def test_rate_limit_pattern_no_status(self):
        e = _err("rate limit exceeded, try again in 10 seconds")
        r = classify_error(e)
        assert r.kind == ErrorKind.RATE_LIMIT

    def test_context_overflow_pattern_no_status(self):
        e = _err("prompt is too long for context window")
        r = classify_error(e)
        assert r.kind == ErrorKind.CONTEXT_OVERFLOW
        assert r.should_compact is True

    def test_auth_pattern_no_status(self):
        e = _err("invalid api key")
        r = classify_error(e)
        assert r.kind == ErrorKind.AUTH_PERMANENT

    def test_timeout_pattern_no_status(self):
        e = _err("request timed out after 60s")
        r = classify_error(e)
        assert r.kind == ErrorKind.TIMEOUT
        assert r.retryable is True

    # ── Transport errors ──────────────────────────────────────────────────────

    def test_oserror_is_timeout(self):
        e = OSError("connection refused")
        r = classify_error(e)
        assert r.kind == ErrorKind.TIMEOUT
        assert r.retryable is True

    def test_python_timeout_error(self):
        e = TimeoutError("timed out")
        r = classify_error(e)
        assert r.kind == ErrorKind.TIMEOUT

    def test_rate_limit_error_type_forces_429(self):
        # SDK RateLimitError without a status code should still classify as rate limit
        class RateLimitError(Exception):
            pass

        exc = RateLimitError("rate limited")
        r = classify_error(exc)
        assert r.kind == ErrorKind.RATE_LIMIT

    # ── Unknown ───────────────────────────────────────────────────────────────

    def test_unknown_error(self):
        e = Exception("something went wrong")
        r = classify_error(e)
        assert r.kind == ErrorKind.UNKNOWN
        assert r.retryable is True
