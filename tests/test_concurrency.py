"""Concurrency tests: parallel runs, run-id uniqueness, resource leak guards."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from dialectic import core, protocol as p
from dialectic.agents.claude import ClaudeResult
from dialectic.agents.codex import CodexResult


def _writer_report_dict(**ov: Any) -> dict:
    base = {
        "diff": "+ x", "summary": "x", "approaches": ["fix"], "confidence": "high",
        "files_touched": [], "assumptions": [], "open_questions": [],
    }
    base.update(ov)
    return base


def _critique_dict(verdict: str) -> dict:
    return {"verdict": verdict, "items": [], "summary": "ok", "reviewer_id": None}


@pytest.mark.asyncio
async def test_two_concurrent_runs_get_distinct_run_ids(tmp_git_repo: Path) -> None:
    """Two runs started in parallel produce distinct run_ids and non-overlapping worktrees."""

    async def fake_writer(prompt, cfg, cwd, schema, perm, timeout):
        # Mutate a unique file so each worktree's diff is non-empty and distinguishable.
        (cwd / f"writer-{cwd.name}.txt").write_text("hello\n")
        await asyncio.sleep(0.05)  # let both runs interleave
        return ClaudeResult(raw_text="", structured=_writer_report_dict(), cost_usd=0.01)

    async def fake_reviewer(prompt, cfg, cwd, schema, sandbox, timeout):
        await asyncio.sleep(0.05)
        return CodexResult(raw_text="", structured=_critique_dict("approve"), cost_usd=0.01)

    cfg = p.RunConfig(prompt="x")
    r1, r2 = await asyncio.gather(
        core.run(cfg, tmp_git_repo, writer_invoke=fake_writer, reviewer_invoke=fake_reviewer),
        core.run(cfg, tmp_git_repo, writer_invoke=fake_writer, reviewer_invoke=fake_reviewer),
    )

    assert r1.run_id != r2.run_id
    assert r1.status == p.RunStatus.AWAITING_APPROVAL
    assert r2.status == p.RunStatus.AWAITING_APPROVAL
    # Both audit logs persisted.
    assert (tmp_git_repo / ".dialectic" / "runs" / f"{r1.run_id}.json").exists()
    assert (tmp_git_repo / ".dialectic" / "runs" / f"{r2.run_id}.json").exists()


def test_run_id_uniqueness_under_clock_collision() -> None:
    """1000 sequential _gen_run_id calls all distinct (the 6-hex suffix saves us)."""
    ids = {core._gen_run_id() for _ in range(1000)}
    assert len(ids) == 1000


@pytest.mark.asyncio
async def test_temp_schema_file_cleaned_after_codex_failure(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the codex invoker raises mid-run, the temp schema file written by the
    default codex invoker should still be cleaned up by its finally block."""
    from dialectic import core as core_mod
    from dialectic.protocol import AgentConfig, AgentCli, SandboxMode

    # The default codex invoker imports `invoke` from agents.codex as `_codex_invoke`;
    # patch the name as core sees it.
    seen_schema_paths: list[Path] = []

    async def boom(invocation, timeout_s=1500):
        seen_schema_paths.append(invocation.output_schema_path)
        raise RuntimeError("simulated codex failure")

    monkeypatch.setattr(core_mod, "_codex_invoke", boom)

    cfg = AgentConfig(cli=AgentCli.CODEX, model="gpt-5.4", effort="xhigh")
    with pytest.raises(RuntimeError, match="simulated codex"):
        await core_mod._default_codex_invoker(
            prompt="x", cfg=cfg, cwd=tmp_git_repo,
            output_schema={"type": "object", "properties": {}},
            sandbox=SandboxMode.READ_ONLY, timeout_s=10,
        )

    # The temp file existed when codex was invoked, and the finally cleaned it up.
    # We assert specifically on the file THIS test created, not a global tempdir
    # glob — concurrent dialectic runs (or a parallel test run) would have their
    # own dialectic-schema-* files briefly in tempdir, which would make a global
    # assertion racy. This was caught when an outer dialectic-on-dialectic run's
    # reviewer ran pytest while the orchestrator's own schema temp file was open.
    assert seen_schema_paths and seen_schema_paths[0] is not None
    assert not seen_schema_paths[0].exists(), "temp schema file not cleaned up after failure"
