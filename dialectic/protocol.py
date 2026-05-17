"""Pydantic models defining the contract between orchestrator and subagents.

Every subagent invocation (writer, reviewer, revision, rebuttal) sends and receives
one of these. They are passed to the CLIs as `--json-schema` / `--output-schema` so
the LLMs are forced to produce structured output the orchestrator can parse without
prose-fishing.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


# ──────────────────────────────────────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────────────────────────────────────


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class Category(str, Enum):
    CORRECTNESS = "correctness"
    SECURITY = "security"
    PERFORMANCE = "performance"
    ARCHITECTURE = "architecture"
    TESTS = "tests"
    STYLE = "style"
    OTHER = "other"


class ReviewerVerdict(str, Enum):
    APPROVE = "approve"
    REVISE = "revise"
    REJECT = "reject"


class WriterAction(str, Enum):
    ACCEPT = "accept"
    REJECT = "reject"


class RebuttalVerdict(str, Enum):
    APPROVE = "approve"
    APPROVE_WITH_DISSENT = "approve_with_dissent"
    STILL_DISPUTED = "still_disputed"


class AgentCli(str, Enum):
    CLAUDE = "claude"
    CODEX = "codex"


class ApplyMode(str, Enum):
    UNCOMMITTED = "uncommitted"
    BRANCH = "branch"
    DRY_RUN = "dry_run"


class RunStatus(str, Enum):
    SUCCESS = "success"
    REJECTED_BY_USER = "rejected_by_user"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_ARBITRATION = "awaiting_arbitration"


# ──────────────────────────────────────────────────────────────────────────────
# Reviewer → orchestrator
# ──────────────────────────────────────────────────────────────────────────────


class CritiqueItem(BaseModel):
    id: int = Field(description="Stable id within this critique; writer references by id.")
    severity: Severity
    categories: list[Category] = Field(
        default_factory=lambda: [Category.OTHER],
        description="One or more categories. A single item can be both e.g. security and performance.",
    )
    file: str | None = Field(default=None, description="Path relative to repo root.")
    lines: str | None = Field(
        default=None,
        description="Line range (e.g., '42-48') or pattern. None for non-line-specific issues.",
    )
    issue: str = Field(description="What's wrong.")
    suggested_fix: str | None = Field(default=None, description="Optional concrete fix.")


class ReviewerCritique(BaseModel):
    """Reviewer's structured output after seeing the writer's diff."""

    verdict: ReviewerVerdict
    items: list[CritiqueItem] = Field(default_factory=list)
    summary: str = Field(description="One-paragraph overall assessment.")


# ──────────────────────────────────────────────────────────────────────────────
# Writer → orchestrator (revision pass)
# ──────────────────────────────────────────────────────────────────────────────


class WriterItemResponse(BaseModel):
    """Writer's response to one critique item: either accept (and revise) or reject (with rationale)."""

    item_id: int
    action: WriterAction
    rationale: str | None = Field(
        default=None,
        description="Required when action=reject. Why the writer disagrees.",
    )
    change_summary: str | None = Field(
        default=None,
        description="When action=accept, brief note about what was changed.",
    )


class WriterResponseBundle(BaseModel):
    """Writer's full response across all critique items, plus updated diff."""

    responses: list[WriterItemResponse]
    revised_diff_summary: str = Field(
        description="One-paragraph description of what the revision actually changed."
    )


# ──────────────────────────────────────────────────────────────────────────────
# Reviewer → orchestrator (rebuttal pass)
# ──────────────────────────────────────────────────────────────────────────────


class ReviewerRebuttalItem(BaseModel):
    """Reviewer's verdict on one writer-defended (rejected) item."""

    item_id: int
    verdict: Literal["accept_writer_rationale", "still_disputed"]
    rebuttal_reasoning: str | None = Field(
        default=None,
        description="Required when verdict=still_disputed. Why the writer's rationale doesn't hold.",
    )


class ReviewerRebuttal(BaseModel):
    """Reviewer's final pass after the writer's per-item responses."""

    verdict: RebuttalVerdict
    item_rebuttals: list[ReviewerRebuttalItem] = Field(default_factory=list)
    summary: str = Field(description="One-paragraph wrap-up.")


# ──────────────────────────────────────────────────────────────────────────────
# Run configuration & result (orchestrator-level)
# ──────────────────────────────────────────────────────────────────────────────


class AgentConfig(BaseModel):
    cli: AgentCli
    model: str
    effort: str = Field(default="max", description="low | medium | high | xhigh | max")


class RunConfig(BaseModel):
    prompt: str
    writer: AgentConfig = AgentConfig(cli=AgentCli.CLAUDE, model="claude-opus-4-7", effort="max")
    reviewer: AgentConfig = AgentConfig(cli=AgentCli.CODEX, model="gpt-5.4", effort="xhigh")
    max_revisions: int = Field(default=1, ge=0, le=5)
    timeout_per_agent_s: int = Field(default=1500, description="25 minutes default.")
    max_diff_lines: int = Field(default=1000)
    apply_mode: ApplyMode = ApplyMode.UNCOMMITTED
    branch_name: str | None = None
    base_ref: str = Field(default="HEAD", description="Git ref to branch worktrees from.")
    sandbox: str = Field(default="workspace-write")
    context_file: Path | None = Field(
        default=None, description="Defaults to .dialectic/context.md if present."
    )


class DisputedItem(BaseModel):
    """A critique item that writer + reviewer could not resolve. Escalated to user."""

    item: CritiqueItem
    writer_rationale: str
    reviewer_rebuttal: str


class AcknowledgedDissent(BaseModel):
    """Writer rejected an item; reviewer accepted the writer's rationale. Ships, noted in log."""

    item: CritiqueItem
    writer_rationale: str


class RunResult(BaseModel):
    """End-of-run structured output. Presented to the user for approval."""

    run_id: str
    status: RunStatus
    config: RunConfig

    diff: str = Field(default="", description="Final consensus unified diff against base_ref.")
    files_changed: list[str] = Field(default_factory=list)

    revisions_used: int = 0
    resolved_items: list[CritiqueItem] = Field(default_factory=list)
    disputed_items: list[DisputedItem] = Field(default_factory=list)
    acknowledged_dissents: list[AcknowledgedDissent] = Field(default_factory=list)

    summary: str = Field(default="", description="Human-readable wrap-up shown at approval time.")

    cost_usd: float = 0.0
    duration_s: float = 0.0
    started_at: datetime | None = None
    finished_at: datetime | None = None

    audit_log_path: str | None = None
    error: str | None = None


# ──────────────────────────────────────────────────────────────────────────────
# Streaming events (orchestrator → client)
# ──────────────────────────────────────────────────────────────────────────────


class EventType(str, Enum):
    RUN_STARTED = "run_started"
    WRITER_STARTED = "writer_started"
    WRITER_PROGRESS = "writer_progress"
    WRITER_DONE = "writer_done"
    REVIEWER_STARTED = "reviewer_started"
    REVIEWER_PROGRESS = "reviewer_progress"
    REVIEWER_DONE = "reviewer_done"
    REVISION_STARTED = "revision_started"
    REVISION_DONE = "revision_done"
    REBUTTAL_DONE = "rebuttal_done"
    DIFF_READY = "diff_ready"
    AWAITING_APPROVAL = "awaiting_approval"
    APPLIED = "applied"
    REJECTED = "rejected"
    ERROR = "error"
    RUN_FINISHED = "run_finished"


class StreamEvent(BaseModel):
    event_type: EventType
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    run_id: str
    message: str = ""
    payload: dict = Field(default_factory=dict)
