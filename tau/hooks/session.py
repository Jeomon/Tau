from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from tau.session.types import SessionEntry


class SessionStartReason(StrEnum):
    """Why a session start event was fired."""

    Startup = "startup"
    Reload = "reload"
    New = "new"
    Resume = "resume"
    Fork = "fork"
    Clone = "clone"


class SessionShutdownReason(StrEnum):
    """Why a session shutdown event was fired."""

    Quit = "quit"
    Reload = "reload"
    New = "new"
    Resume = "resume"
    Fork = "fork"
    Clone = "clone"


class SessionBeforeSwitchReason(StrEnum):
    """Why the session is about to be switched."""

    New = "new"
    Resume = "resume"


@dataclass
class SessionStartEvent:
    """Fired after a session has been fully loaded and is ready to accept turns."""

    type: Literal["session_start"] = field(default="session_start", init=False)
    reason: SessionStartReason = SessionStartReason.Startup
    previous_session_file: str | None = None


@dataclass
class SessionBeforeSwitchEvent:
    """Fired before the active session is replaced;
    handlers may cancel with SessionBeforeSwitchResult.
    """

    type: Literal["session_before_switch"] = field(default="session_before_switch", init=False)
    reason: SessionBeforeSwitchReason = SessionBeforeSwitchReason.New
    target_session_file: str | None = None


@dataclass
class SessionBeforeForkEvent:
    """Fired before a session tree branch is created;
    handlers may cancel with SessionBeforeForkResult.
    """

    type: Literal["session_before_fork"] = field(default="session_before_fork", init=False)
    entry_id: str = ""
    position: Literal["before", "at"] = "at"


@dataclass
class SessionShutdownEvent:
    """Fired just before the session is torn down; last chance for cleanup."""

    type: Literal["session_shutdown"] = field(default="session_shutdown", init=False)
    reason: SessionShutdownReason = SessionShutdownReason.Quit
    target_session_file: str | None = None


@dataclass
class TreePreparation:
    """Computed plan for a session-tree rewrite, passed inside SessionBeforeTreeEvent."""

    target_id: str
    old_leaf_id: str | None
    common_ancestor_id: str | None
    entries_to_summarize: list[SessionEntry] = field(default_factory=list)
    custom_instructions: str | None = None
    replace_instructions: bool = False
    label: str | None = None


@dataclass
class SessionBeforeTreeEvent:
    """Fired before the session tree is restructured; handlers may mutate the preparation."""

    type: Literal["session_before_tree"] = field(default="session_before_tree", init=False)
    preparation: TreePreparation = field(default_factory=lambda: TreePreparation("", None, None))


@dataclass
class SessionTreeEvent:
    """Fired after the session tree has been rewritten with the new leaf information."""

    type: Literal["session_tree"] = field(default="session_tree", init=False)
    new_leaf_id: str | None = None
    old_leaf_id: str | None = None
    from_extension: bool = False


@dataclass
class BranchSummaryStartEvent:
    """Fired when branch summary generation begins."""

    type: Literal["branch_summary_start"] = field(default="branch_summary_start", init=False)
    old_leaf_id: str | None = None
    target_id: str = ""
    from_extension: bool = False


@dataclass
class BranchSummaryEndEvent:
    """Fired after a branch summary is attached to the destination."""

    type: Literal["branch_summary_end"] = field(default="branch_summary_end", init=False)
    old_leaf_id: str | None = None
    target_id: str = ""
    summary_entry_id: str = ""
    summary_length: int = 0
    from_extension: bool = False


@dataclass
class BranchSummaryFailureEvent:
    """Fired when branch summarization fails and navigation continues."""

    type: Literal["branch_summary_failure"] = field(default="branch_summary_failure", init=False)
    old_leaf_id: str | None = None
    target_id: str = ""
    error: str = ""


@dataclass
class BranchSummaryCancelledEvent:
    """Fired when branch summarization or navigation is cancelled."""

    type: Literal["branch_summary_cancelled"] = field(
        default="branch_summary_cancelled", init=False
    )
    old_leaf_id: str | None = None
    target_id: str = ""
    reason: str = ""


# ── Result types ──────────────────────────────────────────────────────────────


@dataclass
class SessionBeforeSwitchResult:
    """Returned by session_before_switch handlers; cancel=True aborts the session switch."""

    cancel: bool = False


@dataclass
class SessionBeforeForkResult:
    """Returned by session_before_fork handlers; cancel=True aborts the fork."""

    cancel: bool = False


@dataclass
class SessionBeforeTreeResult:
    """Returned by session_before_tree handlers to mutate or cancel the planned tree rewrite."""

    cancel: bool = False
    custom_instructions: str | None = None
    replace_instructions: bool | None = None
    label: str | None = None
    summary: str | None = None
    summary_details: dict[str, Any] | None = None
