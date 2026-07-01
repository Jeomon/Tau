"""Tests for tau/inference/provider/oauth/pkce.py — PKCE challenge generation."""

from __future__ import annotations

import base64
import hashlib

from tau.inference.provider.oauth.pkce import generate_pkce


class TestGeneratePkce:
    def test_returns_two_strings(self):
        verifier, challenge = generate_pkce()
        assert isinstance(verifier, str)
        assert isinstance(challenge, str)

    def test_verifier_is_base64url(self):
        verifier, _ = generate_pkce()
        # base64url uses only A-Z a-z 0-9 - _
        allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
        assert all(c in allowed for c in verifier)

    def test_challenge_is_base64url(self):
        _, challenge = generate_pkce()
        allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
        assert all(c in allowed for c in challenge)

    def test_no_padding(self):
        verifier, challenge = generate_pkce()
        assert "=" not in verifier
        assert "=" not in challenge

    def test_challenge_is_sha256_of_verifier(self):
        verifier, challenge = generate_pkce()
        digest = hashlib.sha256(verifier.encode()).digest()
        expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        assert challenge == expected

    def test_unique_per_call(self):
        pairs = {generate_pkce() for _ in range(20)}
        assert len(pairs) == 20

    def test_verifier_minimum_length(self):
        # RFC 7636 requires verifier to be 43–128 chars
        verifier, _ = generate_pkce()
        assert len(verifier) >= 43
