"""Subprocess wrapper for `claude -p` (Claude Code in non-interactive mode).

This module is the only place that knows the `claude` CLI's specific flag syntax
(`--effort`, `--output-format json`, `--permission-mode`, etc.). The rest of the
orchestrator deals in structured protocol types.

Implementation notes for fill-in:
  * ALWAYS pass `stdin=asyncio.subprocess.DEVNULL` or pipe the prompt — otherwise
    `claude -p` prints `Warning: no stdin data received in 3s, proceeding without it…`
    to stdout, which breaks `json.loads()` on the result.
  * Cost is at `result.total_cost_usd`; text at `result.result`; structured output
    at `result.structured_output`; session at `result.session_id`; error flag at
    `result.is_error`; error reasons at top-level `errors[]`.
  * Exit code 1 overloads "budget exceeded" and "bad input" — distinguish via
    parsed `is_error` + `errors[]` rather than exit code alone.
  * `--json-schema` accepts pydantic's `model_json_schema()` output directly (with
    $defs and anyOf union nulls) — no transformation needed (unlike Codex).
  * `--no-session-persistence` keeps things ephemeral.
  * Pass `ANTHROPIC_API_KEY` from env explicitly when using `--bare` mode.
"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

from pydantic import BaseModel

from ..protocol import AgentConfig, ClaudePermissionMode, StreamEvent


class ClaudeInvocation(BaseModel):
    """One non-interactive invocation of `claude -p`."""

    config: AgentConfig
    prompt: str
    cwd: Path
    output_schema: dict | None = None
    system_prompt: str | None = None
    additional_dirs: list[Path] = []
    max_budget_usd: float | None = None
    permission_mode: ClaudePermissionMode = ClaudePermissionMode.BYPASS
    """The reviewer should use PLAN (no edits) or pass an allowlist of read-only tools."""


class ClaudeResult(BaseModel):
    """Parsed result of a `claude -p` invocation."""

    raw_text: str
    structured: dict | None = None
    cost_usd: float = 0.0
    duration_s: float = 0.0
    session_id: str | None = None
    is_error: bool = False
    error: str | None = None


async def invoke(invocation: ClaudeInvocation) -> ClaudeResult:
    """Run `claude -p` once with the given config; return parsed structured output.

    Builds:
        claude -p <prompt>
          --model <model>
          --effort <effort>
          --output-format json
          --no-session-persistence
          --permission-mode <permission_mode>
          --add-dir <cwd>
          [--add-dir <dir>]*
          [--json-schema '<schema>']
          [--system-prompt '<prompt>']
          [--max-budget-usd <amount>]

    with stdin=DEVNULL.
    """
    raise NotImplementedError("Fill in subprocess logic.")


async def invoke_streaming(invocation: ClaudeInvocation) -> AsyncIterator[StreamEvent]:
    """Run `claude -p` with `--output-format stream-json --include-partial-messages` and yield StreamEvents.

    Observed event types: `system/init`, `system/status`, `stream_event` (partial deltas),
    `assistant`, `rate_limit_event`, `result/success`. Translate the relevant ones into
    orchestrator-level StreamEvents (`WRITER_PROGRESS`, `WRITER_DONE`, etc.).
    """
    raise NotImplementedError("Fill in streaming subprocess logic.")
    yield  # type: ignore[unreachable]
