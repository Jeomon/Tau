from __future__ import annotations

import shutil
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, NotRequired, TypedDict

# ── Server configuration types ────────────────────────────────────────────────

RootFinder = Callable[[str], Coroutine[Any, Any, str | None]]


@dataclass
class ServerDefinition:
    id: str
    extensions: list[str]
    command: list[str]
    root_finder: RootFinder
    initialization: dict[str, Any] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True

    def is_available(self) -> bool:
        return shutil.which(self.command[0]) is not None


# ── LSP protocol response types ───────────────────────────────────────────────


class Position(TypedDict):
    line: int
    character: int


class Range(TypedDict):
    start: Position
    end: Position


class Location(TypedDict):
    uri: str
    range: Range


class DiagnosticSeverity(IntEnum):
    Error = 1
    Warning = 2
    Information = 3
    Hint = 4


class Diagnostic(TypedDict):
    range: Range
    message: str
    severity: NotRequired[int]  # DiagnosticSeverity
    source: NotRequired[str]  # "pyright", "ruff", etc.
    code: NotRequired[str | int]


class SymbolKind(IntEnum):
    File = 1
    Module = 2
    Namespace = 3
    Package = 4
    Class = 5
    Method = 6
    Property = 7
    Field = 8
    Constructor = 9
    Enum = 10
    Interface = 11
    Function = 12
    Variable = 13
    Constant = 14
    String = 15
    Number = 16
    Boolean = 17
    Array = 18
    Object = 19
    Key = 20
    Null = 21
    EnumMember = 22
    Struct = 23
    Event = 24
    Operator = 25
    TypeParameter = 26


class SymbolInformation(TypedDict):
    name: str
    kind: int  # SymbolKind
    location: Location
    containerName: NotRequired[str]


class DocumentSymbol(TypedDict):
    name: str
    kind: int  # SymbolKind
    range: Range
    selectionRange: Range
    detail: NotRequired[str]
    children: NotRequired[list[DocumentSymbol]]


class TextEdit(TypedDict):
    range: Range
    newText: str


class WorkspaceEdit(TypedDict):
    changes: NotRequired[dict[str, list[TextEdit]]]  # uri → edits
    documentChanges: NotRequired[list[dict[str, Any]]]  # versioned edits


class MarkupContent(TypedDict):
    kind: str  # "plaintext" | "markdown"
    value: str


class Hover(TypedDict):
    contents: MarkupContent | str
    range: NotRequired[Range]


class ParameterInformation(TypedDict):
    label: str
    documentation: NotRequired[str | MarkupContent]


class SignatureInformation(TypedDict):
    label: str
    parameters: NotRequired[list[ParameterInformation]]
    documentation: NotRequired[str | MarkupContent]


class SignatureHelp(TypedDict):
    signatures: list[SignatureInformation]
    activeSignature: NotRequired[int]
    activeParameter: NotRequired[int]


class CodeAction(TypedDict):
    title: str
    kind: NotRequired[str]  # "quickfix" | "refactor" | "refactor.extract" | …
    edit: NotRequired[WorkspaceEdit]
    command: NotRequired[dict[str, Any]]
    isPreferred: NotRequired[bool]


class CallHierarchyItem(TypedDict):
    name: str
    kind: int  # SymbolKind
    uri: str
    range: Range
    selectionRange: Range
    detail: NotRequired[str]


class IncomingCall(TypedDict):
    from_: CallHierarchyItem
    fromRanges: list[Range]


class OutgoingCall(TypedDict):
    to: CallHierarchyItem
    fromRanges: list[Range]


class TypeHierarchyItem(TypedDict):
    name: str
    kind: int  # SymbolKind
    uri: str
    range: Range
    selectionRange: Range
    detail: NotRequired[str]


class InlayHintKind(IntEnum):
    Type = 1
    Parameter = 2


class InlayHint(TypedDict):
    position: Position
    label: str | list[dict[str, Any]]  # string or InlayHintLabelPart[]
    kind: NotRequired[int]  # InlayHintKind
    tooltip: NotRequired[str]


class Command(TypedDict):
    title: str
    command: str
    arguments: NotRequired[list[Any]]


class CodeLens(TypedDict):
    range: Range
    command: NotRequired[Command]
