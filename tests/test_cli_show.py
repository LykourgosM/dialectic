"""Tests for the `dialectic show <run-id>` subcommand."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner
from rich.console import Console

from dialectic import cli as cli_mod
from dialectic import core
from dialectic.cli import main
from dialectic.protocol import (
    Category,
    CritiqueItem,
    ItemRebuttalVerdict,
    RebuttalVerdict,
    ReviewerCritique,
    ReviewerRebuttal,
    ReviewerRebuttalItem,
    ReviewerVerdict,
    RevisionRound,
    RunConfig,
    RunResult,
    RunStatus,
    Severity,
    WriterAction,
    WriterApproach,
    WriterConfidence,
    WriterItemResponse,
    WriterReport,
    WriterResponseBundle,
)


@pytest.fixture
def wide_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the CLI's rich console to wide non-terminal output so Tree rendering
    is deterministic regardless of the host terminal's width."""
    monkeypatch.setattr(
        cli_mod, "console", Console(width=240, force_terminal=False, no_color=True)
    )


def _build_synthetic_run(repo_root: Path) -> RunResult:
    """One round that exercises every section: writer report, critique with
    multiple items, writer responses (one accept + two rejects), and a rebuttal
    with both verdict types so rebuttal_reasoning is present."""
    writer_report = WriterReport(
        diff="--- a/x\n+++ b/x\n@@\n-old\n+new\n",
        summary="Refactored greet to support emoji.",
        approaches=[WriterApproach.REFACTOR, WriterApproach.ADD],
        confidence=WriterConfidence.HIGH,
        files_touched=["src/greet.py", "tests/test_greet.py"],
        assumptions=["Unicode handling is acceptable"],
        open_questions=["Should we add a locale parameter?"],
    )

    item_correctness = CritiqueItem(
        id=1,
        severity=Severity.HIGH,
        categories=[Category.CORRECTNESS, Category.TESTS],
        file="src/greet.py",
        lines="12-15",
        issue="Missing test coverage for empty input.",
        suggested_fix="Add an assertion that greet('') raises ValueError.",
    )
    item_style = CritiqueItem(
        id=2,
        severity=Severity.LOW,
        categories=[Category.STYLE],
        file="tests/test_greet.py",
        lines="20",
        issue="Test name could be more descriptive.",
        suggested_fix="Rename test_basic to test_greet_returns_message.",
    )
    item_security = CritiqueItem(
        id=3,
        severity=Severity.CRITICAL,
        categories=[Category.SECURITY],
        file="src/greet.py",
        lines="30-34",
        issue="Potential XSS in HTML output path.",
        suggested_fix="Escape the user-supplied name.",
    )
    critique = ReviewerCritique(
        verdict=ReviewerVerdict.REVISE,
        items=[item_correctness, item_style, item_security],
        summary="Three issues found.",
    )

    responses = WriterResponseBundle(
        responses=[
            WriterItemResponse(
                item_id=1,
                action=WriterAction.ACCEPT,
                change_summary="Added the missing assertion for empty input.",
            ),
            WriterItemResponse(
                item_id=2,
                action=WriterAction.REJECT,
                rationale="Test name follows project convention; renaming breaks consistency.",
            ),
            WriterItemResponse(
                item_id=3,
                action=WriterAction.REJECT,
                rationale="Output is markdown not HTML; escaping would corrupt the message.",
            ),
        ],
        revised_diff="--- a/x\n+++ b/x\n@@\n-old\n+newer\n",
        revised_diff_summary="Accepted item 1; defended items 2 and 3.",
    )

    rebuttal = ReviewerRebuttal(
        verdict=RebuttalVerdict.STILL_DISPUTED,
        item_rebuttals=[
            ReviewerRebuttalItem(
                item_id=2,
                verdict=ItemRebuttalVerdict.ACCEPT_WRITER_RATIONALE,
            ),
            ReviewerRebuttalItem(
                item_id=3,
                verdict=ItemRebuttalVerdict.STILL_DISPUTED,
                rebuttal_reasoning=(
                    "The output is rendered as HTML in dashboard/templates/greet.html; "
                    "this is a real XSS risk, not a false positive."
                ),
            ),
        ],
        summary="Accepted item 2; escalating item 3 to user arbitration.",
    )

    round_obj = RevisionRound(
        round_number=1,
        writer_report=writer_report,
        reviewer_critique=critique,
        writer_responses=responses,
        reviewer_rebuttal=rebuttal,
    )

    result = RunResult(
        run_id="20260517-120000-abcdef",
        status=RunStatus.AWAITING_ARBITRATION,
        config=RunConfig(prompt="Refactor greet to support emojis"),
        rounds=[round_obj],
        summary="One round, one dispute escalated to arbitration.",
        cost_usd=0.1234,
        duration_s=42.5,
    )
    core.persist_run_record(result, repo_root)
    return result


