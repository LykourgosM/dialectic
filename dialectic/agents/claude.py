"""Subprocess wrapper for `claude -p` (Claude Code in non-interactive mode).

This module is the only place that knows about the `claude` CLI's specific flag
syntax (`--effort`, `--output-format json`, `--permission-mode`, etc.). The rest
of the orchestrator deals in structured protocol types.
"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

from pydantic import BaseModel

from ..protocol import AgentConfig, StreamEvent


class ClaudeInvocation(BaseModel):
    """One non-interactive invocation of `claude -p`."""

    config: AgentConfig
    prompt: str
    cwd: Path
    output_schema: dict | None = None
    system_prompt: str | None = None
    additional_dirs: list[Path] = []
    max_budget_usd: float | None = None


class ClaudeResult(BaseModel):
    """Parsed result of a `claude -p` invocation."""

    raw_text: str
    structured: dict | None = None
    cost_usd: float = 0.0
    duration_s: float = 0.0
    session_id: str | None = None
    error: str | None = None


async def invoke(invocation: ClaudeInvocation) -> ClaudeResult:
    """Run `claude -p` once with the given config; return parsed structured output.

    Builds the command:
        claude -p <prompt>
          --model <model>
          --effort <effort>
          --output-format json
          --permission-mode bypassPermissions
          --add-dir <cwd>
          [--json-schema <schema>]
          [--system-prompt <prompt>]
          [--max-budget-usd <amount>]
    """
    raise NotImplementedError("Fill in subprocess logic.")


async def invoke_streaming(invocation: ClaudeInvocation) -> AsyncIterator[StreamEvent]:
    """Run `claude -p` with `--output-format stream-json` and yield StreamEvents.

    Used when the orchestrator wants real-time progress visibility (e.g., "writer
    reading src/foo.py..."). Falls back to `invoke` if streaming isn't supported.
    """
    raise NotImplementedError("Fill in streaming subprocess logic.")
    yield  # type: ignore[unreachable]
