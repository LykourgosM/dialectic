"""Subprocess wrapper for `codex exec` (OpenAI Codex CLI in non-interactive mode)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, AsyncIterator

from pydantic import BaseModel

from ..protocol import AgentConfig, SandboxMode, StreamEvent

logger = logging.getLogger("dialectic.agents.codex")


class CodexInvocation(BaseModel):
    config: AgentConfig
    prompt: str
    cwd: Path
    output_schema_path: Path | None = None
    additional_dirs: list[Path] = []
    sandbox: SandboxMode = SandboxMode.WORKSPACE_WRITE


class CodexResult(BaseModel):
    raw_text: str
    structured: dict | None = None
    cost_usd: float = 0.0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    duration_s: float = 0.0
    thread_id: str | None = None
    is_error: bool = False
    error: str | None = None


# Approximate per-token pricing (USD). Update as needed; missing models ⇒ cost=0.
_CODEX_PRICING: dict[str, dict[str, float]] = {
    "gpt-5.4": {"input": 1.25e-6, "cached_input": 0.13e-6, "output": 10.0e-6},
    "gpt-5": {"input": 1.25e-6, "cached_input": 0.13e-6, "output": 10.0e-6},
    "o3": {"input": 2.0e-6, "cached_input": 0.5e-6, "output": 8.0e-6},
    "o3-mini": {"input": 1.1e-6, "cached_input": 0.275e-6, "output": 4.4e-6},
}


async def invoke(invocation: CodexInvocation, timeout_s: int = 1500) -> CodexResult:
    """Run `codex exec --json` once and return the parsed result.

    Parses the JSONL event stream; extracts the last agent_message as the answer text,
    sums usage tokens across all turns for cost computation. stdin=DEVNULL prevents
    Codex's stdin-poll warning from polluting stdout.
    """
    cmd: list[str] = [
        "codex",
        "exec",
        "-m",
        invocation.config.model,
        "-c",
        f"model_reasoning_effort={invocation.config.effort}",
        "-C",
        str(invocation.cwd),
        "--sandbox",
        invocation.sandbox.value,
        "--ephemeral",
        "--json",
    ]

    if invocation.output_schema_path is not None:
        cmd.extend(["--output-schema", str(invocation.output_schema_path)])

    for d in invocation.additional_dirs:
        cmd.extend(["--add-dir", str(d)])

    # `--` separator so a prompt that happens to start with `-` is unambiguously
    # treated as a positional, not a flag.
    cmd.extend(["--", invocation.prompt])

    start = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(invocation.cwd),
            env=os.environ.copy(),
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except asyncio.TimeoutError:
            # Graceful SIGTERM, fallback to SIGKILL.
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=10)
                except asyncio.TimeoutError:
                    pass
            return CodexResult(
                raw_text="",
                duration_s=time.monotonic() - start,
                is_error=True,
                error=f"codex exec timed out after {timeout_s}s",
            )
    except FileNotFoundError:
        return CodexResult(
            raw_text="",
            duration_s=time.monotonic() - start,
            is_error=True,
            error="`codex` binary not found on PATH",
        )

    duration = time.monotonic() - start
    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")

    parsed = _parse_codex_jsonl(stdout)
    cost_usd = _compute_codex_cost(
        invocation.config.model,
        parsed["input_tokens"],
        parsed["cached_input_tokens"],
        parsed["output_tokens"],
    )

    if proc.returncode != 0 or parsed["error"]:
        return CodexResult(
            raw_text=parsed["final_text"] or stdout,
            cost_usd=cost_usd,
            input_tokens=parsed["input_tokens"],
            cached_input_tokens=parsed["cached_input_tokens"],
            output_tokens=parsed["output_tokens"],
            duration_s=duration,
            thread_id=parsed["thread_id"],
            is_error=True,
            error=parsed["error"] or stderr.strip() or f"exit code {proc.returncode}",
        )

    return CodexResult(
        raw_text=parsed["final_text"],
        structured=_try_parse_structured(parsed["final_text"]),
        cost_usd=cost_usd,
        input_tokens=parsed["input_tokens"],
        cached_input_tokens=parsed["cached_input_tokens"],
        output_tokens=parsed["output_tokens"],
        duration_s=duration,
        thread_id=parsed["thread_id"],
        is_error=False,
    )


def _parse_codex_jsonl(stdout: str) -> dict[str, Any]:
    """Walk the JSONL event stream from `codex exec --json`."""
    final_text = ""
    thread_id: str | None = None
    input_tokens = 0
    cached_input_tokens = 0
    output_tokens = 0
    error_msg: str | None = None

    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        et = event.get("type", "")
        if et == "thread.started":
            thread_id = event.get("thread_id")
        elif et == "item.completed":
            item = event.get("item") or {}
            if item.get("type") == "agent_message":
                final_text = item.get("text") or final_text
        elif et == "turn.completed":
            usage = event.get("usage") or {}
            input_tokens += int(usage.get("input_tokens") or 0)
            cached_input_tokens += int(usage.get("cached_input_tokens") or 0)
            output_tokens += int(usage.get("output_tokens") or 0)
        elif et in ("error", "turn.failed"):
            err = event.get("error") or {}
            if isinstance(err, dict):
                error_msg = err.get("message") or str(err) or str(event)
            else:
                error_msg = str(err) or str(event)

    return {
        "final_text": final_text,
        "thread_id": thread_id,
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "output_tokens": output_tokens,
        "error": error_msg,
    }


def _try_parse_structured(text: str) -> dict | None:
    """Pull a JSON object out of the agent_message text, tolerating fenced blocks."""
    if not text:
        return None
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*\n(.+?)\n```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            return None
    return None


def _compute_codex_cost(
    model: str, input_tokens: int, cached_input_tokens: int, output_tokens: int
) -> float:
    pricing = _CODEX_PRICING.get(model)
    if not pricing:
        if input_tokens or output_tokens:
            logger.warning(
                "No pricing entry for codex model %r; cost reported as $0.00 "
                "(tokens: in=%d cached=%d out=%d). Add to _CODEX_PRICING.",
                model, input_tokens, cached_input_tokens, output_tokens,
            )
        return 0.0
    uncached_input = max(0, input_tokens - cached_input_tokens)
    return (
        uncached_input * pricing["input"]
        + cached_input_tokens * pricing["cached_input"]
        + output_tokens * pricing["output"]
    )


async def invoke_streaming(invocation: CodexInvocation) -> AsyncIterator[StreamEvent]:
    """Stream-json variant. Not implemented in v1 — use invoke() for now."""
    raise NotImplementedError("Streaming variant deferred to v1.1.")
    yield  # type: ignore[unreachable]


def _make_strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Transform a pydantic JSON Schema into OpenAI strict-mode compatible form.

    Codex's `--output-schema` requires:
      * `additionalProperties: false` on every object
      * Every property in `required` (OpenAI strict semantics — even Optional fields)
      * No nullable-via-anyOf patterns where possible: `anyOf: [T, {type: null}]`
        becomes `type: [T, "null"]` when T is a simple type (not a $ref)

    Walks `$defs` and nested properties recursively. Idempotent.
    """
    result = deepcopy(schema)
    _strict_walk(result)
    return result


