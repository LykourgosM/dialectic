"""`dialectic ...` CLI.

Thin wrapper over core.py. Renders streaming events with rich + handles approval prompts.
"""

from __future__ import annotations

import click


@click.group()
@click.version_option()
def main() -> None:
    """dialectic: cross-family writer-reviewer protocol for LLM code generation."""


@main.command()
@click.option("--prompt", "-p", required=True, help="The coding task for the writer.")
@click.option("--writer-model", default="claude-opus-4-7")
@click.option("--reviewer-model", default="gpt-5.4")
@click.option("--max-revisions", default=1, type=int)
@click.option(
    "--apply-mode",
    type=click.Choice(["uncommitted", "branch", "dry_run"]),
    default="uncommitted",
)
@click.option("--branch-name", default=None, help="Branch name when --apply-mode=branch.")
@click.option("--stream/--no-stream", default=True, help="Stream progress events as the run executes.")
@click.option("--auto-approve", is_flag=True, help="Skip the approval prompt (for scripts).")
@click.option("--json", "as_json", is_flag=True, help="Output the final RunResult as JSON.")
def run(  # noqa: PLR0913
    prompt: str,
    writer_model: str,
    reviewer_model: str,
    max_revisions: int,
    apply_mode: str,
    branch_name: str | None,
    stream: bool,
    auto_approve: bool,
    as_json: bool,
) -> None:
    """Run one dialectic pass on a prompt."""
    raise NotImplementedError("Wire up to core.run / core.run_streaming.")


@main.command()
@click.argument("run_id")
def approve(run_id: str) -> None:
    """Apply a previously-completed run's diff."""
    raise NotImplementedError


@main.command()
@click.argument("run_id")
def reject(run_id: str) -> None:
    """Discard a previously-completed run."""
    raise NotImplementedError


@main.command()
def serve() -> None:
    """Run the local HTTP API server (`dialectic serve`)."""
    from . import server

    server.run()


if __name__ == "__main__":
    main()
