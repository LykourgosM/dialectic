"""HTTP API server (`dialectic serve`).

Thin FastAPI wrapper over core.py. Streams events via Server-Sent Events.
Same protocol types as the CLI — the binary is the source of truth.
"""

from __future__ import annotations

from fastapi import FastAPI

from .protocol import RunConfig, RunResult

app = FastAPI(title="dialectic", version="0.1.0")


@app.post("/run", response_model=RunResult)
async def post_run(config: RunConfig) -> RunResult:
    """Execute one run synchronously; return the RunResult."""
    raise NotImplementedError


@app.post("/run/stream")
async def post_run_stream(config: RunConfig):
    """Execute one run; stream StreamEvents via SSE."""
    raise NotImplementedError


@app.post("/run/{run_id}/approve", response_model=RunResult)
async def post_approve(run_id: str) -> RunResult:
    """Apply a previously-completed run's diff."""
    raise NotImplementedError


@app.post("/run/{run_id}/reject", response_model=RunResult)
async def post_reject(run_id: str) -> RunResult:
    """Discard a previously-completed run."""
    raise NotImplementedError


@app.get("/run/{run_id}", response_model=RunResult)
async def get_run(run_id: str) -> RunResult:
    """Fetch a previously-completed run record from the audit log."""
    raise NotImplementedError


def run(host: str = "127.0.0.1", port: int = 8765) -> None:
    """Entry point for `dialectic serve`."""
    import uvicorn

    uvicorn.run(app, host=host, port=port)
