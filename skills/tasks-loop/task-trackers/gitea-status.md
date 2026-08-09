---
name: tasks-status
description: Use when the user asks the running tasks-loop orchestrator for status — "status", "tasks status", "what's in the slots", "what's waiting for me", "how are the agents doing". Answered by the orchestrator from its own run state.
---

# Tasks Status

## Overview

Status command for the **tasks-loop orchestrator**. The orchestrator answers from its own
run state — it already knows its slots, its queue, and what waits on the user. It does NOT
rebuild the picture from scratch; that's why the coordinator-only principle matters: idle
between wakeups, it can always answer immediately.

## Report order

1. **Current slots** — each in-flight issue:
   `#<idx> <type> — sessions: author(busy) reviewer(waiting) codex(busy) — round 2/3 — blocked on: <what>`
2. **Orchestrator queue** — issues selected for dispatch, waiting for a free slot.
3. **Waiting on user** — pending decisions: **ready-to-ship issues (`done`+`pending` —
   "remove pending to deploy, or say go")**, live discussions (incl. /rc remote ones),
   blocking findings awaiting a fix go-ahead, review rounds exhausted (3/3).
4. **Usage line** — append the meter reading if the script exists:
   `~/.local/bin/claude-usage.sh --current` → e.g. `usage: session 13% (resets 12:10pm) · week 20%`.
5. **Fallback** — no active slots AND empty queue AND nothing waiting on the user →
   just print the Gitea issues overview:
   `tea issues list --output json` (run inside the repo), formatted `#idx  labels — title`.

Example (live run):

```
SLOTS (2/2)
- #12 implement→review: PR #18 — reviewer busy (round 2/3), author idle, codex done
- #16 discuss: session live on /rc — waiting on your 3 decisions

QUEUE
- #17 review (next free slot)

WAITING ON YOU
- #12 ready to ship (done+pending) — remove pending or say go

usage: session 13% (resets 12:10pm) · week 20%
```

## Rules

- Verify a possibly-stale entry with `agent-ctl.sh status <id>` or `tea` — but never
  rebuild the whole picture from scratch mid-run.
- Never type into worker sessions and never edit labels while answering status.
- Label/session mismatches (e.g. `progress` but no live session) are reported to the
  user, not fixed silently.
