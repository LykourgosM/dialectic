"""Integration tests for the orchestrator (core.run).

The agent invokers are mocked so we can verify:
  - the orchestrator threads protocol types correctly between writer and reviewer
  - the reviewer's prompt actually contains the writer's diff
  - the writer's revision prompt contains the reviewer's critique items
  - the rebuttal prompt contains the writer's per-item responses (rationale)
  - the final RunResult correctly classifies resolved / dissented / disputed items
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from dialectic import core
from dialectic import protocol as p
from dialectic.agents.claude import ClaudeResult
from dialectic.agents.codex import CodexResult

# ──────────────────────────────────────────────────────────────────────────────
# Helpers for building canned agent responses
# ──────────────────────────────────────────────────────────────────────────────


def writer_report_dict(**overrides: Any) -> dict:
    base = {
        "diff": "+ placeholder",
        "summary": "did the thing",
        "approaches": ["fix"],
        "confidence": "high",
        "files_touched": ["main.py"],
        "assumptions": [],
        "open_questions": [],
    }
    base.update(overrides)
    return base


def critique_dict(verdict: str, items: list[dict], summary: str = "x") -> dict:
    return {"verdict": verdict, "items": items, "summary": summary, "reviewer_id": None}


def critique_item_dict(item_id: int, issue: str, **overrides: Any) -> dict:
    base = {
        "id": item_id,
        "severity": "medium",
        "categories": ["correctness"],
        "file": "main.py",
        "lines": "1-2",
        "issue": issue,
        "suggested_fix": None,
    }
    base.update(overrides)
    return base


def writer_responses_dict(responses: list[dict], summary: str = "revised") -> dict:
    return {
        "responses": responses,
        "revised_diff": "+ revised placeholder",
        "revised_diff_summary": summary,
    }


def rebuttal_dict(verdict: str, item_rebuttals: list[dict], summary: str = "rb") -> dict:
    return {
        "verdict": verdict,
        "item_rebuttals": item_rebuttals,
        "summary": summary,
        "reviewer_id": None,
    }


def claude_ok(structured: dict, cost: float = 0.05) -> ClaudeResult:
    return ClaudeResult(raw_text="", structured=structured, cost_usd=cost)


def codex_ok(structured: dict, cost: float = 0.03) -> CodexResult:
    return CodexResult(raw_text="", structured=structured, cost_usd=cost)


# ──────────────────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_happy_path_first_pass_approval(tmp_git_repo: Path) -> None:
    """Reviewer approves on first pass → status=AWAITING_APPROVAL, one round, no revision/rebuttal."""

    async def fake_writer(prompt, cfg, cwd, schema, perm, timeout):
        (cwd / "main.py").write_text("def greet(name):\n    return f'hi {name}'\n")
        return claude_ok(writer_report_dict())

    async def fake_reviewer(prompt, cfg, cwd, schema, sandbox, timeout):
        return codex_ok(critique_dict("approve", []))

    config = p.RunConfig(prompt="change hello to hi")
    result = await core.run(
        config, tmp_git_repo, writer_invoke=fake_writer, reviewer_invoke=fake_reviewer
    )

    assert result.status == p.RunStatus.AWAITING_APPROVAL
    assert len(result.rounds) == 1
    assert result.rounds[0].reviewer_critique.verdict == p.ReviewerVerdict.APPROVE
    assert result.rounds[0].writer_responses is None
    assert result.rounds[0].reviewer_rebuttal is None
    assert result.disputed_items == []
    assert result.acknowledged_dissents == []
    assert "hi" in result.diff and "hello" in result.diff
    assert result.files_changed == ["main.py"]
    assert result.cost_usd > 0


@pytest.mark.asyncio
async def test_writer_accepts_all_critique_items(tmp_git_repo: Path) -> None:
    """Reviewer revises → writer accepts all items → no rebuttal needed → second reviewer pass approves."""

    writer_calls = 0

    async def fake_writer(prompt, cfg, cwd, schema, perm, timeout):
        nonlocal writer_calls
        writer_calls += 1
        if writer_calls == 1:
            (cwd / "main.py").write_text("def greet(name):\n    return f'hi {name}'\n")
            return claude_ok(writer_report_dict())
        else:
            (cwd / "main.py").write_text("def greet(name: str) -> str:\n    return f'hi {name}'\n")
            return claude_ok(
                writer_responses_dict(
                    [{"item_id": 1, "action": "accept", "change_summary": "added type hints"}]
                )
            )

    reviewer_calls = 0

    async def fake_reviewer(prompt, cfg, cwd, schema, sandbox, timeout):
        nonlocal reviewer_calls
        reviewer_calls += 1
        if reviewer_calls == 1:
            return codex_ok(
                critique_dict(
                    "revise", [critique_item_dict(1, "missing type hints", severity="low")]
                )
            )
        else:
            return codex_ok(critique_dict("approve", []))

    config = p.RunConfig(prompt="x", max_revisions=1)
    result = await core.run(
        config, tmp_git_repo, writer_invoke=fake_writer, reviewer_invoke=fake_reviewer
    )

    assert result.status == p.RunStatus.AWAITING_APPROVAL
    assert len(result.rounds) == 2
    assert result.rounds[0].reviewer_critique.verdict == p.ReviewerVerdict.REVISE
    assert result.rounds[0].writer_responses is not None
    assert result.rounds[0].reviewer_rebuttal is None  # no rejections, no rebuttal
    assert result.rounds[1].reviewer_critique.verdict == p.ReviewerVerdict.APPROVE
    assert "name: str" in result.diff


@pytest.mark.asyncio
async def test_writer_defends_reviewer_accepts_rationale_yields_dissent(
    tmp_git_repo: Path,
) -> None:
    """Writer rejects item with rationale, reviewer accepts the rationale → acknowledged dissent."""

    writer_calls = 0

    async def fake_writer(prompt, cfg, cwd, schema, perm, timeout):
        nonlocal writer_calls
        writer_calls += 1
        if writer_calls == 1:
            (cwd / "main.py").write_text("def greet(name):\n    return f'hi {name}'\n")
            return claude_ok(writer_report_dict())
        else:
            return claude_ok(
                writer_responses_dict(
                    [
                        {
                            "item_id": 1,
                            "action": "reject",
                            "rationale": "single quotes are this project's style per CLAUDE.md",
                        }
                    ]
                )
            )

    reviewer_calls = 0

    async def fake_reviewer(prompt, cfg, cwd, schema, sandbox, timeout):
        nonlocal reviewer_calls
        reviewer_calls += 1
        if reviewer_calls == 1:
            return codex_ok(
                critique_dict(
                    "revise",
                    [critique_item_dict(1, "Use double quotes per PEP 8", categories=["style"])],
                )
            )
        else:
            return codex_ok(
                rebuttal_dict(
                    "approve_with_dissent",
                    [{"item_id": 1, "verdict": "accept_writer_rationale"}],
                )
            )

    config = p.RunConfig(prompt="x", max_revisions=1)
    result = await core.run(
        config, tmp_git_repo, writer_invoke=fake_writer, reviewer_invoke=fake_reviewer
    )

    assert result.status == p.RunStatus.AWAITING_APPROVAL
    assert len(result.rounds) == 1
    assert result.rounds[0].writer_responses is not None
    assert result.rounds[0].reviewer_rebuttal is not None
    assert len(result.acknowledged_dissents) == 1
    assert result.acknowledged_dissents[0].item.id == 1
    assert "single quotes" in result.acknowledged_dissents[0].writer_response.rationale
    assert result.disputed_items == []


@pytest.mark.asyncio
async def test_unresolved_dispute_awaits_arbitration(tmp_git_repo: Path) -> None:
    """Writer rejects, reviewer rebuts as still_disputed → status=AWAITING_ARBITRATION."""

    writer_calls = 0

    async def fake_writer(prompt, cfg, cwd, schema, perm, timeout):
        nonlocal writer_calls
        writer_calls += 1
        if writer_calls == 1:
            (cwd / "main.py").write_text("changed\n")
            return claude_ok(writer_report_dict())
        else:
            return claude_ok(
                writer_responses_dict(
                    [{"item_id": 1, "action": "reject", "rationale": "I disagree"}]
                )
            )

    reviewer_calls = 0

    async def fake_reviewer(prompt, cfg, cwd, schema, sandbox, timeout):
        nonlocal reviewer_calls
        reviewer_calls += 1
        if reviewer_calls == 1:
            return codex_ok(
                critique_dict(
                    "revise",
                    [critique_item_dict(1, "This is wrong", severity="high")],
                )
            )
        else:
            return codex_ok(
                rebuttal_dict(
                    "still_disputed",
                    [
                        {
                            "item_id": 1,
                            "verdict": "still_disputed",
                            "rebuttal_reasoning": "No, the writer is mistaken about X",
                        }
                    ],
                )
            )

    config = p.RunConfig(prompt="x", max_revisions=1)
    result = await core.run(
        config, tmp_git_repo, writer_invoke=fake_writer, reviewer_invoke=fake_reviewer
    )

    assert result.status == p.RunStatus.AWAITING_ARBITRATION
    assert len(result.disputed_items) == 1
    assert result.disputed_items[0].item.id == 1
    assert result.disputed_items[0].writer_response.rationale == "I disagree"
    assert (
        "No, the writer is mistaken"
        in result.disputed_items[0].reviewer_rebuttal_item.rebuttal_reasoning
    )


@pytest.mark.asyncio
async def test_prompt_threading_writer_diff_reaches_reviewer(tmp_git_repo: Path) -> None:
    """The reviewer's prompt must contain the writer's diff verbatim — this is the threading invariant."""
    captured: dict[str, list[str]] = {"writer": [], "reviewer": []}

    async def fake_writer(prompt, cfg, cwd, schema, perm, timeout):
        captured["writer"].append(prompt)
        (cwd / "main.py").write_text("def greet(name):\n    return f'hi {name}'\n")
        return claude_ok(writer_report_dict())

    async def fake_reviewer(prompt, cfg, cwd, schema, sandbox, timeout):
        captured["reviewer"].append(prompt)
        return codex_ok(critique_dict("approve", []))

    await core.run(
        p.RunConfig(prompt="change hello to hi"),
        tmp_git_repo,
        writer_invoke=fake_writer,
        reviewer_invoke=fake_reviewer,
    )

    assert len(captured["writer"]) == 1
    assert len(captured["reviewer"]) == 1
    # Writer's prompt contains the user's task
    assert "change hello to hi" in captured["writer"][0]
    # Reviewer's prompt contains the writer's diff (extracted from worktree)
    reviewer_prompt = captured["reviewer"][0]
    assert "f'hi {name}'" in reviewer_prompt
    assert "f'hello {name}'" in reviewer_prompt
    # And the writer's structured report
    assert "did the thing" in reviewer_prompt


@pytest.mark.asyncio
async def test_prompt_threading_critique_reaches_writer_revision(tmp_git_repo: Path) -> None:
    """The writer's revision prompt must contain the reviewer's critique items by id and issue text."""
    captured: list[str] = []

    writer_calls = 0

    async def fake_writer(prompt, cfg, cwd, schema, perm, timeout):
        nonlocal writer_calls
        writer_calls += 1
        captured.append(prompt)
        if writer_calls == 1:
            (cwd / "main.py").write_text("modified\n")
            return claude_ok(writer_report_dict())
        return claude_ok(
            writer_responses_dict([{"item_id": 7, "action": "accept", "change_summary": "fixed"}])
        )

    reviewer_calls = 0

    async def fake_reviewer(prompt, cfg, cwd, schema, sandbox, timeout):
        nonlocal reviewer_calls
        reviewer_calls += 1
        if reviewer_calls == 1:
            return codex_ok(
                critique_dict(
                    "revise",
                    [critique_item_dict(7, "Magic constant 0.42 needs a name", severity="medium")],
                )
            )
        return codex_ok(critique_dict("approve", []))

    await core.run(
        p.RunConfig(prompt="x", max_revisions=1),
        tmp_git_repo,
        writer_invoke=fake_writer,
        reviewer_invoke=fake_reviewer,
    )

    # captured[1] is the revision prompt
    assert len(captured) >= 2
    revision_prompt = captured[1]
    assert "Magic constant 0.42 needs a name" in revision_prompt
    assert '"id": 7' in revision_prompt


@pytest.mark.asyncio
async def test_prompt_threading_writer_rationale_reaches_rebuttal(tmp_git_repo: Path) -> None:
    """The rebuttal prompt must contain the writer's rationale for rejected items."""
    captured: list[str] = []

    writer_calls = 0

    async def fake_writer(prompt, cfg, cwd, schema, perm, timeout):
        nonlocal writer_calls
        writer_calls += 1
        if writer_calls == 1:
            (cwd / "main.py").write_text("modified\n")
            return claude_ok(writer_report_dict())
        return claude_ok(
            writer_responses_dict(
                [
                    {
                        "item_id": 1,
                        "action": "reject",
                        "rationale": "WONTFIX: the magic number is documented in the spec",
                    }
                ]
            )
        )

    rebuttal_prompt_holder: list[str] = []
    reviewer_calls = 0

    async def fake_reviewer_v2(prompt, cfg, cwd, schema, sandbox, timeout):
        nonlocal reviewer_calls
        reviewer_calls += 1
        if reviewer_calls == 1:
            return codex_ok(
                critique_dict(
                    "revise",
                    [critique_item_dict(1, "Magic number 42 needs a name", severity="medium")],
                )
            )
        rebuttal_prompt_holder.append(prompt)
        return codex_ok(
            rebuttal_dict(
                "approve_with_dissent",
                [{"item_id": 1, "verdict": "accept_writer_rationale"}],
            )
        )

    await core.run(
        p.RunConfig(prompt="x", max_revisions=1),
        tmp_git_repo,
        writer_invoke=fake_writer,
        reviewer_invoke=fake_reviewer_v2,
    )

    assert len(rebuttal_prompt_holder) == 1
    rebuttal_prompt = rebuttal_prompt_holder[0]
    assert "WONTFIX: the magic number is documented in the spec" in rebuttal_prompt
    # And the original critique should still be in context
    assert "Magic number 42" in rebuttal_prompt


