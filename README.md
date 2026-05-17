# dialectic

Cross-family writer-reviewer protocol for LLM code generation.

Claude (Opus) writes. Codex (GPT-5.4) reviews. The writer can defend its choices with rationale. The reviewer rebuts. You arbitrate anything they can't agree on.

## Why this exists

The literature on multi-agent LLM systems for code is mixed-to-negative on the obvious patterns (multi-agent debate, mutual critique iterations). The patterns that *do* work share two properties:

1. **Heterogeneous models** — different families catch different bugs ([Wisdom and Delusion, 2025](https://arxiv.org/abs/2510.21513))
2. **Clean-context review** — reviewers separate from writers catch real issues ([Cognition, "Multi-Agents: What's Actually Working", 2026](https://cognition.ai/blog/multi-agents-working))

`dialectic` implements both, with a structured per-item critique-and-defend protocol that addresses the sycophancy / anchoring failure mode documented in [Talk Isn't Always Cheap (2025)](https://arxiv.org/abs/2509.05396).

## What it doesn't do

- No competing-writers ensemble (literature shows this produces homogenized mush)
- No mutual-iteration debate loops (literature shows this causes degradation)
- No reference-free LLM judge picking winners (literature shows this is biased)

## Status

Pre-alpha. Building.

## Layout

```
dialectic/
  dialectic/              # importable Python package
    core.py               # orchestration loop
    protocol.py           # pydantic models (the contract)
    worktree.py           # git worktree helpers
    cli.py                # `dialectic ...` CLI
    server.py             # `dialectic serve` HTTP API
    agents/
      claude.py           # `claude -p` subprocess wrapper
      codex.py            # `codex exec` subprocess wrapper
  .claude/skills/dialectic/
    SKILL.md              # `/dialectic` slash command in Claude Code
  tests/
  pyproject.toml
```

## License

MIT
