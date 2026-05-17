"""`dialectic ...` CLI."""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from . import core
from .protocol import (
    AgentCli,
    AgentConfig,
    ApplyMode,
    ArbitrationChoice,
    ArbitrationDecision,
    RunConfig,
    RunResult,
    RunStatus,
    SandboxMode,
)

console = Console()


@click.group()
@click.version_option()
@click.option("-v", "--verbose", count=True, help="INFO logging (-v) or DEBUG logging (-vv).")
def main(verbose: int) -> None:
    """dialectic: cross-family writer-reviewer protocol for LLM code generation."""
    import logging

    level = logging.WARNING
    if verbose == 1:
        level = logging.INFO
    elif verbose >= 2:
        level = logging.DEBUG
    logging.basicConfig(level=level, format="%(asctime)s %(name)s %(levelname)s %(message)s")


@main.command("run")
@click.option("--prompt", "-p", required=True, help="The coding task for the writer.")
@click.option("--writer-cli", type=click.Choice(["claude", "codex"]), default="claude")
@click.option("--writer-model", default="claude-opus-4-7")
@click.option("--writer-effort", default="max")
@click.option("--reviewer-cli", type=click.Choice(["claude", "codex"]), default="codex")
@click.option("--reviewer-model", default="gpt-5.4")
@click.option("--reviewer-effort", default="xhigh")
@click.option("--max-revisions", default=1, type=int)
@click.option(
    "--apply-mode",
    type=click.Choice(["uncommitted", "branch", "dry_run"]),
    default="uncommitted",
)
@click.option(
    "--dry-run", "dry_run_shortcut", is_flag=True, help="Shortcut for --apply-mode dry_run."
)
@click.option("--branch-name", default=None)
@click.option("--base-ref", default="HEAD")
@click.option("--keep-worktrees", is_flag=True)
@click.option("--auto-approve", is_flag=True, help="Skip the approval prompt.")
@click.option("--json", "as_json", is_flag=True, help="Output the final RunResult as JSON.")
@click.option("--timeout-per-agent", default=1500, type=int)
@click.option("--max-diff-lines", default=1000, type=int)
@click.option(
    "--repo-root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
)
def run_cmd(
    prompt: str,
    writer_cli: str,
    writer_model: str,
    writer_effort: str,
    reviewer_cli: str,
    reviewer_model: str,
    reviewer_effort: str,
    max_revisions: int,
    apply_mode: str,
    dry_run_shortcut: bool,
    branch_name: str | None,
    base_ref: str,
    keep_worktrees: bool,
    auto_approve: bool,
    as_json: bool,
    timeout_per_agent: int,
    max_diff_lines: int,
    repo_root: Path,
) -> None:
    """Run one dialectic pass on a prompt."""
    if dry_run_shortcut:
        apply_mode = "dry_run"

    config = RunConfig(
        prompt=prompt,
        writer=AgentConfig(cli=AgentCli(writer_cli), model=writer_model, effort=writer_effort),
        reviewer=AgentConfig(
            cli=AgentCli(reviewer_cli), model=reviewer_model, effort=reviewer_effort
        ),
        max_revisions=max_revisions,
        apply_mode=ApplyMode(apply_mode),
        branch_name=branch_name,
        base_ref=base_ref,
        keep_worktrees=keep_worktrees,
        timeout_per_agent_s=timeout_per_agent,
        max_diff_lines=max_diff_lines,
        sandbox=SandboxMode.WORKSPACE_WRITE,
    )

    repo_root = repo_root.resolve()
    console.print(
        f"[bold]dialectic[/bold] · writer={writer_cli}({writer_model}, {writer_effort}) "
        f"reviewer={reviewer_cli}({reviewer_model}, {reviewer_effort})"
    )
    console.print(
        f"[dim]repo: {repo_root}  base_ref: {base_ref}  max_revisions: {max_revisions}[/dim]"
    )

    from .protocol import EventType, StreamEvent

    def render_event(ev: StreamEvent) -> None:
        prefix = {
            EventType.RUN_STARTED: "[bold cyan]●[/bold cyan]",
            EventType.WRITER_STARTED: "[blue]→[/blue]",
            EventType.WRITER_DONE: "[green]✓[/green]",
            EventType.REVIEWER_STARTED: "[blue]→[/blue]",
            EventType.REVIEWER_DONE: "[green]✓[/green]",
            EventType.REVISION_STARTED: "[blue]→[/blue]",
            EventType.REVISION_DONE: "[green]✓[/green]",
            EventType.REBUTTAL_STARTED: "[blue]→[/blue]",
            EventType.REBUTTAL_DONE: "[green]✓[/green]",
            EventType.RUN_FINISHED: "[bold cyan]●[/bold cyan]",
            EventType.ERROR: "[red]✗[/red]",
        }.get(ev.event_type, "·")
        console.print(f"{prefix} {ev.message}")

    result = asyncio.run(core.run(config, repo_root, on_event=render_event))

    if as_json:
        click.echo(result.model_dump_json(indent=2))
        return

    _render_result(result)

    if result.status == RunStatus.FAILED:
        sys.exit(1)
    if result.status == RunStatus.AWAITING_ARBITRATION:
        console.print(
            f"\n[yellow]Run {result.run_id} has {len(result.disputed_items)} disputed item(s). "
            f"Resolve with:[/yellow]"
        )
        console.print(f"  dialectic arbitrate {result.run_id} --accept-writer ID ...")
        return
    if not auto_approve:
        choice = click.prompt(
            "\napprove / reject / view (a/r/v)?",
            type=click.Choice(["a", "r", "v"]),
            default="v",
        )
        if choice == "v":
            console.print(
                Syntax(result.diff or "(empty diff)", "diff", theme="ansi_dark", line_numbers=False)
            )
            choice = click.prompt(
                "approve / reject? (a/r)", type=click.Choice(["a", "r"]), default="r"
            )
        if choice == "r":
            core.reject_run_result(result, repo_root)
            console.print(f"[red]Rejected. Audit log: {result.audit_log_path}[/red]")
            return
    try:
        result = core.apply_run_result(result, repo_root)
    except Exception as exc:
        console.print(f"[red]Apply failed: {exc}[/red]")
        sys.exit(1)

    _render_apply_summary(result, repo_root)


