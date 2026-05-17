"""CLI command tests using Click's CliRunner."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from dialectic import core
from dialectic.cli import main
from dialectic.protocol import (
    AgentCli,
    AgentConfig,
    ApplyMode,
    CritiqueItem,
    ReviewerCritique,
    ReviewerVerdict,
    RevisionRound,
    RunConfig,
    RunResult,
    RunStatus,
    Severity,
    WriterReport,
)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _make_completed_result(repo_root: Path, run_id: str = "20260517-120000-abcdef") -> RunResult:
    cfg = RunConfig(prompt="test", apply_mode=ApplyMode.UNCOMMITTED)
    result = RunResult(
        run_id=run_id,
        status=RunStatus.AWAITING_APPROVAL,
        config=cfg,
        diff="",
        files_changed=[],
        rounds=[
            RevisionRound(
                round_number=1,
                writer_report=WriterReport(diff="", summary="x"),
                reviewer_critique=ReviewerCritique(verdict=ReviewerVerdict.APPROVE, summary="x"),
            )
        ],
        summary="ok",
    )
    core.persist_run_record(result, repo_root)
    return result


def test_cli_main_help_lists_commands(runner: CliRunner) -> None:
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    for cmd in ("run", "approve", "reject", "arbitrate", "serve"):
        assert cmd in result.output


def test_cli_run_dispatches_to_core(
    runner: CliRunner, tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `run` command builds a RunConfig from flags and calls core.run."""
    captured: dict = {}

    async def fake_run(cfg, repo_root, **kwargs):
        captured["cfg"] = cfg
        captured["repo_root"] = repo_root
        return RunResult(
            run_id="20260517-120000-abcdef",
            status=RunStatus.AWAITING_APPROVAL,
            config=cfg,
            diff="",
        )

    monkeypatch.setattr(core, "run", fake_run)

    result = runner.invoke(
        main,
        [
            "run", "--prompt", "test",
            "--max-revisions", "2",
            "--writer-model", "claude-opus-4-7",
            "--writer-effort", "high",
            "--reviewer-effort", "medium",
            "--repo-root", str(tmp_git_repo),
            "--auto-approve", "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    cfg: RunConfig = captured["cfg"]
    assert cfg.prompt == "test"
    assert cfg.max_revisions == 2
    assert cfg.writer.effort == "high"
    assert cfg.reviewer.effort == "medium"
    assert cfg.apply_mode == ApplyMode.DRY_RUN  # --dry-run shortcut


def test_cli_run_dry_run_shortcut_overrides_apply_mode(
    runner: CliRunner, tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {}

    async def fake_run(cfg, repo_root, **kwargs):
        captured["cfg"] = cfg
        return RunResult(run_id="20260517-120000-abcdef", status=RunStatus.AWAITING_APPROVAL, config=cfg)

    monkeypatch.setattr(core, "run", fake_run)
    runner.invoke(
        main,
        ["run", "--prompt", "x", "--apply-mode", "uncommitted", "--dry-run",
         "--repo-root", str(tmp_git_repo), "--auto-approve"],
    )
    assert captured["cfg"].apply_mode == ApplyMode.DRY_RUN


def test_cli_approve_loads_record_and_applies(
    runner: CliRunner, tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result_obj = _make_completed_result(tmp_git_repo)

    applied: list = []

    def fake_apply(res, repo_root):
        applied.append(res.run_id)
        res.status = RunStatus.SUCCESS
        return res

    monkeypatch.setattr(core, "apply_run_result", fake_apply)
    cli_result = runner.invoke(
        main,
        ["approve", result_obj.run_id, "--repo-root", str(tmp_git_repo)],
    )
    assert cli_result.exit_code == 0, cli_result.output
    assert applied == [result_obj.run_id]


def test_cli_reject_loads_record_and_rejects(
    runner: CliRunner, tmp_git_repo: Path
) -> None:
    result_obj = _make_completed_result(tmp_git_repo)
    cli_result = runner.invoke(
        main,
        ["reject", result_obj.run_id, "--repo-root", str(tmp_git_repo)],
    )
    assert cli_result.exit_code == 0
    reloaded = core.load_run_record(result_obj.run_id, tmp_git_repo)
    assert reloaded.status == RunStatus.REJECTED_BY_USER


def test_cli_approve_invalid_run_id_format_errors(
    runner: CliRunner, tmp_git_repo: Path
) -> None:
    """Path-traversal regression: bogus run_ids must be rejected before disk access."""
    cli_result = runner.invoke(
        main,
        ["approve", "../../etc/passwd", "--repo-root", str(tmp_git_repo)],
    )
    assert cli_result.exit_code != 0


def test_cli_arbitrate_builds_decisions(
    runner: CliRunner, tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {}

    async def fake_resume(run_id, decisions, repo_root):
        captured["run_id"] = run_id
        captured["decisions"] = decisions
        return RunResult(
            run_id=run_id, status=RunStatus.AWAITING_APPROVAL, config=RunConfig(prompt="x")
        )

    monkeypatch.setattr(core, "resume_with_arbitration", fake_resume)
    cli_result = runner.invoke(
        main,
        [
            "arbitrate", "20260517-120000-abcdef",
            "--accept-writer", "1", "--accept-writer", "3",
            "--skip", "2",
            "--repo-root", str(tmp_git_repo),
        ],
    )
    assert cli_result.exit_code == 0, cli_result.output
    decisions = captured["decisions"]
    assert {d.item_id for d in decisions} == {1, 2, 3}
    accept_w = {d.item_id for d in decisions if d.choice.value == "accept_writer"}
    skip = {d.item_id for d in decisions if d.choice.value == "skip"}
    assert accept_w == {1, 3}
    assert skip == {2}


def test_cli_run_failed_status_exits_nonzero(
    runner: CliRunner, tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_run(cfg, repo_root, **kwargs):
        return RunResult(
            run_id="20260517-120000-abcdef",
            status=RunStatus.FAILED,
            config=cfg,
            error="simulated",
        )

    monkeypatch.setattr(core, "run", fake_run)
    cli_result = runner.invoke(
        main,
        ["run", "--prompt", "x", "--auto-approve", "--repo-root", str(tmp_git_repo)],
    )
    assert cli_result.exit_code != 0


def test_cli_run_awaiting_arbitration_does_not_apply(
    runner: CliRunner, tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run with disputes/unresolved items should NOT apply automatically."""
    applied: list = []

    async def fake_run(cfg, repo_root, **kwargs):
        return RunResult(
            run_id="20260517-120000-abcdef",
            status=RunStatus.AWAITING_ARBITRATION,
            config=cfg,
            disputed_items=[],
        )

    def fake_apply(res, repo_root):
        applied.append(res.run_id)
        return res

    monkeypatch.setattr(core, "run", fake_run)
    monkeypatch.setattr(core, "apply_run_result", fake_apply)
    cli_result = runner.invoke(
        main,
        ["run", "--prompt", "x", "--auto-approve", "--repo-root", str(tmp_git_repo)],
    )
    assert cli_result.exit_code == 0
    assert applied == []  # didn't auto-apply
    assert "arbitrate" in cli_result.output.lower()


def test_cli_serve_refuses_non_localhost_without_token(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hardening regression: `dialectic serve --host 0.0.0.0` should fail without --token."""
    monkeypatch.delenv("DIALECTIC_TOKEN", raising=False)

    # uvicorn.run would otherwise block; patch it to no-op so test completes if we got that far.
    import uvicorn
    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: None)

    cli_result = runner.invoke(main, ["serve", "--host", "0.0.0.0"])
    assert cli_result.exit_code != 0
    assert "loopback" in cli_result.output.lower() or "token" in cli_result.output.lower()
