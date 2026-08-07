"""Prompt-cache waste detection.

Every turn resends the whole conversation, and prompt caching is what keeps a
long session's cost flat rather than quadratic. When the cache misses the whole
prompt is re-billed at full price and nothing in the transcript says so — the
turn looks normal, the bill is just bigger.
"""

from __future__ import annotations

from tau.message.types import AssistantMessage, Usage, UsageCost
from tau.session.cache_stats import (
    CACHE_TTL_SECONDS,
    NOISE_FLOOR_TOKENS,
    compute_cache_waste,
    scan_cache_misses,
)
from tau.session.types import (
    BranchSummaryEntry,
    CompactionEntry,
    MessageEntry,
    ModelChangeEntry,
)

INPUT_RATE = 3.0 / 1_000_000  # $3 per million
READ_RATE = 0.30 / 1_000_000  # $0.30 per million — a tenth, as Anthropic prices it


def _turn(
    timestamp: float,
    input_tokens: int,
    cache_read: int = 0,
    cache_write: int = 0,
    *,
    folded: bool = False,
) -> MessageEntry:
    msg = AssistantMessage.from_text("ok")
    msg.timestamp = timestamp
    msg.usage = Usage(
        input_tokens=input_tokens,
        output_tokens=50,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        input_tokens_include_cache_read=folded,
        cost=UsageCost(
            input=input_tokens * INPUT_RATE,
            cache_read=cache_read * READ_RATE,
            total=input_tokens * INPUT_RATE + cache_read * READ_RATE,
        ),
    )
    return MessageEntry(message=msg)


def _model(provider: str = "anthropic", model_id: str = "claude-sonnet-4-5") -> ModelChangeEntry:
    return ModelChangeEntry(provider_id=provider, model_id=model_id)


class TestDetection:
    def test_a_warm_cache_is_not_a_miss(self):
        waste = compute_cache_waste([_model(), _turn(0, 2_000), _turn(60, 200, cache_read=20_000)])

        assert waste.miss_count == 0

    def test_an_idle_gap_past_the_ttl_is_counted(self):
        """The cache expired while nobody was typing, so the next turn re-paid
        for the whole prompt."""
        branch = [
            _model(),
            _turn(0, 2_000),
            _turn(60, 200, cache_read=22_000),
            _turn(60 + CACHE_TTL_SECONDS * 2, 24_000),
        ]

        waste, misses = scan_cache_misses(branch)

        assert waste.miss_count == 1
        assert waste.expired_count == 1
        assert waste.missed_tokens == 22_200
        assert waste.missed_cost > 0
        assert next(iter(misses.values())).likely_expired is True

    def test_a_model_switch_is_counted_and_labelled(self):
        """A different model has a different cache, so the first turn after a
        switch re-bills everything — separately from an idle gap."""
        branch = [
            _model(),
            _turn(0, 500, cache_read=20_000),
            _model("openai", "gpt-5"),
            _turn(30, 20_000),
        ]

        waste, misses = scan_cache_misses(branch)

        assert waste.miss_count == 1
        assert waste.model_change_count == 1
        assert waste.expired_count == 0  # only 30s passed
        assert next(iter(misses.values())).model_changed is True

    def test_the_first_turn_is_never_a_miss(self):
        assert compute_cache_waste([_model(), _turn(0, 50_000)]).miss_count == 0

    def test_small_prefix_movement_is_below_the_noise_floor(self):
        """A breakpoint shifting by a message is not a re-bill worth reporting."""
        branch = [
            _model(),
            _turn(0, 100, cache_read=10_000),
            _turn(30, NOISE_FLOOR_TOKENS - 100, cache_read=9_000),
        ]

        assert compute_cache_waste(branch).miss_count == 0


class TestProvidersWithoutCaching:
    def test_a_provider_that_never_reports_caching_is_not_accused(self):
        """No cache numbers at all means unknown, not missed — counting it
        would report waste on every turn of every non-caching provider."""
        branch = [_model("ollama", "llama"), _turn(0, 20_000), _turn(30, 21_000)]

        assert compute_cache_waste(branch).miss_count == 0

    def test_a_read_only_provider_reporting_zero_is_a_real_miss(self):
        """Once a turn has shown the provider does report reads, a later zero
        is a genuine total miss rather than absent instrumentation."""
        branch = [
            _model("openai", "gpt-5"),
            _turn(0, 500, cache_read=20_000),
            _turn(30, 21_000),
        ]

        assert compute_cache_waste(branch).miss_count == 1


