"""Tests for tau/inference/utils.py — API error classification."""

from __future__ import annotations

import json

from tau.inference.utils import (
    ErrorKind,
    classify_error,
    format_error_body,
    format_exception_message,
)


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

    def test_overloaded_without_status_code(self):
        # Mid-stream SSE "overloaded_error" events arrive after the HTTP 200
        # headers, so the SDK exception has no status_code — text match only.
        e = _err("Overloaded")
        r = classify_error(e)
        assert r.kind == ErrorKind.OVERLOADED
        assert r.retryable is True

    # ── Pattern-only (no status code) ────────────────────────────────────────

    def test_billing_pattern_no_status(self):
        e = _err("insufficient credits to complete request")
        r = classify_error(e)
        assert r.kind == ErrorKind.BILLING

    def test_rate_limit_pattern_no_status(self):
        e = _err("rate limit exceeded, try again in 10 seconds")
        r = classify_error(e)
        assert r.kind == ErrorKind.RATE_LIMIT

    def test_resource_exhausted_camel_case_no_status(self):
        e = _err("ResourceExhausted: Worker local total request limit reached (288/48)")
        r = classify_error(e)
        assert r.kind == ErrorKind.RATE_LIMIT
        assert r.retryable is True

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


# ---------------------------------------------------------------------------
# Human-readable message extraction
# ---------------------------------------------------------------------------


class TestFormatExceptionMessage:
    """Each SDK hands us a different shape; the user must never see a dict repr.

    The shapes here are what the SDKs actually attach, not what their wire
    payloads look like — the openai client unwraps ``body["error"]`` onto the
    exception before we see it, so a provider returning ``error`` as a plain
    string leaves ``.body`` a ``str``.
    """

    def _sdk_error(self, text: str, body: object) -> Exception:
        exc = Exception(text)
        exc.body = body  # type: ignore[attr-defined]
        return exc

    def test_openai_shape_unwrapped_dict(self):
        error = self._sdk_error(
            "Error code: 429 - {'error': {'message': 'Rate limit reached.'}}",
            {"message": "Rate limit reached.", "type": "rate_limit"},
        )
        assert format_exception_message(error) == "Rate limit reached."

    def test_provider_returning_error_as_a_string(self):
        """xAI: {"code": ..., "error": "..."} leaves .body a bare string."""
        error = self._sdk_error(
            "Error code: 429 - {'code': 'quota', 'error': \"You've used all your usage.\"}",
            "You've used all your usage.",
        )
        assert format_exception_message(error) == "You've used all your usage."

    def test_anthropic_shape_whole_body(self):
        error = self._sdk_error(
            "Error code: 429 - {...}",
            {
                "type": "error",
                "error": {"type": "rate_limit_error", "message": "Token rate exceeded."},
            },
        )
        assert format_exception_message(error) == "Token rate exceeded."

    def test_error_as_string_inside_a_dict_body(self):
        error = self._sdk_error("Error code: 429 - {...}", {"code": "q", "error": "Plain reason."})
        assert format_exception_message(error) == "Plain reason."

    def test_top_level_message_in_body(self):
        error = self._sdk_error("Error code: 500 - {...}", {"message": "Internal error."})
        assert format_exception_message(error) == "Internal error."

    def test_google_genai_uses_the_message_attribute(self):
        """google-genai attaches no .body; it parses the payload onto .message."""
        error = Exception("429 RESOURCE_EXHAUSTED. {'error': {'message': 'Resource exhausted.'}}")
        error.message = "Resource exhausted."  # type: ignore[attr-defined]
        assert format_exception_message(error) == "Resource exhausted."

    def test_body_is_preferred_over_message(self):
        """openai defines .message too, holding the dict-repr string we avoid."""
        raw = "Error code: 429 - {'error': {'message': 'Real reason.'}}"
        error = self._sdk_error(raw, {"message": "Real reason."})
        error.message = raw  # type: ignore[attr-defined]
        assert format_exception_message(error) == "Real reason."

    def test_blank_string_body_falls_back(self):
        error = self._sdk_error("Error code: 429 - something", "   ")
        assert format_exception_message(error) == "Error code: 429 - something"

    def test_body_without_anything_useful_falls_back(self):
        error = self._sdk_error("Error code: 429 - {'code': 'x'}", {"code": "x"})
        assert format_exception_message(error) == "Error code: 429 - {'code': 'x'}"

    def test_no_metadata_falls_back_to_str(self):
        assert format_exception_message(Exception("Connection reset")) == "Connection reset"

    def test_non_mapping_body_falls_back(self):
        error = self._sdk_error("Error code: 500 - [1, 2]", [1, 2])
        assert format_exception_message(error) == "Error code: 500 - [1, 2]"

    def test_mistral_body_is_an_undecoded_json_document(self):
        """mistral attaches the raw JSON text, not a parsed mapping."""
        error = self._sdk_error(
            'API error occurred: Status 429. Body: {"message": "Rate limit exceeded"}',
            '{"message": "Rate limit exceeded"}',
        )
        assert format_exception_message(error) == "Rate limit exceeded"

    def test_a_json_string_body_with_a_nested_error(self):
        error = self._sdk_error("boom", '{"error": {"message": "Nested in a string."}}')
        assert format_exception_message(error) == "Nested in a string."

    def test_a_string_body_that_is_not_json_is_the_message(self):
        """openai has already unwrapped error -> str by the time we see it."""
        error = self._sdk_error("Error code: 429 - {...}", "You've used all your usage.")
        assert format_exception_message(error) == "You've used all your usage."

    def test_a_json_string_body_with_nothing_useful_returns_the_text(self):
        error = self._sdk_error("boom", '{"code": "only"}')
        assert format_exception_message(error) == '{"code": "only"}'


class TestFormatErrorBody:
    """Raw HTTP bodies: the image, video and audio layers hold text, not exceptions."""

    def test_nested_error_message(self):
        body = json.dumps({"error": {"message": "Rate limit exceeded.", "code": 429}})
        assert format_error_body(body) == "Rate limit exceeded."

    def test_error_as_a_string(self):
        body = json.dumps({"code": "quota", "error": "Insufficient balance."})
        assert format_error_body(body) == "Insufficient balance."

    def test_top_level_message(self):
        assert format_error_body(json.dumps({"message": "Warming up."})) == "Warming up."

    def test_an_unrecognised_payload_is_collapsed_not_hidden(self):
        """Better a one-line body than dropping detail we cannot interpret."""
        body = json.dumps({"weird": "shape"}, indent=4)

        result = format_error_body(body)

        assert "\n" not in result
        assert "weird" in result

    def test_non_json_text_is_collapsed(self):
        assert format_error_body("  plain   text\n  here ") == "plain text here"

    def test_empty_body(self):
        assert format_error_body("") == ""

    def test_a_multiline_message_becomes_one_line(self):
        body = json.dumps({"error": {"message": "line one\nline two"}})
        assert format_error_body(body) == "line one line two"


class TestOAuthBodyFormatterSharesTheRules:
    """The OAuth helper delegates, so the two cannot drift apart."""

    def test_it_matches_format_error_body(self):
        from tau.inference.provider.oauth.utils import format_http_error_body

        for payload in (
            json.dumps({"error": {"message": "Bad token."}}),
            json.dumps({"code": "q", "error": "Quota gone."}),
            json.dumps({"message": "Top."}),
            json.dumps({"code": "x"}),
            "  not   json  ",
        ):
            assert format_http_error_body(payload) == format_error_body(payload)
