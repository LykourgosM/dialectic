"""`dialectic ...` CLI.

Thin wrapper over core.py. Renders streaming events with rich + handles approval prompts.
"""

from __future__ import annotations

from pathlib import Path

import click


@click.group()
@click.version_option()
def main() -> None:
    """dialectic: cross-family writer-reviewer protocol for LLM code generation."""


@main.command("run")
@click.option("--prompt", "-p", required=True, help="The coding task for the writer.")
@click.option("--writer-cli", type=click.Choice(["claude", "codex"]), default="claude")
@click.option("--writer-model", default="claude-opus-4-7")
@click.option("--writer-effort", default="max", help="low|medium|high|xhigh|max (Claude); minimal..xhigh (Codex)")
@click.option("--reviewer-cli", type=click.Choice(["claude", "codex"]), default="codex")
@click.option("--reviewer-model", default="gpt-5.4")
@click.option("--reviewer-effort", default="xhigh")
@click.option("--max-revisions", default=1, type=int)
@click.option(
    "--apply-mode",
    type=click.Choice(["uncommitted", "branch", "dry_run"]),
    default="uncommitted",
    help="How to land the approved diff. 'uncommitted' = on your current branch, like Claude Code edits.",
)
@click.option("--dry-run", "dry_run_shortcut", is_flag=True, help="Shortcut for --apply-mode dry_run.")
@click.option("--branch-name", default=None, help="Branch name when --apply-mode=branch.")
@click.option("--base-ref", default="HEAD", help="Git ref to branch worktrees from.")
@click.option("--keep-worktrees", is_flag=True, help="Keep worktrees on failure for debugging.")
@click.option("--stream/--no-stream", default=True)
@click.option("--auto-approve", is_flag=True, help="Skip the approval prompt (for scripts).")
@click.option("--json", "as_json", is_flag=True, help="Output the final RunResult as JSON.")
@click.option("--timeout-per-agent", default=1500, type=int, help="Seconds per agent invocation.")
@click.option("--max-diff-lines", default=1000, type=int)
def run_cmd(  # noqa: PLR0913
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
    stream: bool,
    auto_approve: bool,
    as_json: bool,
    timeout_per_agent: int,
    max_diff_lines: int,
) -> None:
    """Run one dialectic pass on a prompt."""
    raise NotImplementedError("Wire up to core.run / core.run_streaming.")


@main.command()
@click.argument("run_id")
@click.option("--repo-root", type=click.Path(exists=True, file_okay=False, path_type=Path), default=".")
def approve(run_id: str, repo_root: Path) -> None:
    """Apply a previously-completed run's diff.

    Loads the persisted RunResult via core.load_run_record, then calls core.apply_run_result.
    Refuses if the run has unresolved disputed_items (use `dialectic arbitrate` first).
    """
    raise NotImplementedError


@main.command()
@click.argument("run_id")
@click.option("--repo-root", type=click.Path(exists=True, file_okay=False, path_type=Path), default=".")
def reject(run_id: str, repo_root: Path) -> None:
    """Discard a previously-completed run."""
    raise NotImplementedError


@main.command()
@click.argument("run_id")
@click.option(
    "--accept-writer",
    "accept_writer",
    multiple=True,
    type=int,
    help="Item IDs where you side with the writer (repeatable).",
)
@click.option(
    "--accept-reviewer",
    "accept_reviewer",
    multiple=True,
    type=int,
    help="Item IDs where you side with the reviewer (repeatable).",
)
@click.option(
    "--skip",
    "skip_items",
    multiple=True,
    type=int,
    help="Item IDs to ship as-is and note as unresolved in audit log (repeatable).",
)
@click.option("--note", default=None, help="Optional note attached to all decisions.")
@click.option("--repo-root", type=click.Path(exists=True, file_okay=False, path_type=Path), default=".")
def arbitrate(  # noqa: PLR0913
    run_id: str,
    accept_writer: tuple[int, ...],
    accept_reviewer: tuple[int, ...],
    skip_items: tuple[int, ...],
    note: str | None,
    repo_root: Path,
) -> None:
    """Resolve disputed items in a run, then surface the final diff for approval.

    Wraps core.resume_with_arbitration. The union of --accept-writer / --accept-reviewer
    / --skip must cover all disputed_items in the run; missing items will error.

    After arbitration, the run moves to AWAITING_APPROVAL — run `dialectic approve <id>` to apply.
    """
    raise NotImplementedError


@main.command()
@click.option("--host", default="127.0.0.1")
@click.option("--port", default=8765, type=int)
def serve(host: str, port: int) -> None:
    """Run the local HTTP API server."""
    from . import server

    server.run(host=host, port=port)


if __name__ == "__main__":
    main()
