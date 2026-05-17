"""Orchestrator core: the dialectic loop.

This is the only place that knows the protocol sequence:
    writer.initial → reviewer.critique → [writer.respond → reviewer.rebut]
    → assemble RunResult → present to user → apply on approval.

Subagent CLI specifics live in agents/. Git mechanics live in worktree.py.
Pydantic types live in protocol.py. Everything here works in protocol types.
"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

from .protocol import RunConfig, RunResult, StreamEvent


async def run(config: RunConfig, repo_root: Path) -> RunResult:
    """Execute one full dialectic run: write → review → [respond → rebut]* → assemble result.

    Does NOT apply the diff. That's a separate step (apply_run_result) so the user can
    inspect the RunResult before changes touch their working tree.
    """
    raise NotImplementedError


async def run_streaming(config: RunConfig, repo_root: Path) -> AsyncIterator[StreamEvent]:
    """Same as run(), but yields StreamEvents as the dance progresses.

    Final event is always RUN_FINISHED with the full RunResult in its payload.
    """
    raise NotImplementedError
    yield  # type: ignore[unreachable]


def apply_run_result(result: RunResult, repo_root: Path) -> None:
    """Apply the consensus diff per result.config.apply_mode.

    Honors the safety checks: working tree clean, HEAD matches base_sha used for the run.
    Refuses with a clear error if either fails; user must stash / branch / abort.
    """
    raise NotImplementedError


def reject_run_result(result: RunResult, repo_root: Path) -> None:
    """User rejected. Cleanup is already done; just record the rejection in the audit log."""
    raise NotImplementedError


def load_project_context(repo_root: Path, config: RunConfig) -> str:
    """Read .dialectic/context.md (and journal.md in v1.5) into a single string for prompts."""
    raise NotImplementedError


def persist_run_record(result: RunResult, repo_root: Path) -> Path:
    """Write structured run record to .dialectic/runs/<run-id>.json. Returns the path."""
    raise NotImplementedError