@main.command()
@click.argument("run_id")
@click.option(
    "--repo-root", type=click.Path(exists=True, file_okay=False, path_type=Path), default="."
)
def approve(run_id: str, repo_root: Path) -> None:
    """Apply a previously-completed run's diff."""
    repo_root = repo_root.resolve()
    result = core.load_run_record(run_id, repo_root)
    try:
        result = core.apply_run_result(result, repo_root)
    except Exception as exc:
        console.print(f"[red]Apply failed: {exc}[/red]")
        sys.exit(1)
    _render_apply_summary(result, repo_root)


@main.command()
@click.argument("run_id")
@click.option(
    "--repo-root", type=click.Path(exists=True, file_okay=False, path_type=Path), default="."
)
def reject(run_id: str, repo_root: Path) -> None:
    """Discard a previously-completed run."""
    repo_root = repo_root.resolve()
    result = core.load_run_record(run_id, repo_root)
    core.reject_run_result(result, repo_root)
    console.print(f"[red]Rejected {run_id}. Audit log: {result.audit_log_path}[/red]")


@main.command()
@click.argument("run_id")
@click.option("--accept-writer", "accept_writer", multiple=True, type=int)
@click.option("--accept-reviewer", "accept_reviewer", multiple=True, type=int)
@click.option("--skip", "skip_items", multiple=True, type=int)
@click.option("--note", default=None)
@click.option(
    "--repo-root", type=click.Path(exists=True, file_okay=False, path_type=Path), default="."
)
def arbitrate(
    run_id: str,
    accept_writer: tuple[int, ...],
    accept_reviewer: tuple[int, ...],
    skip_items: tuple[int, ...],
    note: str | None,
    repo_root: Path,
) -> None:
    """Resolve disputed items in a run."""
    repo_root = repo_root.resolve()
    decisions: list[ArbitrationDecision] = []
    for item_id in accept_writer:
        decisions.append(
            ArbitrationDecision(item_id=item_id, choice=ArbitrationChoice.ACCEPT_WRITER, note=note)
        )
    for item_id in accept_reviewer:
        decisions.append(
            ArbitrationDecision(
                item_id=item_id, choice=ArbitrationChoice.ACCEPT_REVIEWER, note=note
            )
        )
    for item_id in skip_items:
        decisions.append(
            ArbitrationDecision(item_id=item_id, choice=ArbitrationChoice.SKIP, note=note)
        )
    try:
        result = asyncio.run(core.resume_with_arbitration(run_id, decisions, repo_root))
    except Exception as exc:
        console.print(f"[red]Arbitration failed: {exc}[/red]")
        sys.exit(1)
    console.print(
        f"[green]Arbitration recorded. Run {run_id} now awaiting approval — "
        f"`dialectic approve {run_id}` to apply.[/green]"
    )
    _render_result(result)


