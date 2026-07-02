from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SectionData:
    session_goal: list[str] = field(default_factory=list)
    outstanding_context: list[str] = field(default_factory=list)
    files_and_changes: list[str] = field(default_factory=list)
    commits: list[str] = field(default_factory=list)
    user_preferences: list[str] = field(default_factory=list)
    brief_transcript: str = ""
