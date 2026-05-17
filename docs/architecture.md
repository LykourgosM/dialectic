# Architecture

Dialectic is a Python orchestrator that runs two coding CLIs (Claude Code as the writer, OpenAI Codex CLI as the reviewer) through a structured protocol. The user is the final arbiter when the two agents can't agree.

## At a glance

```
┌────────────────────────────────────────────────────────────────────────┐
│  Your repo                                                              │
│                                                                         │
│   ┌─────────────┐        ┌───────────────────────────────────────┐     │
│   │   You       │   ──>  │   /dialectic <prompt>   (Claude Code) │     │
│   └─────────────┘        │     ↓                                  │     │
│         ↑                │   $ dialectic run --prompt "..."       │     │
│         │  diff for      └─────────────┬─────────────────────────┘     │
│         │  approval                    ↓                                │
│         │              ┌───────────────────────────────────────┐       │
│         │              │   Python orchestrator (core.run)       │       │
│         │              │   - holds protocol state               │       │
│         │              │   - constructs each agent's prompt     │       │
│         │              │   - validates structured responses     │       │
│         │              │   - persists audit log                 │       │
│         │              └─────┬─────────────────────────┬───────┘       │
│         │                    ↓                          ↓               │
│         │     ┌────────────────────────┐   ┌────────────────────────┐  │
│         │     │ Writer worktree         │   │ Reviewer worktree       │ │
│         │     │ .dialectic/wt/writer-X  │   │ .dialectic/wt/reviewer-X│ │
│         │     │                         │   │                          │ │
│         │     │ $ claude -p ...         │   │ $ codex exec ...         │ │
│         │     │   --json-schema ...     │   │   --output-schema ...    │ │
│         │     │   --permission-mode     │   │   --sandbox              │ │
│         │     │     bypassPermissions   │   │     workspace-write      │ │
│         │     └──────────┬──────────────┘   └──────────┬──────────────┘ │
│         │                ↓                              ↓                │
│         │           WriterReport                  ReviewerCritique       │
│         │           (structured JSON)             (structured JSON)      │
│         │                ↓                              ↓                │
│         │   ┌─────────────────────────────────────────────────────┐    │
│         │   │   Protocol loop (cycles until approve/reject/limit)  │    │
│         │   │                                                       │    │
│         │   │   1. writer writes        → WriterReport             │    │
│         │   │   2. reviewer critiques   → ReviewerCritique         │    │
│         │   │   3. writer responds      → WriterResponseBundle     │    │
│         │   │      (per-item: accept-and-revise OR reject-with-    │    │
│         │   │       rationale)                                      │    │
│         │   │   4. reviewer rebuts      → ReviewerRebuttal         │    │
│         │   │      (per-rejected-item: accept rationale OR         │    │
│         │   │       escalate as still_disputed)                    │    │
│         │   └──────────────────────┬──────────────────────────────┘    │
│         │                          ↓                                    │
│         │              ┌────────────────────────┐                       │
│         └──────────────│   RunResult            │                       │
│                        │   .diff                │                       │
│                        │   .rounds[]            │                       │
│                        │   .resolved_items      │                       │
│                        │   .acknowledged_dissents│                      │
│                        │   .disputed_items      │                       │
│                        │   .unresolved_items    │                       │
│                        └────────────────────────┘                       │
│                                                                          │
│                       persisted to .dialectic/runs/<id>.json            │
│                              + .dialectic/runs/<id>.prompts.jsonl       │
└──────────────────────────────────────────────────────────────────────────┘
```

## Layering

Three thin frontends share one core:

| Layer | What it is |
|---|---|
| `/dialectic` skill | A `.claude/skills/dialectic/SKILL.md` markdown file that tells Claude Code how to invoke the CLI binary, surface its events to the user, and ask for approval. |
| `dialectic` CLI | Click + rich. Renders streaming events live, prompts for approval/rejection, handles arbitration input. |
| `dialectic serve` | FastAPI. Same protocol types, optional bearer auth, suitable for embedding in another tool. |
| **`dialectic.core`** | The orchestrator. Deterministic Python state machine. Doesn't know about CLI rendering, HTTP, or how the writer/reviewer are reached. |
| `dialectic.agents.*` | Subprocess wrappers. The only place that knows about `claude -p` and `codex exec` flag syntax. |
| `dialectic.worktree` | Git worktree lifecycle and safety checks. |
| `dialectic.protocol` | Pydantic models (the contract). Generates the JSON schemas passed to `--json-schema` / `--output-schema`. |

## Protocol invariants

These are checked at runtime; violations terminate the run with `RunStatus.FAILED`:

- **Critique item IDs are unique** within a `ReviewerCritique`.
- **Writer responses cover every critique item exactly once**, no extras.
- **Reviewer rebuttal addresses every rejected response item exactly once**, no extras.
- **Action/rationale coupling** (validated by pydantic): `action=reject` requires `rationale`; `action=accept` requires `change_summary`; `verdict=still_disputed` requires `rebuttal_reasoning`.

## Safety checks at apply time

Before any diff lands on your working tree, `apply_run_result` checks:

1. The run's status is `AWAITING_APPROVAL` (not `AWAITING_ARBITRATION`, `FAILED`, etc.).
2. No git operation in progress (rebase, merge, cherry-pick, bisect, revert).
3. Current HEAD equals the SHA captured at run-start (`result.base_sha`). Re-resolving `config.base_ref` here would be tautological.
4. Working tree is clean (ignoring orchestrator artifacts in `.dialectic/`).
5. The diff itself doesn't target `.git/`, contain `..`, or use absolute paths.
6. `git apply --check` passes (so partial-apply on conflict is impossible).

## What the orchestrator does NOT do

- Run any agent twice on the same prompt (no debate loops).
- Aggregate multiple writer outputs (no competing-writer ensembles).
- Use an LLM as the final arbiter for unresolved disputes (escalates to the human).
- Persist session state across runs (each run is atomic; project memory lives in `.dialectic/context.md` and the journal file, both file-based and version-controllable).

These are deliberate omissions — see the README's "What this doesn't do" and the bibliography for the literature behind each.
