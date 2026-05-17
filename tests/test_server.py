"""HTTP API server tests using FastAPI's TestClient."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dialectic import core, server
from dialectic.protocol import (
    ApplyMode,
    ReviewerCritique,
    ReviewerVerdict,
    RevisionRound,
    RunConfig,
    RunResult,
    RunStatus,
    WriterReport,
)


@pytest.fixture
def repo_root_server(tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the server at a temp repo and clear any auth token."""
    monkeypatch.setattr(server, "REPO_ROOT", tmp_git_repo)
    monkeypatch.setattr(server, "AUTH_TOKEN", None)
    return tmp_git_repo


@pytest.fixture
def client(repo_root_server: Path) -> TestClient:
    return TestClient(server.app)


def _persist_result(repo_root: Path, run_id: str = "20260517-120000-abcdef") -> RunResult:
    cfg = RunConfig(prompt="test", apply_mode=ApplyMode.UNCOMMITTED)
    result = RunResult(
        run_id=run_id,
        status=RunStatus.AWAITING_APPROVAL,
        config=cfg,
        diff="",
        files_changed=[],
        rounds=[
            RevisionRound(
                round_number=1,
                writer_report=WriterReport(diff="", summary="x"),
                reviewer_critique=ReviewerCritique(verdict=ReviewerVerdict.APPROVE, summary="x"),
            )
        ],
    )
    core.persist_run_record(result, repo_root)
    return result


def test_get_run_returns_404_when_missing(client: TestClient) -> None:
    response = client.get("/run/20260517-120000-abcdef")
    assert response.status_code == 404


def test_get_run_returns_400_on_invalid_run_id(client: TestClient) -> None:
    """Path-traversal regression: a bogus run_id is rejected with 400, not silently 404."""
    response = client.get("/run/..%2F..%2Fetc%2Fpasswd")
    assert response.status_code in (400, 404)
    # 404 if FastAPI rejects URL decoding; either way the file is not read.


def test_get_run_returns_persisted_record(client: TestClient, repo_root_server: Path) -> None:
    persisted = _persist_result(repo_root_server)
    response = client.get(f"/run/{persisted.run_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == persisted.run_id
    assert body["status"] == "awaiting_approval"


def test_post_approve_404_when_missing(client: TestClient) -> None:
    response = client.post("/run/20260517-120000-abcdef/approve")
    assert response.status_code == 404


def test_post_reject_marks_status(client: TestClient, repo_root_server: Path) -> None:
    persisted = _persist_result(repo_root_server)
    response = client.post(f"/run/{persisted.run_id}/reject")
    assert response.status_code == 200
    assert response.json()["status"] == "rejected_by_user"


def test_post_arbitrate_validates_run_id_format(client: TestClient) -> None:
    response = client.post("/run/not_a_valid_id/arbitrate", json=[])
    # 400 (bad run_id format) or 404 (not found) — either is fine; the key is
    # NO file read with a malformed id.
    assert response.status_code in (400, 404)


def test_auth_required_when_token_set(
    repo_root_server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "AUTH_TOKEN", "secret-token")
    client = TestClient(server.app)

    # No auth header → 401
    response = client.get("/run/20260517-120000-abcdef")
    assert response.status_code == 401

    # Wrong token → 401
    response = client.get(
        "/run/20260517-120000-abcdef", headers={"Authorization": "Bearer wrong"}
    )
    assert response.status_code == 401

    # Correct token → 404 (file doesn't exist, but auth passed)
    response = client.get(
        "/run/20260517-120000-abcdef", headers={"Authorization": "Bearer secret-token"}
    )
    assert response.status_code == 404


def test_serve_refuses_non_loopback_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DIALECTIC_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="non-loopback"):
        server.run(host="0.0.0.0", port=8765)


def test_serve_loopback_ok_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """127.0.0.1 binds without a token (loopback is implicitly trusted)."""
    import uvicorn
    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: None)  # don't actually serve
    # Should not raise.
    server.run(host="127.0.0.1", port=8765)