# OpenAI strict-mode's `format` whitelist is narrow. Anything else trips schema
# validation. We drop unknown formats defensively.
_STRICT_FORMAT_WHITELIST = frozenset({"date-time"})


def _strict_walk(node: Any) -> None:
    if isinstance(node, dict):
        # Drop unsupported `format` keys (e.g. "path" from pydantic Path fields).
        fmt = node.get("format")
        if isinstance(fmt, str) and fmt not in _STRICT_FORMAT_WHITELIST:
            del node["format"]

        # Flatten anyOf [<simple-type>, null] patterns into type-array form, only
        # when the two-branch structure permits it. 3+ branch unions, oneOf, and
        # anyOf-with-$ref-and-null are left alone (OpenAI accepts them as-is).
        any_of = node.get("anyOf")
        if isinstance(any_of, list) and len(any_of) == 2:
            non_nulls = [s for s in any_of if not _is_null_schema(s)]
            nulls = [s for s in any_of if _is_null_schema(s)]
            if (
                len(non_nulls) == 1
                and len(nulls) == 1
                and "$ref" not in non_nulls[0]
                and "type" in non_nulls[0]
                and isinstance(non_nulls[0]["type"], str)
            ):
                non_null = non_nulls[0]
                del node["anyOf"]
                for k, v in non_null.items():
                    if k == "type":
                        node["type"] = [v, "null"]
                    elif k not in node:
                        node[k] = v

        # Object schemas: enforce additionalProperties: false and require every
        # property. We also handle the legitimate-but-rare "type: object with no
        # properties" case (e.g. `dict[str, Any]`) by forcing `properties: {}` +
        # `required: []` so the schema is well-formed for strict mode (OpenAI
        # rejects bare `additionalProperties: true` in strict).
        is_object = node.get("type") == "object" or "properties" in node
        if is_object:
            if "properties" in node and isinstance(node["properties"], dict):
                node["additionalProperties"] = False
                node["required"] = list(node["properties"].keys())
            elif node.get("type") == "object":
                node.setdefault("properties", {})
                node["required"] = []
                node["additionalProperties"] = False

        for value in node.values():
            _strict_walk(value)
    elif isinstance(node, list):
        for item in node:
            _strict_walk(item)


def _is_null_schema(s: Any) -> bool:
    """A schema fragment that means 'null'. Tolerates harmless extras like `title`."""
    if not isinstance(s, dict):
        return False
    if s.get("type") != "null":
        return False
    extras = set(s.keys()) - {"type", "title", "description"}
    return not extras
