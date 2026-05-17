"""Git worktree lifecycle helpers.

Each run creates two ephemeral worktrees under `.dialectic/wt/`:
  - writer-<run-id>/    the writer's playground (workspace-write sandbox)
  - reviewer-<run-id>/  a clean checkout for the reviewer to read from

Both share the parent repo's `.git/` via standard git worktree pointers, so disk
cost is just the working files. Both are deleted at end-of-run unless the run
failed and `keep_on_failure=True`.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from pydantic import BaseModel


class WorktreePair(BaseModel):
    run_id: str
    repo_root: Path
    writer_path: Path
    reviewer_path: Path
    base_ref: str
    base_sha: str


class GitError(RuntimeError):
    """Raised when a git operation fails."""


def _git(repo_root: Path, *args: str, check: bool = True, input_text: str | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
        input=input_text,
    )
    if check and result.returncode != 0:
        raise GitError(
            f"git {' '.join(args)} failed (cwd={repo_root}):\n{result.stderr.strip()}"
        )
    return result.stdout


def resolve_base_sha(repo_root: Path, base_ref: str) -> str:
    return _git(repo_root, "rev-parse", base_ref).strip()


def current_head_sha(repo_root: Path) -> str:
    return _git(repo_root, "rev-parse", "HEAD").strip()


def working_tree_is_clean(repo_root: Path, *, ignore_dialectic: bool = True) -> bool:
    """True if the working tree has no modifications.

    By default ignores anything under `.dialectic/` since those are orchestrator-managed
    artifacts (audit logs, context files, transient worktrees) — they're not part of
    "user-facing dirty state" the safety check is guarding against.
    """
    for line in _git(repo_root, "status", "--porcelain").splitlines():
        if not line.strip():
            continue
        path = line[3:] if len(line) > 3 else ""
        if ignore_dialectic and (path == ".dialectic" or path.startswith(".dialectic/")):
            continue
        return False
    return True


def current_branch_name(repo_root: Path) -> str:
    return _git(repo_root, "rev-parse", "--abbrev-ref", "HEAD").strip()


def create_worktree_pair(repo_root: Path, run_id: str, base_ref: str) -> WorktreePair:
    """Create writer + reviewer worktrees both checked out at base_ref's SHA."""
    base_sha = resolve_base_sha(repo_root, base_ref)
    wt_root = repo_root / ".dialectic" / "wt"
    wt_root.mkdir(parents=True, exist_ok=True)

    writer_path = wt_root / f"writer-{run_id}"
    reviewer_path = wt_root / f"reviewer-{run_id}"

    _git(repo_root, "worktree", "add", "--detach", str(writer_path), base_sha)
    _git(repo_root, "worktree", "add", "--detach", str(reviewer_path), base_sha)

    return WorktreePair(
        run_id=run_id,
        repo_root=repo_root,
        writer_path=writer_path,
        reviewer_path=reviewer_path,
        base_ref=base_ref,
        base_sha=base_sha,
    )


def extract_diff(pair: WorktreePair) -> str:
    """Unified diff in the writer's worktree vs the recorded base_sha.

    Includes untracked files via `git add -N` (intent-to-add) so newly-created
    files show up in the diff alongside modifications to tracked files.
    """
    _git(pair.writer_path, "add", "-N", ".", check=False)
    return _git(pair.writer_path, "diff", pair.base_sha)


def cleanup(pair: WorktreePair, *, keep_on_failure: bool = False, failed: bool = False) -> None:
    """Remove both worktrees. No-op if keep_on_failure and failed."""
    if keep_on_failure and failed:
        return
    for path in [pair.writer_path, pair.reviewer_path]:
        _git(pair.repo_root, "worktree", "remove", "--force", str(path), check=False)
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)


@contextmanager
def worktree_pair(
    repo_root: Path, run_id: str, base_ref: str, *, keep_on_failure: bool = False
) -> Iterator[WorktreePair]:
    pair = create_worktree_pair(repo_root, run_id, base_ref)
    failed = False
    try:
        yield pair
    except Exception:
        failed = True
        raise
    finally:
        cleanup(pair, keep_on_failure=keep_on_failure, failed=failed)


