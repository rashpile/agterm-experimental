# Status — markdown

How the orchestrator answers `--status` (or a bare "status" mid-run). It answers from its
**own run state** — slots, queue, what waits on the user — and does not rebuild the board
from scratch.

## Report order

1. **Slots** — one line per in-flight task:
   `<id> <phase> — sessions: author(busy) reviewer(waiting) — round 2/3 — blocked on: <what>`
2. **Queue** — tasks selected for dispatch, waiting for a free slot.
3. **Waiting on you** — tasks parked ready-to-ship, live discussions, findings awaiting a
   go-ahead, review rounds exhausted (3/3).
4. **Fallback** — no slots, empty queue, nothing waiting → print the backlog instead:
   `ls tasks/*/*.md`, formatted `<phase>  <id> — <title>` (title = the file's `#` heading).

## Rules

- Verify one possibly-stale entry with the driver's `status`/`list` or by reading the task
  file — never rebuild the whole board mid-run.
- Never type into a worker session and never move a task file while answering status.
- A mismatch (`owner:` set with no live session) is **reported** to the user, not fixed silently.
