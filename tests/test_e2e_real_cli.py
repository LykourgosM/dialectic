"""End-to-end tests that invoke the REAL `claude` and `codex` CLIs.

Slow (5–15 min per run), costs real money (~$0.30–$2 with conservative settings),
and depends on:
  - `claude` and `codex` binaries on PATH
  - Valid auth for both
  - Network access

These are GATED by `DIALECTIC_E2E=1`. The defensive fixture in conftest.py blocks
real CLI invocations unless that env var is set.

Run a single test:
    DIALECTIC_E2E=1 pytest tests/test_e2e_real_cli.py::test_e2e_trivial_rename -v -s

The `-s` flag matters — without it pytest swallows stdout/stderr from the subagent
calls and you can't tell what's happening.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from dialectic import core, protocol as p

pytestmark = pytest.mark.skipif(
    not os.environ.get("DIALECTIC_E2E"),
    reason="set DIALECTIC_E2E=1 to run real-CLI E2E tests (slow, ~$1/run)",
)


@pytest.mark.asyncio
async def test_e2e_trivial_rename(tmp_git_repo: Path) -> None:
    """Smallest reasonable real-CLI smoke test.

    Renames a function in the fixture repo's main.py. Should converge in one round
    (reviewer approves or makes one minor suggestion). Budget: ~$0.30 with cheap models.
    """
    config = p.RunConfig(
        prompt=(
            "In main.py, rename the function `greet` to `say_hello`. "
            "Make sure to keep the same signature and body, just rename."
        ),
        max_revisions=0,  # Just write+review, no iteration
        writer=p.AgentConfig(
            cli=p.AgentCli.CLAUDE, model="claude-sonnet-4-6", effort="medium"
        ),
        reviewer=p.AgentConfig(cli=p.AgentCli.CODEX, model="gpt-5.4", effort="medium"),
        timeout_per_agent_s=300,
    )

    events: list[p.StreamEvent] = []
    result = await core.run(
        config, tmp_git_repo, on_event=lambda ev: (events.append(ev), print(f"  {ev.message}"))[1] or None,
    )

    # Status sanity
    assert result.status in (
        p.RunStatus.AWAITING_APPROVAL,
        p.RunStatus.AWAITING_ARBITRATION,
    ), f"unexpected status {result.status}; error={result.error}"

    # Budget guard
    assert result.cost_usd < 2.0, f"Cost ${result.cost_usd:.2f} exceeded $2 budget"
    assert result.cost_usd > 0, "Cost should be > 0 for real CLI calls"

    # The diff should rename greet → say_hello somewhere
    assert "say_hello" in result.diff, f"Expected 'say_hello' in diff:\n{result.diff[:1000]}"

    # Round captured
    assert len(result.rounds) >= 1
    assert result.rounds[0].writer_report.diff or "say_hello" in result.diff

    # Streaming events fired
    event_types = {e.event_type for e in events}
    assert p.EventType.WRITER_DONE in event_types
    assert p.EventType.REVIEWER_DONE in event_types
    assert p.EventType.RUN_FINISHED in event_types

    # Audit log written
    audit = tmp_git_repo / ".dialectic" / "runs" / f"{result.run_id}.prompts.jsonl"
    assert audit.exists()
    print(f"\n✓ E2E run complete: ${result.cost_usd:.4f}, {result.duration_s:.1f}s")
    print(f"  Audit log: {audit}")


@pytest.mark.asyncio
async def test_e2e_schema_enforcement_real_models(tmp_git_repo: Path) -> None:
    """Verify both CLIs respect the --json-schema / --output-schema flags.

    Without strict-mode schema enforcement, the LLMs may emit free-form text and
    parsing will fail. This test catches schema-passthrough regressions.
    """
    config = p.RunConfig(
        prompt="In main.py, add a single line comment '# greeting helper' above the function def.",
        max_revisions=0,
        writer=p.AgentConfig(cli=p.AgentCli.CLAUDE, model="claude-sonnet-4-6", effort="low"),
        reviewer=p.AgentConfig(cli=p.AgentCli.CODEX, model="gpt-5.4", effort="low"),
        timeout_per_agent_s=180,
    )
    result = await core.run(config, tmp_git_repo)
    assert result.status in (p.RunStatus.AWAITING_APPROVAL, p.RunStatus.AWAITING_ARBITRATION)
    # If schemas didn't enforce, we'd see parse errors in result.error
    assert result.error is None or "json" not in result.error.lower(), result.error
    # Rounds should have valid structured outputs
    assert len(result.rounds) >= 1
    assert result.rounds[0].writer_report is not None
    assert result.rounds[0].reviewer_critique is not None
