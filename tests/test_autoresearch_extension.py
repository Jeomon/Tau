"""Tests for the autoresearch extension — log, confidence, tools, dashboard."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from tau.tool.types import ToolInvocation


def _load_extension():
    """Import the extension exactly as tau's loader does — as a package.

    Sibling modules are relative imports inside that package, so they must be
    reached through it rather than by bare name; importing them bare is what
    lands a generic name like ``state`` in the global namespace, where it
    collides with the other extensions that ship one.
    """
    root = Path(__file__).parent.parent / ".tau" / "extensions" / "autoresearch"
    name = f"_tau_ext_{hashlib.sha1(str(root.resolve()).encode()).hexdigest()[:16]}"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, root / "__init__.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return name


_PKG = _load_extension()
_state = importlib.import_module(f"{_PKG}.state")
_dashboard = importlib.import_module(f"{_PKG}.dashboard")
_tools = importlib.import_module(f"{_PKG}.tools")

MetricDef = _state.MetricDef
Result = _state.Result
State = _state.State
append_config = _state.append_config
append_result = _state.append_result
compute_confidence = _state.compute_confidence
format_num = _state.format_num
is_better = _state.is_better
load = _state.load
log_path = _state.log_path
parse_metrics = _state.parse_metrics

summary_lines = _dashboard.summary_lines
table_lines = _dashboard.table_lines
widget_lines = _dashboard.widget_lines
DashboardOverlay = _dashboard.DashboardOverlay

InitExperimentTool = _tools.InitExperimentTool
LogExperimentTool = _tools.LogExperimentTool
RunExperimentTool = _tools.RunExperimentTool


# ── METRIC parsing ───────────────────────────────────────────────────────────


class TestParseMetrics:
    def test_reads_metric_lines_anywhere_in_the_output(self):
        out = "building…\nMETRIC seconds=12.5\nnoise\nMETRIC compile_ms=420\ndone"
        assert parse_metrics(out) == {"seconds": 12.5, "compile_ms": 420.0}

    def test_ignores_malformed_and_unparsable_values(self):
        assert parse_metrics("METRIC noequals\nMETRIC bad=abc\nMETRIC =5") == {}

    def test_rejects_names_that_would_clobber_dict_internals(self):
        assert parse_metrics("METRIC __proto__=1\nMETRIC ok=2") == {"ok": 2.0}

    def test_no_metrics_is_empty_not_an_error(self):
        assert parse_metrics("just some output") == {}


# ── Numbers ──────────────────────────────────────────────────────────────────


class TestFormatNum:
    @pytest.mark.parametrize(
        ("value", "unit", "expected"),
        [
            (1234.5, "ms", "1,234ms"),
            (93.25, "s", "93.25s"),
            (0.04213, "", "0.0421"),
            (5, "", "5"),
        ],
    )
    def test_scales_precision_to_magnitude(self, value, unit, expected):
        assert format_num(value, unit) == expected


class TestDirection:
    def test_lower_is_better(self):
        assert is_better(1.0, 2.0, "lower")
        assert not is_better(3.0, 2.0, "lower")

    def test_higher_is_better(self):
        assert is_better(3.0, 2.0, "higher")


# ── Confidence ───────────────────────────────────────────────────────────────


def _results(values, statuses=None):
    statuses = statuses or ["keep"] * len(values)
    return [
        Result(commit=f"c{i}", metric=v, status=s, description=f"run {i}")
        for i, (v, s) in enumerate(zip(values, statuses, strict=True))
    ]


class TestConfidence:
    def test_needs_at_least_three_runs(self):
        assert compute_confidence(_results([10.0, 9.0]), "lower") is None

    def test_identical_values_have_no_noise_floor(self):
        # MAD of 0 — a ratio against it would be meaningless, not infinite.
        assert compute_confidence(_results([10.0, 10.0, 10.0]), "lower") is None

    def test_a_large_win_against_small_noise_scores_high(self):
        # baseline 10, best 5, deviations tiny → well above the noise floor
        conf = compute_confidence(_results([10.0, 9.9, 10.1, 5.0]), "lower")
        assert conf is not None and conf > 2.0

    def test_a_win_inside_the_jitter_scores_low(self):
        # Wild discarded runs set a noise floor of ~3.5; the best *kept* run is
        # only 0.5 better than baseline, so the win is well inside it.
        conf = compute_confidence(
            _results([10.0, 3.0, 17.0, 9.5], ["keep", "discard", "discard", "keep"]), "lower"
        )
        assert conf is not None and conf < 1.0

    def test_crashes_are_excluded_from_the_noise_estimate(self):
        # A 0-metric crash must not drag the median around.
        values = _results([10.0, 9.9, 10.1, 5.0], ["keep"] * 4)
        crash = Result(commit="x", metric=0.0, status="crash", description="boom")
        assert compute_confidence([*values, crash], "lower") == compute_confidence(values, "lower")

    def test_none_without_a_kept_run(self):
        assert compute_confidence(_results([10.0, 11.0, 12.0], ["discard"] * 3), "lower") is None


# ── Log round-trip ───────────────────────────────────────────────────────────


class TestLog:
    def test_config_and_results_round_trip(self, tmp_path):
        state = State(name="Speed", metric_name="seconds", metric_unit="s", direction="lower")
        append_config(tmp_path, state)
        append_result(
            tmp_path, Result(commit="abc1234", metric=10.0, status="keep", description="baseline")
        )
        append_result(
            tmp_path, Result(commit="def5678", metric=8.0, status="keep", description="cached")
        )

        loaded = load(tmp_path)

        assert loaded.name == "Speed"
        assert loaded.metric_name == "seconds"
        assert loaded.direction == "lower"
        assert [r.metric for r in loaded.results] == [10.0, 8.0]
        assert loaded.baseline().metric == 10.0
        assert loaded.best().metric == 8.0

    def test_a_second_config_opens_a_new_segment(self, tmp_path):
        state = State(name="First", metric_name="seconds")
        append_config(tmp_path, state)
        append_result(
            tmp_path, Result(commit="a", metric=10.0, status="keep", description="old", segment=0)
        )
        state.name = "Second"
        append_config(tmp_path, state)
        append_result(
            tmp_path, Result(commit="b", metric=99.0, status="keep", description="new", segment=1)
        )

        loaded = load(tmp_path)

        assert loaded.segment == 1
        assert loaded.name == "Second"
        # The old run is kept for reference but excluded from the live baseline.
        assert len(loaded.results) == 2
        assert [r.metric for r in loaded.current()] == [99.0]
        assert loaded.baseline().metric == 99.0

    def test_a_truncated_final_line_does_not_lose_the_session(self, tmp_path):
        append_config(tmp_path, State(name="Speed", metric_name="seconds"))
        append_result(tmp_path, Result(commit="a", metric=10.0, status="keep", description="ok"))
        with log_path(tmp_path).open("a", encoding="utf-8") as handle:
            handle.write('{"type": "result", "metric": 8.0, "sta')  # killed mid-write

        loaded = load(tmp_path)

        assert len(loaded.results) == 1
        assert loaded.name == "Speed"

    def test_missing_log_is_an_empty_session(self, tmp_path):
        loaded = load(tmp_path)
        assert loaded.results == []
        assert loaded.baseline() is None

    def test_max_experiments_comes_from_config_json(self, tmp_path):
        (tmp_path / ".auto").mkdir()
        (tmp_path / ".auto" / "config.json").write_text(json.dumps({"max_experiments": 25}))
        assert load(tmp_path).max_experiments == 25


# ── Tools ────────────────────────────────────────────────────────────────────


class _Session:
    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd
        self.state = State()
        self.refreshed = 0

    def refresh(self) -> None:
        self.refreshed += 1


def _invoke(tool, params, cwd):
    invocation = ToolInvocation(id="c1", name=tool.name, cwd=cwd, params=params)
    return asyncio.run(tool.execute(invocation))


class TestTools:
    def test_init_writes_a_config_header(self, tmp_path):
        session = _Session(tmp_path)
        result = _invoke(
            InitExperimentTool(session),
            {"name": "Speed", "metric_name": "seconds", "metric_unit": "s", "direction": "lower"},
            tmp_path,
        )

        assert not result.is_error
        assert session.state.metric_name == "seconds"
        assert load(tmp_path).name == "Speed"

    def test_re_init_starts_a_new_segment(self, tmp_path):
        session = _Session(tmp_path)
        tool = InitExperimentTool(session)
        _invoke(tool, {"name": "First", "metric_name": "seconds"}, tmp_path)
        session.state.results.append(
            Result(commit="a", metric=1.0, status="keep", description="x", segment=0)
        )
        _invoke(tool, {"name": "Second", "metric_name": "bytes"}, tmp_path)

        assert session.state.segment == 1

    def test_run_reports_exit_code_metrics_and_duration(self, tmp_path):
        session = _Session(tmp_path)
        result = _invoke(
            RunExperimentTool(session),
            {"command": "echo METRIC seconds=1.5"},
            tmp_path,
        )

        assert result.metadata["exit_code"] == 0
        assert result.metadata["metrics"] == {"seconds": 1.5}
        assert "seconds=1.5" in result.content

    def test_run_clears_the_running_flag_even_on_failure(self, tmp_path):
        session = _Session(tmp_path)
        _invoke(RunExperimentTool(session), {"command": "exit 3"}, tmp_path)

        assert session.state.running_command is None
        assert session.state.running_since is None

    def test_a_timeout_is_reported_rather_than_measured(self, tmp_path):
        session = _Session(tmp_path)
        result = _invoke(
            RunExperimentTool(session), {"command": "sleep 5", "timeout_seconds": 1}, tmp_path
        )

        assert result.metadata["timed_out"] is True
        assert "Timed out" in result.content

    def test_checks_run_after_a_passing_benchmark(self, tmp_path):
        (tmp_path / ".auto").mkdir()
        (tmp_path / ".auto" / "checks.sh").write_text("#!/usr/bin/env bash\nexit 1\n")
        session = _Session(tmp_path)

        result = _invoke(RunExperimentTool(session), {"command": "true"}, tmp_path)

        assert result.metadata["checks_passed"] is False
        assert "checks_failed" in result.content

    def test_checks_are_skipped_when_the_benchmark_failed(self, tmp_path):
        (tmp_path / ".auto").mkdir()
        (tmp_path / ".auto" / "checks.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
        session = _Session(tmp_path)

        result = _invoke(RunExperimentTool(session), {"command": "false"}, tmp_path)

        assert result.metadata["checks_passed"] is None

    def test_log_appends_and_reports_the_delta(self, tmp_path):
        session = _Session(tmp_path)
        tool = LogExperimentTool(session)
        _invoke(
            tool,
            {"commit": "a", "metric": 10.0, "status": "keep", "description": "baseline"},
            tmp_path,
        )
        result = _invoke(
            tool,
            {"commit": "b", "metric": 8.0, "status": "keep", "description": "cached"},
            tmp_path,
        )

        assert "-20.0%" in result.content
        assert "better" in result.content
        assert len(load(tmp_path).results) == 2

    def test_log_rejects_an_unknown_status(self, tmp_path):
        result = _invoke(
            LogExperimentTool(_Session(tmp_path)),
            {"commit": "a", "metric": 1.0, "status": "maybe", "description": "x"},
            tmp_path,
        )
        assert result.is_error

    def test_log_stores_the_confidence_it_showed_the_agent(self, tmp_path):
        session = _Session(tmp_path)
        tool = LogExperimentTool(session)
        for i, metric in enumerate([10.0, 9.9, 10.1, 5.0]):
            _invoke(
                tool,
                {"commit": f"c{i}", "metric": metric, "status": "keep", "description": f"run {i}"},
                tmp_path,
            )

        last = load(tmp_path).results[-1]
        assert last.confidence is not None and last.confidence > 2.0

    def test_log_warns_when_the_experiment_budget_is_spent(self, tmp_path):
        session = _Session(tmp_path)
        session.state.max_experiments = 1
        result = _invoke(
            LogExperimentTool(session),
            {"commit": "a", "metric": 1.0, "status": "keep", "description": "x"},
            tmp_path,
        )
        assert "max_experiments" in result.content


# ── Dashboard ────────────────────────────────────────────────────────────────


def _theme():
    from tau.tui.theme import LayoutTheme

    return LayoutTheme()


def _plain(lines: list[str]) -> str:
    from tau.tui.utils import strip_ansi

    return "\n".join(strip_ansi(line) for line in lines)


class TestDashboard:
    def _state(self):
        state = State(name="Speed", metric_name="seconds", metric_unit="s", direction="lower")
        state.results = _results([10.0, 12.0, 8.0], ["keep", "discard", "keep"])
        return state

    def test_summary_reports_counts_baseline_and_best(self):
        text = _plain(summary_lines(self._state(), _theme(), 100))

        assert "Runs: 3" in text
        assert "2 kept" in text and "1 discarded" in text
        assert "Baseline:" in text and "10s" in text
        assert "Progress:" in text and "8s" in text
        assert "-20.0%" in text

    def test_the_table_lists_runs_with_status_and_description(self):
        text = _plain(table_lines(self._state(), _theme(), 100, max_rows=6))

        assert "commit" in text and "status" in text and "description" in text
        assert "run 0" in text and "run 2" in text
        assert "keep" in text and "discard" in text

    def test_older_runs_are_summarised_when_they_do_not_fit(self):
        state = self._state()
        state.results = _results([float(i) for i in range(10)])

        text = _plain(table_lines(state, _theme(), 100, max_rows=3))

        assert "7 earlier runs" in text
        assert "run 9" in text

    def test_secondary_metric_columns_appear_when_there_is_room(self):
        state = self._state()
        state.secondary = [MetricDef(name="compile_ms")]
        state.results[0].metrics = {"compile_ms": 420.0}

        text = _plain(table_lines(state, _theme(), 120, max_rows=6))

        assert "compile_ms" in text and "420" in text

    def test_narrow_terminals_drop_secondary_columns_not_the_description(self):
        state = self._state()
        state.secondary = [MetricDef(name="a_very_long_metric_name")]
        state.results[0].metrics = {"a_very_long_metric_name": 1.0}

        text = _plain(table_lines(state, _theme(), 50, max_rows=6))

        assert "a_very_long_metric_name" not in text
        assert "description" in text

    def test_an_empty_session_says_so(self):
        assert "No experiments yet" in _plain(table_lines(State(), _theme(), 80, 6))

    def test_the_widget_shows_the_title_and_the_table(self):
        text = _plain(widget_lines(self._state(), _theme(), 100))

        assert "autoresearch: Speed" in text
        assert "Runs: 3" in text
        assert "description" in text

    def test_a_running_experiment_shows_a_spinner_line(self):
        import time as _time

        state = self._state()
        state.running_command = "bash .auto/measure.sh"
        state.running_since = _time.time() - 5

        text = _plain(summary_lines(state, _theme(), 100))

        assert "running" in text and "measure.sh" in text and "5s" in text


# ── Fullscreen overlay ───────────────────────────────────────────────────────


class TestOverlay:
    def _overlay(self, rows: int = 40):
        state = State(name="Speed", metric_name="seconds", metric_unit="s", direction="lower")
        state.results = _results([float(10 - i) for i in range(rows)])
        closed: list[bool] = []
        return DashboardOverlay(state, _theme(), lambda: closed.append(True)), closed

    def _render(self, overlay, width=100, height=20):
        from tau.tui.buffer import Buffer
        from tau.tui.geometry import Rect
        from tau.tui.utils import strip_ansi

        buf = Buffer.empty(Rect(0, 0, width, height))
        overlay.render_cells(Rect(0, 0, width, height), buf)
        return [
            strip_ansi("".join(buf.get(x, y).symbol for x in range(width))).rstrip()
            for y in range(height)
        ]

    def _key(self, name: str):
        from tau.tui.input import KeyEvent

        return KeyEvent(key=name, char=None)

    def test_shows_the_session_and_a_footer(self):
        overlay, _ = self._overlay()
        rows = self._render(overlay)

        assert any("autoresearch: Speed" in r for r in rows)
        assert "scroll" in rows[-1] and "Esc close" in rows[-1]

    def test_scrolling_moves_the_window(self):
        overlay, _ = self._overlay()
        first = self._render(overlay)[1]

        overlay.handle_input(self._key("down"))
        overlay.handle_input(self._key("down"))

        assert self._render(overlay)[1] != first

    def test_g_and_shift_g_jump_to_the_ends(self):
        overlay, _ = self._overlay()
        overlay.handle_input(self._key("G"))
        bottom = self._render(overlay)
        overlay.handle_input(self._key("g"))
        top = self._render(overlay)

        assert bottom != top
        assert any("autoresearch: Speed" in r for r in top)

    def test_scrolling_past_the_end_clamps(self):
        overlay, _ = self._overlay(rows=3)
        for _ in range(50):
            overlay.handle_input(self._key("down"))

        rows = self._render(overlay)
        assert any(r.strip() for r in rows)  # never scrolls into empty space

    def test_escape_and_q_close(self):
        for key in ("escape", "q"):
            overlay, closed = self._overlay()
            overlay.handle_input(self._key(key))
            assert closed == [True]

    def test_unrelated_keys_are_not_consumed(self):
        overlay, _ = self._overlay()
        assert overlay.handle_input(self._key("f5")) is False


# ── /autoresearch command ────────────────────────────────────────────────────


class _UI:
    """The slice of ctx.ui the command touches. ExtensionContext has no
    notify() of its own — it lives here, and is absent entirely when headless."""

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.theme = None

    def notify(self, message: str) -> None:
        self.messages.append(message)


class _Ctx:
    def __init__(self, ui=None) -> None:
        self.ui = ui
        self.sent: list[str] = []
        self.triggered: list[bool] = []

    async def send_user_message(self, content: str, *, trigger_turn: bool = False) -> None:
        self.sent.append(content)
        self.triggered.append(trigger_turn)


class _Registry:
    """Captures what register() wires up."""

    def __init__(self) -> None:
        self.tools: list = []
        self.commands: dict = {}
        self.shortcuts: dict = {}
        self.hooks: dict = {}
        self.services: dict = {}

    def register_tool(self, tool):
        self.tools.append(tool)

    def register_command(self, name, description, handler, **kwargs):
        self.commands[name] = handler

    def register_shortcut(self, key, description=None, handler=None):
        def _decorator(fn):
            self.shortcuts[key] = fn
            return fn

        return _decorator if handler is None else self.shortcuts.setdefault(key, handler)

    def on(self, event_type, handler=None):
        def _decorator(fn):
            self.hooks[event_type] = fn
            return fn

        return _decorator if handler is None else self.hooks.setdefault(event_type, handler)

    def provide(self, name, service):
        self.services[name] = service


@pytest.fixture
def wired(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    registry = _Registry()
    importlib.import_module(_PKG).register(registry)
    return registry, tmp_path


class TestCommand:
    def _run(self, registry, ctx, *args):
        asyncio.run(registry.commands["autoresearch"](ctx, list(args)))

    def test_registers_tools_command_and_shortcut(self, wired):
        registry, _ = wired

        assert {t.name for t in registry.tools} == {
            "init_experiment",
            "run_experiment",
            "log_experiment",
        }
        assert "autoresearch" in registry.commands
        assert "ctrl+shift+f" in registry.shortcuts

    def test_status_reports_through_the_ui(self, wired):
        registry, _ = wired
        ui = _UI()
        ctx = _Ctx(ui)

        self._run(registry, ctx, "status")

        assert ui.messages and "No experiments logged yet" in ui.messages[0]

    def test_status_summarises_a_real_session(self, wired):
        registry, cwd = wired
        append_config(cwd, State(name="Speed", metric_name="seconds", metric_unit="s"))
        append_result(cwd, Result(commit="a", metric=10.0, status="keep", description="baseline"))
        append_result(cwd, Result(commit="b", metric=8.0, status="keep", description="faster"))
        ui = _UI()

        self._run(registry, _Ctx(ui), "status")

        text = ui.messages[0]
        assert "Speed" in text and "2 runs" in text
        assert "10s" in text and "8s" in text and "better" in text

    def test_help_is_shown_for_help_and_for_a_bare_call(self, wired):
        registry, _ = wired
        ui = _UI()

        self._run(registry, _Ctx(ui), "help")
        self._run(registry, _Ctx(ui), *[])

        assert all("/autoresearch" in m for m in ui.messages)

    def test_clear_deletes_the_log(self, wired):
        registry, cwd = wired
        append_config(cwd, State(name="Speed", metric_name="seconds"))
        append_result(cwd, Result(commit="a", metric=1.0, status="keep", description="x"))
        assert log_path(cwd).exists()

        self._run(registry, _Ctx(_UI()), "clear")

        assert not log_path(cwd).exists()

    def test_a_goal_asks_the_agent_to_set_the_session_up(self, wired):
        registry, _ = wired
        ctx = _Ctx(_UI())

        self._run(registry, ctx, "optimize", "test", "runtime")

        assert ctx.sent and "autoresearch-create" in ctx.sent[0]
        assert "optimize test runtime" in ctx.sent[0]
        # A plain steer would queue behind a turn that never starts.
        assert ctx.triggered == [True]

    def test_a_goal_resumes_when_a_prompt_file_exists(self, wired):
        registry, cwd = wired
        (cwd / ".auto").mkdir()
        (cwd / ".auto" / "prompt.md").write_text("# session")
        ctx = _Ctx(_UI())

        self._run(registry, ctx, "keep", "going")

        assert ctx.sent and "Continue the autoresearch loop" in ctx.sent[0]
        assert "keep going" in ctx.sent[0]
        assert ctx.triggered == [True]

    def test_commands_survive_a_headless_context(self, wired):
        registry, _ = wired
        # ctx.ui is None in print/JSON mode; notifying must not explode.
        self._run(registry, _Ctx(None), "status")