def test_show_renders_all_round_sections(tmp_path: Path, wide_console: None) -> None:
    result = _build_synthetic_run(tmp_path)

    runner = CliRunner()
    cli_result = runner.invoke(
        main, ["show", result.run_id, "--repo-root", str(tmp_path)]
    )

    assert cli_result.exit_code == 0, cli_result.output
    out = cli_result.output

    # Header
    assert result.run_id in out
    assert "awaiting_arbitration" in out

    # Round delimiter
    assert "Round 1" in out

    # Writer report fields
    assert "Refactored greet to support emoji" in out
    assert "refactor" in out  # approach
    assert "add" in out  # approach
    assert "high" in out  # confidence (also matches the HIGH severity item; both should appear)
    assert "src/greet.py" in out
    assert "tests/test_greet.py" in out
    assert "Unicode handling is acceptable" in out
    assert "Should we add a locale parameter?" in out

    # Reviewer critique: verdict + per-item fields
    assert "revise" in out
    assert "Three issues found" in out
    assert "#1" in out
    assert "#2" in out
    assert "#3" in out
    assert "correctness" in out
    assert "tests" in out
    assert "style" in out
    assert "security" in out
    assert "critical" in out
    assert "low" in out
    assert "src/greet.py:12-15" in out
    assert "tests/test_greet.py:20" in out
    assert "src/greet.py:30-34" in out
    assert "Missing test coverage for empty input" in out
    assert "Add an assertion that greet('') raises ValueError" in out
    assert "Potential XSS" in out
    assert "Escape the user-supplied name" in out

    # Writer responses: action + rationale or change_summary
    assert "accept" in out
    assert "reject" in out
    assert "Added the missing assertion for empty input" in out
    assert "Test name follows project convention" in out
    assert "Output is markdown not HTML" in out
    assert "Accepted item 1; defended items 2 and 3" in out  # revised_diff_summary

    # Reviewer rebuttal: overall verdict, per-item verdict, rebuttal_reasoning
    assert "still_disputed" in out
    assert "accept_writer_rationale" in out
    assert "dashboard/templates/greet.html" in out  # rebuttal_reasoning content
    assert "real XSS risk" in out

    # Run-level summary
    assert "One round, one dispute escalated" in out


def test_show_missing_run_id_fails_gracefully(
    tmp_path: Path, wide_console: None
) -> None:
    """File-not-found should exit nonzero with a friendly message, not a traceback."""
    runner = CliRunner()
    cli_result = runner.invoke(
        main,
        ["show", "20260517-120000-deadbe", "--repo-root", str(tmp_path)],
    )
    assert cli_result.exit_code != 0
    assert "No run record" in cli_result.output


def test_show_invalid_run_id_fails_gracefully(
    tmp_path: Path, wide_console: None
) -> None:
    """Malformed run_ids are rejected before disk access (path-traversal guard)."""
    runner = CliRunner()
    cli_result = runner.invoke(
        main,
        ["show", "../../etc/passwd", "--repo-root", str(tmp_path)],
    )
    assert cli_result.exit_code != 0
    assert "Invalid run_id" in cli_result.output
