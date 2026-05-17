"""Orchestrator core: the dialectic loop."""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from . import worktree as wt
from .agents.claude import ClaudeInvocation, ClaudeResult
from .agents.claude import invoke as _claude_invoke
from .agents.codex import CodexInvocation, CodexResult, _make_strict_schema
from .agents.codex import invoke as _codex_invoke
from .protocol import (
    CURRENT_PROTOCOL_VERSION,
    AcknowledgedDissent,
    AgentCli,
    AgentConfig,
    ApplyMode,
    ArbitrationChoice,
    ArbitrationDecision,
    ClaudePermissionMode,
    CritiqueItem,
    DisputedItem,
    EventType,
    ItemRebuttalVerdict,
    ReviewerCritique,
    ReviewerRebuttal,
    ReviewerVerdict,
    RevisionRound,
    RunConfig,
    RunResult,
    RunStatus,
    SandboxMode,
    StreamEvent,
    WriterAction,
    WriterReport,
    WriterResponseBundle,
)

logger = logging.getLogger("dialectic")

_M = TypeVar("_M", bound=BaseModel)

# Invokers can be swapped for testing. Writer takes a permission_mode; reviewer takes a sandbox.
WriterInvoke = Callable[
    [str, AgentConfig, Path, dict[str, Any], ClaudePermissionMode | None, int],
    Awaitable[Any],
]
ReviewerInvoke = Callable[
    [str, AgentConfig, Path, dict[str, Any], SandboxMode | None, int],
    Awaitable[Any],
]


__all__ = [
    "apply_run_result",
    "load_project_context",
    "load_run_record",
    "persist_run_record",
    "reject_run_result",
    "resume_with_arbitration",
    "run",
]


# ──────────────────────────────────────────────────────────────────────────────
# Default invokers — thin adapters around agents.claude.invoke / agents.codex.invoke
# ──────────────────────────────────────────────────────────────────────────────


async def _default_claude_invoker(
    prompt: str,
    cfg: AgentConfig,
    cwd: Path,
    output_schema: dict,
    permission_mode: ClaudePermissionMode | None,
    timeout_s: int,
) -> ClaudeResult:
    return await _claude_invoke(
        ClaudeInvocation(
            config=cfg,
            prompt=prompt,
            cwd=cwd,
            output_schema=output_schema,
            permission_mode=permission_mode or ClaudePermissionMode.BYPASS,
        ),
        timeout_s=timeout_s,
    )


async def _default_codex_invoker(
    prompt: str,
    cfg: AgentConfig,
    cwd: Path,
    output_schema: dict,
    sandbox: SandboxMode | None,
    timeout_s: int,
) -> CodexResult:
    strict = _make_strict_schema(output_schema)
    fd, path_str = tempfile.mkstemp(suffix=".json", prefix="dialectic-schema-")
    os.close(fd)  # mkstemp leaves the fd open; close it to avoid leaking one per call.
    schema_path = Path(path_str)
    schema_path.write_text(json.dumps(strict))
    try:
        return await _codex_invoke(
            CodexInvocation(
                config=cfg,
                prompt=prompt,
                cwd=cwd,
                output_schema_path=schema_path,
                sandbox=sandbox or SandboxMode.WORKSPACE_WRITE,
            ),
            timeout_s=timeout_s,
        )
    finally:
        schema_path.unlink(missing_ok=True)


def _invoker_for(cli: AgentCli, *, override: Any = None) -> Any:
    if override is not None:
        return override
    return _default_claude_invoker if cli == AgentCli.CLAUDE else _default_codex_invoker


# ──────────────────────────────────────────────────────────────────────────────
# Prompt construction
# ──────────────────────────────────────────────────────────────────────────────


_SYSTEM_HEADER = (
    "You are participating in the dialectic protocol. You MUST output a single JSON object "
    "matching the provided schema — no prose, no fenced code blocks, just the JSON."
)


def _build_prompt_header(role: str, user_prompt: str, project_context: str) -> list[str]:
    """Shared header lines for any agent prompt: system note, role, task, project context.

    The initial writer pass labels the task `TASK FROM USER:` because the user
    is speaking directly to it; every subsequent phase refers back to the same
    text as `ORIGINAL TASK:` to make the chronological framing explicit.
    """
    task_label = "TASK FROM USER" if role == "writer" else "ORIGINAL TASK"
    parts = [
        _SYSTEM_HEADER,
        "",
        f"ROLE: {role}",
        "",
        f"{task_label}:",
        user_prompt,
        "",
    ]
    if project_context:
        parts.extend(["PROJECT CONTEXT:", project_context, ""])
    return parts


def _build_writer_initial_prompt(user_prompt: str, project_context: str) -> str:
    parts = _build_prompt_header("writer", user_prompt, project_context)
    parts.extend(
        [
            "INSTRUCTIONS:",
            "1. Implement the task in the working directory (your current cwd). Edit files freely.",
            "2. When done, output a single WriterReport JSON object describing what you did.",
            "3. The 'diff' field should contain the unified diff of your changes.",
            "4. Include any assumptions you made and any open questions.",
        ]
    )
    return "\n".join(parts)


