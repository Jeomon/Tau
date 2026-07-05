"""Layout: split one Rect into many.

Unlike ``component.py``'s ``Columns``/``Rows`` (which compute sizes *and*
render children in the same method), ``Layout.split(area)`` only computes
``Rect``s — rendering is a separate step, so the same split can be reused,
tested, or handed to widgets that have no idea a Layout exists.

Sizing is solved with ``kiwisolver`` — the actual Cassowary constraint
algorithm (the same one matplotlib uses), not a hand-rolled approximation.
Min/Max bounds are ``REQUIRED`` (never violated); Length/Percentage/Ratio
are ``STRONG`` preferences; the "sizes sum to the available space" rule is
``MEDIUM`` (yields if the strong preferences don't fit); Fill/Min/Max
segments splitting leftover space proportionally is ``WEAK`` (lowest
priority — this is a defensible priority scheme, not a claim of matching
the solver's undocumented internal strengths exactly). Leftover-space
*placement* (``Flex``) stays a separate closed-form step below — it's a
simple positional rule per variant, not something a constraint solver adds
value to.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto

import kiwisolver as kiwi

from tau.tui.geometry import Rect


class Alignment(Enum):
    LEFT = auto()
    CENTER = auto()
    RIGHT = auto()


class Direction(Enum):
    HORIZONTAL = auto()
    VERTICAL = auto()


class Flex(Enum):
    """What happens to leftover space once every constraint is satisfied."""

    LEGACY = auto()  # all leftover space goes to the last segment
    START = auto()  # segments packed at the start; leftover trails at the end
    END = auto()  # segments packed at the end; leftover leads at the start
    CENTER = auto()  # segments centered; leftover split evenly on both sides
    SPACE_BETWEEN = auto()  # leftover distributed as gaps between segments only
    SPACE_AROUND = auto()  # leftover distributed as half-gaps around each segment
    SPACE_EVENLY = auto()  # leftover distributed as equal gaps, including the edges


class _Kind(Enum):
    LENGTH = auto()
    PERCENTAGE = auto()
    RATIO = auto()
    MIN = auto()
    MAX = auto()
    FILL = auto()


def _round_floats(values: list[float]) -> list[int]:
    """Round floats to ints via largest-remainder, preserving *their own* total.

    Deliberately does not force the result to sum to any externally chosen
    target — when constraints conflict (e.g. two Length(5)s in 40 columns,
    with nothing to absorb the other 30), the solver correctly leaves the
    total short of ``usable`` rather than stretching a STRONG constraint to
    fill it; ``Layout.split``'s existing leftover/``Flex`` step is what
    decides where that legitimate shortfall goes, not this rounding step.
    """
    target = round(sum(values))
    floors = [int(v) for v in values]
    remainder = target - sum(floors)
    if remainder <= 0:
        return floors
    order = sorted(range(len(values)), key=lambda i: values[i] - floors[i], reverse=True)
    for i in order[:remainder]:
        floors[i] += 1
    return floors


@dataclass(frozen=True, slots=True)
class Constraint:
    """A sizing rule for one segment of a Layout split.

    Construct via the classmethods (``Constraint.length(10)``,
    ``Constraint.fill(1)``, ...) rather than the constructor directly.
    """

    kind: _Kind
    a: float = 0
    b: float = 1

    @staticmethod
    def length(n: int) -> Constraint:
        return Constraint(_Kind.LENGTH, n)

    @staticmethod
    def percentage(pct: float) -> Constraint:
        return Constraint(_Kind.PERCENTAGE, pct)

    @staticmethod
    def ratio(numerator: int, denominator: int) -> Constraint:
        return Constraint(_Kind.RATIO, numerator, denominator)

    @staticmethod
    def min(n: int) -> Constraint:
        return Constraint(_Kind.MIN, n)

    @staticmethod
    def max(n: int) -> Constraint:
        return Constraint(_Kind.MAX, n)

    @staticmethod
    def fill(weight: int = 1) -> Constraint:
        return Constraint(_Kind.FILL, weight)


@dataclass(slots=True)
class Layout:
    direction: Direction = Direction.VERTICAL
    constraints: list[Constraint] = field(default_factory=list)
    margin_horizontal: int = 0
    margin_vertical: int = 0
    spacing: int = 0
    flex: Flex = Flex.LEGACY

    @staticmethod
    def horizontal(constraints: list[Constraint], **kwargs: object) -> Layout:
        return Layout(Direction.HORIZONTAL, constraints, **kwargs)  # type: ignore[arg-type]

    @staticmethod
    def vertical(constraints: list[Constraint], **kwargs: object) -> Layout:
        return Layout(Direction.VERTICAL, constraints, **kwargs)  # type: ignore[arg-type]

    def margin(self, n: int) -> Layout:
        self.margin_horizontal = self.margin_vertical = n
        return self

    def split(self, area: Rect) -> list[Rect]:
        working = area.inner(horizontal=self.margin_horizontal, vertical=self.margin_vertical)
        n = len(self.constraints)
        if n == 0:
            return []

        total = working.width if self.direction is Direction.HORIZONTAL else working.height
        usable = max(0, total - self.spacing * (n - 1))

        sizes = self._resolve_sizes(usable)
        remaining = usable - sum(sizes)
        if remaining > 0 and self.flex is Flex.LEGACY:
            sizes[-1] += remaining
            remaining = 0
        gaps = self._resolve_gaps(remaining, n)

        rects: list[Rect] = []
        pos = 0
        for i, size in enumerate(sizes):
            pos += gaps[i]
            if self.direction is Direction.HORIZONTAL:
                rects.append(Rect(working.x + pos, working.y, size, working.height))
            else:
                rects.append(Rect(working.x, working.y + pos, working.width, size))
            pos += size + (self.spacing if i < n - 1 else 0)
        return rects

    # -- sizing (real Cassowary solve via kiwisolver) -----------------------

    def _resolve_sizes(self, usable: int) -> list[int]:
        n = len(self.constraints)
        if n == 0:
            return []

        solver = kiwi.Solver()
        size_vars = [kiwi.Variable(f"size{i}") for i in range(n)]
        for v in size_vars:
            solver.addConstraint(v >= 0)  # REQUIRED by default

        fill_vars: list[tuple[kiwi.Variable, float]] = []
        for i, c in enumerate(self.constraints):
            v = size_vars[i]
            if c.kind is _Kind.LENGTH:
                solver.addConstraint((v == c.a) | kiwi.strength.strong)
            elif c.kind is _Kind.PERCENTAGE:
                solver.addConstraint((v == usable * c.a / 100) | kiwi.strength.strong)
            elif c.kind is _Kind.RATIO:
                solver.addConstraint((v == usable * c.a / c.b) | kiwi.strength.strong)
            elif c.kind is _Kind.MIN:
                solver.addConstraint(v >= c.a)  # REQUIRED lower bound
                fill_vars.append((v, 1.0))
            elif c.kind is _Kind.MAX:
                solver.addConstraint(v <= c.a)  # REQUIRED upper bound
                fill_vars.append((v, 1.0))
            else:  # FILL
                fill_vars.append((v, max(1.0, float(c.a))))

        # Fill/Min/Max segments share leftover space proportionally to weight
        # (lowest priority — only kicks in once strong/required sizes are set).
        if fill_vars:
            base_var, base_w = fill_vars[0]
            for v, w in fill_vars[1:]:
                solver.addConstraint((v * base_w == base_var * w) | kiwi.strength.weak)

        total: kiwi.Expression = size_vars[0] + 0
        for v in size_vars[1:]:
            total = total + v
        solver.addConstraint((total == usable) | kiwi.strength.medium)

        solver.updateVariables()
        raw = [max(0.0, v.value()) for v in size_vars]
        return _round_floats(raw)

    # -- leftover-space distribution (Flex) --------------------------------

    def _resolve_gaps(self, leftover: int, n: int) -> list[int]:
        leftover = max(0, leftover)
        gaps = [0] * n
        if self.flex is Flex.LEGACY or self.flex is Flex.START:
            return gaps
        if self.flex is Flex.END:
            gaps[0] = leftover
            return gaps
        if self.flex is Flex.CENTER:
            gaps[0] = leftover // 2
            return gaps
        if self.flex is Flex.SPACE_BETWEEN:
            if n > 1:
                share, rem = divmod(leftover, n - 1)
                for i in range(1, n):
                    gaps[i] = share + (1 if i <= rem else 0)
            return gaps
        if self.flex is Flex.SPACE_AROUND:
            share, rem = divmod(leftover, n)
            for i in range(n):
                gaps[i] = share // 2 if i > 0 else 0
            gaps[0] = share // 2 + rem // 2
            return gaps
        # SPACE_EVENLY
        share, rem = divmod(leftover, n + 1)
        for i in range(n):
            gaps[i] = share + (1 if i < rem else 0)
        return gaps