@main.command("list-runs")
@click.option(
    "--repo-root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
)
@click.option(
    "--limit",
    type=click.IntRange(min=1),
    default=10,
    help="Maximum number of runs to display (positive integer).",
)
def list_runs(repo_root: Path, limit: int) -> None:
    """Print the N most recent runs as a table (default 10)."""
    repo_root = repo_root.resolve()
    runs_dir = repo_root / ".dialectic" / "runs"
    if not runs_dir.is_dir():
        console.print("[dim]No runs found (.dialectic/runs/ does not exist).[/dim]")
        return

    results: list[RunResult] = []
    for path in runs_dir.glob("*.json"):
        try:
            results.append(RunResult.model_validate_json(path.read_text()))
        except Exception as exc:
            console.print(f"[yellow]warning: skipping {path.name} ({exc})[/yellow]")

    if not results:
        console.print("[dim]No runs found.[/dim]")
        return

    epoch = datetime.min.replace(tzinfo=UTC)
    results.sort(key=lambda r: r.started_at or epoch, reverse=True)
    results = results[:limit]

    status_color = {
        RunStatus.SUCCESS: "green",
        RunStatus.APPLIED_WITH_DISSENT: "green",
        RunStatus.AWAITING_APPROVAL: "yellow",
        RunStatus.AWAITING_ARBITRATION: "yellow",
        RunStatus.REJECTED_BY_USER: "red",
        RunStatus.FAILED: "red",
        RunStatus.TIMED_OUT: "red",
    }

    table = Table(show_header=True, header_style="bold")
    table.add_column("run_id", style="cyan", no_wrap=True)
    table.add_column("status")
    table.add_column("prompt")
    table.add_column("cost", justify="right")
    table.add_column("duration", justify="right")

    for r in results:
        color = status_color.get(r.status, "white")
        table.add_row(
            r.run_id[-8:],
            f"[{color}]{r.status.value}[/{color}]",
            _truncate_prompt(r.config.prompt, 60),
            f"${r.cost_usd:.4f}",
            f"{r.duration_s:.1f}s",
        )

    console.print(table)


@main.command("costs")
@click.option(
    "--repo-root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
)
def costs(repo_root: Path) -> None:
    """Aggregate USD spent across all runs, broken down by writer/reviewer model."""
    repo_root = repo_root.resolve()
    runs_dir = repo_root / ".dialectic" / "runs"
    if not runs_dir.is_dir():
        console.print("[dim]No runs found (.dialectic/runs/ does not exist).[/dim]")
        return

    results: list[RunResult] = []
    for path in runs_dir.glob("*.json"):
        try:
            results.append(RunResult.model_validate_json(path.read_text()))
        except Exception as exc:
            console.print(f"[yellow]warning: skipping {path.name} ({exc})[/yellow]")

    if not results:
        console.print("[dim]No runs found.[/dim]")
        return

    # (role, model) → [runs, total_cost]. Each run contributes its full cost to
    # both its writer-model row and its reviewer-model row — RunResult doesn't
    # split cost per-role, so each row answers "what did runs using <model> as
    # <role> cost in total?" rather than implying a 50/50 split.
    agg: dict[tuple[str, str], list[float]] = {}
    total_spent = 0.0
    for r in results:
        total_spent += r.cost_usd
        for role, model in (("writer", r.config.writer.model), ("reviewer", r.config.reviewer.model)):
            row = agg.setdefault((role, model), [0.0, 0.0])
            row[0] += 1
            row[1] += r.cost_usd

    table = Table(show_header=True, header_style="bold")
    table.add_column("model", style="cyan", no_wrap=True)
    table.add_column("role")
    table.add_column("runs", justify="right")
    table.add_column("cost", justify="right")

    # Writers first, then reviewers; within each role, sort by cost desc.
    for role in ("writer", "reviewer"):
        rows = [(model, runs, cost) for (r, model), (runs, cost) in agg.items() if r == role]
        rows.sort(key=lambda x: (-x[2], x[0]))
        for model, runs, cost in rows:
            table.add_row(model, role, str(int(runs)), f"${cost:.4f}")

    console.print(table)
    console.print(
        f"\n[bold]Total spent across {len(results)} run(s):[/bold] ${total_spent:.4f}"
    )


