"""Shared SSL context for httpx clients.

``httpx.Client``/``httpx.AsyncClient`` each build their own default SSL
context on init (parsing the CA bundle off disk) unless one is passed via
``verify=``. Tau creates many short-lived httpx clients — one per inference
call, plus several at startup for local-model discovery (Ollama, LM Studio,
vLLM, llama.cpp) that often run concurrently — so without sharing, the same
CA bundle gets parsed from disk over and over on every single client
construction. That disk read is measurably slower on some platforms (e.g.
when real-time antivirus scanning intercepts every file open), making
redundant parsing a real, avoidable cost. Building the context once per
process and reusing it for every client removes that duplication.
"""

from __future__ import annotations

import ssl
from functools import lru_cache

import httpx


@lru_cache(maxsize=1)
def get_shared_ssl_context() -> ssl.SSLContext:
    """Return a process-wide SSL context, built once and reused by every httpx client."""
    return httpx.create_ssl_context()
