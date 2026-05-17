"""HTTP API server (`dialectic serve`).

Thin FastAPI wrapper over core.py. Same protocol types as the CLI — the binary
is the source of truth; this is just another frontend.

**Security**: the server is loopback-only by default (host=127.0.0.1). To bind
to a non-loopback interface (or 0.0.0.0) you must set a bearer token via
`--token TOKEN` or the env var `DIALECTIC_TOKEN`, and clients must send
`Authorization: Bearer TOKEN` on every request.

The app is constructed via :func:`create_app`. Tests instantiate a fresh app
per test instead of monkeypatching module globals.
"""

from __future__ import annotations

import ipaddress
import os
import secrets
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.security.utils import get_authorization_scheme_param

from . import core
from .protocol import ArbitrationDecision, RunConfig, RunResult


def create_app(repo_root: Path, auth_token: str | None = None) -> FastAPI:
    """Construct a FastAPI app scoped to a specific repo root and optional bearer token.

    Closes over its configuration so multiple apps can coexist in one process
    (useful for tests and for embedding the server in a host application).
    """
    app = FastAPI(title="dialectic", version="0.1.0")
    app.state.repo_root = repo_root.resolve()
    app.state.auth_token = auth_token

    async def require_token(request: Request) -> None:
        token = request.app.state.auth_token
        if token is None:
            return
        scheme, presented = get_authorization_scheme_param(
            request.headers.get("Authorization")
        )
        if scheme.lower() != "bearer" or not presented:
            raise HTTPException(status_code=401, detail="Missing Bearer token")
        if not secrets.compare_digest(presented, token):
            raise HTTPException(status_code=401, detail="Invalid token")

    def _load_or_raise(run_id: str, repo_root: Path) -> RunResult:
        try:
            return core.load_run_record(run_id, repo_root)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"No run record for {run_id}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/run", response_model=RunResult)
    async def post_run(
        config: RunConfig, request: Request, _: None = Depends(require_token)
    ) -> RunResult:
        return await core.run(config, request.app.state.repo_root)

    @app.get("/run/{run_id}", response_model=RunResult)
    async def get_run(
        run_id: str, request: Request, _: None = Depends(require_token)
    ) -> RunResult:
        return _load_or_raise(run_id, request.app.state.repo_root)

    @app.post("/run/{run_id}/approve", response_model=RunResult)
    async def post_approve(
        run_id: str, request: Request, _: None = Depends(require_token)
    ) -> RunResult:
        result = _load_or_raise(run_id, request.app.state.repo_root)
        try:
            return core.apply_run_result(result, request.app.state.repo_root)
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/run/{run_id}/reject", response_model=RunResult)
    async def post_reject(
        run_id: str, request: Request, _: None = Depends(require_token)
    ) -> RunResult:
        result = _load_or_raise(run_id, request.app.state.repo_root)
        return core.reject_run_result(result, request.app.state.repo_root)

    @app.post("/run/{run_id}/arbitrate", response_model=RunResult)
    async def post_arbitrate(
        run_id: str,
        decisions: list[ArbitrationDecision],
        request: Request,
        _: None = Depends(require_token),
    ) -> RunResult:
        try:
            return await core.resume_with_arbitration(
                run_id, decisions, request.app.state.repo_root
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return app


def _is_loopback(host: str) -> bool:
    if host in ("localhost", "127.0.0.1", "::1"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def run(
    host: str = "127.0.0.1",
    port: int = 8765,
    repo_root: Path | None = None,
    token: str | None = None,
) -> None:
    """Entry point for ``dialectic serve``.

    Refuses to bind a non-loopback host unless a bearer token is provided (via
    ``token=`` or the ``DIALECTIC_TOKEN`` env var). Loopback binds permit no
    auth so local CLIs can hit the server without juggling tokens.
    """
    resolved_token = token or os.environ.get("DIALECTIC_TOKEN")
    if not _is_loopback(host) and resolved_token is None:
        raise RuntimeError(
            f"Refusing to bind non-loopback host {host!r} without a bearer token. "
            "Pass --token TOKEN or set DIALECTIC_TOKEN env var. (The server drives "
            "subprocess execution with elevated permissions on your repo.)"
        )

    app = create_app(repo_root or Path.cwd(), auth_token=resolved_token)

    import uvicorn

    uvicorn.run(app, host=host, port=port)


__all__ = ["create_app", "run"]
