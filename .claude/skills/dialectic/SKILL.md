---
name: dialectic
description: Run a coding task through the dialectic protocol — Claude (Opus) writes, Codex (GPT-5.4) reviews, the writer can defend its choices with rationale, the reviewer rebuts, and the user arbitrates anything they can't agree on. Use when the user wants a cross-family-reviewed implementation rather than a single-agent output. The user invokes this with /dialectic <prompt>.
---

# dialectic

You are dispatching a coding task to the `dialectic` orchestrator. The orchestrator runs Claude as the writer and Codex as the reviewer in isolated git worktrees, executes a structured critique-and-defend protocol between them, and produces a consensus diff. Your job is to invoke it and surface the result for user approval.

## Procedure

1. **Run the orchestrator:**
   ```
   dialectic run --prompt "$ARGS" --stream
   ```
   `$ARGS` is the prompt the user passed after `/dialectic`. Quote it.

2. **Stream events** to the user as they arrive. The orchestrator emits one line per event; show them in real time so the user can see progress (`Writer reading src/foo.py...`, `Reviewer analyzing diff (3 files)...`, etc.). Do NOT batch.

3. **At end-of-run**, the orchestrator prints a structured summary including the consensus diff and any items the writer and reviewer could not agree on. Display:
   - File-by-file diff summary (count of files changed, lines added/removed)
   - The summary string (`"Reviewer raised 3 items, writer addressed 2 and defended 1, reviewer accepted the defense."`)
   - Any **disputed items** as a clearly-marked block — these are items the user must arbitrate

4. **Ask for approval.** If there are disputed items, present each one with both perspectives and ask the user to pick a resolution per item. If there are no disputes, ask: approve / reject / view full diff.

5. **On approval:** run `dialectic approve <run-id>` to apply the diff to the user's working tree (uncommitted, on their current branch). The run-id is in the orchestrator's final output.

6. **On rejection:** run `dialectic reject <run-id>`. No changes touch the user's repo.

## What you should NOT do

- Do not re-invent the dialectic loop yourself — the binary does it. Just run it.
- Do not silently swallow disputed items. They must be surfaced for user arbitration.
- Do not approve on the user's behalf. They are the final arbiter.
- Do not commit changes (the orchestrator applies uncommitted by default; the user commits when ready).

## Flags worth knowing

- `--apply-mode branch` — create a new branch `orchestrate/run-<timestamp>` instead of applying uncommitted
- `--max-revisions N` — allow N rounds of writer revision (default 1; ceiling 5)
- `--dry-run` — show the diff without applying anything
- `--writer-model`, `--reviewer-model` — override defaults (Claude Opus 4.7 / Codex GPT-5.4)

Don't add flags unless the user asked for the behavior.
