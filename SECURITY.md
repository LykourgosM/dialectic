# Security policy

## Reporting a vulnerability

Please report security issues privately via GitHub's "Report a vulnerability" feature on this repo, not via public issues.

I'll respond within a few days. If the issue is valid I'll work on a fix and credit you in the changelog (unless you'd prefer to remain anonymous).

## Threat model

Dialectic shells out to `claude` and `codex` with elevated permissions, applies LLM-generated diffs to your working tree, and (optionally) exposes an HTTP API. Hardening assumes:

- **The orchestrator host is trusted** (your laptop, your dev VM). Anyone with shell access to the host has equivalent access to your repos and your LLM API keys.
- **LLM-generated diffs may be adversarial** (prompt injection, deliberately-malicious instructions to the writer). The orchestrator validates diffs before applying.
- **Worktrees are isolated** (`.dialectic/wt/`). Anything the writer or reviewer does inside its worktree is discarded at the end of the run; only the consensus diff escapes into the user's working tree.

## Guards currently in place

- `run_id` is validated against `^[0-9]{8}-[0-9]{6}-[0-9a-f]+$` before being interpolated into any filesystem path, with a defense-in-depth `Path.resolve().is_relative_to(runs_dir)` check.
- Diffs are rejected if they target `.git/`, contain `..` path components, or use absolute paths. `git apply --check` runs before any actual apply, so partial-apply on conflict is impossible.
- Branch names (`--branch-name`) are validated against `[A-Za-z0-9._/-]+` before being passed to `git checkout -b`, defanging flag-injection like `--upload-pack=evil`.
- The HTTP server refuses to bind a non-loopback host without a bearer token (`--token`/`DIALECTIC_TOKEN`). When the token is set, all requests require `Authorization: Bearer <token>`, compared with `secrets.compare_digest`.
- Per-agent timeouts with graceful SIGTERM → SIGKILL escalation.
- Subprocess invocation uses `asyncio.create_subprocess_exec` with argv lists (never a shell string). Positional prompts are passed last so a prompt starting with `-` can't bleed into other flag values.

## Privacy note

The per-run forensic audit log at `.dialectic/runs/<id>.prompts.jsonl` records the *full prompts and structured responses* of every agent invocation. These prompts include any context the orchestrator forwarded — your project's `CLAUDE.md`, `.dialectic/context.md`, the writer's diff. If your project's context contains secrets (API keys in `.env`-style files, credentials in comments), they will be persisted to disk.

The auto-generated `.dialectic/.gitignore` excludes `runs/` from version control by default, but the files still live on your disk. Treat `.dialectic/runs/` like you would `~/.bash_history` — useful for debugging, but not safe to share verbatim.

A `RunConfig.persist_prompts: bool` opt-out is on the roadmap.
