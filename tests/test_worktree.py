"""Tests for git worktree helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dialectic import worktree as wt


def test_create_pair_makes_two_worktrees_at_base_sha(tmp_git_repo: Path) -> None:
    pair = wt.create_worktree_pair(tmp_git_repo, "run-001", "HEAD")
    try:
        assert pair.writer_path.exists()
        assert pair.reviewer_path.exists()
        assert (pair.writer_path / "main.py").exists()
        assert pair.base_sha == wt.current_head_sha(tmp_git_repo)
    finally:
        wt.cleanup(pair)


def test_extract_diff_captures_writer_changes(tmp_git_repo: Path) -> None:
    pair = wt.create_worktree_pair(tmp_git_repo, "run-002", "HEAD")
    try:
        (pair.writer_path / "main.py").write_text("def greet(name):\n    return f'hi {name}'\n")
        diff = wt.extract_diff(pair)
        assert "+    return f'hi {name}'" in diff
        assert "-    return f'hello {name}'" in diff
    finally:
        wt.cleanup(pair)


def test_cleanup_removes_both_dirs(tmp_git_repo: Path) -> None:
    pair = wt.create_worktree_pair(tmp_git_repo, "run-003", "HEAD")
    wt.cleanup(pair)
    assert not pair.writer_path.exists()
    assert not pair.reviewer_path.exists()


def test_cleanup_idempotent(tmp_git_repo: Path) -> None:
    pair = wt.create_worktree_pair(tmp_git_repo, "run-004", "HEAD")
    wt.cleanup(pair)
    wt.cleanup(pair)  # should not raise


def test_keep_on_failure_preserves_worktrees(tmp_git_repo: Path) -> None:
    pair = wt.create_worktree_pair(tmp_git_repo, "run-005", "HEAD")
    wt.cleanup(pair, keep_on_failure=True, failed=True)
    assert pair.writer_path.exists()
    assert pair.reviewer_path.exists()
    wt.cleanup(pair)


def test_context_manager_cleans_on_success(tmp_git_repo: Path) -> None:
    paths = []
    with wt.worktree_pair(tmp_git_repo, "run-006", "HEAD") as pair:
        paths = [pair.writer_path, pair.reviewer_path]
        assert all(p.exists() for p in paths)
    assert not any(p.exists() for p in paths)


def test_context_manager_keeps_on_failure_when_flagged(tmp_git_repo: Path) -> None:
    paths = []
    with pytest.raises(RuntimeError):
        with wt.worktree_pair(tmp_git_repo, "run-007", "HEAD", keep_on_failure=True) as pair:
            paths = [pair.writer_path, pair.reviewer_path]
            raise RuntimeError("simulated failure")
    assert all(p.exists() for p in paths)


def test_apply_diff_to_working_tree_modifies_files(tmp_git_repo: Path) -> None:
    pair = wt.create_worktree_pair(tmp_git_repo, "run-008", "HEAD")
    try:
        (pair.writer_path / "main.py").write_text("def greet(name):\n    return f'hi {name}'\n")
        diff = wt.extract_diff(pair)
    finally:
        wt.cleanup(pair)
    wt.apply_diff_to_working_tree(tmp_git_repo, diff)
    assert "hi" in (tmp_git_repo / "main.py").read_text()


def test_apply_refuses_when_dirty(tmp_git_repo: Path) -> None:
    (tmp_git_repo / "main.py").write_text("dirty\n")
    with pytest.raises(wt.GitError, match="not clean"):
        wt.apply_diff_to_working_tree(tmp_git_repo, "diff --git a/x b/x\n")


def test_apply_to_new_branch_creates_branch_and_commits(tmp_git_repo: Path) -> None:
    pair = wt.create_worktree_pair(tmp_git_repo, "run-009", "HEAD")
    try:
        (pair.writer_path / "main.py").write_text("def greet(name):\n    return f'hi {name}'\n")
        diff = wt.extract_diff(pair)
        base_sha = pair.base_sha
    finally:
        wt.cleanup(pair)

    wt.apply_diff_to_new_branch(tmp_git_repo, diff, "feature-x", base_sha, "test commit")

    current = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=tmp_git_repo, capture_output=True, text=True
    ).stdout.strip()
    assert current == "feature-x"
    log = subprocess.run(
        ["git", "log", "--oneline", "-n", "1"], cwd=tmp_git_repo, capture_output=True, text=True
    ).stdout
    assert "test commit" in log


def test_apply_to_existing_branch_refuses(tmp_git_repo: Path) -> None:
    subprocess.run(["git", "branch", "exists"], cwd=tmp_git_repo, check=True)
    with pytest.raises(wt.GitError, match="already exists"):
        wt.apply_diff_to_new_branch(
            tmp_git_repo, "", "exists", wt.current_head_sha(tmp_git_repo), "x"
        )
