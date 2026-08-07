"""Session counts and spend, computed once for `/session` and RPC alike.

Two things were wrong before this module existed. Compaction and branch-summary
entries carried no usage at all, so the model calls they make — which are billed
like any other — were absent from every cost figure and unrecoverable after the
fact. And the `/session` panel and RPC's `get_session_stats` each walked the
branch themselves, so the panel reported tokens and spend while RPC reported
message counts only.
"""

from __future__ import annotations

from tau.message.types import (
    AssistantMessage,
    ToolMessage,
    ToolResultContent,
    Usage,
    UsageCost,
    UserMessage,
)
from tau.session.stats import compute_session_stats
from tau.session.types import BranchSummaryEntry, CompactionEntry, MessageEntry


def _assistant(text: str = "hi", **usage) -> AssistantMessage:
    msg = AssistantMessage.from_text(text)
    if usage:
        cost = UsageCost(**usage.pop("cost")) if "cost" in usage else UsageCost()
        msg.usage = Usage(cost=cost, **usage)
    return msg


def _tool_results(n: int) -> ToolMessage:
    return ToolMessage.from_results(
        [ToolResultContent(id=f"c{i}", content="ok", tool_name="read") for i in range(n)]
    )


class TestCounts:
    def test_messages_tools_and_summaries_are_counted(self):
        stats = compute_session_stats(
            [
                MessageEntry(message=UserMessage.from_text("q")),
                MessageEntry(message=_assistant()),
                MessageEntry(message=_tool_results(3)),
                CompactionEntry(summary="s", first_kept_entry_id="k", tokens_before=1),
                BranchSummaryEntry(from_id="x", summary="b"),
            ]
        )

        assert stats.user_messages == 1
        assert stats.assistant_messages == 1
        assert stats.tool_results == 3
        assert stats.summaries == 2

    def test_tool_results_collapse_into_one_logical_message(self):
        """The transcript renders a batch as a single block, so the total
        counts it once however many results it holds."""
        stats = compute_session_stats(
            [
                MessageEntry(message=UserMessage.from_text("q")),
                MessageEntry(message=_tool_results(5)),
            ]
        )

        assert stats.tool_results == 5
        assert stats.total_messages == 2

    def test_non_message_entries_are_ignored(self):
        from tau.session.types import LabelEntry

        stats = compute_session_stats([LabelEntry(label="x", target_id="e1")])

        assert stats.total_messages == 0


class TestSpend:
    def test_compaction_cost_lands_in_the_total(self):
        """Compacting calls the model. Nothing else in the history records what
        that cost, so omitting it lost the spend permanently."""
        stats = compute_session_stats(
            [
                MessageEntry(
                    message=_assistant(input_tokens=1_000, cost={"input": 3.0, "total": 3.0})
                ),
                CompactionEntry(
                    summary="s",
                    first_kept_entry_id="k",
                    tokens_before=1,
                    usage=Usage(input_tokens=5_000, cost=UsageCost(input=15.0, total=15.0)),
                ),
            ]
        )

        assert stats.usage.input_tokens == 6_000
        assert stats.total_cost == 18.0

    def test_branch_summary_cost_lands_in_the_total(self):
        stats = compute_session_stats(
            [
                BranchSummaryEntry(
                    from_id="x",
                    summary="b",
                    usage=Usage(input_tokens=2_000, cost=UsageCost(input=6.0, total=6.0)),
                )
            ]
        )

        assert stats.total_cost == 6.0

    def test_an_entry_without_usage_contributes_nothing(self):
        """Entries written before usage was tracked, and extension-supplied
        summaries, which cost nothing."""
        stats = compute_session_stats(
            [
                CompactionEntry(summary="s", first_kept_entry_id="k", tokens_before=1),
                BranchSummaryEntry(from_id="x", summary="b", from_hook=True),
            ]
        )

        assert stats.total_cost == 0.0
        assert stats.total_tokens == 0

    def test_cache_tokens_are_only_added_when_reported_separately(self):
        """Anthropic reports cache tokens apart from input_tokens; OpenAI and
        Gemini fold them in. Summing both would count the same tokens twice."""
        folded = compute_session_stats(
            [
                MessageEntry(
                    message=_assistant(
                        input_tokens=1_000,
                        cache_read_tokens=800,
                        input_tokens_include_cache_read=True,
                    )
                )
            ]
        )
        separate = compute_session_stats(
            [
                MessageEntry(
                    message=_assistant(
                        input_tokens=1_000,
                        cache_read_tokens=800,
                        input_tokens_include_cache_read=False,
                    )
                )
            ]
        )

        assert folded.total_tokens == 1_000
        assert separate.total_tokens == 1_800


class TestBothSurfacesAgree:
    """The panel and the protocol read the same numbers from the same code."""

    @staticmethod
    def _branch() -> list:
        return [
            MessageEntry(message=UserMessage.from_text("q")),
            MessageEntry(
                message=_assistant(input_tokens=1_000, output_tokens=100, cost={"total": 4.5})
            ),
            CompactionEntry(
                summary="s",
                first_kept_entry_id="k",
                tokens_before=9,
                usage=Usage(input_tokens=5_000, cost=UsageCost(total=15.0)),
            ),
        ]

    def test_the_wire_shape_matches_the_computed_stats(self):
        stats = compute_session_stats(self._branch())
        payload = stats.to_dict()

        assert payload["totalMessages"] == stats.total_messages
        assert payload["usage"]["totalTokens"] == stats.total_tokens
        assert payload["usage"]["cost"]["total"] == stats.total_cost == 19.5
        assert payload["summaries"] == 1

    def test_rpc_reports_tokens_and_spend_not_just_counts(self):
        """RPC used to return message counts only, so a client had to pull
        every entry and re-derive the rest — cache accounting included."""
        payload = compute_session_stats(self._branch()).to_dict()

        for key in ("toolCalls", "toolResults", "summaries", "usage"):
            assert key in payload
        assert payload["usage"]["cost"]["total"] > 0

    def test_the_session_panel_uses_the_shared_function(self):
        import inspect

        from tau.modes.interactive.commands import session as panel

        assert "compute_session_stats" in inspect.getsource(panel.cmd_session)

    def test_the_rpc_handler_uses_the_shared_function(self):
        import inspect

        import tau.modes.rpc.mode as rpc_mode

        source = inspect.getsource(rpc_mode._handle_command)

        assert "compute_session_stats" in source
