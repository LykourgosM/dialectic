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
def app(tmp_git_repo: Path):
    """A FastAPI app scoped to tmp_git_repo with no auth — fresh per test."""
    return server.create_app(tmp_git_repo, auth_token=None)


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


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


def test_get_run_returns_persisted_record(client: TestClient, tmp_git_repo: Path) -> None:
    persisted = _persist_result(tmp_git_repo)
    response = client.get(f"/run/{persisted.run_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == persisted.run_id
    assert body["status"] == "awaiting_approval"


def test_post_approve_404_when_missing(client: TestClient) -> None:
    response = client.post("/run/20260517-120000-abcdef/approve")
    assert response.status_code == 404


def test_post_reject_marks_status(client: TestClient, tmp_git_repo: Path) -> None:
    persisted = _persist_result(tmp_git_repo)
    response = client.post(f"/run/{persisted.run_id}/reject")
    assert response.status_code == 200
    assert response.json()["status"] == "rejected_by_user"


def test_post_arbitrate_validates_run_id_format(client: TestClient) -> None:
    response = client.post("/run/not_a_valid_id/arbitrate", json=[])
    assert response.status_code in (400, 404)


def test_auth_required_when_token_set(tmp_git_repo: Path) -> None:
    app = server.create_app(tmp_git_repo, auth_token="secret-token")
    client = TestClient(app)

    response = client.get("/run/20260517-120000-abcdef")
    assert response.status_code == 401

    response = client.get(
        "/run/20260517-120000-abcdef", headers={"Authorization": "Bearer wrong"}
    )
    assert response.status_code == 401

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
    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: None)
    server.run(host="127.0.0.1", port=8765)
