"""Tests for the `dialectic costs` subcommand."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from click.testing import CliRunner
from rich.console import Console

from dialectic import cli as cli_mod
from dialectic.cli import main
from dialectic.protocol import AgentCli, AgentConfig, RunConfig, RunResult, RunStatus


def _write_run(
    runs_dir: Path,
    *,
    run_id: str,
    writer_model: str,
    reviewer_model: str,
    cost_usd: float,
    status: RunStatus = RunStatus.SUCCESS,
    writer_cli: AgentCli = AgentCli.CLAUDE,
    writer_effort: str = "max",
    reviewer_cli: AgentCli = AgentCli.CODEX,
    reviewer_effort: str = "xhigh",
) -> Path:
    config = RunConfig(
        prompt="dummy",
        writer=AgentConfig(cli=writer_cli, model=writer_model, effort=writer_effort),
        reviewer=AgentConfig(cli=reviewer_cli, model=reviewer_model, effort=reviewer_effort),
    )
    result = RunResult(
        run_id=run_id,
        status=status,
        config=config,
        cost_usd=cost_usd,
        duration_s=1.0,
    )
    path = runs_dir / f"{run_id}.json"
    path.write_text(result.model_dump_json(indent=2))
    return path


@pytest.fixture
def wide_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the CLI's rich console to a wide, non-terminal output so the table
    doesn't wrap based on the testing terminal's dimensions."""
    monkeypatch.setattr(cli_mod, "console", Console(width=200, force_terminal=False, no_color=True))


_CELL_SEP = r"[\s│|]+"


def _find_row(output: str, model: str, role: str) -> tuple[int, float]:
    """Locate the table row for (model, role) and parse out (runs, cost_usd)."""
    pattern = re.compile(
        rf"{re.escape(model)}{_CELL_SEP}{re.escape(role)}{_CELL_SEP}(\d+){_CELL_SEP}\$([0-9]+\.[0-9]+)",
    )
    match = pattern.search(output)
    assert match is not None, f"row for ({model}, {role}) not found in:\n{output}"
    return int(match.group(1)), float(match.group(2))


def test_costs_aggregates_by_writer_and_reviewer(tmp_path: Path, wide_console: None) -> None:
    runs_dir = tmp_path / ".dialectic" / "runs"
    runs_dir.mkdir(parents=True)

    # 3 runs: claude-opus-4-7 (writer) + gpt-5.4 (reviewer)
    _write_run(
        runs_dir,
        run_id="20260101-000001-aaaaaa",
        writer_model="claude-opus-4-7",
        reviewer_model="gpt-5.4",
        cost_usd=0.10,
    )
    _write_run(
        runs_dir,
        run_id="20260101-000002-bbbbbb",
        writer_model="claude-opus-4-7",
        reviewer_model="gpt-5.4",
        cost_usd=0.25,
    )
    _write_run(
        runs_dir,
        run_id="20260101-000003-cccccc",
        writer_model="claude-opus-4-7",
        reviewer_model="gpt-5.4",
        cost_usd=0.05,
    )
    # 2 runs: claude-sonnet-4-6 (writer) + gpt-5 (reviewer)
    _write_run(
        runs_dir,
        run_id="20260101-000004-dddddd",
        writer_model="claude-sonnet-4-6",
        reviewer_model="gpt-5",
        cost_usd=0.03,
    )
    _write_run(
        runs_dir,
        run_id="20260101-000005-eeeeee",
        writer_model="claude-sonnet-4-6",
        reviewer_model="gpt-5",
        cost_usd=0.07,
    )
    # 1 cross-pairing: claude-opus-4-7 (writer) + gpt-5 (reviewer)
    _write_run(
        runs_dir,
        run_id="20260101-000006-ffffff",
        writer_model="claude-opus-4-7",
        reviewer_model="gpt-5",
        cost_usd=0.40,
    )

    runner = CliRunner()
    result = runner.invoke(main, ["costs", "--repo-root", str(tmp_path)])
    assert result.exit_code == 0, result.output

    # Writer aggregation.
    opus_writer_runs, opus_writer_cost = _find_row(result.output, "claude-opus-4-7", "writer")
    assert opus_writer_runs == 4
    assert opus_writer_cost == pytest.approx(0.10 + 0.25 + 0.05 + 0.40)

    sonnet_writer_runs, sonnet_writer_cost = _find_row(result.output, "claude-sonnet-4-6", "writer")
    assert sonnet_writer_runs == 2
    assert sonnet_writer_cost == pytest.approx(0.03 + 0.07)

    # Reviewer aggregation.
    gpt54_runs, gpt54_cost = _find_row(result.output, "gpt-5.4", "reviewer")
    assert gpt54_runs == 3
    assert gpt54_cost == pytest.approx(0.10 + 0.25 + 0.05)

    gpt5_runs, gpt5_cost = _find_row(result.output, "gpt-5", "reviewer")
    assert gpt5_runs == 3
    assert gpt5_cost == pytest.approx(0.03 + 0.07 + 0.40)

    # Total: each run counted once.
    total_match = re.search(
        r"Total spent across (\d+) run\(s\):\s*\$([0-9]+\.[0-9]+)", result.output
    )
    assert total_match is not None, result.output
    assert int(total_match.group(1)) == 6
    assert float(total_match.group(2)) == pytest.approx(0.10 + 0.25 + 0.05 + 0.03 + 0.07 + 0.40)

    # Writers appear before reviewers in the table.
    opus_writer_pos = result.output.find("claude-opus-4-7")
    gpt54_pos = result.output.find("gpt-5.4")
    assert opus_writer_pos < gpt54_pos


