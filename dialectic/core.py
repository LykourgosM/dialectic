"""Orchestrator core: the dialectic loop."""

from __future__ import annotations

import json
import logging
import tempfile
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable

logger = logging.getLogger("dialectic")

from . import worktree as wt
from .agents.claude import ClaudeInvocation, ClaudeResult
from .agents.claude import invoke as _claude_invoke
from .agents.codex import CodexInvocation, CodexResult
from .agents.codex import _make_strict_schema
from .agents.codex import invoke as _codex_invoke
from .protocol import (
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
    RebuttalVerdict,
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

# Invokers can be swapped for testing.
WriterInvoke = Callable[[str, AgentConfig, Path, dict, ClaudePermissionMode | None, int], Awaitable[Any]]
ReviewerInvoke = Callable[[str, AgentConfig, Path, dict, SandboxMode | None, int], Awaitable[Any]]


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
    schema_path = Path(tempfile.mkstemp(suffix=".json", prefix="dialectic-schema-")[1])
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


def _build_writer_initial_prompt(user_prompt: str, project_context: str) -> str:
    parts = [
        _SYSTEM_HEADER,
        "",
        "ROLE: writer",
        "",
        "TASK FROM USER:",
        user_prompt,
        "",
    ]
    if project_context:
        parts.extend(["PROJECT CONTEXT:", project_context, ""])
    parts.extend([
        "INSTRUCTIONS:",
        "1. Implement the task in the working directory (your current cwd). Edit files freely.",
        "2. When done, output a single WriterReport JSON object describing what you did.",
        "3. The 'diff' field should contain the unified diff of your changes.",
        "4. Include any assumptions you made and any open questions.",
    ])
    return "\n".join(parts)


def _build_reviewer_critique_prompt(
    user_prompt: str, writer_report: WriterReport, authoritative_diff: str, project_context: str
) -> str:
    parts = [
        _SYSTEM_HEADER,
        "",
        "ROLE: reviewer",
        "",
        "ORIGINAL TASK:",
        user_prompt,
        "",
    ]
    if project_context:
        parts.extend(["PROJECT CONTEXT:", project_context, ""])
    parts.extend([
        "WRITER'S REPORT:",
        writer_report.model_dump_json(indent=2),
        "",
        "AUTHORITATIVE DIFF (extracted from the writer's worktree):",
        "```diff",
        authoritative_diff or "(empty diff — writer made no changes)",
        "```",
        "",
        "INSTRUCTIONS:",
        "1. You have read-only access to the codebase via your cwd (a clean copy at the base ref).",
        "2. Review the diff against the original task and project conventions.",
        "3. Output a single ReviewerCritique JSON object.",
        "4. Use unique integer ids for each CritiqueItem. The writer references items by id.",
        "5. Set verdict=approve if the diff is ready as-is.",
        "6. Set verdict=revise if changes are needed (provide CritiqueItems).",
        "7. Set verdict=reject ONLY if the approach is fundamentally wrong and revision won't help.",
    ])
    return "\n".join(parts)


def _build_writer_revision_prompt(
    user_prompt: str,
    writer_report: WriterReport,
    critique: ReviewerCritique,
    authoritative_diff: str,
    project_context: str,
) -> str:
    parts = [
        _SYSTEM_HEADER,
        "",
        "ROLE: writer (revision pass)",
        "",
        "ORIGINAL TASK:",
        user_prompt,
        "",
    ]
    if project_context:
        parts.extend(["PROJECT CONTEXT:", project_context, ""])
    parts.extend([
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
    ])
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
    parts = [
        _SYSTEM_HEADER,
        "",
        "ROLE: reviewer (rebuttal pass)",
        "",
        "ORIGINAL TASK:",
        user_prompt,
        "",
    ]
    if project_context:
        parts.extend(["PROJECT CONTEXT:", project_context, ""])
    parts.extend([
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
    ])
    return "\n".join(parts)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _gen_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


def _coerce_structured(raw: Any, model_cls: type) -> Any:
    """Try several shapes that the CLIs might return; raise ValueError if none parse."""
    if isinstance(raw, model_cls):
        return raw
    if isinstance(raw, dict):
        return model_cls.model_validate(raw)
    if isinstance(raw, str):
        try:
            return model_cls.model_validate_json(raw)
        except Exception:
            try:
                return model_cls.model_validate(json.loads(raw))
            except Exception as exc:
                raise ValueError(
                    f"Could not coerce string to {model_cls.__name__}: {exc}; raw={raw[:300]!r}"
                ) from exc
    raise ValueError(f"Could not coerce {type(raw).__name__} to {model_cls.__name__}")


def _classify_round_items(
    rounds: list[RevisionRound],
) -> tuple[list[DisputedItem], list[AcknowledgedDissent], list[CritiqueItem]]:
    """Walk all rounds; produce disputed, dissents, resolved from the final round's outcomes."""
    if not rounds:
        return [], [], []
    final = rounds[-1]
    critique = final.reviewer_critique
    responses = final.writer_responses
    rebuttal = final.reviewer_rebuttal

    disputed: list[DisputedItem] = []
    dissents: list[AcknowledgedDissent] = []
    resolved: list[CritiqueItem] = []

    if responses is None:
        # No revision happened in the final round — either initial approve or aborted.
        if critique.verdict == ReviewerVerdict.APPROVE:
            resolved.extend(critique.items)
        return disputed, dissents, resolved

    item_by_id = {item.id: item for item in critique.items}
    response_by_id = {r.item_id: r for r in responses.responses}
    rebuttal_by_id = (
        {r.item_id: r for r in rebuttal.item_rebuttals} if rebuttal is not None else {}
    )

    for item in critique.items:
        resp = response_by_id.get(item.id)
        if resp is None:
            resolved.append(item)
            continue
        if resp.action == WriterAction.ACCEPT:
            resolved.append(item)
            continue
        rb = rebuttal_by_id.get(item.id)
        if rb is None or rb.verdict == ItemRebuttalVerdict.ACCEPT_WRITER_RATIONALE:
            dissents.append(AcknowledgedDissent(item=item, writer_response=resp))
        else:
            disputed.append(
                DisputedItem(item=item, writer_response=resp, reviewer_rebuttal_item=rb)
            )
    return disputed, dissents, resolved


def _build_summary(
    rounds: list[RevisionRound],
    disputed: list[DisputedItem],
    dissents: list[AcknowledgedDissent],
) -> str:
    if not rounds:
        return "(no rounds executed)"
    first_verdict = rounds[0].reviewer_critique.verdict
    if len(rounds) == 1 and first_verdict == ReviewerVerdict.APPROVE:
        return "Reviewer approved on first pass."
    total_items = sum(len(r.reviewer_critique.items) for r in rounds)
    parts = [
        f"{len(rounds)} round(s), {total_items} critique item(s) raised."
    ]
    if dissents:
        parts.append(f"{len(dissents)} acknowledged dissent(s) (writer defended, reviewer accepted).")
    if disputed:
        parts.append(f"{len(disputed)} disputed item(s) requiring user arbitration.")
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


def _runs_dir(repo_root: Path) -> Path:
    d = _ensure_dialectic_dir(repo_root) / "runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def persist_run_record(result: RunResult, repo_root: Path) -> Path:
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
        "response": response if isinstance(response, (dict, list, str, int, float, type(None))) else str(response),
        "cost_usd": cost_usd,
        "duration_s": duration_s,
    }
    with path.open("a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def load_run_record(run_id: str, repo_root: Path) -> RunResult:
    path = _runs_dir(repo_root) / f"{run_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"No run record for {run_id!r} at {path}")
    return RunResult.model_validate_json(path.read_text())


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
    started_at = datetime.now(timezone.utc)

    def emit(event_type: EventType, message: str = "", payload: dict[str, Any] | None = None) -> None:
        if on_event is None:
            return
        try:
            on_event(StreamEvent(
                event_type=event_type, run_id=run_id, message=message, payload=payload or {},
            ))
        except Exception as exc:  # never let event handlers break the run
            logger.warning("on_event handler raised: %s", exc)

    writer_inv = _invoker_for(config.writer.cli, override=writer_invoke)
    reviewer_inv = _invoker_for(config.reviewer.cli, override=reviewer_invoke)

    writer_report_schema = WriterReport.model_json_schema()
    critique_schema = ReviewerCritique.model_json_schema()
    response_schema = WriterResponseBundle.model_json_schema()
    rebuttal_schema = ReviewerRebuttal.model_json_schema()

    result = RunResult(
        run_id=run_id,
        status=RunStatus.AWAITING_APPROVAL,
        config=config,
        started_at=started_at,
    )

    total_cost = 0.0
    rounds: list[RevisionRound] = []

    emit(EventType.RUN_STARTED, f"Starting run {run_id}", {
        "writer": f"{config.writer.cli.value}/{config.writer.model}/{config.writer.effort}",
        "reviewer": f"{config.reviewer.cli.value}/{config.reviewer.model}/{config.reviewer.effort}",
        "max_revisions": config.max_revisions,
    })

    try:
        with wt.worktree_pair(
            repo_root, run_id, config.base_ref, keep_on_failure=config.keep_worktrees
        ) as pair:
            project_context = load_project_context(repo_root, config)

            for round_num in range(1, config.max_revisions + 2):
                # ─── WRITER ───
                if round_num == 1:
                    writer_prompt = _build_writer_initial_prompt(config.prompt, project_context)
                    writer_call_kwargs: dict[str, Any] = {
                        "permission_mode": ClaudePermissionMode.BYPASS,
                    }
                else:
                    prev = rounds[-1]
                    writer_prompt = _build_writer_revision_prompt(
                        config.prompt,
                        prev.writer_report,
                        prev.reviewer_critique,
                        wt.extract_diff(pair),
                        project_context,
                    )
                    writer_call_kwargs = {"permission_mode": ClaudePermissionMode.BYPASS}

                phase_label = "writer_initial" if round_num == 1 else "writer_continuation"
                emit(EventType.WRITER_STARTED, f"Round {round_num}: writer ({config.writer.cli.value})")
                writer_result = await _invoke_logged(
                    writer_inv,
                    cli=config.writer.cli,
                    prompt=writer_prompt,
                    cfg=config.writer,
                    cwd=pair.writer_path,
                    output_schema=(
                        writer_report_schema if round_num == 1 else response_schema
                    ),
                    extra=writer_call_kwargs,
                    timeout_s=config.timeout_per_agent_s,
                    repo_root=repo_root,
                    run_id=run_id,
                    phase=phase_label,
                    round_num=round_num,
                    role="writer",
                )
                total_cost += float(writer_result.cost_usd or 0.0)
                emit(
                    EventType.WRITER_DONE,
                    f"Round {round_num}: writer done (${writer_result.cost_usd:.4f}, "
                    f"{writer_result.duration_s:.1f}s)",
                    {"cost_usd": writer_result.cost_usd, "duration_s": writer_result.duration_s},
                )
                if writer_result.is_error:
                    raise RuntimeError(f"Writer failed: {writer_result.error}")

                if round_num == 1:
                    writer_report = _coerce_structured(writer_result.structured, WriterReport)
                    writer_responses_bundle: WriterResponseBundle | None = None
                else:
                    writer_responses_bundle = _coerce_structured(
                        writer_result.structured, WriterResponseBundle
                    )
                    writer_report = rounds[-1].writer_report  # carry forward

                # Source of truth for the diff is the worktree, not the LLM's report.
                authoritative_diff = wt.extract_diff(pair)
                if len(authoritative_diff.splitlines()) > config.max_diff_lines:
                    raise RuntimeError(
                        f"Diff exceeds max_diff_lines={config.max_diff_lines} "
                        f"({len(authoritative_diff.splitlines())} lines); narrow the prompt."
                    )

                # ─── REVIEWER ───
                reviewer_prompt = _build_reviewer_critique_prompt(
                    config.prompt, writer_report, authoritative_diff, project_context
                )
                emit(EventType.REVIEWER_STARTED, f"Round {round_num}: reviewer ({config.reviewer.cli.value})")
                reviewer_result = await _invoke_logged(
                    reviewer_inv,
                    cli=config.reviewer.cli,
                    prompt=reviewer_prompt,
                    cfg=config.reviewer,
                    cwd=pair.reviewer_path,
                    output_schema=critique_schema,
                    extra={"sandbox": SandboxMode.READ_ONLY},
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
                    writer_responses=writer_responses_bundle,
                    reviewer_rebuttal=None,
                )

                # ─── Terminal verdicts ───
                if critique.verdict == ReviewerVerdict.APPROVE:
                    rounds.append(round_obj)
                    break

                if critique.verdict == ReviewerVerdict.REJECT:
                    rounds.append(round_obj)
                    result.status = RunStatus.FAILED
                    result.error = f"Reviewer rejected outright: {critique.summary}"
                    rounds_done = True  # noqa: F841
                    break

                # ─── REVISE path ───
                if round_num > config.max_revisions:
                    # Out of revisions; ship with current critique unresolved.
                    rounds.append(round_obj)
                    break

                # Need a writer revision next round. But we also need the writer's response NOW
                # (this iteration), then check for rebuttal — the loop body covers both in one round.
                writer_revision_prompt = _build_writer_revision_prompt(
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
                writer_revision_result = await _invoke_logged(
                    writer_inv,
                    cli=config.writer.cli,
                    prompt=writer_revision_prompt,
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
                total_cost += float(writer_revision_result.cost_usd or 0.0)
                if writer_revision_result.is_error:
                    raise RuntimeError(f"Writer revision failed: {writer_revision_result.error}")
                response_bundle = _coerce_structured(
                    writer_revision_result.structured, WriterResponseBundle
                )
                _validate_response_covers_critique(response_bundle, critique)
                round_obj.writer_responses = response_bundle
                n_accept = sum(1 for r in response_bundle.responses if r.action == WriterAction.ACCEPT)
                n_reject = len(response_bundle.responses) - n_accept
                emit(
                    EventType.REVISION_DONE,
                    f"Round {round_num}: writer accepted {n_accept}, defended {n_reject} "
                    f"(${writer_revision_result.cost_usd:.4f})",
                    {"accepted": n_accept, "rejected": n_reject},
                )

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
                        extra={"sandbox": SandboxMode.READ_ONLY},
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

                    # If reviewer approves (no still_disputed at item level), we're done.
                    still_disputed = any(
                        r.verdict == ItemRebuttalVerdict.STILL_DISPUTED
                        for r in rebuttal.item_rebuttals
                    )
                    if not still_disputed:
                        break
                    # Otherwise: stop here. Disputes go to user, no more iteration.
                    break
                else:
                    # Writer accepted everything; loop back for another reviewer pass on the revision.
                    rounds.append(round_obj)
                    continue

            # ─── Final assembly ───
            result.diff = wt.extract_diff(pair)
            result.files_changed = _files_in_diff(result.diff)

    except Exception as exc:
        result.status = RunStatus.FAILED
        result.error = str(exc)

    finally:
        result.rounds = rounds
        result.cost_usd = round(total_cost, 6)
        result.finished_at = datetime.now(timezone.utc)
        if result.started_at is not None:
            result.duration_s = (result.finished_at - result.started_at).total_seconds()

        if result.status != RunStatus.FAILED:
            disputed, dissents, resolved = _classify_round_items(rounds)
            result.disputed_items = disputed
            result.acknowledged_dissents = dissents
            result.resolved_items = resolved
            if disputed:
                result.status = RunStatus.AWAITING_ARBITRATION
            else:
                result.status = RunStatus.AWAITING_APPROVAL
            result.summary = _build_summary(rounds, disputed, dissents)

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
        run_id, phase, round_num, role, cli.value, cfg.model, cfg.effort,
    )
    logger.debug("[%s] %s prompt (%d chars):\n%s", run_id, phase, len(prompt), prompt)

    result = await _invoke(
        invoker, cli=cli, prompt=prompt, cfg=cfg, cwd=cwd,
        output_schema=output_schema, extra=extra, timeout_s=timeout_s,
    )

    logger.info(
        "[%s] %s done cost=$%.4f duration=%.1fs%s",
        run_id, phase, result.cost_usd or 0.0, result.duration_s or 0.0,
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
        raise RuntimeError(f"Writer responded to the same item id(s) multiple times: {sorted(dupes)}")


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
    files: list[str] = []
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            files.append(line[len("+++ b/") :])
        elif line.startswith("--- a/") and line[len("--- a/") :] not in files:
            # Track deletions too
            pass
    return files


# ──────────────────────────────────────────────────────────────────────────────
# Streaming variant
# ──────────────────────────────────────────────────────────────────────────────


async def run_streaming(config: RunConfig, repo_root: Path) -> AsyncIterator[StreamEvent]:
    """Streaming wrapper — emits coarse-grained events. Real-time CLI streaming is v1.1."""
    raise NotImplementedError("Streaming variant deferred to v1.1. Use run() for now.")
    yield  # type: ignore[unreachable]


# ──────────────────────────────────────────────────────────────────────────────
# Apply, reject, arbitrate
# ──────────────────────────────────────────────────────────────────────────────


def apply_run_result(result: RunResult, repo_root: Path) -> RunResult:
    if result.status not in (RunStatus.AWAITING_APPROVAL,):
        raise RuntimeError(
            f"Cannot apply run {result.run_id}: status={result.status.value} "
            "(must be AWAITING_APPROVAL — resolve any disputes via `dialectic arbitrate` first)."
        )

    current_head = wt.current_head_sha(repo_root)
    base_sha = wt.resolve_base_sha(repo_root, result.config.base_ref)
    if current_head != base_sha:
        raise RuntimeError(
            f"HEAD moved since run started (base was {base_sha[:8]}, now {current_head[:8]}). "
            "Reset to the base ref, or rebase and re-run."
        )

    if result.config.apply_mode == ApplyMode.DRY_RUN:
        # No-op; just mark as success.
        result.status = (
            RunStatus.APPLIED_WITH_DISSENT if result.acknowledged_dissents else RunStatus.SUCCESS
        )
        persist_run_record(result, repo_root)
        return result

    if result.config.apply_mode == ApplyMode.UNCOMMITTED:
        wt.apply_diff_to_working_tree(repo_root, result.diff)
    elif result.config.apply_mode == ApplyMode.BRANCH:
        branch_name = result.config.branch_name or f"dialectic/{result.run_id}"
        commit_msg = f"dialectic run {result.run_id}\n\n{result.summary or result.config.prompt[:200]}"
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

    disputed_ids = {d.item.id for d in result.disputed_items}
    decision_ids = {d.item_id for d in decisions}

    missing = disputed_ids - decision_ids
    extra_ids = decision_ids - disputed_ids
    if missing:
        raise RuntimeError(f"Missing arbitration decisions for item id(s): {sorted(missing)}")
    if extra_ids:
        raise RuntimeError(f"Got decisions for non-disputed item id(s): {sorted(extra_ids)}")

    # For v1, ACCEPT_WRITER and SKIP both keep the writer's diff as-is. ACCEPT_REVIEWER
    # would require applying suggested_fix as a patch on top, which needs a fix-format
    # convention we haven't pinned down yet — defer to v1.1 with explicit "reviewer
    # rewrite" pass. For now, ACCEPT_REVIEWER raises.
    for d in decisions:
        if d.choice == ArbitrationChoice.ACCEPT_REVIEWER:
            raise NotImplementedError(
                "ACCEPT_REVIEWER not yet supported in v1 — pick ACCEPT_WRITER or SKIP, "
                "then manually apply the reviewer's suggested_fix after `dialectic approve`."
            )

    result.arbitration = decisions
    # Move disputed items to dissents (they ship with arbitration noted).
    for d in result.disputed_items:
        decision = next(dec for dec in decisions if dec.item_id == d.item.id)
        if decision.choice in (ArbitrationChoice.ACCEPT_WRITER, ArbitrationChoice.SKIP):
            result.acknowledged_dissents.append(
                AcknowledgedDissent(item=d.item, writer_response=d.writer_response)
            )
    result.disputed_items = []
    result.status = RunStatus.AWAITING_APPROVAL
    persist_run_record(result, repo_root)
    return result
