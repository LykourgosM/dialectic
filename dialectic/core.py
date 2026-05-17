"""Orchestrator core: the dialectic loop.

This is the only place that knows the protocol sequence:
    writer.initial → reviewer.critique → [writer.respond → reviewer.rebut]
    → assemble RunResult → present to user → [arbitrate if disputes] → apply on approval.

Subagent CLI specifics live in agents/. Git mechanics live in worktree.py.
Pydantic types live in protocol.py. Everything here works in protocol types.
"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

from .protocol import ArbitrationDecision, RunConfig, RunResult, StreamEvent


# ──────────────────────────────────────────────────────────────────────────────
# Run lifecycle
# ──────────────────────────────────────────────────────────────────────────────


async def run(config: RunConfig, repo_root: Path) -> RunResult:
    """Execute one full dialectic run: write → review → [respond → rebut]* → assemble result.

    Does NOT apply the diff. Returns a RunResult with one of:
      - status=AWAITING_APPROVAL (no disputes — user just approves/rejects)
      - status=AWAITING_ARBITRATION (disputes present — user must resolve via `arbitrate`)
      - status=FAILED / TIMED_OUT (error path; error field populated)

    The result is persisted to .dialectic/runs/<run-id>.json before returning.
    """
    raise NotImplementedError


async def run_streaming(config: RunConfig, repo_root: Path) -> AsyncIterator[StreamEvent]:
    """Same as run(), but yields StreamEvents as the dance progresses.

    Final event is always RUN_FINISHED with the full RunResult JSON in its payload.
    """
    raise NotImplementedError
    yield  # type: ignore[unreachable]


# ──────────────────────────────────────────────────────────────────────────────
# Approval, arbitration, rejection
# ──────────────────────────────────────────────────────────────────────────────


def apply_run_result(result: RunResult, repo_root: Path) -> RunResult:
    """Apply the consensus diff per result.config.apply_mode.

    Refuses to apply if:
      - result.status not in {AWAITING_APPROVAL, SUCCESS, APPLIED_WITH_DISSENT after arbitration}
      - working tree not clean (worktree.working_tree_is_clean)
      - current HEAD has moved from result's base_ref since the run started

    Updates and re-persists the RunResult with status=SUCCESS or APPLIED_WITH_DISSENT,
    and audit_log_path. Returns the updated RunResult.
    """
    raise NotImplementedError


def reject_run_result(result: RunResult, repo_root: Path) -> RunResult:
    """User rejected. Worktrees already cleaned; update result.status=REJECTED_BY_USER, re-persist."""
    raise NotImplementedError


async def resume_with_arbitration(
    run_id: str, decisions: list[ArbitrationDecision], repo_root: Path
) -> RunResult:
    """User has supplied per-disputed-item resolutions. Fold them in and assemble the final diff.

    Loads the AWAITING_ARBITRATION RunResult, applies the user's choices to disputed_items,
    constructs the final diff (writer's version for ACCEPT_WRITER, reviewer's suggested_fix
    for ACCEPT_REVIEWER, original-with-skip-note for SKIP), and returns the updated RunResult
    with status=AWAITING_APPROVAL (caller still needs to approve before apply).
    """
    raise NotImplementedError


# ──────────────────────────────────────────────────────────────────────────────
# Persistence
# ──────────────────────────────────────────────────────────────────────────────


def persist_run_record(result: RunResult, repo_root: Path) -> Path:
    """Write the RunResult to .dialectic/runs/<run-id>.json. Returns the path."""
    raise NotImplementedError


def load_run_record(run_id: str, repo_root: Path) -> RunResult:
    """Load a previously-persisted RunResult by run_id. Raises FileNotFoundError if missing."""
    raise NotImplementedError


# ──────────────────────────────────────────────────────────────────────────────
# Context loading
# ──────────────────────────────────────────────────────────────────────────────


def load_project_context(repo_root: Path, config: RunConfig) -> str:
    """Read .dialectic/context.md and (v1.5) journal.md into a single string for agent prompts."""
    raise NotImplementedError
