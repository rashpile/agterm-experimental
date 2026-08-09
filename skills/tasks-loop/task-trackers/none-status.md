# Status — none

There is no backlog to fall back on: everything comes from this run's own state.

1. **Slots** — one line per in-flight task:
   `<slug> — sessions: author(busy) reviewer(waiting) — round 2/3 — blocked on: <what>`
2. **Queue** — tasks the user has given that are waiting for a free slot.
3. **Waiting on you** — human checkpoints, live discussions, review rounds exhausted (3/3).
4. **Nothing running, nothing queued** → say so and ask for the next task. There is no list to
   print instead.

Never type into a worker session and never advance a phase while answering status. A mismatch
(run state says busy, the driver shows no session) is reported, not fixed silently.
