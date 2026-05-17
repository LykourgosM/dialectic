"""Pydantic models defining the contract between orchestrator and subagents.

Every subagent invocation (writer initial, reviewer critique, writer revision,
reviewer rebuttal) sends and receives one of these. They are passed to the CLIs
as `--json-schema` (Claude) / `--output-schema` (Codex) so the LLMs are forced
to produce structured output the orchestrator can parse without prose-fishing.

All models inherit from `_Strict`, which sets `extra="forbid"` — the JSON Schema
emitted will include `additionalProperties: false` so LLMs can't hallucinate
extra keys that silently vanish.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    REJECT = "reject"  # Diff so bad it shouldn't even be revised; abort.


class WriterAction(str, Enum):
    ACCEPT = "accept"
    REJECT = "reject"


class ItemRebuttalVerdict(str, Enum):
    """Per-item verdict during the reviewer's rebuttal pass."""

    ACCEPT_WRITER_RATIONALE = "accept_writer_rationale"
    STILL_DISPUTED = "still_disputed"


class RebuttalVerdict(str, Enum):
    """Reviewer's overall verdict in the rebuttal pass."""

    APPROVE = "approve"
    APPROVE_WITH_DISSENT = "approve_with_dissent"
    STILL_DISPUTED = "still_disputed"


class WriterApproach(str, Enum):
    FIX = "fix"
    ADD = "add"
    REFACTOR = "refactor"
    OPTIMIZE = "optimize"
    REMOVE = "remove"
    OTHER = "other"


class WriterConfidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class AgentCli(str, Enum):
    CLAUDE = "claude"
    CODEX = "codex"


