<div align="center">

# ⚒ XGEN MAKER

**Ship code from a single sentence — grounded in a knowledge graph of your codebase.**

Ask in plain language. MAKER finds *where* in your code to change, works on a fresh branch,
lets a coding agent implement it, then **tests and fixes itself until it passes** — and stops at
a merge request for a human to review.

[![tests](https://img.shields.io/badge/tests-304%20passing-3aa8c9)](#testing)
[![python](https://img.shields.io/badge/python-3.12%2B-3aa8c9)](#requirements)
[![deps](https://img.shields.io/badge/dependencies-stdlib%20first-3aa8c9)](#requirements)
[![license](https://img.shields.io/badge/license-private-8894a0)](#license)

[Korean / 한국어 →](README.ko.md)

</div>

---

## Why

Coding agents are good at *writing* code and bad at *knowing where it goes*. On a multi-repo
platform, "fix the login bug" means finding the right file across thousands, understanding who
depends on it, not breaking them, and following the team's branch and review rules.

MAKER puts a **code knowledge graph** in front of the agent and a **convergence loop** behind it.

```
you: "the ontology graph doesn't refresh after rebuild — fix it"

  ├─ classify intent            bug / feature / refactor / question
  ├─ locate in knowledge graph  → repo:path:line  (98% symbol accuracy)
  ├─ find dependents            who breaks if this changes
  ├─ pull latest, branch        naming + protected-branch guards
  ├─ agent implements           with graph context + real source excerpts
  ├─ verify                     syntax · tests · sandbox isolation
  ├─ judge                      quality gate, retries until it passes
  └─ prepare merge request      ← stops here, on purpose

you: review · merge · deploy
```

**MAKER never deploys.** It prepares a merge request and observes. Humans merge and release.

---

## Features

| | |
|---|---|
| 🕸 **Code knowledge graph** | AST-level nodes (files, classes, functions, endpoints, routes) with `contains` / `imports` / `calls` edges across repos. Incrementally kept fresh — no full rebuilds. |
| 🎯 **Grounded landing** | Natural-language query → the exact `repo:path:line` to change, plus the code that *depends* on it so the agent doesn't break callers. |
| 🔁 **Convergence loop** | Implement → sandbox + tests + regression → quality judge → feed failures back → retry until it passes or gives up honestly. |
| 🛡 **Safety by construction** | Protected branches untouchable, branch-naming enforced, infrastructure files vetoed, merge-request-only, one-command undo. |
| 📊 **Web dashboard** | Live streaming run log, drill-down graph viewer, session history with replay/undo, test records, visual regression, health metrics. |
| 🖥 **Three surfaces** | CLI, web dashboard, and MCP server — all driving the same engine. |

---

## Quick start

```bash
pip install -e .                                   # provides the `maker` command

cp .env.example .env                               # fill in your own tokens
cp maker.config.example.json maker.config.json     # map your repos

maker login                                        # detects your Claude CLI session
maker doctor --config maker.config.json            # verifies every capability for real
```

> **Nothing works without configuration — by design.** This repository ships placeholders only.
> Your hosts, tokens and repository paths live in `.env` and `maker.config.json`, both gitignored.

### Build the graph

```bash
maker kg build --repo "core=/path/to/core" --repo "web=/path/to/web::apps/web/src" --out kg
maker kg merge kg/*.repo.json --out kg/merged.json
maker kg enrich --kg kg/merged.json        # optional: semantic summaries
```

### Run a query

```bash
maker run "fix the login redirect bug" --config maker.config.json           # analyze only
maker run "fix the login redirect bug" --config maker.config.json --mode observe   # + branch & commit
maker run "fix the login redirect bug" --config maker.config.json --mode act       # + push & MR
```

### Or open the dashboard

```bash
maker web --config maker.config.json        # http://127.0.0.1:8760
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

## The dashboard

```bash
maker web --config maker.config.json
```

| Tab | What it gives you |
|---|---|
| **Run** | Live step-by-step stream, the code it landed on, stop button that actually kills the agent |
| **Pipeline** | All 25 stages, what ran, and which setting gates each one — editable in place |
| **Knowledge graph** | Repo-level map → drill into a repo → click a node for real source + AI summary. Annotate nodes; edits persist across rebuilds |
| **History** | Every session with its step timeline, resume, and one-click undo |
| **Tests** | Verification records per run — sandbox, checks, quality score with its basis |
| **Visual check** | Screenshot a page, save a baseline, pixel-diff later changes |
| **Health** | Graph freshness per repo, integrity, symbol accuracy — measured, not asserted |

The dashboard has **no authentication**. It refuses to bind to a non-loopback address unless you
explicitly opt in, because anyone who reaches the port acts with your stored credentials. Put it
behind an authenticating proxy before exposing it.

---

## Keeping the graph fresh

A stale graph sends the agent to the wrong file, so freshness is a first-class concern.

```bash
maker kg sync              # re-extract only what changed locally
maker kg refresh           # fetch remotes, fast-forward when safe, then sync
maker kg hook --install    # git hooks: refresh on commit / merge / checkout
```

`refresh` is deliberately conservative — it **fetches** (which never touches your working tree)
and fast-forwards only when the tree is clean, an upstream exists, and the branch has not
diverged. Otherwise it skips and tells you why. It never checks out, stashes, rebases, or forces.

Schedule it daily and the graph stays current without anyone thinking about it.

---

## Architecture

```
                    ┌──────────── surfaces ────────────┐
                    │   CLI      Web (SSE)      MCP    │
                    └────────────────┬─────────────────┘
                                     ▼
                            MakerLoop (pipeline)
                                     │
  ┌──────────────┬──────────────┬────┴─────┬──────────────┬──────────────┐
  ▼              ▼              ▼          ▼              ▼              ▼
intent      knowledge       git ops    coding agent   verification   merge request
classify      graph      branch/commit   (subprocess)  tests·sandbox     draft
              │                                          ·judge
              ▼
   build · sync · refresh · overlay · search · impact
```

- `xgen_maker/kg/` — graph build, incremental sync, safe refresh, search, human overlay edits
- `xgen_maker/loop/` — the pipeline: intent → landing → branch → implement → verify → judge → MR
- `xgen_maker/web.py` — dashboard (stdlib `http.server`, server-sent events, single-file UI)
- `xgen_maker/mcp_server.py` — expose the graph and planner to other agents
- `scripts/` — operational helpers (scheduled refresh, tunnel routing)

---

## Requirements

- **Python 3.12+**, `git`
- **Stdlib-first.** The core graph, loop, and dashboard use no third-party runtime dependencies.
  Optional extras unlock optional features: `Pillow` (pixel diff), Playwright via `npx`
  (screenshots), an LLM provider (semantic summaries, quality judging, query expansion).
- A coding agent CLI for the implement step — Claude CLI by default, or any command via
  `agent_cmd`.

---

## Testing

```bash
python -m pytest -q
```

304 tests covering the graph extractors, incremental sync, safety guards, the convergence loop
end-to-end over a real temporary repository, and the dashboard's endpoints. Regression tests are
written against bugs that actually occurred — including the ones this project introduced and
then fixed.

---

## Safety model

MAKER is designed to be *boring* in production:

- **Never deploys.** The pipeline stops at a merge request.
- **Protected branches** cannot be created, committed to, or pushed.
- **Infrastructure files** (Dockerfiles, CI descriptors, charts) are vetoed — source only.
- **Authorization is checked before writing**, not after.
- **Everything is journaled** per session and can be undone with one command.
- **Failures are reported honestly** — a skipped test says skipped, an unverified regression says
  unverified. Nothing green that isn't.

---

## License

Private. All rights reserved.