@pytest.mark.asyncio
async def test_arbitration_resolves_disputes(tmp_git_repo: Path) -> None:
    """User arbitration moves run from AWAITING_ARBITRATION to AWAITING_APPROVAL."""
    # Reproduce a disputed run
    writer_calls = 0

    async def fake_writer(prompt, cfg, cwd, schema, perm, timeout):
        nonlocal writer_calls
        writer_calls += 1
        if writer_calls == 1:
            (cwd / "main.py").write_text("changed\n")
            return claude_ok(writer_report_dict())
        return claude_ok(
            writer_responses_dict([{"item_id": 1, "action": "reject", "rationale": "no"}])
        )

    reviewer_calls = 0

    async def fake_reviewer(prompt, cfg, cwd, schema, sandbox, timeout):
        nonlocal reviewer_calls
        reviewer_calls += 1
        if reviewer_calls == 1:
            return codex_ok(
                critique_dict("revise", [critique_item_dict(1, "wrong", severity="high")])
            )
        return codex_ok(
            rebuttal_dict(
                "still_disputed",
                [{"item_id": 1, "verdict": "still_disputed", "rebuttal_reasoning": "still wrong"}],
            )
        )

    result = await core.run(
        p.RunConfig(prompt="x", max_revisions=1),
        tmp_git_repo,
        writer_invoke=fake_writer,
        reviewer_invoke=fake_reviewer,
    )
    assert result.status == p.RunStatus.AWAITING_ARBITRATION
    assert len(result.disputed_items) == 1

    decisions = [
        p.ArbitrationDecision(item_id=1, choice=p.ArbitrationChoice.ACCEPT_WRITER, note="ok")
    ]
    resolved = await core.resume_with_arbitration(result.run_id, decisions, tmp_git_repo)

    assert resolved.status == p.RunStatus.AWAITING_APPROVAL
    assert resolved.disputed_items == []
    assert len(resolved.acknowledged_dissents) == 1
    assert len(resolved.arbitration) == 1


