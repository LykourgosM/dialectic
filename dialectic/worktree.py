"""Git worktree lifecycle helpers.

Each run creates two ephemeral worktrees under .dialectic/wt/:
  - writer-<run-id>/    the writer's playground (workspace-write sandbox)
  - reviewer-<run-id>/  a clean checkout for the reviewer to read from

Both share the parent repo's .git/ via standard git worktree pointers, so disk
cost is just the working files (no .git duplication). Both are deleted at the
end of each run unless the run failed and --keep-worktrees was set.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from pydantic import BaseModel


class WorktreePair(BaseModel):
    """The pair of worktrees used by one run."""

    run_id: str
    repo_root: Path
    writer_path: Path
    reviewer_path: Path
    base_ref: str
    base_sha: str  # The resolved SHA of base_ref at run start (immutable)


def resolve_base_sha(repo_root: Path, base_ref: str) -> str:
    """Resolve a git ref (e.g., 'HEAD', 'main') to an immutable SHA."""
    raise NotImplementedError


def create_worktree_pair(repo_root: Path, run_id: str, base_ref: str) -> WorktreePair:
    """Create writer + reviewer worktrees from base_ref. Adds .dialectic/wt/ to gitignore if needed."""
    raise NotImplementedError


def extract_diff(pair: WorktreePair) -> str:
    """`git diff <base_sha>` inside the writer's worktree → unified diff string."""
    raise NotImplementedError


def cleanup(pair: WorktreePair, keep_on_failure: bool = False, failed: bool = False) -> None:
    """`git worktree remove` both, then rm -rf the parent dir. No-op if keep_on_failure and failed."""
    raise NotImplementedError


@contextmanager
def worktree_pair(
    repo_root: Path, run_id: str, base_ref: str, *, keep_on_failure: bool = False
) -> Iterator[WorktreePair]:
    """Create worktree pair; cleanup on exit (unless failed and keep_on_failure)."""
    raise NotImplementedError
    yield  # type: ignore[unreachable]


def apply_diff_to_working_tree(repo_root: Path, diff: str) -> None:
    """`git apply` the diff to the user's working tree on their current branch. Uncommitted."""
    raise NotImplementedError


def apply_diff_to_new_branch(
    repo_root: Path, diff: str, branch_name: str, base_sha: str, commit_message: str
) -> None:
    """Create branch from base_sha, apply diff, commit, leave user on the new branch."""
    raise NotImplementedError


def working_tree_is_clean(repo_root: Path) -> bool:
    """True if `git status --porcelain` is empty."""
    raise NotImplementedError


def current_head_sha(repo_root: Path) -> str:
    """Resolved SHA of current HEAD."""
    raise NotImplementedError
