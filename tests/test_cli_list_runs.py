"""Tests for the `dialectic list-runs` subcommand."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner
from rich.console import Console

from dialectic import cli as cli_mod
from dialectic.cli import main
from dialectic.protocol import RunConfig, RunResult, RunStatus


def _write_run(
    runs_dir: Path,
    *,
    run_id: str,
    prompt: str,
    status: RunStatus,
    cost_usd: float,
    duration_s: float,
    started_at: datetime,
) -> Path:
    result = RunResult(
        run_id=run_id,
        status=status,
        config=RunConfig(prompt=prompt),
        cost_usd=cost_usd,
        duration_s=duration_s,
        started_at=started_at,
    )
    path = runs_dir / f"{run_id}.json"
    path.write_text(result.model_dump_json(indent=2))
    return path


@pytest.fixture
def wide_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the CLI's rich console to a wide, non-terminal output so the table
    doesn't wrap based on the testing terminal's dimensions."""
    monkeypatch.setattr(
        cli_mod, "console", Console(width=200, force_terminal=False, no_color=True)
    )


def test_list_runs_renders_three_runs_sorted_desc(
    tmp_path: Path, wide_console: None
) -> None:
    runs_dir = tmp_path / ".dialectic" / "runs"
    runs_dir.mkdir(parents=True)

    base = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
    _write_run(
        runs_dir,
        run_id="20260517-100000-aaaaaa",
        prompt="oldest task",
        status=RunStatus.SUCCESS,
        cost_usd=0.0123,
        duration_s=12.3,
        started_at=base - timedelta(hours=2),
    )
    _write_run(
        runs_dir,
        run_id="20260517-110000-bbbbbb",
        prompt="middle task",
        status=RunStatus.FAILED,
        cost_usd=0.0456,
        duration_s=45.6,
        started_at=base - timedelta(hours=1),
    )
    long_prompt = "X" * 100  # exceeds the 60-char cell limit
    _write_run(
        runs_dir,
        run_id="20260517-120000-cccccc",
        prompt=long_prompt,
        status=RunStatus.AWAITING_APPROVAL,
        cost_usd=0.789,
        duration_s=78.9,
        started_at=base,
    )

    # A stray non-RunResult file in the runs dir should be skipped, not crash.
    (runs_dir / "notes.json").write_text("not a valid run result")

    runner = CliRunner()
    result = runner.invoke(main, ["list-runs", "--repo-root", str(tmp_path)])

    assert result.exit_code == 0, result.output

    # Short ids (last 8 chars) appear; full ids do not.
    assert "0-aaaaaa" in result.output
    assert "0-bbbbbb" in result.output
    assert "0-cccccc" in result.output
    assert "20260517-100000-aaaaaa" not in result.output
    assert "20260517-110000-bbbbbb" not in result.output
    assert "20260517-120000-cccccc" not in result.output

    # Sorted by started_at descending: cccccc (newest) → bbbbbb → aaaaaa (oldest).
    pos_c = result.output.find("0-cccccc")
    pos_b = result.output.find("0-bbbbbb")
    pos_a = result.output.find("0-aaaaaa")
    assert pos_c != -1 and pos_b != -1 and pos_a != -1
    assert pos_c < pos_b < pos_a

    # Status values present as plain text (colors stripped on non-terminal output).
    assert "success" in result.output
    assert "failed" in result.output
    assert "awaiting_approval" in result.output

    # Short prompts appear verbatim.
    assert "oldest task" in result.output
    assert "middle task" in result.output

    # Long prompt is truncated to 60 chars (59 X's + ellipsis); untruncated form absent.
    assert ("X" * 59 + "…") in result.output
    assert ("X" * 60) not in result.output

    # Cost formatted to 4 decimal places with $ prefix.
    assert "$0.0123" in result.output
    assert "$0.0456" in result.output
    assert "$0.7890" in result.output

    # Duration formatted to 1 decimal with 's' suffix.
    assert "12.3s" in result.output
    assert "45.6s" in result.output
    assert "78.9s" in result.output

    # Warning for the unparseable file surfaces, but the command still succeeds.
    assert "notes.json" in result.output


def test_list_runs_empty_directory(tmp_path: Path, wide_console: None) -> None:
    (tmp_path / ".dialectic" / "runs").mkdir(parents=True)

    runner = CliRunner()
    result = runner.invoke(main, ["list-runs", "--repo-root", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "No runs" in result.output


def test_list_runs_missing_directory(tmp_path: Path, wide_console: None) -> None:
    # .dialectic/runs/ does not exist at all
    runner = CliRunner()
    result = runner.invoke(main, ["list-runs", "--repo-root", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "No runs" in result.output


def test_list_runs_limit_overrides_default(
    tmp_path: Path, wide_console: None
) -> None:
    """--limit N caps the table at N rows, overriding the default of 10."""
    runs_dir = tmp_path / ".dialectic" / "runs"
    runs_dir.mkdir(parents=True)

    base = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
    # Create 15 runs (more than the default limit of 10) with strictly
    # increasing started_at; their short ids encode rank so we can assert
    # exactly which rows are kept.
    for i in range(15):
        _write_run(
            runs_dir,
            run_id=f"20260517-{i:06d}-zzzz{i:02d}",
            prompt=f"task {i}",
            status=RunStatus.SUCCESS,
            cost_usd=0.0001 * (i + 1),
            duration_s=float(i + 1),
            started_at=base + timedelta(minutes=i),
        )

    runner = CliRunner()

    # --limit 3: only the 3 newest runs (i=14, 13, 12) should appear.
    result = runner.invoke(
        main, ["list-runs", "--repo-root", str(tmp_path), "--limit", "3"]
    )
    assert result.exit_code == 0, result.output
    assert "zzzz14" in result.output
    assert "zzzz13" in result.output
    assert "zzzz12" in result.output
    for older in range(12):
        assert f"zzzz{older:02d}" not in result.output

    # --limit 15: all 15 runs should appear (above the default of 10).
    result = runner.invoke(
        main, ["list-runs", "--repo-root", str(tmp_path), "--limit", "15"]
    )
    assert result.exit_code == 0, result.output
    for i in range(15):
        assert f"zzzz{i:02d}" in result.output


def test_list_runs_limit_rejects_zero(tmp_path: Path, wide_console: None) -> None:
    (tmp_path / ".dialectic" / "runs").mkdir(parents=True)
    runner = CliRunner()
    result = runner.invoke(
        main, ["list-runs", "--repo-root", str(tmp_path), "--limit", "0"]
    )
    assert result.exit_code != 0
    assert "limit" in result.output.lower()


def test_list_runs_limit_rejects_negative(
    tmp_path: Path, wide_console: None
) -> None:
    (tmp_path / ".dialectic" / "runs").mkdir(parents=True)
    runner = CliRunner()
    result = runner.invoke(
        main, ["list-runs", "--repo-root", str(tmp_path), "--limit", "-5"]
    )
    assert result.exit_code != 0
    assert "limit" in result.output.lower()