@pytest.mark.asyncio
async def test_arbitration_missing_decision_errors(tmp_git_repo: Path) -> None:
    """Arbitration must cover every disputed item; missing → error."""
    writer_calls = 0

    async def fake_writer(prompt, cfg, cwd, schema, perm, timeout):
        nonlocal writer_calls
        writer_calls += 1
        if writer_calls == 1:
            (cwd / "main.py").write_text("changed\n")
            return claude_ok(writer_report_dict())
        return claude_ok(
            writer_responses_dict(
                [
                    {"item_id": 1, "action": "reject", "rationale": "a"},
                    {"item_id": 2, "action": "reject", "rationale": "b"},
                ]
            )
        )

    reviewer_calls = 0

    async def fake_reviewer(prompt, cfg, cwd, schema, sandbox, timeout):
        nonlocal reviewer_calls
        reviewer_calls += 1
        if reviewer_calls == 1:
            return codex_ok(
                critique_dict(
                    "revise",
                    [
                        critique_item_dict(1, "x", severity="high"),
                        critique_item_dict(2, "y", severity="high"),
                    ],
                )
            )
        return codex_ok(
            rebuttal_dict(
                "still_disputed",
                [
                    {"item_id": 1, "verdict": "still_disputed", "rebuttal_reasoning": "no"},
                    {"item_id": 2, "verdict": "still_disputed", "rebuttal_reasoning": "no"},
                ],
            )
        )

    result = await core.run(
        p.RunConfig(prompt="x", max_revisions=1),
        tmp_git_repo,
        writer_invoke=fake_writer,
        reviewer_invoke=fake_reviewer,
    )
    assert result.status == p.RunStatus.AWAITING_ARBITRATION
    assert len(result.disputed_items) == 2

    # Only resolve item 1, omit item 2
    decisions = [p.ArbitrationDecision(item_id=1, choice=p.ArbitrationChoice.ACCEPT_WRITER)]
    with pytest.raises(RuntimeError, match="Missing arbitration"):
        await core.resume_with_arbitration(result.run_id, decisions, tmp_git_repo)


