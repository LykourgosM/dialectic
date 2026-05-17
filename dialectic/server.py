"""HTTP API server (`dialectic serve`).

Thin FastAPI wrapper over core.py. Same protocol types as the CLI — the binary
is the source of truth; this is just another frontend.

**Security**: the server is loopback-only by default (host=127.0.0.1). To bind
to a non-loopback interface (or 0.0.0.0) you must set a bearer token via
`--token TOKEN` or the env var `DIALECTIC_TOKEN`, and clients must send
`Authorization: Bearer TOKEN` on every request.
"""

from __future__ import annotations

import ipaddress
import os
import secrets
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException

from . import core
from .protocol import ArbitrationDecision, RunConfig, RunResult

app = FastAPI(title="dialectic", version="0.1.0")

REPO_ROOT: Path = Path.cwd()
AUTH_TOKEN: str | None = None  # Set by run() when --token is provided.


def require_token(authorization: str | None = Header(default=None)) -> None:
    """Auth dependency. No-op if AUTH_TOKEN is unset (loopback-only mode)."""
    if AUTH_TOKEN is None:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    presented = authorization[len("Bearer ") :]
    if not secrets.compare_digest(presented, AUTH_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid token")


@app.post("/run", response_model=RunResult)
async def post_run(config: RunConfig, _: None = Depends(require_token)) -> RunResult:
    return await core.run(config, REPO_ROOT)


@app.get("/run/{run_id}", response_model=RunResult)
async def get_run(run_id: str, _: None = Depends(require_token)) -> RunResult:
    try:
        return core.load_run_record(run_id, REPO_ROOT)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"No run record for {run_id}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/run/{run_id}/approve", response_model=RunResult)
async def post_approve(run_id: str, _: None = Depends(require_token)) -> RunResult:
    try:
        result = core.load_run_record(run_id, REPO_ROOT)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"No run record for {run_id}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        return core.apply_run_result(result, REPO_ROOT)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.post("/run/{run_id}/reject", response_model=RunResult)
async def post_reject(run_id: str, _: None = Depends(require_token)) -> RunResult:
    try:
        result = core.load_run_record(run_id, REPO_ROOT)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"No run record for {run_id}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return core.reject_run_result(result, REPO_ROOT)


@app.post("/run/{run_id}/arbitrate", response_model=RunResult)
async def post_arbitrate(
    run_id: str,
    decisions: list[ArbitrationDecision],
    _: None = Depends(require_token),
) -> RunResult:
    try:
        return await core.resume_with_arbitration(run_id, decisions, REPO_ROOT)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc))


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
    """Entry point for `dialectic serve`.

    Refuses to bind to a non-loopback host unless `token` is provided (or
    DIALECTIC_TOKEN env var is set). The token may also be set for loopback
    binds — clients must then send `Authorization: Bearer <token>`.
    """
    global REPO_ROOT, AUTH_TOKEN
    if repo_root is not None:
        REPO_ROOT = repo_root.resolve()

    AUTH_TOKEN = token or os.environ.get("DIALECTIC_TOKEN")

    if not _is_loopback(host) and AUTH_TOKEN is None:
        raise RuntimeError(
            f"Refusing to bind to non-loopback host {host!r} without a bearer token. "
            "Pass --token TOKEN or set DIALECTIC_TOKEN env var. (This is a safety "
            "guard: the server can drive subprocess execution with elevated "
            "permissions on your repo.)"
        )

    import uvicorn

    uvicorn.run(app, host=host, port=port)
