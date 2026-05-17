# Examples

Runnable scripts demonstrating dialectic's three entry points.

| File | What it shows |
|---|---|
| `01_basic_run.sh` | The default CLI flow: writer → reviewer → approve. Use `--dry-run` to inspect the diff without applying. |
| `02_inspect_audit_log.py` | Print a per-phase summary of any completed run's prompts/responses by reading the forensic JSONL log. |
| `03_http_api_client.py` | Programmatic client against `dialectic serve`, including bearer-token auth. |

All three assume `dialectic` is on your `PATH` (`pip install -e .` or `pip install dialectic`).
