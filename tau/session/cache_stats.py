"""Prompt-cache waste: tokens that should have been cache reads but were re-billed.

Every turn resends the whole conversation. Providers that support prompt
caching bill the unchanged prefix at a fraction of the input rate — Anthropic
reads at a tenth — so on a long session the cache is what keeps the cost flat
instead of quadratic. When it misses, the entire prompt is charged again at
full price, and nothing in the transcript says so: the turn looks normal and
the bill is simply larger.

The two ordinary causes are both invisible at the time:

* **Idling past the TTL.** A cache entry expires (Anthropic's default is five
  minutes). Come back after lunch and the first turn pays for everything.
* **Switching model.** A different model has a different cache, so the first
  turn after a switch re-bills the whole prompt.

This module reconstructs both from what the session already records. It reports
what was lost, not what to do about it — the fix is a user's call.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

#: Anthropic's default prompt-cache TTL. An idle gap longer than this is the
#: likely explanation for a miss, so it is worth reporting alongside one.
CACHE_TTL_SECONDS = 5 * 60

#: Misses at or below this are the granularity of cache breakpoints rather than
#: a real re-bill — the prefix moved by a message, not by the conversation.
NOISE_FLOOR_TOKENS = 1024

#: Resolves a per-token cache-read price for a model, used only when a turn had
#: no cache read of its own to derive the rate from.
PriceLookup = Callable[[str, str], float]


@dataclass
class CacheMiss:
    """One turn that re-paid for a prefix it should have read from cache."""

    missed_tokens: int
    #: Extra dollars over what the same tokens would have cost as cache reads.
    missed_cost: float
    #: Gap since the previous request — past :data:`CACHE_TTL_SECONDS` the
    #: cache had expired, which is usually the whole explanation.
    idle_seconds: float
    model_changed: bool

    @property
    def likely_expired(self) -> bool:
        return self.idle_seconds > CACHE_TTL_SECONDS


@dataclass
class CacheWaste:
    """Totals across a branch."""

    missed_tokens: int = 0
    missed_cost: float = 0.0
    miss_count: int = 0
    expired_count: int = 0
    model_change_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "missedTokens": self.missed_tokens,
            "missedCost": self.missed_cost,
            "missCount": self.miss_count,
            "expiredCount": self.expired_count,
            "modelChangeCount": self.model_change_count,
        }


@dataclass
class _PreviousRequest:
    """The last request seen; everything in its prompt should still be cached."""

    prompt_tokens: int
    timestamp: float
    model_key: str
    #: Sticky. Distinguishes a total miss on a provider that reports reads only
    #: from one that never reports caching at all — on the latter a zero-cache
    #: turn means nothing and must not be counted.
    reported_cache: bool


def _prompt_and_paid_tokens(usage: Any) -> tuple[int, int]:
    """Split a usage into (whole prompt, tokens billed above the read rate).

    Providers disagree on whether ``input_tokens`` already contains the cache
    breakdown: Anthropic reports them separately, OpenAI and Gemini fold them
    in. ``input_tokens_include_cache_read`` says which, and the rest of the
    codebase treats it as covering writes as well as reads.
    """
    read = usage.cache_read_tokens
    write = usage.cache_write_tokens
    if usage.input_tokens_include_cache_read:
        return usage.input_tokens, max(0, usage.input_tokens - read)
    return usage.input_tokens + read + write, usage.input_tokens + write


def _detect_miss(
    prev: _PreviousRequest | None,
    message: Any,
    model_key: str,
    price_lookup: PriceLookup | None,
) -> CacheMiss | None:
    """What this turn re-paid relative to the previous request, if anything."""
    usage = message.usage
    prompt_tokens, paid_tokens = _prompt_and_paid_tokens(usage)
    cached = usage.cache_read_tokens + usage.cache_write_tokens

    if prev is None or prompt_tokens <= 0:
        return None
    # A turn with no cache activity only counts once some earlier turn showed
    # the provider does report it; otherwise there is nothing to compare to.
    if cached == 0 and not prev.reported_cache:
        return None

    # Only the prefix shared with the previous prompt could have been cached.
    missed = min(prev.prompt_tokens, prompt_tokens) - usage.cache_read_tokens
    if missed <= NOISE_FLOOR_TOKENS:
        return None

    # The missed tokens were billed at whatever this turn actually paid — input
    # rate, or the write premium — instead of the read rate. Both come from the
    # turn's own cost breakdown, so no rate table is needed for the common case.
    paid_per_token = (
        (usage.cost.input + usage.cost.cache_write) / paid_tokens if paid_tokens > 0 else 0.0
    )
    if usage.cache_read_tokens > 0:
        read_per_token = usage.cost.cache_read / usage.cache_read_tokens
    elif price_lookup is not None:
        provider, _, model_id = model_key.partition("/")
        read_per_token = price_lookup(provider, model_id)
    else:
        read_per_token = 0.0

    return CacheMiss(
        missed_tokens=missed,
        missed_cost=missed * max(0.0, paid_per_token - read_per_token),
        idle_seconds=max(0.0, message.timestamp - prev.timestamp),
        model_changed=model_key != prev.model_key,
    )


def scan_cache_misses(
    entries: list[Any],
    price_lookup: PriceLookup | None = None,
) -> tuple[CacheWaste, dict[str, CacheMiss]]:
    """Walk a branch, returning the totals and each miss keyed by message id."""
    from tau.message.types import AssistantMessage
    from tau.session.types import (
        BranchSummaryEntry,
        CompactionEntry,
        MessageEntry,
        ModelChangeEntry,
    )

    waste = CacheWaste()
    misses: dict[str, CacheMiss] = {}
    prev: _PreviousRequest | None = None
    model_key = ""

    for entry in entries:
        if isinstance(entry, ModelChangeEntry):
            model_key = f"{entry.provider_id}/{entry.model_id}"
            continue
        if isinstance(entry, (CompactionEntry, BranchSummaryEntry)):
            # The context legitimately changed, so the next prompt is new
            # content rather than a re-bill. A model switch is *not* exempt:
            # it really does re-pay for the whole prompt.
            prev = None
            continue
        if not isinstance(entry, MessageEntry):
            continue
        message = entry.message
        if not isinstance(message, AssistantMessage):
            continue

        miss = _detect_miss(prev, message, model_key, price_lookup)
        if miss is not None:
            waste.missed_tokens += miss.missed_tokens
            waste.missed_cost += miss.missed_cost
            waste.miss_count += 1
            waste.expired_count += 1 if miss.likely_expired else 0
            waste.model_change_count += 1 if miss.model_changed else 0
            misses[message.id] = miss

        prompt_tokens, _ = _prompt_and_paid_tokens(message.usage)
        if prompt_tokens > 0:
            cached = message.usage.cache_read_tokens + message.usage.cache_write_tokens
            prev = _PreviousRequest(
                prompt_tokens=prompt_tokens,
                timestamp=message.timestamp,
                model_key=model_key,
                reported_cache=(prev.reported_cache if prev else False) or cached > 0,
            )

    return waste, misses


def registry_price_lookup() -> PriceLookup:
    """Cache-read price per token, from the built-in model catalog.

    Needed for a *total* miss: with no cache read of its own, a turn carries no
    read rate to compare against, and assuming zero would bill the whole
    difference as waste when part of it was always going to be paid.
    """
    from tau.inference.model.registry import ModelRegistry

    registry = ModelRegistry.from_text_builtins()

    def _lookup(provider: str, model_id: str) -> float:
        model = registry.get(model_id, provider or None)
        cost = getattr(model, "cost", None)
        return (getattr(cost, "cache_read", 0.0) or 0.0) / 1_000_000

    return _lookup


def compute_cache_waste(
    entries: list[Any],
    price_lookup: PriceLookup | None = None,
) -> CacheWaste:
    """Cumulative prompt-cache waste across a branch.

    Without a ``price_lookup`` a total miss is costed against a read rate of
    zero, which over-reports; callers with access to the catalog should pass
    :func:`registry_price_lookup`.
    """
    return scan_cache_misses(entries, price_lookup)[0]