def _validate_diff_paths(diff: str) -> None:
    """Reject diffs touching .git/, hooks, or paths with `..` components.

    Defense against a malicious model output that targets `.git/hooks/post-commit`
    for arbitrary code execution on the user's next git operation, or escapes
    the repo via `../../etc/passwd`.
    """
    suspect: list[str] = []
    for line in diff.splitlines():
        if not (line.startswith("+++ b/") or line.startswith("--- a/")):
            continue
        path = line[6:].strip()
        if path == "/dev/null":
            continue
        # Reject anything under .git/ or .git itself
        if path == ".git" or path.startswith(".git/") or "/.git/" in path:
            suspect.append(path)
            continue
        # Reject parent-traversal anywhere in the path
        parts = path.split("/")
        if any(p == ".." for p in parts):
            suspect.append(path)
            continue
        # Reject absolute paths
        if path.startswith("/"):
            suspect.append(path)
    if suspect:
        raise GitError(
            f"Refusing diff: targets restricted paths {sorted(set(suspect))}. "
            "Diffs may not modify .git/, escape the repo via .., or use absolute paths."
        )


def apply_diff_to_working_tree(repo_root: Path, diff: str) -> None:
    """Apply a diff to repo_root's working tree as uncommitted modifications.

    Refuses if:
      - working tree is dirty (caller should check first and present options),
      - diff targets `.git/`, contains `..` traversal, or has absolute paths,
      - `git apply --check` rejects (so we never half-apply on conflict).

    Raises GitError on any of these.
    """
    if not diff.strip():
        return
    if not working_tree_is_clean(repo_root):
        raise GitError(
            "Working tree is not clean; refusing to apply. "
            "Commit/stash your changes or use --apply-mode=branch."
        )
    _validate_diff_paths(diff)
    # --check verifies cleanly applicable before any actual modification.
    _git(repo_root, "apply", "--check", "--whitespace=nowarn", input_text=diff)
    _git(repo_root, "apply", "--whitespace=nowarn", input_text=diff)


_BRANCH_NAME_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


def apply_diff_to_new_branch(
    repo_root: Path,
    diff: str,
    branch_name: str,
    base_sha: str,
    commit_message: str,
) -> None:
    """Create branch_name from base_sha, apply the diff, commit, leave user on the new branch.

    Refuses if:
      - working tree is dirty,
      - branch name has shell/git-flag-injection characters (only [A-Za-z0-9._/-]),
      - branch already exists,
      - diff targets restricted paths.

    On commit failure, attempts to restore the original branch and delete the
    half-built new branch (rollback) so the user isn't stranded.
    """
    if not _BRANCH_NAME_RE.match(branch_name):
        raise GitError(
            f"Invalid branch name {branch_name!r}: only A-Z, a-z, 0-9, dot, underscore, "
            "slash, hyphen permitted."
        )
    if not working_tree_is_clean(repo_root):
        raise GitError(
            "Working tree is not clean; refusing to switch branches. "
            "Commit/stash your changes first."
        )
    _validate_diff_paths(diff)
    existing = _git(repo_root, "branch", "--list", branch_name).strip()
    if existing:
        raise GitError(f"Branch {branch_name!r} already exists; pick a different --branch-name.")

    original_branch = current_branch_name(repo_root)

    _git(repo_root, "checkout", "-b", branch_name, base_sha)
    try:
        if diff.strip():
            _git(repo_root, "apply", "--check", "--whitespace=nowarn", input_text=diff)
            _git(repo_root, "apply", "--whitespace=nowarn", input_text=diff)
            _git(repo_root, "add", "-A")
            # --no-verify skips pre-commit hooks (we generated this commit;
            # user can run their hooks manually if they want).
            _git(
                repo_root,
                "-c", "commit.gpgsign=false",
                "commit", "--no-verify", "-m", commit_message,
            )
    except GitError:
        # Roll back: return to the original branch and delete the half-built one.
        _git(repo_root, "checkout", "--", ".", check=False)
        _git(repo_root, "checkout", original_branch, check=False)
        _git(repo_root, "branch", "-D", branch_name, check=False)
        raise