def _build_reviewer_critique_prompt(
    user_prompt: str, writer_report: WriterReport, authoritative_diff: str, project_context: str
) -> str:
    parts = _build_prompt_header("reviewer", user_prompt, project_context)
    parts.extend(
        [
            "WRITER'S REPORT:",
            writer_report.model_dump_json(indent=2),
            "",
            "AUTHORITATIVE DIFF (extracted from the writer's worktree):",
            "```diff",
            authoritative_diff or "(empty diff — writer made no changes)",
            "```",
            "",
        ]
    )
    # v0.2: surface the writer's open_questions explicitly as items the reviewer
    # MUST address. Without this nudge, reviewers in past runs ignored every one
    # of the writer's open_questions (Evaluator A finding).
    if writer_report.open_questions:
        parts.extend(
            [
                "WRITER'S OPEN QUESTIONS (you MUST respond to each one in your critique):",
                *(f"  - {q}" for q in writer_report.open_questions),
                "",
                "For each open question above, either (a) raise it as a CritiqueItem with a "
                "concrete recommendation, or (b) explicitly endorse the writer's current choice "
                "in your summary by referring to the question. Do NOT silently ignore them.",
                "",
            ]
        )
    parts.extend(
        [
            "INSTRUCTIONS:",
            "1. Your cwd is a clean copy of the codebase at the base ref. You have a "
            "writable sandbox there — feel free to apply the diff (via `git apply` from "
            "stdin), run tests, lint, type-check, or otherwise execute verification "
            "commands. Your worktree is throwaway; any changes you make here are "
            "discarded at the end of the run.",
            "2. Review the diff against the original task and project conventions. "
            "Whenever feasible, EXECUTE something — tests, a lint, a quick script — "
            "rather than relying on inspection alone. State in your summary what you "
            "actually executed.",
            "3. Output a single ReviewerCritique JSON object.",
            "4. Use unique integer ids for each CritiqueItem. The writer references items by id.",
            "5. Set verdict=approve if the diff is ready as-is.",
            "6. Set verdict=revise if changes are needed (provide CritiqueItems).",
            "7. Set verdict=reject ONLY if the approach is fundamentally wrong and revision won't help.",
        ]
    )
    return "\n".join(parts)


def _build_writer_revision_prompt(
    user_prompt: str,
    writer_report: WriterReport,
    critique: ReviewerCritique,
    authoritative_diff: str,
    project_context: str,
) -> str:
    parts = _build_prompt_header("writer (revision pass)", user_prompt, project_context)
    parts.extend(
        [
            "YOUR PREVIOUS WORK (current state of your worktree):",
            "```diff",
            authoritative_diff,
            "```",
            "",
            "REVIEWER'S CRITIQUE:",
            critique.model_dump_json(indent=2),
            "",
            "INSTRUCTIONS:",
            "1. For each critique item, decide: accept (change the code) or reject (defend with rationale).",
            "2. If you accept, EDIT THE FILES in your worktree to address the issue, and set change_summary.",
            "3. If you reject, DO NOT change the code for that item, and provide a clear rationale.",
            "4. Your revised_diff field should reflect the cumulative state of your worktree after all accepts.",
            "5. Output a single WriterResponseBundle JSON object with one response per critique item id.",
        ]
    )
    return "\n".join(parts)


def _build_reviewer_rebuttal_prompt(
    user_prompt: str,
    writer_report: WriterReport,
    critique: ReviewerCritique,
    response_bundle: WriterResponseBundle,
    revised_diff: str,
    project_context: str,
) -> str:
    rejected = [r for r in response_bundle.responses if r.action == WriterAction.REJECT]
    parts = _build_prompt_header("reviewer (rebuttal pass)", user_prompt, project_context)
    parts.extend(
        [
            "YOUR PREVIOUS CRITIQUE:",
            critique.model_dump_json(indent=2),
            "",
            "REVISED DIFF (after writer's accepts):",
            "```diff",
            revised_diff,
            "```",
            "",
            "WRITER'S RESPONSES (focus on the rejected items below):",
            response_bundle.model_dump_json(indent=2),
            "",
            f"REJECTED ITEMS TO EVALUATE: {[r.item_id for r in rejected]}",
            "",
            "INSTRUCTIONS:",
            "1. For each item the writer REJECTED, evaluate their rationale.",
            "2. accept_writer_rationale: rationale is reasonable; let it ship.",
            "3. still_disputed: rationale is wrong or misses something; provide rebuttal_reasoning.",
            "4. Use accept where reasonable — do not insist for the sake of insisting.",
            "5. Output a single ReviewerRebuttal JSON object with item_rebuttals for each rejected item.",
        ]
    )
    return "\n".join(parts)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _gen_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


def _coerce_structured(raw: Any, model_cls: type[_M]) -> _M:
    """Try several shapes that the CLIs might return; raise ValueError if none parse."""
    from pydantic import ValidationError

    if isinstance(raw, model_cls):
        return raw
    if isinstance(raw, dict):
        return model_cls.model_validate(raw)
    if isinstance(raw, str):
        first_exc: Exception | None = None
        try:
            return model_cls.model_validate_json(raw)
        except (ValidationError, ValueError) as exc:
            first_exc = exc
        try:
            return model_cls.model_validate(json.loads(raw))
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"Could not coerce string to {model_cls.__name__}: {exc} "
                f"(first parse error: {first_exc}); raw={raw[:300]!r}"
            ) from exc
    raise ValueError(f"Could not coerce {type(raw).__name__} to {model_cls.__name__}")


