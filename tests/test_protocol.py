"""Unit tests for protocol validators, enums, and pydantic correctness."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dialectic import protocol as p


class TestCritiqueItem:
    def test_lines_without_file_rejects(self) -> None:
        with pytest.raises(ValidationError, match="lines requires file"):
            p.CritiqueItem(id=1, severity=p.Severity.HIGH, lines="42-48", issue="x")

    def test_lines_with_file_ok(self) -> None:
        item = p.CritiqueItem(id=1, severity=p.Severity.HIGH, file="x.py", lines="42-48", issue="x")
        assert item.lines == "42-48"

    def test_default_category_is_other(self) -> None:
        item = p.CritiqueItem(id=1, severity=p.Severity.LOW, issue="x")
        assert item.categories == [p.Category.OTHER]

    def test_multiple_categories_allowed(self) -> None:
        item = p.CritiqueItem(
            id=1,
            severity=p.Severity.HIGH,
            categories=[p.Category.SECURITY, p.Category.PERFORMANCE, p.Category.CORRECTNESS],
            issue="x",
        )
        assert len(item.categories) == 3


class TestWriterItemResponse:
    def test_reject_without_rationale_rejects(self) -> None:
        with pytest.raises(ValidationError, match="rationale is required"):
            p.WriterItemResponse(item_id=1, action=p.WriterAction.REJECT)

    def test_accept_without_change_summary_rejects(self) -> None:
        with pytest.raises(ValidationError, match="change_summary is required"):
            p.WriterItemResponse(item_id=1, action=p.WriterAction.ACCEPT)

    def test_reject_with_rationale_ok(self) -> None:
        r = p.WriterItemResponse(item_id=1, action=p.WriterAction.REJECT, rationale="because")
        assert r.rationale == "because"

    def test_accept_with_change_summary_ok(self) -> None:
        r = p.WriterItemResponse(item_id=1, action=p.WriterAction.ACCEPT, change_summary="x")
        assert r.change_summary == "x"


class TestReviewerRebuttalItem:
    def test_still_disputed_without_reasoning_rejects(self) -> None:
        with pytest.raises(ValidationError, match="rebuttal_reasoning is required"):
            p.ReviewerRebuttalItem(item_id=1, verdict=p.ItemRebuttalVerdict.STILL_DISPUTED)

    def test_accept_rationale_no_reasoning_ok(self) -> None:
        r = p.ReviewerRebuttalItem(
            item_id=1, verdict=p.ItemRebuttalVerdict.ACCEPT_WRITER_RATIONALE
        )
        assert r.rebuttal_reasoning is None


class TestRunConfigEffortValidation:
    def test_codex_max_effort_rejected(self) -> None:
        with pytest.raises(ValidationError, match="not valid for cli=codex"):
            p.RunConfig(
                prompt="x",
                reviewer=p.AgentConfig(cli=p.AgentCli.CODEX, model="gpt-5.4", effort="max"),
            )

    def test_claude_max_effort_ok(self) -> None:
        cfg = p.RunConfig(
            prompt="x",
            writer=p.AgentConfig(cli=p.AgentCli.CLAUDE, model="claude-opus-4-7", effort="max"),
        )
        assert cfg.writer.effort == "max"

    def test_unknown_effort_rejected(self) -> None:
        with pytest.raises(ValidationError, match="not valid for cli="):
            p.RunConfig(
                prompt="x",
                writer=p.AgentConfig(
                    cli=p.AgentCli.CLAUDE, model="claude-opus-4-7", effort="bogus"
                ),
            )


class TestStrictMode:
    def test_extra_fields_rejected(self) -> None:
        with pytest.raises(ValidationError):
            p.CritiqueItem.model_validate(
                {"id": 1, "severity": "high", "issue": "x", "unknown_field": "y"}
            )

    def test_run_config_max_revisions_bounds(self) -> None:
        with pytest.raises(ValidationError):
            p.RunConfig(prompt="x", max_revisions=-1)
        with pytest.raises(ValidationError):
            p.RunConfig(prompt="x", max_revisions=6)


class TestRoundTrip:
    def test_run_result_round_trips(self) -> None:
        config = p.RunConfig(prompt="test")
        result = p.RunResult(run_id="abc", status=p.RunStatus.AWAITING_APPROVAL, config=config)
        as_json = result.model_dump_json()
        restored = p.RunResult.model_validate_json(as_json)
        assert restored.run_id == "abc"
        assert restored.status == p.RunStatus.AWAITING_APPROVAL

    def test_revision_round_serialises(self) -> None:
        round_obj = p.RevisionRound(
            round_number=1,
            writer_report=p.WriterReport(diff="", summary="x"),
            reviewer_critique=p.ReviewerCritique(verdict=p.ReviewerVerdict.APPROVE, summary="y"),
        )
        as_json = round_obj.model_dump_json()
        restored = p.RevisionRound.model_validate_json(as_json)
        assert restored.round_number == 1
        assert restored.reviewer_critique.verdict == p.ReviewerVerdict.APPROVE
