---
name: dialectic
description: Run a coding task through the dialectic protocol — Claude (Opus) writes, Codex (GPT-5.4) reviews, the writer can defend its choices with rationale, the reviewer rebuts, and the user arbitrates anything they can't agree on. Use when the user wants a cross-family-reviewed implementation rather than a single-agent output. The user invokes this with /dialectic <prompt>.
---

# dialectic

You are dispatching a coding task to the `dialectic` orchestrator. The orchestrator runs Claude as the writer and Codex as the reviewer in isolated git worktrees, executes a structured critique-and-defend protocol between them, and produces a consensus diff with explicit user arbitration on anything the two models couldn't resolve.

## Procedure

1. **Run the orchestrator:**
   ```
   dialectic run --prompt "$ARGS" --stream
   ```
   `$ARGS` is the user's prompt after `/dialectic`. Quote it.

2. **Stream events to the user as they arrive.** The orchestrator emits one event per line (JSON if `--stream --json`, otherwise human-readable). Display them in real time so the user can see progress (`Writer reading src/foo.py...`, `Reviewer analyzing diff (3 files)...`, `Writer revising 2 items, defending 1...`). Do NOT batch.

3. **At end-of-run** the orchestrator persists a `RunResult` and prints its `run_id` + summary. Inspect `RunResult.status`:

   - **`AWAITING_APPROVAL`** — writer and reviewer reached consensus. Show the user:
     - File-by-file diff summary (count of files, lines added/removed)
     - The `summary` string
     - Any `acknowledged_dissents` (items the writer rejected and the reviewer accepted; these ship but are noted in the audit log)
     - Then ask: **approve / reject / view full diff**
     - On approve → run `dialectic approve <run-id>`
     - On reject → run `dialectic reject <run-id>`

   - **`AWAITING_ARBITRATION`** — the writer and reviewer could not agree on one or more items. Show the user each disputed item with both perspectives:
     ```
     Item #N (file:lines) — [issue]
       Writer (Claude):  [writer_response.rationale]
       Reviewer (Codex): [reviewer_rebuttal_item.rebuttal_reasoning]
       Pick: [a] accept writer's choice
             [b] accept reviewer's suggested fix
             [s] skip (ship as-is, note in audit log)
     ```
     Collect the user's resolution for every disputed item, then run:
     ```
     dialectic arbitrate <run-id> \
       --accept-writer <id> --accept-writer <id> \
       --accept-reviewer <id> \
       --skip <id>
     ```
     This moves the run to `AWAITING_APPROVAL`. Then re-display the consensus diff and ask for approval as above.

   - **`FAILED` / `TIMED_OUT`** — surface the `error` field and the run_id (so the user can inspect the audit log at `.dialectic/runs/<run-id>.json`). Don't retry automatically.

4. **On approval**, after `dialectic approve <run-id>` succeeds, confirm to the user:
   ```
   ✓ Applied <N> files (<+X −Y> lines) to your working tree (uncommitted, on <current branch>).
     Run `git diff` to review, then commit when ready.
   ```

## What you should NOT do

- Do not re-invent the dialectic loop yourself — the binary does it. Just run it.
- Do not silently swallow disputed items. Every disputed item needs a user-supplied resolution before approve will succeed.
- Do not approve on the user's behalf. They are the final arbiter.
- Do not commit changes (the orchestrator applies uncommitted by default; the user commits when ready).
- Do not pass `--auto-approve` unless the user explicitly asked for unattended runs.

## Flags worth knowing

- `--dry-run` — show the diff without applying anything (shortcut for `--apply-mode dry_run`)
- `--apply-mode branch [--branch-name NAME]` — create a new branch instead of applying uncommitted
- `--max-revisions N` — allow N rounds of writer revision (default 1; ceiling 5)
- `--writer-model` / `--reviewer-model` — override defaults (Claude Opus 4.7 / Codex GPT-5.4)
- `--writer-cli` / `--reviewer-cli` — swap which family writes vs. reviews (rotated experiments)
- `--keep-worktrees` — keep `.dialectic/wt/<run-id>/` on failure for debugging

Don't add flags unless the user asked for the behavior.