class ClaudeEffort(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


class CodexEffort(str, Enum):
    """Codex 0.121 accepts minimal/low/medium/high/xhigh. 'max' is NOT valid."""

    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"


class SandboxMode(str, Enum):
    READ_ONLY = "read-only"
    WORKSPACE_WRITE = "workspace-write"
    DANGER_FULL_ACCESS = "danger-full-access"


class ClaudePermissionMode(str, Enum):
    ACCEPT_EDITS = "acceptEdits"
    AUTO = "auto"
    BYPASS = "bypassPermissions"
    DEFAULT = "default"
    DONT_ASK = "dontAsk"
    PLAN = "plan"


class ApplyMode(str, Enum):
    UNCOMMITTED = "uncommitted"
    BRANCH = "branch"
    DRY_RUN = "dry_run"


class RunStatus(str, Enum):
    SUCCESS = "success"
    APPLIED_WITH_DISSENT = "applied_with_dissent"
    REJECTED_BY_USER = "rejected_by_user"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_ARBITRATION = "awaiting_arbitration"


class ArbitrationChoice(str, Enum):
    ACCEPT_WRITER = "accept_writer"  # Keep writer's code as-is (writer wins the dispute).
    ACCEPT_REVIEWER = "accept_reviewer"  # Apply the reviewer's suggested_fix.
    SKIP = "skip"  # Ship the diff but flag the item as known-unresolved in audit log.


# ──────────────────────────────────────────────────────────────────────────────
# Base model — strict by default
# ──────────────────────────────────────────────────────────────────────────────


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


# ──────────────────────────────────────────────────────────────────────────────
# Writer initial output
# ──────────────────────────────────────────────────────────────────────────────


class WriterReport(_Strict):
    """The writer's initial output: diff plus structured metadata.

    Wrapping the diff lets the reviewer see what the writer was *trying* to do,
    and gives the v1.5 auto-journal something to record beyond the raw patch.
    """

    diff: str = Field(description="Unified diff against base_ref.")
    summary: str = Field(description="One-paragraph description of what was changed and why.")
    approaches: list[WriterApproach] = Field(
        default_factory=lambda: [WriterApproach.OTHER],
        description="One or more approaches (a change can be both e.g. refactor and fix).",
    )
    confidence: WriterConfidence = WriterConfidence.MEDIUM
    files_touched: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(
        default_factory=list, description="Things the writer assumed about intent."
    )
    open_questions: list[str] = Field(
        default_factory=list, description="Things the writer is unsure about."
    )


# ──────────────────────────────────────────────────────────────────────────────
# Reviewer critique
# ──────────────────────────────────────────────────────────────────────────────


class CritiqueItem(_Strict):
    id: int = Field(description="Stable id within this critique; writer references by id.")
    severity: Severity
    categories: list[Category] = Field(
        default_factory=lambda: [Category.OTHER],
        description="One or more categories. A single item can be both e.g. security and performance.",
    )
    file: str | None = Field(default=None, description="Path relative to repo root.")
    lines: str | None = Field(
        default=None, description="Line range (e.g., '42-48'). None for non-line-specific issues."
    )
    issue: str
    suggested_fix: str | None = None

    @model_validator(mode="after")
    def _lines_requires_file(self) -> CritiqueItem:
        if self.lines is not None and self.file is None:
            raise ValueError("lines requires file to be set")
        return self


class ReviewerCritique(_Strict):
    """Reviewer's output after seeing the writer's diff."""

    reviewer_id: str | None = Field(
        default=None,
        description="Identifier for the reviewer (for v1.1 multi-reviewer panels). None in v1.",
    )
    verdict: ReviewerVerdict
    items: list[CritiqueItem] = Field(default_factory=list)
    summary: str


# ──────────────────────────────────────────────────────────────────────────────
# Writer response to critique
# ──────────────────────────────────────────────────────────────────────────────


class WriterItemResponse(_Strict):
    item_id: int
    action: WriterAction
    rationale: str | None = Field(default=None, description="Required when action=reject.")
    change_summary: str | None = Field(default=None, description="Required when action=accept.")

    @model_validator(mode="after")
    def _validate_action_fields(self) -> WriterItemResponse:
        if self.action == WriterAction.REJECT and not self.rationale:
            raise ValueError("rationale is required when action=reject")
        if self.action == WriterAction.ACCEPT and not self.change_summary:
            raise ValueError("change_summary is required when action=accept")
        return self


class WriterResponseBundle(_Strict):
    """Writer's per-item responses plus updated diff."""

    responses: list[WriterItemResponse]
    revised_diff: str = Field(description="Unified diff against base_ref after revision.")
    revised_diff_summary: str


# ──────────────────────────────────────────────────────────────────────────────
# Reviewer rebuttal
# ──────────────────────────────────────────────────────────────────────────────


class ReviewerRebuttalItem(_Strict):
    item_id: int
    verdict: ItemRebuttalVerdict
    rebuttal_reasoning: str | None = Field(
        default=None, description="Required when verdict=still_disputed."
    )

    @model_validator(mode="after")
    def _validate_disputed_requires_reasoning(self) -> ReviewerRebuttalItem:
        if self.verdict == ItemRebuttalVerdict.STILL_DISPUTED and not self.rebuttal_reasoning:
            raise ValueError("rebuttal_reasoning is required when verdict=still_disputed")
        return self


class ReviewerRebuttal(_Strict):
    reviewer_id: str | None = None
    verdict: RebuttalVerdict
    item_rebuttals: list[ReviewerRebuttalItem] = Field(default_factory=list)
    summary: str


# ──────────────────────────────────────────────────────────────────────────────
# Per-round trajectory (audit + v1.5 auto-journal)
# ──────────────────────────────────────────────────────────────────────────────


class RevisionRound(_Strict):
    """One write → review → respond → rebut cycle. RunResult holds a list of these."""

    round_number: int = Field(ge=1)
    writer_report: WriterReport
    reviewer_critique: ReviewerCritique
    writer_responses: WriterResponseBundle | None = Field(
        default=None,
        description="None when reviewer approved on initial pass (no revision needed).",
    )
    reviewer_rebuttal: ReviewerRebuttal | None = Field(
        default=None, description="None when there were no rejected items to rebut."
    )


# ──────────────────────────────────────────────────────────────────────────────
# Disputes, dissents, arbitration
# ──────────────────────────────────────────────────────────────────────────────


class DisputedItem(_Strict):
    """Writer + reviewer could not agree. Escalated to user for arbitration."""

    item: CritiqueItem
    writer_response: WriterItemResponse
    reviewer_rebuttal_item: ReviewerRebuttalItem


class AcknowledgedDissent(_Strict):
    """Writer rejected; reviewer accepted the rationale. Ships, noted in audit log."""

    item: CritiqueItem
    writer_response: WriterItemResponse


class ArbitrationDecision(_Strict):
    """User's resolution of one disputed item, supplied to `dialectic arbitrate`."""

    item_id: int
    choice: ArbitrationChoice
    note: str | None = None


# ──────────────────────────────────────────────────────────────────────────────
# Run configuration
# ──────────────────────────────────────────────────────────────────────────────


_EFFORT_BY_CLI: dict[AgentCli, type[Enum]] = {
    AgentCli.CLAUDE: ClaudeEffort,
    AgentCli.CODEX: CodexEffort,
}


class AgentConfig(_Strict):
    cli: AgentCli
    model: str
    effort: str = Field(
        default="max",
        description="Per-cli validated in RunConfig. Codex tops out at 'xhigh' — 'max' is Claude-only.",
    )


class RunConfig(_Strict):
    prompt: str
    writer: AgentConfig = Field(
        default_factory=lambda: AgentConfig(
            cli=AgentCli.CLAUDE, model="claude-opus-4-7", effort="max"
        )
    )
    reviewer: AgentConfig = Field(
        default_factory=lambda: AgentConfig(cli=AgentCli.CODEX, model="gpt-5.4", effort="xhigh")
    )
    max_revisions: int = Field(default=1, ge=0, le=5)
    timeout_per_agent_s: int = Field(default=1500, description="25 minutes default.")
    max_diff_lines: int = Field(default=1000)
    apply_mode: ApplyMode = ApplyMode.UNCOMMITTED
    branch_name: str | None = None
    base_ref: str = Field(default="HEAD")
    sandbox: SandboxMode = SandboxMode.WORKSPACE_WRITE
    context_file: Path | None = Field(
        default=None, description="Defaults to .dialectic/context.md if present."
    )
    journal_file: Path | None = Field(
        default=None, description="v1.5: .dialectic/journal.md for past-run context."
    )
    keep_worktrees: bool = Field(
        default=False, description="Keep worktrees on failure for debugging."
    )

    @model_validator(mode="after")
    def _validate_efforts(self) -> RunConfig:
        for label, cfg in [("writer", self.writer), ("reviewer", self.reviewer)]:
            valid_enum = _EFFORT_BY_CLI[cfg.cli]
            try:
                valid_enum(cfg.effort)
            except ValueError as exc:
                raise ValueError(
                    f"{label}.effort '{cfg.effort}' is not valid for cli={cfg.cli.value}; "
                    f"choose from {[v.value for v in valid_enum]}"
                ) from exc
        return self


# ──────────────────────────────────────────────────────────────────────────────
# Run result
# ──────────────────────────────────────────────────────────────────────────────


#: Bumped whenever a non-additive change is made to the persisted RunResult shape.
#: load_run_record uses this to detect records produced by an older/newer schema.
CURRENT_PROTOCOL_VERSION = "0.2.0"


class RunResult(_Strict):
    """End-of-run structured output. Persisted to .dialectic/runs/<id>.json."""

    protocol_version: str = Field(
        default="0.1.0",
        description=(
            "Schema version of this record. load_run_record warns when loading a record "
            "from an older version and fails loudly when loading a newer one."
        ),
    )
    run_id: str
    status: RunStatus
    config: RunConfig

    base_sha: str = Field(
        default="",
        description=(
            "Immutable SHA the run was based on (resolved from config.base_ref at run start). "
            "apply_run_result compares this against current HEAD to detect drift — re-resolving "
            "base_ref at apply time would be tautological when base_ref='HEAD'."
        ),
    )

    diff: str = Field(default="", description="Final consensus unified diff against base_sha.")
    files_changed: list[str] = Field(default_factory=list)

    rounds: list[RevisionRound] = Field(
        default_factory=list,
        description="Full trajectory: every writer/reviewer exchange in this run.",
    )
    resolved_items: list[CritiqueItem] = Field(default_factory=list)
    disputed_items: list[DisputedItem] = Field(default_factory=list)
    unresolved_items: list[CritiqueItem] = Field(
        default_factory=list,
        description=(
            "Critique items raised in the final round but never reached the writer-response phase "
            "(e.g. max_revisions exhausted). User must arbitrate via the same flow as disputed_items."
        ),
    )
    acknowledged_dissents: list[AcknowledgedDissent] = Field(default_factory=list)
    arbitration: list[ArbitrationDecision] = Field(
        default_factory=list,
        description="User's resolutions for disputed/unresolved items (empty until arbitration completes).",
    )

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
    REVISION_PROGRESS = "revision_progress"
    REVISION_DONE = "revision_done"
    REBUTTAL_STARTED = "rebuttal_started"
    REBUTTAL_DONE = "rebuttal_done"
    DIFF_READY = "diff_ready"
    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_ARBITRATION = "awaiting_arbitration"
    APPLIED = "applied"
    REJECTED = "rejected"
    ERROR = "error"
    RUN_FINISHED = "run_finished"


class StreamEvent(_Strict):
    event_type: EventType
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    run_id: str
    message: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
