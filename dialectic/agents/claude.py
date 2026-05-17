"""Subprocess wrapper for `claude -p` (Claude Code in non-interactive mode)."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import AsyncIterator

from pydantic import BaseModel

from ..protocol import AgentConfig, ClaudePermissionMode, StreamEvent


class ClaudeInvocation(BaseModel):
    config: AgentConfig
    prompt: str
    cwd: Path
    output_schema: dict | None = None
    system_prompt: str | None = None
    additional_dirs: list[Path] = []
    max_budget_usd: float | None = None
    permission_mode: ClaudePermissionMode = ClaudePermissionMode.BYPASS


class ClaudeResult(BaseModel):
    raw_text: str
    structured: dict | None = None
    cost_usd: float = 0.0
    duration_s: float = 0.0
    session_id: str | None = None
    is_error: bool = False
    error: str | None = None


async def invoke(invocation: ClaudeInvocation, timeout_s: int = 1500) -> ClaudeResult:
    """Run `claude -p` once and return the parsed result.

    stdin=DEVNULL is critical: without it, `claude -p` waits 3s and prints a warning to
    stdout, which breaks `json.loads()`.
    """
    cmd: list[str] = [
        "claude",
        "-p",
        invocation.prompt,
        "--model",
        invocation.config.model,
        "--effort",
        invocation.config.effort,
        "--output-format",
        "json",
        "--no-session-persistence",
        "--permission-mode",
        invocation.permission_mode.value,
        "--add-dir",
        str(invocation.cwd),
    ]

    for d in invocation.additional_dirs:
        cmd.extend(["--add-dir", str(d)])

    if invocation.output_schema is not None:
        cmd.extend(["--json-schema", json.dumps(invocation.output_schema)])

    if invocation.system_prompt:
        cmd.extend(["--append-system-prompt", invocation.system_prompt])

    if invocation.max_budget_usd is not None:
        cmd.extend(["--max-budget-usd", str(invocation.max_budget_usd)])

    start = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(invocation.cwd),
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return ClaudeResult(
                raw_text="",
                duration_s=time.monotonic() - start,
                is_error=True,
                error=f"claude -p timed out after {timeout_s}s",
            )
    except FileNotFoundError:
        return ClaudeResult(
            raw_text="",
            duration_s=time.monotonic() - start,
            is_error=True,
            error="`claude` binary not found on PATH",
        )

    duration = time.monotonic() - start
    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")

    if proc.returncode != 0 and not stdout.strip():
        return ClaudeResult(
            raw_text=stdout,
            duration_s=duration,
            is_error=True,
            error=stderr.strip() or f"exit code {proc.returncode}",
        )

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return ClaudeResult(
            raw_text=stdout,
            duration_s=duration,
            is_error=True,
            error=f"JSON parse error: {exc}; first 500 chars of stdout: {stdout[:500]!r}",
        )

    errors_field = data.get("errors") or []
    error_msg = str(errors_field[0]) if errors_field else None

    return ClaudeResult(
        raw_text=data.get("result", ""),
        structured=data.get("structured_output"),
        cost_usd=float(data.get("total_cost_usd") or 0.0),
        duration_s=duration,
        session_id=data.get("session_id"),
        is_error=bool(data.get("is_error", False)),
        error=error_msg,
    )


async def invoke_streaming(invocation: ClaudeInvocation) -> AsyncIterator[StreamEvent]:
    """Stream-json variant. Not implemented in v1 — use invoke() for now."""
    raise NotImplementedError("Streaming variant deferred to v1.1.")
    yield  # type: ignore[unreachable]
