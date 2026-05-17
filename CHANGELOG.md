# Changelog

All notable changes to dialectic. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning: [SemVer](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] — 2026-05-17

Initial release. v0.1 ships the full protocol end-to-end, validated by two
real dogfood runs against the dialectic codebase itself.

### Added
- Cross-family writer-reviewer protocol with structured per-item
  critique-and-defend, reviewer rebuttal, and user-arbitrated escalation
  for unresolved disputes.
- Subprocess wrappers for `claude -p` (writer) and `codex exec` (reviewer)
  with strict-mode JSON schema enforcement, graceful SIGTERM/SIGKILL
  timeout handling, BOM-tolerant JSON parsing, and `stdin=DEVNULL` to
  avoid CLI warning noise.
- Ephemeral git worktrees per run (`.dialectic/wt/`), with safety checks
  for clean working tree, HEAD-matches-base, and in-progress
  rebase/merge/cherry-pick detection.
- Diff application paths: `uncommitted` (default, modifies working tree)
  and `branch` (creates a new branch with the change committed), with
  rollback on commit failure.
- Per-run forensic audit log at `.dialectic/runs/<id>.prompts.jsonl`
  capturing every agent invocation's prompt, structured response, cost,
  and duration.
- Runtime invariant checks: critique items must have unique ids, writer
  must respond to every critique item exactly once, reviewer must rebut
  every rejected response exactly once.
- CLI: `dialectic run / approve / reject / arbitrate / list-runs / serve`
  with `-v/-vv` for logging verbosity.
- HTTP API: `POST /run`, `GET/POST /run/{id}/{approve,reject,arbitrate}`
  with optional bearer-token auth (mandatory for non-loopback binds).
- Claude Code skill at `.claude/skills/dialectic/SKILL.md` for `/dialectic`
  invocation.
- 99 tests across unit / integration / concurrency / CLI / server layers,
  running in under 6s; real-CLI E2E tests gated by `DIALECTIC_E2E=1`.
- Security regression tests: path-traversal in `run_id`, diffs touching
  `.git/`, branch-name injection through `git checkout -b`.

### Security
- `run_id` validated against `^[0-9]{8}-[0-9]{6}-[0-9a-f]+$` plus
  defense-in-depth path-resolution check.
- `git apply --check` before any apply; diffs targeting `.git/`, paths
  with `..`, or absolute paths are refused.
- HTTP server refuses non-loopback binds without a bearer token.

### Known limitations
- `ACCEPT_REVIEWER` arbitration is not yet implemented (raises
  `NotImplementedError`); use `ACCEPT_WRITER` or `SKIP` and apply the
  reviewer's `suggested_fix` manually after `dialectic approve`.
- Single reviewer in v1; multi-reviewer panel design is in `RunConfig`
  (`reviewer_id`) but not yet exposed.
- Streaming variants of agent invocations (`invoke_streaming`) deferred
  to v1.1.

[Unreleased]: https://github.com/LykourgosM/dialectic/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/LykourgosM/dialectic/releases/tag/v0.1.0
