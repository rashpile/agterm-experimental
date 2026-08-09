# Status — Gitea

How the orchestrator answers `--status` (or a bare "status" from the user mid-run). It
answers from its **own run state** — it already knows its slots, its queue, and what waits on
the user. It does not rebuild the picture from scratch; that is what the coordinator-only
rule buys.

## Report order

1. **Slots** — one line per in-flight task:
   `#<idx> <phase> — sessions: author(busy) reviewer(waiting) — round 2/3 — blocked on: <what>`
2. **Queue** — tasks selected for dispatch, waiting for a free slot.
3. **Waiting on you** — ready-to-ship tasks (`done`+`pending` — "drop `pending` to deploy, or
   say go"), live discussions, findings awaiting a go-ahead, review rounds exhausted (3/3).
4. **Fallback** — no slots, empty queue, nothing waiting → print the backlog instead:
   `tea issues list --output json` from inside the repo, formatted `#idx  labels — title`.

```
SLOTS (2/2)
- #12 implement→review: PR #18 — reviewer busy (round 2/3), author idle
- #16 discuss: session live — waiting on your 3 decisions

QUEUE
- #17 review (next free slot)

WAITING ON YOU
- #12 ready to ship (done+pending) — drop pending or say go
```

## Rules

- Verify one possibly-stale entry with the driver's `status`/`list` or with `tea` — never
  rebuild the whole board mid-run.
- Never type into a worker session and never edit a label while answering status.
- A mismatch (`progress` with no live session) is **reported** to the user, not fixed silently.
