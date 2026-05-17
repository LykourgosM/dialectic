"""HTTP API server (`dialectic serve`).

Thin FastAPI wrapper over core.py. Same protocol types as the CLI — the binary
is the source of truth; this is just another frontend.
"""

from __future__ import annotations

from fastapi import FastAPI

from .protocol import ArbitrationDecision, RunConfig, RunResult

app = FastAPI(title="dialectic", version="0.1.0")


@app.post("/run", response_model=RunResult)
async def post_run(config: RunConfig) -> RunResult:
    """Execute one run synchronously; return the RunResult.

    Response status_code is always 200 — inspect RunResult.status for outcome
    (AWAITING_APPROVAL, AWAITING_ARBITRATION, FAILED, etc.).
    """
    raise NotImplementedError


@app.post("/run/stream")
async def post_run_stream(config: RunConfig):
    """Execute one run; stream StreamEvents via Server-Sent Events."""
    raise NotImplementedError


@app.get("/run/{run_id}", response_model=RunResult)
async def get_run(run_id: str) -> RunResult:
    """Fetch a previously-persisted RunResult."""
    raise NotImplementedError


@app.post("/run/{run_id}/approve", response_model=RunResult)
async def post_approve(run_id: str) -> RunResult:
    """Apply a previously-completed run's diff. 409 if the run has unresolved disputes."""
    raise NotImplementedError


@app.post("/run/{run_id}/reject", response_model=RunResult)
async def post_reject(run_id: str) -> RunResult:
    """Discard a previously-completed run."""
    raise NotImplementedError


@app.post("/run/{run_id}/arbitrate", response_model=RunResult)
async def post_arbitrate(run_id: str, decisions: list[ArbitrationDecision]) -> RunResult:
    """Submit user resolutions for disputed items. Moves the run to AWAITING_APPROVAL."""
    raise NotImplementedError


def run(host: str = "127.0.0.1", port: int = 8765) -> None:
    """Entry point for `dialectic serve`."""
    import uvicorn

    uvicorn.run(app, host=host, port=port)
