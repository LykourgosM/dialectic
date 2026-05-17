# Contributing

Thanks for considering a contribution. Dialectic is a small project — please open an issue describing the change before submitting a non-trivial PR so we can agree on the shape.

## Dev setup

Requires Python 3.11+ and `uv` (or pip). The repo also expects `claude` and `codex` on PATH for end-to-end tests, but most of the suite mocks them.

```bash
git clone https://github.com/LykourgosM/dialectic
cd dialectic
make install      # uv sync --extra dev + pre-commit install
```

## Running things

```bash
make test         # ~5s; excludes real-CLI E2E
make lint         # ruff check + ruff format --check
make typecheck    # mypy dialectic
make check        # lint + typecheck + test
make cov          # with coverage report
make e2e          # real-CLI E2E (DIALECTIC_E2E=1; ~$1/run, ~5-15 min)
```

CI runs `lint + typecheck + test` on Python 3.11 and 3.12. Make sure `make check` passes locally before pushing.

## Code style

- Ruff handles formatting and most lint. Run `make format` before committing.
- Type annotations on all `dialectic/*` public surfaces. Mypy strict-ish (no untyped defs, no implicit Optional). Tests are looser.
- Pydantic v2 idioms throughout. Models that travel between orchestrator and subagents inherit `_Strict` (extra=forbid, validate_assignment=true).
- No comments that explain *what* — name things well. Comments explain *why* when the rationale isn't obvious (a hidden constraint, a surprising past incident, a deliberate workaround).

## Tests

- Unit tests in `tests/test_protocol.py`, `tests/test_strict_schema.py`, `tests/test_worktree.py`.
- Integration tests (mocked agents) in `tests/test_orchestrator.py`.
- CLI/server tests in `tests/test_cli.py`, `tests/test_server.py`.
- Concurrency tests in `tests/test_concurrency.py`.
- E2E tests against real CLIs in `tests/test_e2e_real_cli.py`, gated by `DIALECTIC_E2E=1`.

The `conftest.py` autouse fixture blocks accidental real-CLI subprocess calls — if you add a new agent wrapper, either patch the relevant `invoke` function in your tests or extend the conftest block.

## Pull requests

- One topic per PR. Small PRs review fastest.
- Update `CHANGELOG.md` under `[Unreleased]` for user-visible changes.
- Update or add tests. CI will reject PRs that drop below the coverage threshold.
- For protocol changes (`dialectic/protocol.py`): include rationale in the PR description. The pydantic contract is what makes the schemas reliable across model versions — changes there ripple.

## Reporting a bug

Open an issue with: the run id of an affected run, the `.dialectic/runs/<id>.json` contents (with prompts/responses redacted if they contain anything sensitive), and the dialectic version (`dialectic --version`).

## Security issues

See [`SECURITY.md`](./SECURITY.md).