def test_costs_empty_directory(tmp_path: Path, wide_console: None) -> None:
    (tmp_path / ".dialectic" / "runs").mkdir(parents=True)

    runner = CliRunner()
    result = runner.invoke(main, ["costs", "--repo-root", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "No runs" in result.output


def test_costs_missing_directory(tmp_path: Path, wide_console: None) -> None:
    # .dialectic/runs/ does not exist at all
    runner = CliRunner()
    result = runner.invoke(main, ["costs", "--repo-root", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "No runs" in result.output


def test_costs_skips_unparseable_files(tmp_path: Path, wide_console: None) -> None:
    runs_dir = tmp_path / ".dialectic" / "runs"
    runs_dir.mkdir(parents=True)
    _write_run(
        runs_dir,
        run_id="20260101-000001-aaaaaa",
        writer_model="claude-opus-4-7",
        reviewer_model="gpt-5.4",
        cost_usd=0.12,
    )
    (runs_dir / "garbage.json").write_text("definitely not a RunResult")

    runner = CliRunner()
    result = runner.invoke(main, ["costs", "--repo-root", str(tmp_path)])

    assert result.exit_code == 0, result.output
    # Warning surfaces, but the real run is still counted.
    assert "garbage.json" in result.output
    runs, cost = _find_row(result.output, "claude-opus-4-7", "writer")
    assert runs == 1
    assert cost == pytest.approx(0.12)


def test_costs_single_run_counted_once_in_total(tmp_path: Path, wide_console: None) -> None:
    """One run should contribute its full cost to both its writer and reviewer
    rows, but the 'Total spent' footer must count it only once."""
    runs_dir = tmp_path / ".dialectic" / "runs"
    runs_dir.mkdir(parents=True)
    _write_run(
        runs_dir,
        run_id="20260101-000001-aaaaaa",
        writer_model="claude-opus-4-7",
        reviewer_model="gpt-5.4",
        cost_usd=0.42,
    )

    runner = CliRunner()
    result = runner.invoke(main, ["costs", "--repo-root", str(tmp_path)])

    assert result.exit_code == 0, result.output

    w_runs, w_cost = _find_row(result.output, "claude-opus-4-7", "writer")
    r_runs, r_cost = _find_row(result.output, "gpt-5.4", "reviewer")
    assert w_runs == 1
    assert r_runs == 1
    assert w_cost == pytest.approx(0.42)
    assert r_cost == pytest.approx(0.42)

    total_match = re.search(
        r"Total spent across (\d+) run\(s\):\s*\$([0-9]+\.[0-9]+)", result.output
    )
    assert total_match is not None, result.output
    assert int(total_match.group(1)) == 1
    assert float(total_match.group(2)) == pytest.approx(0.42)
