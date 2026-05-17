"""HTTP API server (`dialectic serve`).

Thin FastAPI wrapper over core.py.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException

from . import core
from .protocol import ArbitrationDecision, RunConfig, RunResult

app = FastAPI(title="dialectic", version="0.1.0")

# For v1, the server operates on a single repo (the cwd at startup). v1.1 can add multi-repo.
REPO_ROOT: Path = Path.cwd()


@app.post("/run", response_model=RunResult)
async def post_run(config: RunConfig) -> RunResult:
    return await core.run(config, REPO_ROOT)


@app.get("/run/{run_id}", response_model=RunResult)
async def get_run(run_id: str) -> RunResult:
    try:
        return core.load_run_record(run_id, REPO_ROOT)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"No run record for {run_id}")


@app.post("/run/{run_id}/approve", response_model=RunResult)
async def post_approve(run_id: str) -> RunResult:
    try:
        result = core.load_run_record(run_id, REPO_ROOT)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"No run record for {run_id}")
    try:
        return core.apply_run_result(result, REPO_ROOT)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.post("/run/{run_id}/reject", response_model=RunResult)
async def post_reject(run_id: str) -> RunResult:
    try:
        result = core.load_run_record(run_id, REPO_ROOT)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"No run record for {run_id}")
    return core.reject_run_result(result, REPO_ROOT)


@app.post("/run/{run_id}/arbitrate", response_model=RunResult)
async def post_arbitrate(run_id: str, decisions: list[ArbitrationDecision]) -> RunResult:
    try:
        return await core.resume_with_arbitration(run_id, decisions, REPO_ROOT)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc))


def run(host: str = "127.0.0.1", port: int = 8765, repo_root: Path | None = None) -> None:
    """Entry point for `dialectic serve`."""
    global REPO_ROOT
    if repo_root is not None:
        REPO_ROOT = repo_root.resolve()
    import uvicorn

    uvicorn.run(app, host=host, port=port)