@pytest.mark.asyncio
async def test_persisted_run_record_loads_back(tmp_git_repo: Path) -> None:
    """RunResult is persisted to disk and can be rehydrated by run_id."""

    async def fake_writer(prompt, cfg, cwd, schema, perm, timeout):
        (cwd / "main.py").write_text("changed\n")
        return claude_ok(writer_report_dict())

    async def fake_reviewer(prompt, cfg, cwd, schema, sandbox, timeout):
        return codex_ok(critique_dict("approve", []))

    result = await core.run(
        p.RunConfig(prompt="x"),
        tmp_git_repo,
        writer_invoke=fake_writer,
        reviewer_invoke=fake_reviewer,
    )
    loaded = core.load_run_record(result.run_id, tmp_git_repo)
    assert loaded.run_id == result.run_id
    assert loaded.status == result.status
    assert loaded.diff == result.diff


@pytest.mark.asyncio
async def test_apply_uncommitted_modifies_working_tree(tmp_git_repo: Path) -> None:
    """The default apply_mode places changes as uncommitted modifications on the current branch."""

    async def fake_writer(prompt, cfg, cwd, schema, perm, timeout):
        (cwd / "main.py").write_text("def greet(name):\n    return f'hi {name}'\n")
        return claude_ok(writer_report_dict())

    async def fake_reviewer(prompt, cfg, cwd, schema, sandbox, timeout):
        return codex_ok(critique_dict("approve", []))

    result = await core.run(
        p.RunConfig(prompt="change hello to hi"),
        tmp_git_repo,
        writer_invoke=fake_writer,
        reviewer_invoke=fake_reviewer,
    )
    applied = core.apply_run_result(result, tmp_git_repo)
    assert applied.status == p.RunStatus.SUCCESS
    main_py = (tmp_git_repo / "main.py").read_text()
    assert "f'hi {name}'" in main_py
    # Should be uncommitted
    import subprocess

    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=tmp_git_repo, capture_output=True, text=True
    ).stdout
    assert "M  main.py" in status or " M main.py" in status


