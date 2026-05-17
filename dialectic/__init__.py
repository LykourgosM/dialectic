"""Dialectic: cross-family writer-reviewer protocol for LLM code generation.

The public API is intentionally narrow. Most users invoke the tool via the
``dialectic`` CLI or the ``/dialectic`` Claude Code skill. Programmatic users
should import from :mod:`dialectic` directly::

    from dialectic import RunConfig, run

    result = await run(RunConfig(prompt="..."), repo_root=Path("."))
"""

from __future__ import annotations

__version__ = "0.1.0"

from .core import (
    apply_run_result,
    load_project_context,
    load_run_record,
    persist_run_record,
    reject_run_result,
    resume_with_arbitration,
    run,
)
from .protocol import (
    AcknowledgedDissent,
    AgentCli,
    AgentConfig,
    ApplyMode,
    ArbitrationChoice,
    ArbitrationDecision,
    Category,
    ClaudeEffort,
    ClaudePermissionMode,
    CodexEffort,
    CritiqueItem,
    DisputedItem,
    EventType,
    ItemRebuttalVerdict,
    RebuttalVerdict,
    ReviewerCritique,
    ReviewerRebuttal,
    ReviewerVerdict,
    RevisionRound,
    RunConfig,
    RunResult,
    RunStatus,
    SandboxMode,
    Severity,
    StreamEvent,
    WriterAction,
    WriterApproach,
    WriterConfidence,
    WriterReport,
    WriterResponseBundle,
)

__all__ = [
    "__version__",
    # Orchestration
    "run",
    "apply_run_result",
    "reject_run_result",
    "resume_with_arbitration",
    "load_run_record",
    "persist_run_record",
    "load_project_context",
    # Configuration
    "RunConfig",
    "AgentConfig",
    "AgentCli",
    "ApplyMode",
    "SandboxMode",
    "ClaudePermissionMode",
    "ClaudeEffort",
    "CodexEffort",
    # Result + classification
    "RunResult",
    "RunStatus",
    "RevisionRound",
    "DisputedItem",
    "AcknowledgedDissent",
    # Protocol messages
    "WriterReport",
    "WriterApproach",
    "WriterConfidence",
    "WriterResponseBundle",
    "WriterAction",
    "ReviewerCritique",
    "ReviewerRebuttal",
    "ReviewerVerdict",
    "RebuttalVerdict",
    "ItemRebuttalVerdict",
    "CritiqueItem",
    "Severity",
    "Category",
    # Arbitration
    "ArbitrationDecision",
    "ArbitrationChoice",
    # Streaming
    "StreamEvent",
    "EventType",
]
