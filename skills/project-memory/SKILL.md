---
name: project-memory
description: Use when resuming, planning, or completing work in this repository. Reads AGENTS.md plus .agents state, then keeps JSONL/project memory updated.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [project-memory, handoff, agents, repo-state]
    related_skills: []
---

# Project Memory

## Purpose

This project-local skill makes repo state portable across agents, models, IDEs, and machines.

## Required start sequence

1. Read `AGENTS.md`.
2. Read `.agents/project-memory.json`.
3. Tail `.agents/task-log.jsonl` for recent events.
4. Review `README.md` when setup, behavior, or deployment matters.
5. Log a `start` event before changing files.
6. Create or update a handoff capsule for active work: `.agents/handoff-capsules/<task_id>.json`.

## Required completion sequence

1. Run the repo's validation/tests or document why not.
2. Update `README.md` if user-facing behavior, setup, deployment, or operations changed.
3. Update `AGENTS.md` if project instructions changed.
4. Append a `complete` or `blocker` event to `.agents/task-log.jsonl`.
5. Update the handoff capsule with final status, next action, tests, artifacts, and resume instructions.
6. Keep reusable skill code/templates inside `skills/`.

## JSONL event shape

```json
{"ts":"2026-07-04T00:00:00Z","actor":"agent","event":"change","summary":"what changed","files":["path"],"next":["optional next action"]}
```

## Principles

- `AGENTS.md` is the single source of truth.
- `.agents/context-bullets.jsonl` provides compact structured context for session loading.
- `.agents/handoff-capsules/` provides resumability; capsules reference JSONL/project memory instead of duplicating full history.
- `.agents/job-ledger.jsonl` attributes every worker/model/tool result before Hermes accepts it.
- `CLAUDE.md` and other agent-specific files point back to `AGENTS.md`.
- Prefer IaC/config/scripts over clickops.
- Prefer local/free/self-hosted tooling; paid fallbacks require explicit approval.


## Structured context and handoff capsules

Before assigning work to any worker/model/IDE, create or update a handoff capsule in `.agents/handoff-capsules/<task_id>.json` and append a checkpoint to `.agents/task-log.jsonl`. Use `.agents/context-bullets.jsonl` for concise facts, constraints, preferences, risks and open questions that should load into future sessions.

Recommended helper:

```bash
~/.hermes/scripts/project_agent_state.py init --repo .
~/.hermes/scripts/project_agent_state.py add-context --repo . --project <name> --id <id> --type fact --text "..." --source "..."
~/.hermes/scripts/project_agent_state.py upsert-capsule --repo . --task-id <id> --project <name> --objective "..." --next-action "..."
~/.hermes/scripts/project_agent_state.py session-context --repo .
~/.hermes/scripts/project_agent_state.py validate --repo . --strict
```

Capsules are mutable current-state summaries. The JSONL task log remains the append-only source of truth for what happened.

## Job ledger attribution

Before accepting worker/model/CLI/IDE/browser/scheduler output, append a row to `.agents/job-ledger.jsonl`:

```bash
~/.hermes/scripts/project_agent_state.py add-job --repo . --job-id <id> --project <name> --task-type <type> --goal "..." --assigned-worker "..." --worker-type "model" --model-name "..." --model-provider "..." --interface-used Hermes --cost-tier paid --status accepted --selection-reason "..." --accepted-by "Hermes orchestrator"
```

For multiple models/agents on the same goal, create one parent job and a child job per worker. Hermes accepts/rejects/supersedes each child only after reconciling against tests, source files, JSONL, project memory and user instructions.


## Fan-out execution

Use `system/fanout-execution.md` plus `~/.hermes/scripts/project_agent_fanout.py` for competing workstreams. Create a parent fan-out job, prepare one branch/worktree per worker, write worker briefs under `.agents/fanout/<task_id>/workers/`, and record reconciliation before updating project memory or merging.

```bash
~/.hermes/scripts/project_agent_fanout.py start --repo . --task-id <task> --project <project> --goal "..."
~/.hermes/scripts/project_agent_fanout.py add-worker --repo . --task-id <task> --worker-name <worker> --worker-type "coding agent" --interface-used Hermes --cost-tier paid --goal "..." --brief "..." --selection-reason "..."
~/.hermes/scripts/project_agent_fanout.py reconcile --repo . --record-file .agents/fanout/<task>/reconciliation.json
~/.hermes/scripts/project_agent_fanout.py validate --repo . --strict
```


## Cross-project priority queue and cost review

Use `.agents/priority-queue.jsonl` plus `~/.hermes/scripts/project_agent_priority.py` to rank tasks across projects by value, urgency, blockers, effort, risk and cost. The latest JSONL row per `task_id` is the current queue state.

```bash
~/.hermes/scripts/project_agent_priority.py upsert-task --repo . --task-id <id> --title "..." --status ready --expected-value "..." --cost-budget "local/free first" --next-action "..."
~/.hermes/scripts/project_agent_priority.py review --root /home/ubuntu --write-report --format text
~/.hermes/scripts/project_agent_priority.py validate --repo . --strict
```

Cost rule: use deterministic scripts/tests/search/static analysis before model calls where possible; use free/local models for low-risk drafts/exploration; reserve paid/current best model for architecture, important tradeoffs, high-risk work and final review.