@pytest.mark.asyncio
async def test_apply_branch_mode_creates_branch_and_commits(tmp_git_repo: Path) -> None:
    async def fake_writer(prompt, cfg, cwd, schema, perm, timeout):
        (cwd / "main.py").write_text("def greet(name):\n    return f'hi {name}'\n")
        return claude_ok(writer_report_dict())

    async def fake_reviewer(prompt, cfg, cwd, schema, sandbox, timeout):
        return codex_ok(critique_dict("approve", []))

    cfg = p.RunConfig(prompt="x", apply_mode=p.ApplyMode.BRANCH, branch_name="dialectic/test")
    result = await core.run(
        cfg, tmp_git_repo, writer_invoke=fake_writer, reviewer_invoke=fake_reviewer
    )
    applied = core.apply_run_result(result, tmp_git_repo)
    assert applied.status == p.RunStatus.SUCCESS

    import subprocess

    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=tmp_git_repo,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert branch == "dialectic/test"


@pytest.mark.asyncio
async def test_reject_run_persists_status_without_touching_tree(tmp_git_repo: Path) -> None:
    async def fake_writer(prompt, cfg, cwd, schema, perm, timeout):
        (cwd / "main.py").write_text("def greet(name):\n    return f'hi {name}'\n")
        return claude_ok(writer_report_dict())

    async def fake_reviewer(prompt, cfg, cwd, schema, sandbox, timeout):
        return codex_ok(critique_dict("approve", []))

    original = (tmp_git_repo / "main.py").read_text()
    result = await core.run(
        p.RunConfig(prompt="x"),
        tmp_git_repo,
        writer_invoke=fake_writer,
        reviewer_invoke=fake_reviewer,
    )
    rejected = core.reject_run_result(result, tmp_git_repo)
    assert rejected.status == p.RunStatus.REJECTED_BY_USER
    assert (tmp_git_repo / "main.py").read_text() == original


@pytest.mark.asyncio
async def test_failed_writer_yields_failed_status(tmp_git_repo: Path) -> None:
    async def fake_writer(prompt, cfg, cwd, schema, perm, timeout):
        return ClaudeResult(raw_text="", is_error=True, error="simulated crash")

    async def fake_reviewer(prompt, cfg, cwd, schema, sandbox, timeout):
        return codex_ok(critique_dict("approve", []))

    result = await core.run(
        p.RunConfig(prompt="x"),
        tmp_git_repo,
        writer_invoke=fake_writer,
        reviewer_invoke=fake_reviewer,
    )
    assert result.status == p.RunStatus.FAILED
    assert "simulated crash" in (result.error or "")


@pytest.mark.asyncio
async def test_audit_log_captures_every_invocation(tmp_git_repo: Path) -> None:
    """The per-run prompts.jsonl audit log records every agent call with full prompts & responses."""
    writer_calls = 0

    async def fake_writer(prompt, cfg, cwd, schema, perm, timeout):
        nonlocal writer_calls
        writer_calls += 1
        if writer_calls == 1:
            (cwd / "main.py").write_text("modified\n")
            return claude_ok(writer_report_dict())
        return claude_ok(
            writer_responses_dict(
                [{"item_id": 1, "action": "reject", "rationale": "stylistic preference"}]
            )
        )

    reviewer_calls = 0

    async def fake_reviewer(prompt, cfg, cwd, schema, sandbox, timeout):
        nonlocal reviewer_calls
        reviewer_calls += 1
        if reviewer_calls == 1:
            return codex_ok(critique_dict("revise", [critique_item_dict(1, "Use better naming")]))
        return codex_ok(
            rebuttal_dict(
                "approve_with_dissent",
                [{"item_id": 1, "verdict": "accept_writer_rationale"}],
            )
        )

    result = await core.run(
        p.RunConfig(prompt="x", max_revisions=1),
        tmp_git_repo,
        writer_invoke=fake_writer,
        reviewer_invoke=fake_reviewer,
    )

    audit_path = tmp_git_repo / ".dialectic" / "runs" / f"{result.run_id}.prompts.jsonl"
    assert audit_path.exists()
    lines = [json.loads(line) for line in audit_path.read_text().splitlines() if line.strip()]
    # Expected phases: writer_initial, reviewer_critique, writer_response, reviewer_rebuttal
    phases = [entry["phase"] for entry in lines]
    assert phases == [
        "writer_initial",
        "reviewer_critique",
        "writer_response",
        "reviewer_rebuttal",
    ]
    # Each entry has full prompt + structured response
    for entry in lines:
        assert "prompt" in entry and isinstance(entry["prompt"], str) and entry["prompt"]
        assert "response" in entry
        assert "cost_usd" in entry
        assert "round" in entry and entry["round"] >= 1
        assert entry["role"] in ("writer", "reviewer")
    # The rebuttal entry's prompt must contain the writer's rationale
    assert "stylistic preference" in lines[3]["prompt"]


