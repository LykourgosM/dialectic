"""Inspect a completed run's forensic audit log.

Useful for debugging: every agent invocation's prompt and structured response
is one line of JSONL in `.dialectic/runs/<run-id>.prompts.jsonl`. This script
prints a compact per-phase summary.

Usage:
    python examples/02_inspect_audit_log.py <run-id>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python examples/02_inspect_audit_log.py <run-id>", file=sys.stderr)
        return 2

    run_id = sys.argv[1]
    audit = Path(".dialectic/runs") / f"{run_id}.prompts.jsonl"
    if not audit.exists():
        print(f"no audit log at {audit}", file=sys.stderr)
        return 1

    for line in audit.read_text().splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        phase = entry["phase"]
        role = entry["role"]
        round_n = entry["round"]
        cost = entry["cost_usd"]
        duration = entry["duration_s"]
        prompt_len = len(entry["prompt"])
        response = entry["response"]
        if isinstance(response, dict):
            response_summary = f"keys={list(response.keys())}"
        else:
            response_summary = f"raw={str(response)[:80]!r}"
        print(
            f"round={round_n:>2}  phase={phase:<22}  role={role:<8}  "
            f"cost=${cost:.4f}  {duration:>6.1f}s  prompt={prompt_len:>6}c  {response_summary}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
