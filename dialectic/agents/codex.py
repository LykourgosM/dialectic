"""Subprocess wrapper for `codex exec` (OpenAI Codex CLI in non-interactive mode).

This module is the only place that knows the `codex` CLI's specific flag syntax
(`-c model_reasoning_effort=xhigh`, `--sandbox`, `--output-schema`, etc.).

Implementation notes for fill-in:
  * ALWAYS pass `stdin=asyncio.subprocess.DEVNULL` (or pipe the prompt explicitly).
    With a positional prompt and no stdin redirect, codex prints
    `Reading additional input from stdin...` to stdout for ~3s, polluting parsing.
  * `--output-schema <FILE>` takes a JSON Schema file path, NOT inline JSON.
  * Codex requires OpenAI strict-mode schema: `additionalProperties: false` on
    every object, every property `required`, no `anyOf[T, null]`. Pydantic's raw
    output fails — use `_make_strict_schema()` below before writing the file.
  * No `--max-budget-usd` flag. Cost is NOT in `turn.completed.usage` — only
    `input_tokens`, `cached_input_tokens`, `output_tokens`. Compute cost
    orchestrator-side from tokens × pricing table.
  * `--ephemeral` keeps no session files.
  * Event types from `--json`: `thread.started`, `turn.started`, `item.started`,
    `item.completed` (item.type ∈ {agent_message, command_execution}),
    `turn.completed`, `error`, `turn.failed`. Final answer text is in the last
    `item.completed` of type `agent_message`.
  * `model_reasoning_effort` tops out at `xhigh` — `max` is NOT valid for Codex.
    Protocol validates this; double-check here defensively.
  * `-C <worktree-path>` works without `--skip-git-repo-check` for git worktrees.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, AsyncIterator

from pydantic import BaseModel

from ..protocol import AgentConfig, SandboxMode, StreamEvent


class CodexInvocation(BaseModel):
    """One non-interactive invocation of `codex exec`."""

    config: AgentConfig
    prompt: str
    cwd: Path
    output_schema_path: Path | None = None
    additional_dirs: list[Path] = []
    sandbox: SandboxMode = SandboxMode.WORKSPACE_WRITE


class CodexResult(BaseModel):
    """Parsed result of a `codex exec` invocation."""

    raw_text: str
    structured: dict | None = None
    cost_usd: float = 0.0
    """Computed orchestrator-side from token counts; Codex does not report cost directly."""
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    duration_s: float = 0.0
    thread_id: str | None = None
    is_error: bool = False
    error: str | None = None


async def invoke(invocation: CodexInvocation) -> CodexResult:
    """Run `codex exec` once with the given config; return parsed structured output.

    Builds:
        codex exec
          -m <model>
          -c model_reasoning_effort=<effort>
          -C <cwd>
          --sandbox <sandbox>
          --ephemeral
          --json
          [--output-schema <schema_path>]
          [--add-dir <dir>]*
          <prompt>            # passed positionally; stdin=DEVNULL

    Streams `--json` JSONL events; collects the final agent_message text.
    """
    raise NotImplementedError("Fill in subprocess logic.")


async def invoke_streaming(invocation: CodexInvocation) -> AsyncIterator[StreamEvent]:
    """Run `codex exec --json` and yield StreamEvents as JSONL events arrive."""
    raise NotImplementedError("Fill in streaming subprocess logic.")
    yield  # type: ignore[unreachable]


def _make_strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Transform a pydantic JSON Schema into OpenAI strict-mode compatible form.

    Codex's `--output-schema` requires:
      * `additionalProperties: false` on every object
      * Every property in `required` (OpenAI strict semantics)
      * No nullable-via-anyOf patterns: replace `anyOf: [T, {type: null}]` with T
        (treat fields that were `Optional` as effectively required-but-can-be-null
        by adding `"null"` to the type list where possible)

    Idempotent; safe to call on already-strict schemas.
    """
    raise NotImplementedError("Fill in strict-mode transformation.")
    _ = deepcopy  # silence unused import warning until implemented
