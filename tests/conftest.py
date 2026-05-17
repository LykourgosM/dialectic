"""Shared pytest fixtures."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def tmp_git_repo(tmp_path: Path) -> Path:
    """A throwaway git repo with one initial commit, returned as an absolute Path."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@dialectic.local"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Dialectic Test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    (repo / "README.md").write_text("# test repo\n")
    (repo / "main.py").write_text("def greet(name):\n    return f'hello {name}'\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "initial commit"], cwd=repo, check=True)
    return repo.resolve()


@pytest.fixture(autouse=True)
def _no_real_cli_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Defensive: tests should not accidentally invoke real CLIs.

    Tests that NEED real CLIs (Layer 3) explicitly unset this via the
    `allow_real_cli` fixture or by running with DIALECTIC_E2E=1.
    """
    if os.environ.get("DIALECTIC_E2E"):
        return
    # Replace the binary lookup so any accidental call fails loudly.
    import asyncio

    original = asyncio.create_subprocess_exec

    async def _block(*args, **kwargs):
        if args and args[0] in ("claude", "codex"):
            raise RuntimeError(
                f"Real CLI invocation blocked in tests: {args[0]}. "
                f"Use writer_invoke / reviewer_invoke overrides, or set DIALECTIC_E2E=1."
            )
        return await original(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _block)