def _classify_round_items(
    rounds: list[RevisionRound],
) -> tuple[list[DisputedItem], list[AcknowledgedDissent], list[CritiqueItem], list[CritiqueItem]]:
    """Walk all rounds; classify every critique item raised across the entire run.

    Returns (disputed, dissents, resolved, unresolved). Walking ALL rounds (not just
    the final one) is necessary so items resolved by the writer in earlier rounds
    aren't lost from `resolved_items`.

    Final-round-specific logic: if the reviewer raised items in the final round
    but the writer never got to respond (REVISE verdict + max_revisions exhausted),
    those items go to `unresolved` so the user can arbitrate them rather than
    have them silently disappear.
    """
    disputed: list[DisputedItem] = []
    dissents: list[AcknowledgedDissent] = []
    resolved: list[CritiqueItem] = []
    unresolved: list[CritiqueItem] = []

    if not rounds:
        return disputed, dissents, resolved, unresolved

    for idx, round_obj in enumerate(rounds):
        critique = round_obj.reviewer_critique
        responses = round_obj.writer_responses
        rebuttal = round_obj.reviewer_rebuttal
        is_final = idx == len(rounds) - 1

        if responses is None:
            # No writer response in this round.
            if critique.verdict == ReviewerVerdict.APPROVE:
                # Reviewer approved this round's diff — every item resolved trivially.
                resolved.extend(critique.items)
            elif is_final and critique.verdict == ReviewerVerdict.REVISE:
                # REVISE-at-max_revisions: writer never got to respond. Items need user attention.
                unresolved.extend(critique.items)
            # For REJECT, items are documented in the rounds but don't go to any bucket
            # (the run will be marked FAILED upstream).
            continue

        response_by_id = {r.item_id: r for r in responses.responses}
        rebuttal_by_id = (
            {r.item_id: r for r in rebuttal.item_rebuttals} if rebuttal is not None else {}
        )

        for item in critique.items:
            resp = response_by_id.get(item.id)
            if resp is None:
                # Defensive: shouldn't happen because _validate_response_covers_critique runs first.
                resolved.append(item)
                continue
            if resp.action == WriterAction.ACCEPT:
                resolved.append(item)
                continue
            # Writer rejected. Look for rebuttal.
            rb = rebuttal_by_id.get(item.id)
            if rb is None or rb.verdict == ItemRebuttalVerdict.ACCEPT_WRITER_RATIONALE:
                dissents.append(AcknowledgedDissent(item=item, writer_response=resp))
            else:
                disputed.append(
                    DisputedItem(item=item, writer_response=resp, reviewer_rebuttal_item=rb)
                )

    return disputed, dissents, resolved, unresolved


def _build_summary(
    rounds: list[RevisionRound],
    disputed: list[DisputedItem],
    dissents: list[AcknowledgedDissent],
    unresolved: list[CritiqueItem],
) -> str:
    if not rounds:
        return "(no rounds executed)"
    first_verdict = rounds[0].reviewer_critique.verdict
    if len(rounds) == 1 and first_verdict == ReviewerVerdict.APPROVE:
        return "Reviewer approved on first pass."
    total_items = sum(len(r.reviewer_critique.items) for r in rounds)
    parts = [f"{len(rounds)} round(s), {total_items} critique item(s) raised."]
    if dissents:
        parts.append(
            f"{len(dissents)} acknowledged dissent(s) (writer defended, reviewer accepted)."
        )
    if disputed:
        parts.append(f"{len(disputed)} disputed item(s) requiring user arbitration.")
    if unresolved:
        parts.append(
            f"{len(unresolved)} unresolved item(s) (max_revisions exhausted before writer could respond)."
        )
    return " ".join(parts)


# ──────────────────────────────────────────────────────────────────────────────
# Persistence
# ──────────────────────────────────────────────────────────────────────────────


def _ensure_dialectic_dir(repo_root: Path) -> Path:
    """Create .dialectic/ with a gitignore for transient artifacts (runs/, wt/)."""
    d = repo_root / ".dialectic"
    d.mkdir(exist_ok=True)
    gi = d / ".gitignore"
    if not gi.exists():
        gi.write_text(
            "# Auto-generated by dialectic. Safe to commit this file.\n"
            "# Transient run artifacts; project memory (context.md, journal.md) is not ignored.\n"
            "wt/\nruns/\n"
        )
    return d


_RUN_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6,}$")


def _validate_run_id(run_id: str) -> None:
    """Reject anything that isn't a well-formed run id.

    Guards against path traversal: `run_id` is interpolated into a filesystem
    path in load_run_record / persist_run_record / arbitrate. Without this check
    a user-supplied id like '../../etc/passwd' would escape `.dialectic/runs/`.
    """
    if not isinstance(run_id, str) or not _RUN_ID_RE.match(run_id):
        raise ValueError(f"Invalid run_id {run_id!r}: expected YYYYMMDD-HHMMSS-<hex>")


def _runs_dir(repo_root: Path) -> Path:
    d = _ensure_dialectic_dir(repo_root) / "runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def persist_run_record(result: RunResult, repo_root: Path) -> Path:
    _validate_run_id(result.run_id)
    path = _runs_dir(repo_root) / f"{result.run_id}.json"
    path.write_text(result.model_dump_json(indent=2))
    return path


