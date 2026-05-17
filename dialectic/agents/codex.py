"""Subprocess wrapper for `codex exec` (OpenAI Codex CLI in non-interactive mode).

This module is the only place that knows about the `codex` CLI's specific flag
syntax (`-c model_reasoning_effort=xhigh`, `--sandbox`, `--output-schema`, etc.).
"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

from pydantic import BaseModel

from ..protocol import AgentConfig, StreamEvent


class CodexInvocation(BaseModel):
    """One non-interactive invocation of `codex exec`."""

    config: AgentConfig
    prompt: str
    cwd: Path
    output_schema_path: Path | None = None
    additional_dirs: list[Path] = []
    sandbox: str = "workspace-write"


class CodexResult(BaseModel):
    """Parsed result of a `codex exec` invocation."""

    raw_text: str
    structured: dict | None = None
    cost_usd: float = 0.0
    duration_s: float = 0.0
    session_id: str | None = None
    error: str | None = None


async def invoke(invocation: CodexInvocation) -> CodexResult:
    """Run `codex exec` once with the given config; return parsed structured output.

    Builds the command:
        codex exec
          -m <model>
          -c model_reasoning_effort=<effort>
          -C <cwd>
          --sandbox <sandbox>
          --ephemeral
          --json
          [--output-schema <path>]
          [--add-dir <dir>]+
          <prompt via stdin>
    """
    raise NotImplementedError("Fill in subprocess logic.")


async def invoke_streaming(invocation: CodexInvocation) -> AsyncIterator[StreamEvent]:
    """Run `codex exec --json` and yield StreamEvents as JSONL events arrive.

    Codex emits events like `thread.started`, `turn.started/completed`, `item.*`,
    `error`. We translate them into StreamEvents.
    """
    raise NotImplementedError("Fill in streaming subprocess logic.")
    yield  # type: ignore[unreachable]
