<div align="center">

# ⚒ XGEN MAKER

**Ask in plain language. Get a reviewable merge request — grounded in a knowledge graph of your own code.**

[![tests](https://img.shields.io/badge/tests-567%20passing-3aa8c9)](#testing)
[![retrieval](https://img.shields.io/badge/landing%20R%4010-0.868-3aa8c9)](#measured-not-asserted)
[![python](https://img.shields.io/badge/python-3.12%2B-3aa8c9)](#requirements)
[![deps](https://img.shields.io/badge/dependencies-stdlib%20first-3aa8c9)](#requirements)
[![license](https://img.shields.io/badge/license-private-8894a0)](#license)

[한국어 README](README.ko.md)

</div>

---

Coding agents write code well and locate it badly. On a multi-repository platform, *"fix the login
redirect"* means finding one file among thousands, knowing who depends on it, not breaking them,
and following the team's branch and review rules. The agent never sees any of that.

MAKER puts a **code knowledge graph** in front of the agent and a **convergence loop** behind it.
It stops at a merge request — humans merge, humans deploy.

---

## Why MAKER?

| Problem | MAKER's answer |
|---|---|
| The agent starts from an empty context and guesses at file paths | A graph of every file, class, function, endpoint and route across all your repositories — landing resolves to `repo:path:line` |
| Keyword search fails when the request and the code share no words | **Hybrid retrieval**: BM25 over the graph *plus* dense vectors, fused by reciprocal rank. Neither layer is trusted alone |
| A change breaks callers nobody looked at | `imports` / `calls` / cross-repo `resolves_to` edges surface dependents *before* the agent writes anything |
| One MR usually touches several files; the agent sees one | Landing is extended with the files it is actually wired to, so the agent knows what its change can break |
| "It works" means nothing without a gate | Sandbox + tests + regression + an LLM judge, looping until it passes — or reporting failure honestly |
| Agents that can deploy are agents that can take production down | MAKER cannot push to protected branches, cannot touch infrastructure files, and cannot deploy. Ever |
| Retrieval tuning is usually vibes | Every ranking constant in this repository is measured against real merged MRs, and the measurement is written next to the constant |

---

## What a run looks like

```
you: "the ontology graph doesn't refresh after rebuild — fix it"

  ├─ classify intent            bug / feature / refactor / question
  ├─ expand to code vocabulary  Korean request → the words this codebase uses
  ├─ land in the graph          lexical + semantic, fused        → repo:path:line
  ├─ pull in wired files        what this change can break
  ├─ pull latest, branch        naming rules + protected-branch guards
  ├─ agent implements           graph context + real source excerpts
  ├─ verify                     syntax · tests · sandbox isolation · regression
  ├─ judge                      quality gate — failures feed back, retry
  └─ prepare merge request      ← stops here, on purpose

you: review · merge · deploy
```

---

## Measured, not asserted

MAKER is evaluated the only way that means anything: against **merge requests that humans already
merged**. Each MR title becomes the request; the files that MR changed are the ground truth. If
MAKER lands where the team actually worked, retrieval is right.

The benchmark is **2,587 merged MRs the tuning has never seen**, drawn from every repository in
the platform. Exclusions are declared, counted, and tested — not quietly applied. From 4,155 collected MRs:
667 touch only files the graph does not index, 343 share a title with a different answer (one
query, several truths), 217 are release trains or branch merges, 101 have titles too short to be
a request. Each rule is a property of the data, not of the ranker, and each has a regression test
so the definition cannot drift.

The graph is built from *today's* code, so an old MR describes a place that has since moved.
Scores track that distance, which is why the window is stated rather than hidden:

| Metric | Lexical only | **Hybrid, tuned** |
|---|---|---|
| Landing is exactly right (R@1) | 0.371 | **0.455** |
| Answer in the agent's evidence list (R@10) | 0.777 | **0.813** |
| …restricted to MRs merged in the last ~3 months | — | **0.868** |
| MRR | 0.505 | **0.572** |
| MR's changed files fully covered | — | **47.3%** (66.2% average) |

Every ranking constant was re-checked against this set. Three had to move: they had been fitted
to an earlier 294-MR sample whose answers happened to sit in small repositories (14% and 13% of
answers in repositories holding 1.0% and 3.6% of the graph). One of them — a repository-size
correction — was removed entirely, because on a representative sample there is no skew left to
correct. The measurement that justified each surviving value is written next to it.

Tuning runs against a stratified 600-MR slice that preserves the repository and month mix, which
is four times faster; it is only trusted because it ranks the candidate values in the same order
as the full set, and that agreement is itself checked.

Not every measurable gain is kept. Loosening the penalty on test files raises R@1 by nine points
— but only because the benchmark counts a hit on *any* file the MR touched, and test files are
easy to find. Score the implementation files alone and the same change makes it worse
(0.455 → 0.448). The penalty stayed where it was, and the trap is written next to it.

**Graph quality is audited against the repositories themselves**, not assumed:

| Check | Result |
|---|---|
| Files on disk present in the graph | 99.5% |
| Python definitions vs. AST ground truth | 2,628 sampled — **0 missing, 0 extra** |
| `imports` edges with a real import behind them | 300 sampled — **100%** |
| `calls` edges with a real call behind them | 99.2% → comment/string prose no longer scanned |

---

## Architecture at a glance

### Retrieval — three layers, fused, none trusted alone

```
  request ─┬─→ lexical (BM25 over graph nodes)  ──┐
           │     names · paths · docstrings       │
           │     referenced identifiers           │
           │     config keys, values and versions ├─→ reciprocal rank fusion ─→ landing
           │     the words the UI shows people    │
           ├─→ semantic (dense vectors)         ──┤
           │     for requests whose words         │
           │     never appear in the code         │
           └─→ anchors (exact mentions)         ──┘
                 a path or symbol you named
                                                     ↓
                                          wired files (graph 1-hop)
                                          workflow chain (2-hop)
```

The semantic layer is **optional and off by default**. Point it at any OpenAI-compatible
embedding endpoint and it turns on; if the endpoint is unreachable, landing falls back to lexical
so nothing that worked yesterday stops working. It does not fall back *silently*, though —
"not configured" and "configured but unreachable" are different states, and the run log and
`maker doctor` say which one you are in. A layer that can vanish without a word is a layer whose
absence you discover from a bad answer.

### Pipeline

```
                    ┌──────────── surfaces ────────────┐
                    │   CLI      Web (SSE)      MCP    │
                    └────────────────┬─────────────────┘
                                     ▼
                            MakerLoop — 28 stages
                                     │
  ┌──────────────┬──────────────┬────┴─────┬──────────────┬──────────────┐
  ▼              ▼              ▼          ▼              ▼              ▼
intent      knowledge       git ops    coding agent   verification   merge request
classify      graph      branch/commit  (subprocess)  tests·sandbox      draft
              │                                          ·judge
              ▼
   build · sync · refresh · embed · overlay · search · impact
```

| Module | Responsibility |
|---|---|
| `xgen_maker/kg/` | Graph build, incremental sync, safe refresh, lexical + dense search, human overlay edits |
| `xgen_maker/loop/` | intent → landing → branch → implement → verify → judge → merge request |
| `xgen_maker/web.py` | Dashboard — stdlib `http.server`, server-sent events, single-file UI |
| `xgen_maker/mcp_server.py` | Exposes the graph and planner to other agents |
| `scripts/` | Operational helpers and the benchmark harness |

---

## Installation

```bash
pip install -e .                                   # provides the `maker` command

cp .env.example .env                               # your tokens
cp maker.config.example.json maker.config.json     # your repositories

maker login                                        # detects your Claude CLI session
maker doctor --config maker.config.json            # verifies every capability for real
```

> **Nothing works without configuration — by design.** This repository ships placeholders only.
> Hosts, tokens and repository paths live in `.env` and `maker.config.json`, both gitignored.

**Requirements**: Python 3.12+ and `git`. The core graph, loop and dashboard have **no third-party
runtime dependencies**. Optional extras unlock optional features — `numpy` (semantic search),
`Pillow` (pixel diff), Playwright via `npx` (screenshots), an LLM provider (query expansion,
quality judging). The implement step needs a coding-agent CLI: Claude CLI by default, or any
command through `agent_cmd`.

---

## Quick start

### 1. Build the graph

```bash
maker kg rebuild --config maker.config.json     # every repo in your config, extracted and merged
```

Adding a repository is one line in `maker.config.json`; `rebuild` is the only command that follows.

### 2. Turn on semantic search (optional)

```bash
# .env
XGEN_MAKER_EMBED_BASE=http://your-embedding-host/v1
XGEN_MAKER_EMBED_MODEL=your-embedding-model
```

```bash
maker kg embed --config maker.config.json       # re-embeds only what changed
```

Your code is sent to the endpoint you configure and nowhere else. Leave it unset and MAKER runs
lexical-only.

### 3. Ask for something

```bash
maker run "fix the login redirect bug" --config maker.config.json                    # analyze only
maker run "fix the login redirect bug" --config maker.config.json --mode observe     # + branch & commit
maker run "fix the login redirect bug" --config maker.config.json --mode act         # + push & MR
```

### 4. Or open the dashboard

```bash
maker web --config maker.config.json            # http://127.0.0.1:8760
```

---

## Modes

Safety scales with intent. The default touches nothing.

| Mode | Repository | Remote | Use for |
|---|---|---|---|
| `plan` *(default)* | untouched | — | exploring, answering questions |
| `observe` | local branch + commit | — | reviewing a change before it leaves your machine |
| `act` | local branch + commit | push + merge request | handing work to your team |

Unknown mode values are **rejected**, never silently upgraded to write access.

---

## Keeping the graph fresh

A stale graph sends the agent to the wrong file, so freshness is a first-class concern.

```bash
maker kg sync              # re-extract only what changed locally
maker kg refresh           # fetch remotes, fast-forward when safe, then sync
maker kg hook --install    # git hooks: refresh on commit / merge / checkout
```

`refresh` is deliberately conservative. It **fetches** — which never touches your working tree —
and fast-forwards only when the tree is clean, an upstream exists, and the branch has not
diverged. Otherwise it skips and says why. It never checks out, stashes, rebases, or forces.

Incremental sync is tested to produce **the same graph as a full rebuild**, node for node and edge
for edge, including after repeated runs. A graph that quietly erodes as you sync it is worse than
no graph.

---

## The dashboard

| Tab | What it gives you |
|---|---|
| **Run** | Live step-by-step stream, the code it landed on, a stop button that actually kills the agent |
| **Pipeline** | All 28 stages, which ran, and the setting that gates each — editable in place |
| **Knowledge graph** | Repo map → drill into a repo → click a node for real source. Annotations persist across rebuilds |
| **History** | Every session with its timeline, resume, and one-click undo |
| **Tests** | Verification records per run — sandbox, checks, quality score with its basis |
| **Visual check** | Screenshot a page, save a baseline, pixel-diff later changes |
| **Health** | Graph freshness per repo, integrity, symbol accuracy — measured, not asserted |

The dashboard has **no authentication**. It refuses to bind to a non-loopback address unless you
explicitly opt in, because anyone who reaches the port acts with your stored credentials. Put it
behind an authenticating proxy before exposing it.

---

## Safety model

MAKER is designed to be *boring* in production.

- **Never deploys.** The pipeline stops at a merge request.
- **Protected branches** cannot be created, committed to, or pushed.
- **Infrastructure files** — Dockerfiles, CI descriptors, charts — are vetoed. Source only.
- **Authorization is checked before writing**, not after.
- **Secrets are masked** in every journal, summary and error, by URL shape *and* by token shape.
- **Everything is journaled** per session and undone with one command.
- **Failures are reported honestly.** A skipped test says skipped; an unverified regression says
  unverified. Nothing is green that isn't.

---

## Testing

```bash
python -m pytest -q
```

567 tests covering the graph extractors, incremental-sync equivalence, retrieval ranking, safety
guards, the convergence loop end-to-end over a real temporary repository, and the dashboard
endpoints.

Most of them are regression tests written against bugs that actually happened — including the ones
this project introduced and then fixed. Several assert that a tuned constant still carries the
measurement that justified it, so a future edit has to re-measure rather than re-guess.

---

## License

Private. All rights reserved.