def _prompts_log_path(repo_root: Path, run_id: str) -> Path:
    return _runs_dir(repo_root) / f"{run_id}.prompts.jsonl"


def _append_prompt_log(
    repo_root: Path,
    run_id: str,
    *,
    phase: str,
    round_num: int,
    role: str,
    prompt: str,
    response: Any,
    cost_usd: float,
    duration_s: float,
) -> None:
    """Append one agent invocation's prompt+response to the per-run forensic log."""
    path = _prompts_log_path(repo_root, run_id)
    entry = {
        "phase": phase,
        "round": round_num,
        "role": role,
        "prompt": prompt,
        "response": response
        if isinstance(response, (dict, list, str, int, float, type(None)))
        else str(response),
        "cost_usd": cost_usd,
        "duration_s": duration_s,
    }
    with path.open("a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def load_run_record(run_id: str, repo_root: Path) -> RunResult:
    _validate_run_id(run_id)
    runs_dir = _runs_dir(repo_root).resolve()
    path = (runs_dir / f"{run_id}.json").resolve()
    # Defense-in-depth: even after regex validation, refuse if the resolved path
    # escapes the runs directory.
    if not str(path).startswith(str(runs_dir) + "/"):
        raise ValueError(f"run_id {run_id!r} resolves outside runs dir")
    if not path.exists():
        raise FileNotFoundError(f"No run record for {run_id!r} at {path}")
    record = RunResult.model_validate_json(path.read_text())

    # Schema version compatibility check.
    record_ver = _parse_semver(record.protocol_version)
    current_ver = _parse_semver(CURRENT_PROTOCOL_VERSION)
    if record_ver > current_ver:
        raise ValueError(
            f"Run {run_id} was written with protocol_version={record.protocol_version}, "
            f"newer than this dialectic's {CURRENT_PROTOCOL_VERSION}. "
            "Upgrade dialectic to load this record."
        )
    if record_ver < current_ver:
        logger.warning(
            "Run %s was written with protocol_version=%s; loading under %s. "
            "Some fields may be defaulted; apply-time safety checks (e.g. base_sha) "
            "may be unavailable.",
            run_id,
            record.protocol_version,
            CURRENT_PROTOCOL_VERSION,
        )

    return record


def _parse_semver(v: str) -> tuple[int, int, int]:
    """Tolerant semver parse. Treats `X`/`X.Y`/`X.Y.Z` uniformly; ignores any suffix."""
    parts = v.split(".")[:3]
    nums = [int(p.split("-")[0]) for p in parts]
    while len(nums) < 3:
        nums.append(0)
    return (nums[0], nums[1], nums[2])


# ──────────────────────────────────────────────────────────────────────────────
# Context loading
# ──────────────────────────────────────────────────────────────────────────────


def load_project_context(repo_root: Path, config: RunConfig) -> str:
    """Read .dialectic/context.md (+ journal.md when v1.5) into a single string."""
    parts: list[str] = []
    context_file = config.context_file or (repo_root / ".dialectic" / "context.md")
    if context_file.exists():
        parts.append(f"# Project context (from {context_file.name})\n\n{context_file.read_text()}")
    journal_file = config.journal_file
    if journal_file and journal_file.exists():
        parts.append(f"# Project journal (from {journal_file.name})\n\n{journal_file.read_text()}")
    return "\n\n".join(parts)


# ──────────────────────────────────────────────────────────────────────────────
# The dialectic loop
# ──────────────────────────────────────────────────────────────────────────────


async def run(
    config: RunConfig,
    repo_root: Path,
    *,
    writer_invoke: Any | None = None,
    reviewer_invoke: Any | None = None,
    on_event: Callable[[StreamEvent], None] | None = None,
) -> RunResult:
    """Execute one full dialectic run. Returns a RunResult; does NOT apply the diff.

    If `on_event` is provided, it is called synchronously with each StreamEvent
    as the dance progresses (RUN_STARTED, WRITER_STARTED/DONE, REVIEWER_STARTED/DONE,
    REVISION_STARTED/DONE, REBUTTAL_STARTED/DONE, DIFF_READY, RUN_FINISHED, ERROR).
    """
    run_id = _gen_run_id()
    started_at = datetime.now(UTC)

    def emit(
        event_type: EventType, message: str = "", payload: dict[str, Any] | None = None
    ) -> None:
        if on_event is None:
            return
        try:
            on_event(
                StreamEvent(
                    event_type=event_type,
                    run_id=run_id,
                    message=message,
                    payload=payload or {},
                )
            )
        except Exception as exc:  # never let event handlers break the run
            logger.warning("on_event handler raised: %s", exc)

    writer_inv = _invoker_for(config.writer.cli, override=writer_invoke)
    reviewer_inv = _invoker_for(config.reviewer.cli, override=reviewer_invoke)

    writer_report_schema = WriterReport.model_json_schema()
    critique_schema = ReviewerCritique.model_json_schema()
    response_schema = WriterResponseBundle.model_json_schema()
    rebuttal_schema = ReviewerRebuttal.model_json_schema()

    result = RunResult(
        protocol_version=CURRENT_PROTOCOL_VERSION,
        run_id=run_id,
        status=RunStatus.AWAITING_APPROVAL,
        config=config,
        started_at=started_at,
    )

    total_cost = 0.0
    rounds: list[RevisionRound] = []

    emit(
        EventType.RUN_STARTED,
        f"Starting run {run_id}",
        {
            "writer": f"{config.writer.cli.value}/{config.writer.model}/{config.writer.effort}",
            "reviewer": f"{config.reviewer.cli.value}/{config.reviewer.model}/{config.reviewer.effort}",
            "max_revisions": config.max_revisions,
        },
    )

    try:
        with wt.worktree_pair(
            repo_root, run_id, config.base_ref, keep_on_failure=config.keep_worktrees
        ) as pair:
            # Capture base_sha so apply_run_result compares against the actual SHA
            # this run was based on (re-resolving base_ref="HEAD" at apply time
            # would be tautological).
            result.base_sha = pair.base_sha
            project_context = load_project_context(repo_root, config)

            # ─── INITIAL WRITER (runs once before the loop) ───
            writer_prompt = _build_writer_initial_prompt(config.prompt, project_context)
            emit(
                EventType.WRITER_STARTED,
                f"Round 1: writer ({config.writer.cli.value}) initial draft",
            )
            writer_result = await _invoke_logged(
                writer_inv,
                cli=config.writer.cli,
                prompt=writer_prompt,
                cfg=config.writer,
                cwd=pair.writer_path,
                output_schema=writer_report_schema,
                extra={"permission_mode": ClaudePermissionMode.BYPASS},
                timeout_s=config.timeout_per_agent_s,
                repo_root=repo_root,
                run_id=run_id,
                phase="writer_initial",
                round_num=1,
                role="writer",
            )
            total_cost += float(writer_result.cost_usd or 0.0)
            emit(
                EventType.WRITER_DONE,
                f"Round 1: writer done (${writer_result.cost_usd:.4f}, "
                f"{writer_result.duration_s:.1f}s)",
                {"cost_usd": writer_result.cost_usd, "duration_s": writer_result.duration_s},
            )
            if writer_result.is_error:
                raise RuntimeError(f"Writer failed: {writer_result.error}")

            writer_report = _coerce_structured(writer_result.structured, WriterReport)

            # ─── LOOP: critique → respond → (maybe rebut) → loop or terminate ───
            round_num = 1
            while True:
                authoritative_diff = wt.extract_diff(pair)
                if len(authoritative_diff.splitlines()) > config.max_diff_lines:
                    raise RuntimeError(
                        f"Diff exceeds max_diff_lines={config.max_diff_lines} "
                        f"({len(authoritative_diff.splitlines())} lines); narrow the prompt."
                    )

                # ─── REVIEWER critique ───
                reviewer_prompt = _build_reviewer_critique_prompt(
                    config.prompt, writer_report, authoritative_diff, project_context
                )
                emit(
                    EventType.REVIEWER_STARTED,
                    f"Round {round_num}: reviewer ({config.reviewer.cli.value})",
                )
                reviewer_result = await _invoke_logged(
                    reviewer_inv,
                    cli=config.reviewer.cli,
                    prompt=reviewer_prompt,
                    cfg=config.reviewer,
                    cwd=pair.reviewer_path,
                    output_schema=critique_schema,
                    extra={"sandbox": SandboxMode.WORKSPACE_WRITE},
                    timeout_s=config.timeout_per_agent_s,
                    repo_root=repo_root,
                    run_id=run_id,
                    phase="reviewer_critique",
                    round_num=round_num,
                    role="reviewer",
                )
                total_cost += float(reviewer_result.cost_usd or 0.0)
                if reviewer_result.is_error:
                    raise RuntimeError(f"Reviewer failed: {reviewer_result.error}")
                critique = _coerce_structured(reviewer_result.structured, ReviewerCritique)
                _validate_critique_unique_ids(critique)
                emit(
                    EventType.REVIEWER_DONE,
                    f"Round {round_num}: reviewer verdict={critique.verdict.value} "
                    f"({len(critique.items)} items, ${reviewer_result.cost_usd:.4f})",
                    {"verdict": critique.verdict.value, "items": len(critique.items)},
                )

                round_obj = RevisionRound(
                    round_number=round_num,
                    writer_report=writer_report,
                    reviewer_critique=critique,
                    writer_responses=None,
                    reviewer_rebuttal=None,
                )

                # ─── Terminal verdicts on the critique ───
                if critique.verdict == ReviewerVerdict.APPROVE:
                    rounds.append(round_obj)
                    break

                if critique.verdict == ReviewerVerdict.REJECT:
                    rounds.append(round_obj)
                    result.status = RunStatus.FAILED
                    result.error = f"Reviewer rejected outright: {critique.summary}"
                    break

                # REVISE: out of revisions → ship; items get classified as unresolved.
                if round_num > config.max_revisions:
                    rounds.append(round_obj)
                    break

                # ─── WRITER per-item response (the one writer call per revision round) ───
                writer_response_prompt = _build_writer_revision_prompt(
                    config.prompt,
                    writer_report,
                    critique,
                    authoritative_diff,
                    project_context,
                )
                emit(
                    EventType.REVISION_STARTED,
                    f"Round {round_num}: writer responding to {len(critique.items)} item(s)",
                )
                writer_response_result = await _invoke_logged(
                    writer_inv,
                    cli=config.writer.cli,
                    prompt=writer_response_prompt,
                    cfg=config.writer,
                    cwd=pair.writer_path,
                    output_schema=response_schema,
                    extra={"permission_mode": ClaudePermissionMode.BYPASS},
                    timeout_s=config.timeout_per_agent_s,
                    repo_root=repo_root,
                    run_id=run_id,
                    phase="writer_response",
                    round_num=round_num,
                    role="writer",
                )
                total_cost += float(writer_response_result.cost_usd or 0.0)
                if writer_response_result.is_error:
                    raise RuntimeError(f"Writer revision failed: {writer_response_result.error}")
                response_bundle = _coerce_structured(
                    writer_response_result.structured, WriterResponseBundle
                )
                _validate_response_covers_critique(response_bundle, critique)
                round_obj.writer_responses = response_bundle
                n_accept = sum(
                    1 for r in response_bundle.responses if r.action == WriterAction.ACCEPT
                )
                n_reject = len(response_bundle.responses) - n_accept
                emit(
                    EventType.REVISION_DONE,
                    f"Round {round_num}: writer accepted {n_accept}, defended {n_reject} "
                    f"(${writer_response_result.cost_usd:.4f})",
                    {"accepted": n_accept, "rejected": n_reject},
                )

                # Re-check size after writer's edits.
                revised_diff = wt.extract_diff(pair)
                if len(revised_diff.splitlines()) > config.max_diff_lines:
                    raise RuntimeError(
                        f"Revised diff exceeds max_diff_lines={config.max_diff_lines}"
                    )

                rejected_responses = [
                    r for r in response_bundle.responses if r.action == WriterAction.REJECT
                ]

                if rejected_responses:
                    # ─── REVIEWER REBUTTAL ───
                    emit(
                        EventType.REBUTTAL_STARTED,
                        f"Round {round_num}: reviewer rebutting {len(rejected_responses)} defended item(s)",
                    )
                    rebuttal_prompt = _build_reviewer_rebuttal_prompt(
                        config.prompt,
                        writer_report,
                        critique,
                        response_bundle,
                        revised_diff,
                        project_context,
                    )
                    rebuttal_result = await _invoke_logged(
                        reviewer_inv,
                        cli=config.reviewer.cli,
                        prompt=rebuttal_prompt,
                        cfg=config.reviewer,
                        cwd=pair.reviewer_path,
                        output_schema=rebuttal_schema,
                        extra={"sandbox": SandboxMode.WORKSPACE_WRITE},
                        timeout_s=config.timeout_per_agent_s,
                        repo_root=repo_root,
                        run_id=run_id,
                        phase="reviewer_rebuttal",
                        round_num=round_num,
                        role="reviewer",
                    )
                    total_cost += float(rebuttal_result.cost_usd or 0.0)
                    if rebuttal_result.is_error:
                        raise RuntimeError(f"Reviewer rebuttal failed: {rebuttal_result.error}")
                    rebuttal = _coerce_structured(rebuttal_result.structured, ReviewerRebuttal)
                    _validate_rebuttal_covers_rejections(rebuttal, response_bundle)
                    round_obj.reviewer_rebuttal = rebuttal
                    n_still = sum(
                        1
                        for r in rebuttal.item_rebuttals
                        if r.verdict == ItemRebuttalVerdict.STILL_DISPUTED
                    )
                    emit(
                        EventType.REBUTTAL_DONE,
                        f"Round {round_num}: rebuttal verdict={rebuttal.verdict.value} "
                        f"({n_still} still disputed, ${rebuttal_result.cost_usd:.4f})",
                    )
                    rounds.append(round_obj)
                    # After a rebuttal we always terminate this run — any still_disputed
                    # items escalate to the user (no further iteration would change them
                    # without new information).
                    break

                # All accepts: append round, loop back for another reviewer pass on the
                # revised diff. No additional writer call — the writer's accepts already
                # changed the worktree, and the loop's next iteration extracts the new
                # diff and re-critiques.
                rounds.append(round_obj)
                round_num += 1

            # ─── Final assembly ───
            result.diff = wt.extract_diff(pair)
            result.files_changed = _files_in_diff(result.diff)

    except Exception as exc:
        result.status = RunStatus.FAILED
        result.error = str(exc)

    finally:
        result.rounds = rounds
        result.cost_usd = round(total_cost, 6)
        result.finished_at = datetime.now(UTC)
        if result.started_at is not None:
            result.duration_s = (result.finished_at - result.started_at).total_seconds()

        if result.status != RunStatus.FAILED:
            disputed, dissents, resolved, unresolved = _classify_round_items(rounds)
            result.disputed_items = disputed
            result.acknowledged_dissents = dissents
            result.resolved_items = resolved
            result.unresolved_items = unresolved
            if disputed or unresolved:
                result.status = RunStatus.AWAITING_ARBITRATION
            else:
                result.status = RunStatus.AWAITING_APPROVAL
            result.summary = _build_summary(rounds, disputed, dissents, unresolved)

        result.audit_log_path = str(persist_run_record(result, repo_root))

        emit(
            EventType.RUN_FINISHED,
            f"Run {run_id} {result.status.value} (${result.cost_usd:.4f}, {result.duration_s:.1f}s)",
            {"status": result.status.value, "cost_usd": result.cost_usd},
        )

    return result


async def _invoke(
    invoker: Any,
    *,
    cli: AgentCli,
    prompt: str,
    cfg: AgentConfig,
    cwd: Path,
    output_schema: dict,
    extra: dict,
    timeout_s: int,
) -> Any:
    """Adapter so the orchestrator can call writer/reviewer invokers with a uniform signature."""
    if cli == AgentCli.CLAUDE:
        return await invoker(
            prompt, cfg, cwd, output_schema, extra.get("permission_mode"), timeout_s
        )
    return await invoker(prompt, cfg, cwd, output_schema, extra.get("sandbox"), timeout_s)


async def _invoke_logged(
    invoker: Any,
    *,
    cli: AgentCli,
    prompt: str,
    cfg: AgentConfig,
    cwd: Path,
    output_schema: dict,
    extra: dict,
    timeout_s: int,
    repo_root: Path,
    run_id: str,
    phase: str,
    round_num: int,
    role: str,
) -> Any:
    """Wrap _invoke with logging + per-run prompts.jsonl audit append."""
    logger.info(
        "[%s] %s round=%d role=%s cli=%s model=%s effort=%s",
        run_id,
        phase,
        round_num,
        role,
        cli.value,
        cfg.model,
        cfg.effort,
    )
    logger.debug("[%s] %s prompt (%d chars):\n%s", run_id, phase, len(prompt), prompt)

    result = await _invoke(
        invoker,
        cli=cli,
        prompt=prompt,
        cfg=cfg,
        cwd=cwd,
        output_schema=output_schema,
        extra=extra,
        timeout_s=timeout_s,
    )

    logger.info(
        "[%s] %s done cost=$%.4f duration=%.1fs%s",
        run_id,
        phase,
        result.cost_usd or 0.0,
        result.duration_s or 0.0,
        f" ERROR={result.error}" if result.is_error else "",
    )
    logger.debug("[%s] %s response: %s", run_id, phase, result.structured)

    _append_prompt_log(
        repo_root,
        run_id,
        phase=phase,
        round_num=round_num,
        role=role,
        prompt=prompt,
        response=result.structured or result.raw_text,
        cost_usd=result.cost_usd or 0.0,
        duration_s=result.duration_s or 0.0,
    )
    return result


# ──────────────────────────────────────────────────────────────────────────────
# Invariant checks — enforce protocol correctness on parsed responses
# ──────────────────────────────────────────────────────────────────────────────


def _validate_critique_unique_ids(critique: ReviewerCritique) -> None:
    ids = [item.id for item in critique.items]
    dupes = [i for i, c in Counter(ids).items() if c > 1]
    if dupes:
        raise RuntimeError(f"Critique has duplicate item ids: {sorted(dupes)}")


def _validate_response_covers_critique(
    responses: WriterResponseBundle, critique: ReviewerCritique
) -> None:
    """Writer must respond to every critique item exactly once, no extras."""
    critique_ids = {item.id for item in critique.items}
    response_ids_list = [r.item_id for r in responses.responses]
    response_ids = set(response_ids_list)

    missing = critique_ids - response_ids
    if missing:
        raise RuntimeError(f"Writer didn't respond to critique item id(s): {sorted(missing)}")
    extra = response_ids - critique_ids
    if extra:
        raise RuntimeError(f"Writer responded to unknown item id(s): {sorted(extra)}")
    dupes = [i for i, c in Counter(response_ids_list).items() if c > 1]
    if dupes:
        raise RuntimeError(
            f"Writer responded to the same item id(s) multiple times: {sorted(dupes)}"
        )


def _validate_rebuttal_covers_rejections(
    rebuttal: ReviewerRebuttal, responses: WriterResponseBundle
) -> None:
    """Reviewer must address every rejected response item exactly once."""
    rejected = {r.item_id for r in responses.responses if r.action == WriterAction.REJECT}
    rebutted = {r.item_id for r in rebuttal.item_rebuttals}
    missing = rejected - rebutted
    if missing:
        raise RuntimeError(f"Reviewer didn't rebut rejected item id(s): {sorted(missing)}")
    extra = rebutted - rejected
    if extra:
        raise RuntimeError(f"Reviewer rebutted non-rejected item id(s): {sorted(extra)}")


def _files_in_diff(diff: str) -> list[str]:
    """Return the deduped list of files touched by a unified diff (modifications + deletions)."""
    files: list[str] = []
    seen: set[str] = set()
    for line in diff.splitlines():
        path: str | None = None
        if line.startswith("+++ b/"):
            candidate = line[len("+++ b/") :].strip()
            if candidate != "/dev/null":
                path = candidate
        elif line.startswith("--- a/"):
            # Captures deletions where the +++ side is /dev/null.
            candidate = line[len("--- a/") :].strip()
            if candidate != "/dev/null":
                path = candidate
        if path and path not in seen:
            seen.add(path)
            files.append(path)
    return files


# ──────────────────────────────────────────────────────────────────────────────
# Streaming variant
# ──────────────────────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
# Apply, reject, arbitrate
# ──────────────────────────────────────────────────────────────────────────────


def apply_run_result(result: RunResult, repo_root: Path) -> RunResult:
    if result.status != RunStatus.AWAITING_APPROVAL:
        raise RuntimeError(
            f"Cannot apply run {result.run_id}: status={result.status.value} "
            "(must be AWAITING_APPROVAL — resolve any disputes/unresolved items via "
            "`dialectic arbitrate` first)."
        )

    # Refuse if user is mid-rebase / mid-merge / etc. Applying then would corrupt
    # the operation state.
    in_progress = wt.in_progress_git_operation(repo_root)
    if in_progress:
        raise RuntimeError(
            f"Refusing to apply: git operation {in_progress!r} in progress. "
            "Finish (or abort) the rebase/merge/cherry-pick first."
        )

    # Use the SHA captured at run-start, not a fresh resolution of base_ref.
    # Re-resolving "HEAD" here would be tautologically equal to current_head
    # even after the user has switched branches or committed something else.
    # An empty base_sha means the record predates base_sha capture (Phase 2),
    # so we can no longer verify HEAD-stability: refuse rather than silently
    # falling back to re-resolution, which defeats the whole safety check.
    base_sha = result.base_sha
    if not base_sha:
        raise RuntimeError(
            f"Run {result.run_id} has no captured base_sha (record predates the field). "
            "Cannot verify HEAD-stability at apply time. Re-run with the current dialectic "
            "version to capture base_sha, or extract the diff manually with "
            f"`jq -r .diff .dialectic/runs/{result.run_id}.json | git apply` if you accept "
            "that you may be applying against a different base than the run was built from."
        )

    current_head = wt.current_head_sha(repo_root)
    if current_head != base_sha:
        raise RuntimeError(
            f"HEAD moved since run started (base was {base_sha[:8]}, now {current_head[:8]}). "
            "Reset to the base ref, or rebase and re-run."
        )

    if result.config.apply_mode == ApplyMode.DRY_RUN:
        # No-op; keep status as AWAITING_APPROVAL so the user can still
        # `dialectic approve` with a real apply_mode later if they want.
        persist_run_record(result, repo_root)
        return result

    if result.config.apply_mode == ApplyMode.UNCOMMITTED:
        wt.apply_diff_to_working_tree(repo_root, result.diff)
    elif result.config.apply_mode == ApplyMode.BRANCH:
        branch_name = result.config.branch_name or f"dialectic/{result.run_id}"
        commit_msg = (
            f"dialectic run {result.run_id}\n\n{result.summary or result.config.prompt[:200]}"
        )
        wt.apply_diff_to_new_branch(repo_root, result.diff, branch_name, base_sha, commit_msg)
    else:
        raise RuntimeError(f"Unknown apply_mode: {result.config.apply_mode}")

    result.status = (
        RunStatus.APPLIED_WITH_DISSENT if result.acknowledged_dissents else RunStatus.SUCCESS
    )
    persist_run_record(result, repo_root)
    return result


def reject_run_result(result: RunResult, repo_root: Path) -> RunResult:
    result.status = RunStatus.REJECTED_BY_USER
    persist_run_record(result, repo_root)
    return result


async def resume_with_arbitration(
    run_id: str, decisions: list[ArbitrationDecision], repo_root: Path
) -> RunResult:
    result = load_run_record(run_id, repo_root)
    if result.status != RunStatus.AWAITING_ARBITRATION:
        raise RuntimeError(
            f"Run {run_id} is not awaiting arbitration (status={result.status.value})."
        )

    # Combine disputed_items (writer rejected + reviewer disputed) and unresolved_items
    # (max_revisions exhausted before writer could respond) — both need user arbitration.
    needs_decision_ids = {d.item.id for d in result.disputed_items} | {
        item.id for item in result.unresolved_items
    }

    decision_ids_list = [d.item_id for d in decisions]
    decision_ids = set(decision_ids_list)
    if len(decision_ids_list) != len(decision_ids):
        dupes = [i for i, c in Counter(decision_ids_list).items() if c > 1]
        raise RuntimeError(f"Duplicate arbitration decisions for item id(s): {sorted(dupes)}")

    missing = needs_decision_ids - decision_ids
    extra_ids = decision_ids - needs_decision_ids
    if missing:
        raise RuntimeError(f"Missing arbitration decisions for item id(s): {sorted(missing)}")
    if extra_ids:
        raise RuntimeError(f"Got decisions for non-disputed item id(s): {sorted(extra_ids)}")

    # ACCEPT_REVIEWER requires applying suggested_fix as a patch on top of the writer's
    # diff, which needs a fix-format convention we haven't pinned down (suggested_fix is
    # free-text). Defer until v1.1.
    for decision in decisions:
        if decision.choice == ArbitrationChoice.ACCEPT_REVIEWER:
            raise NotImplementedError(
                "ACCEPT_REVIEWER not yet supported in v1 — pick ACCEPT_WRITER or SKIP, "
                "then manually apply the reviewer's suggested_fix after `dialectic approve`."
            )

    result.arbitration = decisions
    decision_by_id = {decision.item_id: decision for decision in decisions}

    # Move disputed items to dissents (they ship with the writer's diff + arbitration log).
    for disputed in result.disputed_items:
        if decision_by_id[disputed.item.id].choice in (
            ArbitrationChoice.ACCEPT_WRITER,
            ArbitrationChoice.SKIP,
        ):
            result.acknowledged_dissents.append(
                AcknowledgedDissent(item=disputed.item, writer_response=disputed.writer_response)
            )
    result.disputed_items = []

    # Unresolved items have no writer_response; if the user accepts/skips, they're noted
    # in arbitration. We don't promote them to dissents (no rationale to record).
    result.unresolved_items = []

    result.status = RunStatus.AWAITING_APPROVAL
    persist_run_record(result, repo_root)
    return result