@main.command()
@click.option("--host", default="127.0.0.1")
@click.option("--port", default=8765, type=int)
@click.option(
    "--token",
    default=None,
    help="Bearer token required on all requests. REQUIRED for non-loopback hosts.",
)
def serve(host: str, port: int, token: str | None) -> None:
    """Run the local HTTP API server."""
    from . import server

    try:
        server.run(host=host, port=port, token=token)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)


# ──────────────────────────────────────────────────────────────────────────────
# Rendering helpers
# ──────────────────────────────────────────────────────────────────────────────


def _truncate_prompt(text: str, max_len: int) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= max_len:
        return collapsed
    return collapsed[: max_len - 1] + "…"


def _render_result(result: RunResult) -> None:
    status_color = {
        RunStatus.AWAITING_APPROVAL: "green",
        RunStatus.AWAITING_ARBITRATION: "yellow",
        RunStatus.FAILED: "red",
        RunStatus.TIMED_OUT: "red",
    }.get(result.status, "white")
    header = (
        f"[bold]Run {result.run_id}[/bold]  "
        f"[{status_color}]{result.status.value}[/{status_color}]  "
        f"·  {result.duration_s:.1f}s  ·  ${result.cost_usd:.4f}"
    )
    console.print()
    console.print(Panel(header, expand=False, border_style="bright_black"))
    if result.summary:
        console.print(f"[dim]{result.summary}[/dim]")

    if result.files_changed:
        console.print(f"\n[bold]Files changed ({len(result.files_changed)}):[/bold]")
        for f in result.files_changed:
            console.print(f"  · {f}")

    if result.acknowledged_dissents:
        console.print(
            f"\n[bold yellow]Acknowledged dissents ({len(result.acknowledged_dissents)}):[/bold yellow]"
        )
        for dissent in result.acknowledged_dissents:
            console.print(f"  · #{dissent.item.id}  {dissent.item.issue}")
            console.print(f"    [dim]writer: {dissent.writer_response.rationale}[/dim]")

    if result.disputed_items:
        console.print(
            f"\n[bold red]Disputed items ({len(result.disputed_items)}) — need user arbitration:[/bold red]"
        )
        for dispute in result.disputed_items:
            loc = (
                f"{dispute.item.file}:{dispute.item.lines}"
                if dispute.item.lines
                else (dispute.item.file or "")
            )
            console.print(f"  · #{dispute.item.id} [{dispute.item.severity.value}] {loc}")
            console.print(f"    issue:   {dispute.item.issue}")
            console.print(f"    writer:  {dispute.writer_response.rationale}")
            console.print(f"    reviewer: {dispute.reviewer_rebuttal_item.rebuttal_reasoning}")

    if result.error:
        console.print(f"\n[red]Error:[/red] {result.error}")


def _render_apply_summary(result: RunResult, repo_root: Path) -> None:
    if result.config.apply_mode.value == "branch":
        msg = f"[green]✓ Applied {len(result.files_changed)} file(s) on new branch.[/green]"
    elif result.config.apply_mode.value == "dry_run":
        msg = f"[green]✓ Dry run complete. Audit log: {result.audit_log_path}[/green]"
    else:
        msg = (
            f"[green]✓ Applied {len(result.files_changed)} file(s) to your working tree (uncommitted).\n"
            f"  Run `git diff` to review, then commit when ready.[/green]"
        )
    console.print(f"\n{msg}")


if __name__ == "__main__":
    main()