@pytest.mark.asyncio
async def test_invariant_duplicate_critique_ids_caught(tmp_git_repo: Path) -> None:
    async def fake_writer(prompt, cfg, cwd, schema, perm, timeout):
        (cwd / "main.py").write_text("changed\n")
        return claude_ok(writer_report_dict())

    async def fake_reviewer(prompt, cfg, cwd, schema, sandbox, timeout):
        return codex_ok(
            critique_dict(
                "revise",
                [critique_item_dict(1, "first"), critique_item_dict(1, "duplicate id!")],
            )
        )

    result = await core.run(
        p.RunConfig(prompt="x", max_revisions=1),
        tmp_git_repo,
        writer_invoke=fake_writer,
        reviewer_invoke=fake_reviewer,
    )
    assert result.status == p.RunStatus.FAILED
    assert "duplicate" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_invariant_writer_missing_response_caught(tmp_git_repo: Path) -> None:
    writer_calls = 0

    async def fake_writer(prompt, cfg, cwd, schema, perm, timeout):
        nonlocal writer_calls
        writer_calls += 1
        if writer_calls == 1:
            (cwd / "main.py").write_text("changed\n")
            return claude_ok(writer_report_dict())
        # Critique had items 1 and 2; writer only responds to 1
        return claude_ok(
            writer_responses_dict([{"item_id": 1, "action": "accept", "change_summary": "fixed"}])
        )

    async def fake_reviewer(prompt, cfg, cwd, schema, sandbox, timeout):
        return codex_ok(
            critique_dict(
                "revise",
                [critique_item_dict(1, "x"), critique_item_dict(2, "y")],
            )
        )

    result = await core.run(
        p.RunConfig(prompt="x", max_revisions=1),
        tmp_git_repo,
        writer_invoke=fake_writer,
        reviewer_invoke=fake_reviewer,
    )
    assert result.status == p.RunStatus.FAILED
    assert "[2]" in (result.error or "")


@pytest.mark.asyncio
async def test_stream_events_emitted_in_expected_order(tmp_git_repo: Path) -> None:
    """on_event callback fires for RUN_STARTED → WRITER → REVIEWER → RUN_FINISHED."""
    events: list[p.StreamEvent] = []

    async def fake_writer(prompt, cfg, cwd, schema, perm, timeout):
        (cwd / "main.py").write_text("changed\n")
        return claude_ok(writer_report_dict())

    async def fake_reviewer(prompt, cfg, cwd, schema, sandbox, timeout):
        return codex_ok(critique_dict("approve", []))

    await core.run(
        p.RunConfig(prompt="x"),
        tmp_git_repo,
        writer_invoke=fake_writer,
        reviewer_invoke=fake_reviewer,
        on_event=events.append,
    )

    event_types = [e.event_type for e in events]
    assert p.EventType.RUN_STARTED in event_types
    assert p.EventType.WRITER_STARTED in event_types
    assert p.EventType.WRITER_DONE in event_types
    assert p.EventType.REVIEWER_STARTED in event_types
    assert p.EventType.REVIEWER_DONE in event_types
    assert p.EventType.RUN_FINISHED in event_types
    # First event is RUN_STARTED, last is RUN_FINISHED
    assert events[0].event_type == p.EventType.RUN_STARTED
    assert events[-1].event_type == p.EventType.RUN_FINISHED
    # Writer events come before reviewer events
    assert event_types.index(p.EventType.WRITER_STARTED) < event_types.index(
        p.EventType.REVIEWER_STARTED
    )


@pytest.mark.asyncio
async def test_stream_events_include_revision_and_rebuttal(tmp_git_repo: Path) -> None:
    events: list[p.StreamEvent] = []
    writer_calls = 0

    async def fake_writer(prompt, cfg, cwd, schema, perm, timeout):
        nonlocal writer_calls
        writer_calls += 1
        if writer_calls == 1:
            (cwd / "main.py").write_text("changed\n")
            return claude_ok(writer_report_dict())
        return claude_ok(
            writer_responses_dict([{"item_id": 1, "action": "reject", "rationale": "no"}])
        )

    reviewer_calls = 0

    async def fake_reviewer(prompt, cfg, cwd, schema, sandbox, timeout):
        nonlocal reviewer_calls
        reviewer_calls += 1
        if reviewer_calls == 1:
            return codex_ok(
                critique_dict("revise", [critique_item_dict(1, "x", severity="medium")])
            )
        return codex_ok(
            rebuttal_dict(
                "approve_with_dissent",
                [{"item_id": 1, "verdict": "accept_writer_rationale"}],
            )
        )

    await core.run(
        p.RunConfig(prompt="x", max_revisions=1),
        tmp_git_repo,
        writer_invoke=fake_writer,
        reviewer_invoke=fake_reviewer,
        on_event=events.append,
    )

    event_types = [e.event_type for e in events]
    assert p.EventType.REVISION_STARTED in event_types
    assert p.EventType.REVISION_DONE in event_types
    assert p.EventType.REBUTTAL_STARTED in event_types
    assert p.EventType.REBUTTAL_DONE in event_types


