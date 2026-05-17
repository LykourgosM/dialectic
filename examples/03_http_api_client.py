"""Programmatic client against `dialectic serve`.

Demonstrates the HTTP API: submit a run, poll its status, and approve when
ready. Run the server in another terminal first:

    dialectic serve --port 8765

Then run this script. With auth enabled:

    DIALECTIC_TOKEN=secret dialectic serve --port 8765
    DIALECTIC_TOKEN=secret python examples/03_http_api_client.py
"""

from __future__ import annotations

import os

import httpx

BASE_URL = os.environ.get("DIALECTIC_URL", "http://127.0.0.1:8765")
TOKEN = os.environ.get("DIALECTIC_TOKEN")


def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}


def main() -> int:
    config = {
        "prompt": "Add a CHANGELOG entry noting that the README was rewritten.",
        "max_revisions": 1,
        "apply_mode": "dry_run",
    }

    with httpx.Client(timeout=httpx.Timeout(30 * 60, connect=10)) as client:
        # Synchronous run — the request blocks until the dance completes.
        response = client.post(f"{BASE_URL}/run", json=config, headers=auth_headers())
        response.raise_for_status()
        result = response.json()

        print(f"run_id      : {result['run_id']}")
        print(f"status      : {result['status']}")
        print(f"cost_usd    : ${result['cost_usd']:.4f}")
        print(f"duration_s  : {result['duration_s']:.1f}")
        print(f"files       : {result['files_changed']}")
        print(f"summary     : {result['summary']}")

        if result["status"] == "awaiting_arbitration":
            print(f"\n{len(result['disputed_items'])} disputed item(s); use the CLI to arbitrate.")
            return 0

        # Auto-approve (in a real script you'd render the diff for the user first).
        approve = client.post(f"{BASE_URL}/run/{result['run_id']}/approve", headers=auth_headers())
        approve.raise_for_status()
        print(f"\napplied: {approve.json()['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