class TestContextChanges:
    def test_compaction_resets_the_comparison(self):
        """The prompt after a compaction is new content, not a re-bill."""
        branch = [
            _model(),
            _turn(0, 500, cache_read=90_000),
            CompactionEntry(summary="s", first_kept_entry_id="k", tokens_before=90_000),
            _turn(30, 12_000),
        ]

        assert compute_cache_waste(branch).miss_count == 0

    def test_branch_summary_resets_the_comparison(self):
        branch = [
            _model(),
            _turn(0, 500, cache_read=40_000),
            BranchSummaryEntry(from_id="x", summary="s"),
            _turn(30, 9_000),
        ]

        assert compute_cache_waste(branch).miss_count == 0


class TestCosting:
    def test_the_extra_cost_is_the_gap_between_paid_and_read_rates(self):
        """Missed tokens were charged at the input rate; they should have been
        charged at the read rate. Only the difference was wasted."""
        branch = [_model(), _turn(0, 500, cache_read=20_000), _turn(30, 20_000)]

        waste = compute_cache_waste(branch, lambda _p, _m: READ_RATE)

        assert waste.missed_tokens == 20_000
        assert waste.missed_cost == 20_000 * (INPUT_RATE - READ_RATE)

    def test_without_a_price_lookup_a_total_miss_is_costed_conservatively(self):
        """A turn with no cache read of its own carries no read rate to
        subtract, so the whole input charge is reported until a catalog says
        otherwise."""
        branch = [_model(), _turn(0, 500, cache_read=20_000), _turn(30, 20_000)]

        assert compute_cache_waste(branch).missed_cost == 20_000 * INPUT_RATE

    def test_the_catalog_supplies_the_rate_when_the_turn_cannot(self):
        """A real catalogued model, so the read rate resolves and only the
        difference between the two rates is reported as waste."""
        from tau.session.cache_stats import registry_price_lookup

        branch = [
            _model("anthropic", "claude-sonnet-5"),
            _turn(0, 500, cache_read=20_000),
            _turn(30, 20_000),
        ]

        priced = compute_cache_waste(branch, registry_price_lookup())

        assert 0 < priced.missed_cost < 20_000 * INPUT_RATE

    def test_folded_cache_tokens_are_not_double_counted(self):
        """OpenAI and Gemini report cache reads inside input_tokens; treating
        them as additional would inflate the prompt size and the miss."""
        folded = compute_cache_waste(
            [
                _model("openai", "gpt-5"),
                _turn(0, 20_000, cache_read=19_000, folded=True),
                _turn(30, 20_000, folded=True),
            ]
        )

        assert folded.missed_tokens == 20_000  # the prompt, not 39,000

    def test_a_priced_turn_with_no_rates_reports_tokens_but_no_cost(self):
        msg = AssistantMessage.from_text("ok")
        msg.timestamp = 30
        msg.usage = Usage(input_tokens=20_000)  # no cost breakdown
        branch = [_model(), _turn(0, 500, cache_read=20_000), MessageEntry(message=msg)]

        waste = compute_cache_waste(branch)

        assert waste.missed_tokens == 20_000
        assert waste.missed_cost == 0.0


class TestSurfaces:
    def test_session_stats_carries_the_waste(self):
        from tau.session.stats import compute_session_stats

        branch = [_model(), _turn(0, 500, cache_read=20_000), _turn(30, 20_000)]

        stats = compute_session_stats(branch)

        assert stats.cache_waste.miss_count == 1
        assert stats.to_dict()["cacheWaste"]["missedTokens"] == 20_000

    def test_the_session_panel_reports_it(self):
        import inspect

        from tau.modes.interactive.commands import session as panel

        assert "cache_waste" in inspect.getsource(panel.cmd_session)