@pytest.mark.asyncio
async def test_max_revisions_two_full_iteration(tmp_git_repo: Path) -> None:
    """max_revisions=2: round1 revise→accept-all → round2 revise→accept-all → round3 approve."""
    writer_calls = 0

    async def fake_writer(prompt, cfg, cwd, schema, perm, timeout):
        nonlocal writer_calls
        writer_calls += 1
        if writer_calls == 1:
            (cwd / "main.py").write_text("v1\n")
            return claude_ok(writer_report_dict())
        elif writer_calls == 2:
            (cwd / "main.py").write_text("v2\n")
            return claude_ok(
                writer_responses_dict([{"item_id": 1, "action": "accept", "change_summary": "v2"}])
            )
        else:
            (cwd / "main.py").write_text("v3\n")
            return claude_ok(
                writer_responses_dict([{"item_id": 2, "action": "accept", "change_summary": "v3"}])
            )

    reviewer_calls = 0

    async def fake_reviewer(prompt, cfg, cwd, schema, sandbox, timeout):
        nonlocal reviewer_calls
        reviewer_calls += 1
        if reviewer_calls == 1:
            return codex_ok(critique_dict("revise", [critique_item_dict(1, "round 1 issue")]))
        elif reviewer_calls == 2:
            return codex_ok(critique_dict("revise", [critique_item_dict(2, "round 2 issue")]))
        else:
            return codex_ok(critique_dict("approve", []))

    cfg = p.RunConfig(prompt="x", max_revisions=2)
    result = await core.run(
        cfg, tmp_git_repo, writer_invoke=fake_writer, reviewer_invoke=fake_reviewer
    )

    assert result.status == p.RunStatus.AWAITING_APPROVAL
    assert len(result.rounds) == 3
    assert result.rounds[0].reviewer_critique.verdict == p.ReviewerVerdict.REVISE
    assert result.rounds[1].reviewer_critique.verdict == p.ReviewerVerdict.REVISE
    assert result.rounds[2].reviewer_critique.verdict == p.ReviewerVerdict.APPROVE
    # Writer called: initial + revision1 + revision2 = 3 (NOT 4 — the refactor
    # removed the wasted top-of-loop writer call)
    assert writer_calls == 3
    # Items accepted across rounds 1+2 should both be in resolved
    assert len(result.resolved_items) >= 2
    assert "v3" in result.diff


@pytest.mark.asyncio
async def test_max_revisions_exhausted_lists_unresolved(tmp_git_repo: Path) -> None:
    """max_revisions=0: critique items raised → status=AWAITING_ARBITRATION with unresolved items."""

    async def fake_writer(prompt, cfg, cwd, schema, perm, timeout):
        (cwd / "main.py").write_text("changed\n")
        return claude_ok(writer_report_dict())

    async def fake_reviewer(prompt, cfg, cwd, schema, sandbox, timeout):
        return codex_ok(
            critique_dict(
                "revise",
                [critique_item_dict(1, "x", severity="high"), critique_item_dict(2, "y")],
            )
        )

    result = await core.run(
        p.RunConfig(prompt="x", max_revisions=0),
        tmp_git_repo,
        writer_invoke=fake_writer,
        reviewer_invoke=fake_reviewer,
    )
    assert result.status == p.RunStatus.AWAITING_ARBITRATION
    assert len(result.unresolved_items) == 2
    assert {item.id for item in result.unresolved_items} == {1, 2}
    assert result.disputed_items == []


@pytest.mark.asyncio
async def test_reviewer_outright_reject_marks_failed(tmp_git_repo: Path) -> None:
    async def fake_writer(prompt, cfg, cwd, schema, perm, timeout):
        (cwd / "main.py").write_text("bad\n")
        return claude_ok(writer_report_dict())

    async def fake_reviewer(prompt, cfg, cwd, schema, sandbox, timeout):
        return codex_ok(critique_dict("reject", [], summary="approach is fundamentally wrong"))

    result = await core.run(
        p.RunConfig(prompt="x"),
        tmp_git_repo,
        writer_invoke=fake_writer,
        reviewer_invoke=fake_reviewer,
    )
    assert result.status == p.RunStatus.FAILED
    assert "rejected outright" in (result.error or "")


