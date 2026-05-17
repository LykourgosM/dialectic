# dialectic

[![ci](https://github.com/LykourgosM/dialectic/actions/workflows/ci.yml/badge.svg)](https://github.com/LykourgosM/dialectic/actions/workflows/ci.yml)
[![python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)](https://www.python.org)
[![license: MIT](https://img.shields.io/badge/license-MIT-green.svg)](./LICENSE)
[![ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

> Cross-family writer-reviewer protocol for LLM code generation. Claude Code writes, OpenAI Codex CLI reviews, the writer can defend its choices, and you arbitrate anything the two models can't agree on.

I wanted a CLI tool that ran Claude Code and OpenAI Codex CLI together on the same coding task, with one writing and the other reviewing, for hackathons and longer dev sessions where I'd want a sanity check before shipping AI-generated code. Most of the existing multi-CLI orchestrators (10+ surveyed below) use voting, debate, or consensus patterns, all of which have known failure modes for code generation. So I built `dialectic` to implement the pattern the literature actually supports: a single writer, a cross-family reviewer with execution access, structured per-item disagreement, and the human as the final arbiter when the two LLMs can't agree.

It's been validated by two real dogfood runs against this codebase itself: a `list-runs` subcommand ($2.34, 8.4 min) and a `--limit` flag for that subcommand ($0.76, 4.2 min). Both diffs were genuinely good code, and the second run caught a real test bug the first run's inspection-only review had missed.

## Quickstart

```bash
# From source (PyPI release coming once v0.1 settles):
git clone https://github.com/LykourgosM/dialectic && cd dialectic
pip install -e .

cd your-repo
dialectic run --prompt "Add a small helper function in src/utils.py that takes a list of ints and returns their mean, with type hints and a one-line docstring. Add a test."
```

You'll see live progress as the dance runs (~5–15 minutes with the default max-effort models — `claude-opus-4-7` and `gpt-5.4` are the current top-of-line as of May 2026; override with `--writer-model` / `--reviewer-model` as needed):

```
dialectic · writer=claude(claude-opus-4-7, max) reviewer=codex(gpt-5.4, xhigh)
● Starting run 20260517-155551-f1286b
→ Round 1: writer (claude) initial draft
✓ Round 1: writer done ($0.62, 108s)
→ Round 1: reviewer (codex)
✓ Round 1: reviewer verdict=approve (0 items, $0.13)
● Run 20260517-155551-f1286b awaiting_approval ($0.76, 250s)

Files changed (2):
  · src/utils.py
  · tests/test_utils.py

approve / reject / view (a/r/v)?
```

The default `--max-revisions 1` keeps cost bounded and matches what most reviews actually need. Use `--dry-run` to preview the diff without applying.

From inside Claude Code, the same flow is available as a slash command:

```
/dialectic Add a small helper function in src/utils.py …
```

## Why this design

The honest claim is narrow: **of the published patterns for orchestrating multiple LLMs on the same coding task, dialectic implements the only one that has held up to scrutiny.** Concretely, the design rejects three popular alternatives.

### What dialectic deliberately does NOT do

**No multi-agent debate loops.** The seminal multi-agent debate paper, [Du et al. 2023](https://arxiv.org/abs/2305.14325), showed N homogeneous debater clones improve factuality on QA tasks. Follow-up benchmarking by [Smit et al. (ICML 2024)](https://arxiv.org/abs/2311.17371) found that under controlled hyperparameter sweeps, multi-agent debate fails to reliably beat plain self-consistency, especially on reasoning. [Estornell et al. 2025](https://arxiv.org/abs/2509.05396) documents how debate can actively *reduce* accuracy over rounds via sycophancy and conformity pressure; agents drift from correct to incorrect answers under peer pressure from confidently-wrong neighbors. **Dialectic has exactly one writer and exactly one reviewer per run; there are no debate rounds.**

**No competing-writers ensembles with voting or consensus selection.** Tools like `claude-octopus` (3.4k stars) run 8 models in parallel and ship if 75% agree. [Wang et al. 2022](https://arxiv.org/abs/2203.11171) on self-consistency works for closed-form answers but generalizes poorly to code, where semantically-equivalent solutions differ lexically. [Tessari et al. 2025 ("Wisdom and Delusion of LLM Ensembles for Code Generation")](https://arxiv.org/abs/2510.21513) explicitly shows that consensus-based selection falls into a *popularity trap*, amplifying common-but-wrong outputs; diversity-based two-model ensembles capture ~95% of the ceiling that majority voting throws away. **Dialectic has one writer per run.** Diversity comes from cross-family review, not parallel writers.

**No reference-free LLM-as-judge for unresolved disputes.** [Zheng et al. (NeurIPS 2023)](https://arxiv.org/abs/2306.05685) documented systematic position, verbosity, and self-enhancement biases in LLM judges. [Wang et al. (ACL 2024)](https://arxiv.org/abs/2305.17926) showed swapping pairwise answer order can flip the ranking. [Panickssery, Bowman & Feng (NeurIPS 2024)](https://arxiv.org/abs/2404.13076) showed LLMs recognize their own outputs and rate them higher; self-preference is a causal effect, not just correlation. **When dialectic's writer and reviewer can't agree, an LLM does not break the tie. You do.**

### What dialectic does (and why)

**Single writer holding the pen.** [Cognition's "Don't Build Multi-Agents" (2024)](https://cognition.ai/blog/dont-build-multi-agents) argued that parallel coding agents make implicit, conflicting decisions and produce inconsistent code. Their May 2026 follow-up [Multi-Agents: What's Actually Working](https://cognition.ai/blog/multi-agents-working) updated the position: multi-agent works when *writes stay single-threaded* and additional agents contribute *intelligence rather than actions* (the "smart friend" consultation, map-reduce-and-manage hierarchy). Dialectic is exactly that shape: the writer holds the pen; the reviewer contributes intelligence; the user arbitrates.

**Cross-family review with execution access.** [Reflexion (Shinn et al. 2023)](https://arxiv.org/abs/2303.11366), [AgentCoder (Huang et al. 2023)](https://arxiv.org/abs/2312.13010), and [MapCoder (Islam et al. ACL 2024)](https://arxiv.org/abs/2405.11403) all share one observation: revision works when grounded in *execution*, not in self-critique. [Huang et al. (ICLR 2024)](https://arxiv.org/abs/2310.01798) and [Olausson et al. (ICLR 2024)](https://arxiv.org/abs/2306.09896) both showed that self-correction *without* an external oracle degrades performance. Dialectic's reviewer runs in `--sandbox workspace-write` on its own ephemeral worktree; it can `git apply` the writer's diff, run `pytest`, run linters, invoke any verification command. The second dogfood run made this concrete: the reviewer's summary read *"Verified the change by applying the diff and running pytest -q (6 passed), python -m dialectic.cli list-runs --help (shows the new --limit option), and git diff --check (clean). I also ran pytest -q; it has an unrelated failure in tests/test_concurrency.py, but nothing in this diff touches that area."* It caught a real bug the orchestrator's previous, inspection-only review had missed.

**Per-item critique-and-defend, not whole-diff arguments.** The reviewer emits a `ReviewerCritique` with discrete `CritiqueItem`s, each with a stable id. The writer can either accept (and revise) OR reject (with a written rationale), per item. The reviewer then does one rebuttal pass per defended item: accept the rationale, or escalate it as `still_disputed`. This structure addresses the [sycophancy failure mode](https://arxiv.org/abs/2509.05396) directly: the writer can't just capitulate to whoever sounds more confident; it has to either change the code or articulate *why* not. And the user only sees disputes, not noise.

**You arbitrate unresolved items, not an LLM.** A run with disputes ends in `RunStatus.AWAITING_ARBITRATION`. `dialectic arbitrate <id> --accept-writer 1 --accept-reviewer 2 --skip 3` resolves them per-item and the run moves to `AWAITING_APPROVAL`. This is the boundary [Verga et al. 2024 (PoLL)](https://arxiv.org/abs/2404.18796) and the LLM-judge bias literature point to: humans make the calls that LLMs are demonstrably bad at making in this regime.

## Architecture

The orchestrator is a deterministic Python state machine. Subagents are stateless subprocesses; the orchestrator threads all context via explicitly-constructed prompts. Three thin frontends share one core:

| Layer | What it is |
|---|---|
| `/dialectic` skill | A markdown file in `.claude/skills/dialectic/SKILL.md` that tells Claude Code how to invoke the CLI, render its events, and ask for approval. |
| `dialectic` CLI | Click + rich. Streams events as the dance runs, prompts for approval, accepts arbitration. |
| `dialectic serve` | FastAPI. Same protocol types, optional bearer auth, suitable for embedding. |
| **`dialectic.core`** | The orchestrator. Doesn't know about CLI rendering, HTTP, or how the subagents are reached. |
| `dialectic.agents.{claude,codex}` | Subprocess wrappers — the only modules that know about the underlying CLI flag syntax. |
| `dialectic.worktree` | Git worktree lifecycle and safety checks. |
| `dialectic.protocol` | Pydantic models. Generates the strict-mode JSON schemas passed to `claude -p --json-schema` and `codex exec --output-schema`. |

The protocol cycles until approve / reject / `max_revisions` reached / disputes remain:

1. Writer writes → `WriterReport` (diff + metadata).
2. Reviewer critiques → `ReviewerCritique` (verdict + critique items[]).
3. Writer responds → `WriterResponseBundle` (per item: accept-and-revise OR reject-with-rationale).
4. Reviewer rebuts rejections → `ReviewerRebuttal` (per item: accept rationale OR escalate as `still_disputed`).
5. If any item is still disputed at this point, status becomes `AWAITING_ARBITRATION` — you resolve via `dialectic arbitrate`.

Each agent invocation runs in an ephemeral `.dialectic/wt/{writer,reviewer}-<id>/` worktree that's deleted after the run. The writer holds workspace-write permissions; the reviewer also has workspace-write so it can actually execute tests/lints. The user's main working tree is untouched until `dialectic approve`.

See [`docs/architecture.md`](./docs/architecture.md) for a fuller diagram and the exact safety checks at apply time.

## Two real runs

These are the actual cost / duration / outcome of running dialectic on this codebase itself. Both are persisted in `.dialectic/runs/`.

| Run | Prompt | Outcome | Writer | Reviewer | Total |
|---|---|---|---|---|---|
| 1 | "Add a `dialectic list-runs` subcommand …" | Approved on first pass; reviewer admitted "approval is based on code inspection" because sandbox blocked tmpdir | $2.21, 6.6 min | $0.14, 1.8 min | **$2.34, 8.4 min** |
| 2 | "Add a `--limit N` flag to `dialectic list-runs` …" | Approved on first pass; reviewer **executed pytest, ran the CLI, ran `git diff --check`**, and **caught an unrelated existing test bug** in the process | $0.63, 1.8 min | $0.13, 2.4 min | **$0.76, 4.2 min** |

The delta between the two runs is direct evidence for the design: switching the reviewer from `--sandbox read-only` to `--sandbox workspace-write` (on its own throwaway worktree) made the difference between inspection-only approval and execution-grounded approval — and immediately surfaced a real test bug we wouldn't have caught otherwise.

## Comparison with existing tools

The multi-CLI / multi-model code-orchestration space is crowded. Honest take on where dialectic fits relative to the field as of May 2026:

- **`ruvnet/claude-flow`/`ruflo` (52k stars)** is the kitchen sink — 5+ CLIs, "Queen-led hierarchy," Raft/Byzantine/Gossip topologies, 32 plugins. Massive surface; consensus is "topology-dependent" and never fully pinned down.
- **`ComposioHQ/agent-orchestrator` (7k stars)** parallelizes agents on *independent* tasks in worktrees with auto-CI-fixing. Different problem: no cross-agent critique on the same change, so structural disagreement is impossible by construction.
- **`nyldn/claude-octopus` (3.4k stars)** runs 8 models in parallel with a 75% consensus gate and Claude as synthesizer. Synthesis is one-shot — reviewers can't *defend* a critique after the lead model responds.
- **`Enderfga/claw-orchestrator` (450 stars)** runs 5 CLIs in worktrees that *vote until they agree*; tie-break is deferred to a `council.md` skill file.
- **`AlessioZazzarini/claude-codex-collab` (17 stars)** is the closest spiritual neighbour — Claude-as-PM + Codex-as-engineer, max 2 unstructured debate rounds, then human escalation. No per-item structure; the debate is at the spec level.

Dialectic is deliberately narrow where the field is wide. It scopes to 2 CLIs and concentrates on a **per-item protocol where the reviewer can rebut, and where any unresolved dispute is a first-class step rather than a flag**. None of the other tools have a structured per-item defend-then-rebut loop. The honest cost: dialectic is younger, smaller, and has fewer integrations than every tool above except `claude-codex-collab`.

## When NOT to use dialectic

- **Short prompts where speed matters more than scrutiny.** A `claude -p "rename foo to bar"` is 30 seconds. Dialectic adds at least the reviewer's round trip (minimum 2–3 minutes, $0.20+). Not worth it for trivial edits.
- **Throughput across many tasks.** If you have 50 independent issues to fix in parallel, you want `ComposioHQ/agent-orchestrator` (or similar) and its auto-CI-fix loop. Dialectic processes one prompt at a time on purpose.
- **Production CI/CD pipelines without a human in the loop.** Dialectic surfaces disputes to you. If "you" is a cron job, the unresolved disputes have nowhere to go. (`--auto-approve` exists but disables the human-arbitration safety; use sparingly.)
- **Closed-form QA / single-token outputs.** Plain self-consistency ([Wang et al. 2022](https://arxiv.org/abs/2203.11171)) outperforms fancier patterns on these, and dialectic has nothing to add.
- **If you only have one CLI installed.** Dialectic needs both `claude` and `codex` on PATH; cross-family review is the whole point.

## Install

```bash
pip install dialectic                # once on PyPI
# or, from source:
git clone https://github.com/LykourgosM/dialectic && cd dialectic
pip install -e ".[dev]"
```

You'll need `claude` and `codex` on PATH and both authenticated. The CLIs use whatever subscription/auth you already have — dialectic doesn't need separate API keys.

Optional Claude Code skill (so you can type `/dialectic` inside a Claude Code session):

```bash
mkdir -p ~/.claude/skills
cp -r .claude/skills/dialectic ~/.claude/skills/
```

## Configuration

```bash
dialectic run --prompt "..." \
    --writer-cli claude --writer-model claude-opus-4-7 --writer-effort max \
    --reviewer-cli codex --reviewer-model gpt-5.4 --reviewer-effort xhigh \
    --max-revisions 1 \
    --apply-mode uncommitted   # or 'branch' or 'dry_run'
```

Defaults are the highest-effort settings on both sides because that's what the cross-family review pattern is designed to exploit; expect $1–$3 per run and 5–15 minutes. Drop to `medium` effort for ~10x cheaper runs at the cost of catching fewer real issues.

`max_revisions=0` skips the revision loop entirely (just write + review + maybe arbitrate). `max_revisions=1` (the default) gives the writer one chance to respond to the critique. Higher values are capped at 5; in practice runs converge or hit disputes within 1–2 revisions.

## Status & roadmap

v0.1 — protocol works end-to-end, 99 tests pass, two real runs validated. Things explicitly not in v0.1:

- `ACCEPT_REVIEWER` arbitration choice (currently `NotImplementedError`) — needs a fix-format design before it can synthesize patches from `suggested_fix`.
- Multi-reviewer panel ([Verga et al. 2024 PoLL](https://arxiv.org/abs/2404.18796)). The protocol has `reviewer_id` ready; needs critique merging logic.
- Auto-journal of past runs as context for future runs. Hook exists in `RunConfig.journal_file`.
- Real-time streaming of agent stdout (Codex's JSONL events) — currently only orchestrator-level events stream to the CLI.

See [`CHANGELOG.md`](./CHANGELOG.md) for the full release notes.

## References

Papers and write-ups the design draws on. Full annotated bibliography in [`docs/architecture.md`](./docs/architecture.md).

**What dialectic builds on**

- Shinn et al., **Reflexion** (NeurIPS 2023). [arXiv:2303.11366](https://arxiv.org/abs/2303.11366).
- Chen et al., **CodeT: Code Generation with Generated Tests** (ICLR 2023). [arXiv:2207.10397](https://arxiv.org/abs/2207.10397).
- Li et al., **Competition-Level Code Generation with AlphaCode** (*Science*, 2022). [arXiv:2203.07814](https://arxiv.org/abs/2203.07814).
- Islam, Ali, Parvez, **MapCoder** (ACL 2024). [arXiv:2405.11403](https://arxiv.org/abs/2405.11403).
- Huang et al., **AgentCoder** (2023). [arXiv:2312.13010](https://arxiv.org/abs/2312.13010).
- Verga et al., **Replacing Judges with Juries (PoLL)** (2024). [arXiv:2404.18796](https://arxiv.org/abs/2404.18796).
- Anthropic, **How we built our multi-agent research system** (2025). [anthropic.com/engineering/multi-agent-research-system](https://www.anthropic.com/engineering/multi-agent-research-system).
- Walden Yan / Cognition, **Multi-Agents: What's Actually Working** (2026). [cognition.ai/blog/multi-agents-working](https://cognition.ai/blog/multi-agents-working).

**What dialectic deliberately avoids**

- Du et al., **Multi-Agent Debate** (ICML 2024). [arXiv:2305.14325](https://arxiv.org/abs/2305.14325).
- Smit et al., **Should we be going MAD?** (ICML 2024). [arXiv:2311.17371](https://arxiv.org/abs/2311.17371).
- Estornell et al., **Talk Isn't Always Cheap** (2025). [arXiv:2509.05396](https://arxiv.org/abs/2509.05396).
- Huang et al., **LLMs Cannot Self-Correct Reasoning Yet** (ICLR 2024). [arXiv:2310.01798](https://arxiv.org/abs/2310.01798).
- Olausson et al., **Is Self-Repair a Silver Bullet for Code Generation?** (ICLR 2024). [arXiv:2306.09896](https://arxiv.org/abs/2306.09896).
- Zheng et al., **Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena** (NeurIPS 2023). [arXiv:2306.05685](https://arxiv.org/abs/2306.05685).
- Wang et al., **Large Language Models are not Fair Evaluators** (ACL 2024). [arXiv:2305.17926](https://arxiv.org/abs/2305.17926).
- Panickssery, Bowman, Feng, **LLM Evaluators Recognize and Favor Their Own Generations** (NeurIPS 2024). [arXiv:2404.13076](https://arxiv.org/abs/2404.13076).
- Tessari et al., **Wisdom and Delusion of LLM Ensembles for Code Generation and Repair** (2025). [arXiv:2510.21513](https://arxiv.org/abs/2510.21513).
- Walden Yan / Cognition, **Don't Build Multi-Agents** (2024). [cognition.ai/blog/dont-build-multi-agents](https://cognition.ai/blog/dont-build-multi-agents).

**Baseline**

- Wang et al., **Self-Consistency Improves Chain-of-Thought Reasoning** (ICLR 2023). [arXiv:2203.11171](https://arxiv.org/abs/2203.11171). The cheap baseline that fancier ensemble patterns consistently fail to beat.

## License

MIT. See [`LICENSE`](./LICENSE).

## Contributing

See [`CONTRIBUTING.md`](./CONTRIBUTING.md). Security issues: [`SECURITY.md`](./SECURITY.md).
