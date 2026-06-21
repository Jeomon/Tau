"""Tests for tau/session/types.py — all entry types, SessionType enum."""
from __future__ import annotations

from tau.session.types import (
    SessionType,
    SessionHeader,
    MessageEntry,
    ThinkingLevelChangeEntry,
    ModelChangeEntry,
    LabelEntry,
    LeafEntry,
    CustomInfoEntry,
    CustomMessageEntry,
    CompactionEntry,
    BranchSummaryEntry,
    SessionInfoEntry,
    SessionOptions,
    SessionInfo,
)
from tau.inference.types import ThinkingLevel


class TestSessionTypeEnum:
    def test_session_header_value(self):
        assert SessionType.SESSION_HEADER == "session"

    def test_session_message_value(self):
        assert SessionType.SESSION_MESSAGE == "message"

    def test_thinking_level_change_value(self):
        assert SessionType.THINKING_LEVEL_CHANGE == "thinking_level_change"

    def test_model_change_value(self):
        assert SessionType.MODEL_CHANGE == "model_change"

    def test_label_value(self):
        assert SessionType.LABEL == "label"

    def test_custom_info_value(self):
        assert SessionType.CUSTOM_INFO == "custom"

    def test_compaction_value(self):
        assert SessionType.COMPACTION == "compaction"

    def test_branch_summary_value(self):
        assert SessionType.BRANCH_SUMMARY == "branch_summary"

    def test_leaf_value(self):
        assert SessionType.LEAF == "leaf"


class TestSessionHeader:
    def test_construction(self, tmp_path):
        header = SessionHeader(cwd=tmp_path)
        assert header.type == SessionType.SESSION_HEADER
        assert header.cwd == tmp_path

    def test_auto_generated_id(self, tmp_path):
        h1 = SessionHeader(cwd=tmp_path)
        h2 = SessionHeader(cwd=tmp_path)
        assert h1.id != h2.id

    def test_parent_session_default_none(self, tmp_path):
        header = SessionHeader(cwd=tmp_path)
        assert header.parent_session is None


class TestSessionInfoEntry:
    def test_type_field(self):
        e = SessionInfoEntry()
        assert e.type == SessionType.SESSION_INFO

    def test_name_default_none(self):
        e = SessionInfoEntry()
        assert e.name is None

    def test_name_set(self):
        e = SessionInfoEntry(name="My Session")
        assert e.name == "My Session"


class TestMessageEntry:
    def test_type_field(self):
        from tau.message.types import UserMessage
        entry = MessageEntry(message=UserMessage())
        assert entry.type == SessionType.SESSION_MESSAGE

    def test_meta_default_none(self):
        from tau.message.types import UserMessage
        entry = MessageEntry(message=UserMessage())
        assert entry.meta is None


class TestThinkingLevelChangeEntry:
    def test_type_field(self):
        e = ThinkingLevelChangeEntry(thinking_level=ThinkingLevel.Low)
        assert e.type == SessionType.THINKING_LEVEL_CHANGE

    def test_thinking_level_stored(self):
        e = ThinkingLevelChangeEntry(thinking_level=ThinkingLevel.High)
        assert e.thinking_level == ThinkingLevel.High


class TestModelChangeEntry:
    def test_type_field(self):
        e = ModelChangeEntry(model_id="claude-3", provider_id="anthropic")
        assert e.type == SessionType.MODEL_CHANGE

    def test_fields_stored(self):
        e = ModelChangeEntry(model_id="gpt-4", provider_id="openai")
        assert e.model_id == "gpt-4"
        assert e.provider_id == "openai"


class TestLabelEntry:
    def test_type_field(self):
        e = LabelEntry(target_id="abc123")
        assert e.type == SessionType.LABEL

    def test_label_default_none(self):
        e = LabelEntry(target_id="x")
        assert e.label is None

    def test_label_set(self):
        e = LabelEntry(target_id="x", label="checkpoint")
        assert e.label == "checkpoint"


class TestLeafEntry:
    def test_type_field(self):
        e = LeafEntry()
        assert e.type == SessionType.LEAF

    def test_target_id_default_none(self):
        e = LeafEntry()
        assert e.target_id is None


class TestCustomInfoEntry:
    def test_type_field(self):
        e = CustomInfoEntry(custom_type="mytype")
        assert e.type == SessionType.CUSTOM_INFO

    def test_custom_type_stored(self):
        e = CustomInfoEntry(custom_type="metric")
        assert e.custom_type == "metric"

    def test_data_default_none(self):
        e = CustomInfoEntry(custom_type="x")
        assert e.data is None

    def test_data_stored(self):
        e = CustomInfoEntry(custom_type="x", data={"val": 42})
        assert e.data == {"val": 42}


class TestCustomMessageEntry:
    def test_type_field(self):
        e = CustomMessageEntry(custom_type="banner", content=[])
        assert e.type == SessionType.CUSTOM_MESSAGE

    def test_display_default_true(self):
        e = CustomMessageEntry(custom_type="x", content=[])
        assert e.display is True

    def test_details_default_none(self):
        e = CustomMessageEntry(custom_type="x", content=[])
        assert e.details is None


class TestCompactionEntry:
    def test_type_field(self):
        e = CompactionEntry(summary="compact", first_kept_entry_id="abc", tokens_before=1000)
        assert e.type == SessionType.COMPACTION

    def test_fields_stored(self):
        e = CompactionEntry(summary="Compact summary", first_kept_entry_id="abc123", tokens_before=5000)
        assert e.summary == "Compact summary"
        assert e.first_kept_entry_id == "abc123"
        assert e.tokens_before == 5000

    def test_details_default_none(self):
        e = CompactionEntry(summary="s", first_kept_entry_id="x", tokens_before=0)
        assert e.details is None


class TestBranchSummaryEntry:
    def test_type_field(self):
        e = BranchSummaryEntry(from_id="abc", summary="Branch merged")
        assert e.type == SessionType.BRANCH_SUMMARY

    def test_fields_stored(self):
        e = BranchSummaryEntry(from_id="parent_id", summary="Summary text")
        assert e.from_id == "parent_id"
        assert e.summary == "Summary text"

    def test_defaults(self):
        e = BranchSummaryEntry(from_id="x", summary="s")
        assert e.details is None
        assert e.from_hook is False
        assert e.label is None

    def test_from_hook_flag(self):
        e = BranchSummaryEntry(from_id="x", summary="s", from_hook=True)
        assert e.from_hook is True

    def test_label_set(self):
        e = BranchSummaryEntry(from_id="x", summary="s", label="v1.0")
        assert e.label == "v1.0"


class TestSessionOptions:
    def test_defaults(self):
        opts = SessionOptions()
        assert opts.id is None
        assert opts.parent_session is None

    def test_id_set(self):
        opts = SessionOptions(id="abc")
        assert opts.id == "abc"