@pytest.mark.asyncio
async def test_writer_crashes_after_reviewer_succeeded_partial_state(tmp_git_repo: Path) -> None:
    """Failure cascade: writer-response crashes after reviewer's initial critique succeeded.

    The result should be FAILED but the first round's writer_report and reviewer_critique
    should still be captured in result.rounds for forensics.
    """
    writer_calls = 0

    async def fake_writer(prompt, cfg, cwd, schema, perm, timeout):
        nonlocal writer_calls
        writer_calls += 1
        if writer_calls == 1:
            (cwd / "main.py").write_text("v1\n")
            return claude_ok(writer_report_dict())
        return ClaudeResult(raw_text="", is_error=True, error="writer crashed mid-revision")

    async def fake_reviewer(prompt, cfg, cwd, schema, sandbox, timeout):
        return codex_ok(critique_dict("revise", [critique_item_dict(1, "x")]))

    result = await core.run(
        p.RunConfig(prompt="x", max_revisions=1),
        tmp_git_repo,
        writer_invoke=fake_writer,
        reviewer_invoke=fake_reviewer,
    )
    assert result.status == p.RunStatus.FAILED
    assert "writer crashed" in (result.error or "")
    # Partial trajectory still in audit
    assert len(result.rounds) == 0  # round didn't make it into the list (crashed before append)
    # But the audit log captured the writer's initial report AND the reviewer's critique
    audit = tmp_git_repo / ".dialectic" / "runs" / f"{result.run_id}.prompts.jsonl"
    assert audit.exists()
    entries = [json.loads(l) for l in audit.read_text().splitlines() if l.strip()]
    phases = [e["phase"] for e in entries]
    assert "writer_initial" in phases
    assert "reviewer_critique" in phases
    assert "writer_response" in phases  # the failing call IS still logged


@pytest.mark.asyncio
async def test_dry_run_preserves_awaiting_approval(tmp_git_repo: Path) -> None:
    """dry_run + auto-approve doesn't flip status to SUCCESS, so a later `approve` still works."""

    async def fake_writer(prompt, cfg, cwd, schema, perm, timeout):
        (cwd / "main.py").write_text("def greet(name):\n    return f'hi {name}'\n")
        return claude_ok(writer_report_dict())

    async def fake_reviewer(prompt, cfg, cwd, schema, sandbox, timeout):
        return codex_ok(critique_dict("approve", []))

    cfg = p.RunConfig(prompt="x", apply_mode=p.ApplyMode.DRY_RUN)
    result = await core.run(
        cfg, tmp_git_repo, writer_invoke=fake_writer, reviewer_invoke=fake_reviewer
    )
    applied = core.apply_run_result(result, tmp_git_repo)
    # Dry run should keep status as AWAITING_APPROVAL so user can change apply_mode
    # and approve for real.
    assert applied.status == p.RunStatus.AWAITING_APPROVAL


@pytest.mark.asyncio
async def test_base_sha_captured_not_re_resolved(tmp_git_repo: Path) -> None:
    """RunResult.base_sha is the SHA at run start; apply uses it (not re-resolves HEAD)."""
    import subprocess

    async def fake_writer(prompt, cfg, cwd, schema, perm, timeout):
        (cwd / "main.py").write_text("v1\n")
        return claude_ok(writer_report_dict())

    async def fake_reviewer(prompt, cfg, cwd, schema, sandbox, timeout):
        return codex_ok(critique_dict("approve", []))

    original_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_git_repo, capture_output=True, text=True
    ).stdout.strip()

    result = await core.run(
        p.RunConfig(prompt="x"),
        tmp_git_repo,
        writer_invoke=fake_writer,
        reviewer_invoke=fake_reviewer,
    )
    assert result.base_sha == original_sha

    # Now move HEAD by committing something else.
    (tmp_git_repo / "other.py").write_text("other\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_git_repo, check=True)
    subprocess.run(["git", "commit", "-qm", "another commit"], cwd=tmp_git_repo, check=True)

    with pytest.raises(RuntimeError, match="HEAD moved"):
        core.apply_run_result(result, tmp_git_repo)


@pytest.mark.asyncio
async def test_load_run_record_rejects_path_traversal(tmp_git_repo: Path) -> None:
    """Security regression: malformed run_ids must be rejected before disk access."""
    with pytest.raises(ValueError, match="Invalid run_id"):
        core.load_run_record("../../etc/passwd", tmp_git_repo)
    with pytest.raises(ValueError, match="Invalid run_id"):
        core.load_run_record("not_a_run_id", tmp_git_repo)
    with pytest.raises(ValueError, match="Invalid run_id"):
        core.load_run_record("20260517-120000-XYZ123", tmp_git_repo)  # uppercase not hex


@pytest.mark.asyncio
async def test_diff_exceeds_max_lines_aborts(tmp_git_repo: Path) -> None:
    async def fake_writer(prompt, cfg, cwd, schema, perm, timeout):
        # Write a huge file
        (cwd / "huge.py").write_text("\n".join(f"line {i}" for i in range(2000)))
        return claude_ok(writer_report_dict())

    async def fake_reviewer(prompt, cfg, cwd, schema, sandbox, timeout):
        return codex_ok(critique_dict("approve", []))

    result = await core.run(
        p.RunConfig(prompt="x", max_diff_lines=100),
        tmp_git_repo,
        writer_invoke=fake_writer,
        reviewer_invoke=fake_reviewer,
    )
    assert result.status == p.RunStatus.FAILED
    assert "max_diff_lines" in (result.error or "")
