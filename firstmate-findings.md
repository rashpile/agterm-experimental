# firstmate — investigation, and what to borrow for our agterm agent flow

Investigation of [kunchenguid/firstmate](https://github.com/kunchenguid/firstmate) at commit `99533c5`
(shallow clone, 50 commits, 2026-07-22 → 2026-07-28), run 2026-07-29 by three parallel agterm worker
agents: one on capabilities, one on internals, one comparing it against
`skills/orchestrating-agterm-agents`.

**Provenance / limits.** Nothing was executed from the firstmate repo, and no agterm commands were run
to verify the comparison — all claims come from reading source, comments, docs, and `git log`. Line
references into *our* scripts (`agent-ctl.sh:47/121/174`, `agent-wait.sh:77`) were checked directly
against the working tree. The proposed diffs in §3 are **proposals, not applied changes**.

---

## The one idea to take

> **Every operation returns a _verdict_, and only a specific positive verdict licenses an action.
> Everything else falls back to a slower safe path — loudly.**

Every good part of firstmate is an instance of this: `empty|pending|unknown` before injecting text;
`alive|dead|missing|ambiguous|unreadable|unverified` before recovering (*"only `dead` and `missing`
license recovery"*, `fm-backend.sh:872-886`); `actionable|absorb|defer|fallback` before waking the LLM;
a send that exits non-zero when it can prove it failed. And under each one, a **permanent fail-closed
backstop**, so an unusable fast path degrades to the old slow path instead of going silent.

Ours does the opposite in the two places it matters most. `agent-ctl.sh:174` and `agent-wait.sh:77`
both resolve *uncertainty* to `idle` — our most consequential state, the one that means "task done,
collect the result." So **a crashed worker is reported as a completed task.** And `say` prints
`said + submitted` unconditionally: it cannot fail. We infer, then act on the inference as if it were
fact.

---

## 1 · What firstmate can do

### It's an "agent distro", not an app

You clone the repo and launch a coding agent *inside* it. Nothing to install, no binary, no daemon, no
MCP server. The product is a 502-line `AGENTS.md` (the always-loaded contract), 18 lazily-loaded
skills, and ~90 bash scripts. Launching Claude / Codex / Pi / Grok / OpenCode in that directory makes
it **the first mate**; you become **the captain**.

You then talk to exactly one agent. It never touches your projects itself — it clones repos, spawns
worker agents into visible terminal windows each in its own disposable git worktree, briefs them,
supervises them, and hands back PRs or reports.

### The vocabulary (load-bearing — nearly every file assumes it)

| Term | Meaning |
|---|---|
| **captain** | You. Holds merge authority and all destructive / irreversible / security decisions. |
| **firstmate** | The one agent you talk to. A *router and supervisor*, not a coder — hard rule: never writes under `projects/`. |
| **crewmate** | A spawned worker in its own worktree + visible window. Crewmates **never address the captain**; output flows back through firstmate. |
| **secondmate** | A *persistent* peer firstmate in an isolated `FM_HOME`, scoped by a natural-language line, idle by default. |
| **ship / scout** | Two task shapes. *ship* delivers via the project's delivery mode; *scout* produces only `data/<id>/report.md` and never pushes. |
| **brief** | The generated task contract handed to a worker at spawn, including a mandatory worktree-isolation assertion. |
| **wake** | A typed event that interrupts firstmate: `signal:` `stale:` `check:` `heartbeat:` |
| **watcher** | `fm-watch.sh` — an always-on bash loop that classifies wakes at **zero LLM cost** and queues only actionable ones. |
| **decision hold** | A durable, keyed backlog item for an unresolved question belonging to the captain. An investigation cannot complete until every one it found is registered. |
| **teardown** | Cleanup of a finished task. **Fail-closed** — refuses if the worktree holds work that has not landed. |

### Capability inventory

**Delegation**
- **Ship a change** — spawns a worker in a fresh worktree, supervises to a PR.
- **Investigate (scout)** — deliverable is a report; never pushes.
- **Promote scout → ship** — flips the task *in place*: same window, same worker, same loaded context.
- **Steer a running worker** — one verified line; exits non-zero if the Enter was swallowed.
- **Parallel by default** — same-file overlap alone is *not* a reason to serialize.

**Supervision engine**
- **Zero-token supervision** — bash classifies and absorbs benign wakes; no LLM turn for routine polling.
- **Durable wake queue** — written *before* detector state advances, so a missed process exit is recoverable.
- **No turn ends blind** — a Stop hook blocks the agent finishing while work is in flight with no live watcher.
- **Anti-footgun guards** — deny watcher-arm anti-patterns and the agent's own native delegation tools.
- **Restart recovery** — all state on disk; a restart is designed to be a non-event.

**Five slash commands**
- `/ahoy` — session recap. Deliberately runs **no** tools or file reads.
- `/bearings` — status report + a strict 4-section digest: Captain's Call / Recently Landed / Underway / Charted Next.
- `/afk` — away mode; a daemon batches escalations.
- `/stow` — sweep the conversation and route each learning to its correct file.
- `/updatefirstmate` — fast-forward-only self-update.

**Delivery and landing**
- Three delivery modes per project: validation pipeline → PR, direct-PR, or local-only.
- `+yolo` autonomy flag — routine gates decided by the agent, never destructive ones.
- Merge validates the full PR URL; refuses malformed ones, and refuses GitLab MR URLs rather than
  sending them to the wrong forge.
- Safe cleanup refuses teardown for dirty or committed-but-unlanded work; recognises
  squash-merge-then-delete as landed.

### The workflow it's really built around: you walk away, the fleet keeps working

1. You type `/afk`.
2. A daemon takes over triage — in bash, no LLM turn.
3. Escalations are batched into one digest typed into your own pane, prefixed with U+2063 so firstmate
   can structurally tell a machine injection from a human message.
4. Undeliverable past `FM_MAX_DEFER_SECS` (300 s) → a loud wedge alarm. The daemon is explicitly not
   allowed to silently wedge.
5. Any unmarked message means you're back → away mode exits and a catch-up gate runs.

Away mode **does not change approval authority**. A PR ready to merge still waits for you.

### Maturity

**Real signals:** 105 behavior tests / 61k lines against 37k of implementation (1.64:1); five CI jobs
including a *test-coverage partition guard* proving the shards equal the full inventory;
`docs/verification/` records empirical evidence per guarantee including counterexamples; it dogfoods
itself; a machine-checked documentation-audience boundary.

**Named limits — no overclaiming found:**
- Only tmux is **verified**. herdr / zellij / orca / cmux are all **experimental** with stated limits.
- `codex-app` backend is **explicitly blocked**, with the exact missing component and acceptance
  contract stated.
- Project *removal* is a documented unimplemented gap — the skill tells the agent to report the gap
  rather than delete anything.
- **Heavy dependency footprint:** `no-mistakes`, `treehouse`, and five bespoke `*-axi` CLIs from the
  same author. The single biggest adoption cost.

---

## 2 · How it's technically implemented

No runtime, no message bus, no orchestrator process holding state in memory. Files are the only
durable state; the LLM is treated as an expensive resource to be woken as rarely as possible.

```
CAPTAIN ⇄ PRIMARY AGENT SESSION  (an ordinary CLI; its program is AGENTS.md)
            │ shells out                    ▲ hooks fire back in
            ▼                               │
LAYER A   orchestration scripts — bin/fm-*.sh, ~90 files
            │                     spawn · send · peek · teardown · promote
            ▼
LAYER C   backend abstraction — fm-backend.sh (980 l), per-op case dispatch
            ├── tmux.sh    211 l  · REFERENCE
            ├── herdr.sh  2550 l  · native push events
            └── zellij / orca / cmux · experimental
            ▼
LAYER D   CREW — one agent CLI per pane, own disposable git worktree
            │ appends status lines / turn-end hook touches a marker
            ▼
LAYER E   STATE — plain files under $FM_HOME
            │     <id>.meta · <id>.status · <id>.turn-ended · .wake-queue
            ▼
LAYER B   SUPERVISION — fm-watch.sh, zero-token poll loop
            │            classifies + absorbs in bash
            └──▶ actionable wakes only ──▶ back to the PRIMARY AGENT
```

### The cleanest architectural statement in the repo

> A backend's supervision surface is conceptually an **EVENT SOURCE** — it produces task events
> (status-changed, went-stale, exited)… The tmux adapter has no native event push, so `fm-watch.sh`'s
> poll loop over the pull primitives **IS** the default event-source implementation that synthesizes
> those events.
>
> — `bin/fm-backend.sh:39-47`

Polling is framed as a *fallback implementation* of an event source. That's what lets one watcher
serve both a scraped backend and a natively-pushing one without forking.

### How it knows a worker is done or blocked — four signals, in priority order

**(a) Turn-end hooks — the push signal.** At spawn it writes a harness-specific hook whose only job is
`touch state/<id>.turn-ended`: a `Stop` hook for Claude, `session.idle` for OpenCode, `notify=` for
Codex. Grok/Kimi get a *global* hook that is an inert no-op unless the cwd holds a firstmate-owned
token pointer resolving through a private registry — because firstmate refuses to write into grok's
managed trust store. Every hook file is added to `.git/info/exclude` so it can't dirty the worktree.

**(b) Status-file mtime scan — the semantic signal.** Compared against a *persisted* `size:mtime`
signature, not a startup timestamp, so signals landing while no watcher runs are still caught. The
`.seen-*` marker advances only **after** a wake is surfaced or intentionally absorbed, so a watcher
killed mid-cycle never swallows a signal.

**(c) Screen scraping — the fallback liveness signal.** Hash the last 40 lines; two consecutive
identical hashes with no busy footer = stale. "Busy" is a **per-harness** regex over the TUI footer,
deliberately scoped so an unrecognised harness matches *no* regex rather than borrowing another's —
Kimi's idle tip contains `ctrl+c: cancel`, which would otherwise read as grok-busy.

**(d) Native push events — herdr only.** `herdr-eventwait.py` (157 lines) opens the control socket and
subscribes to `pane.agent_status_changed`. The watcher replaces its terminal `sleep POLL` with a
bounded wait on it; non-zero exit from the reader → fall back to polling for that cycle.

**And a reconciler over all four.** `fm-crew-state.sh` is explicit that the status log is *not* current
state: crews append only wake-worthy transitions and nothing when they silently resume, so `tail -1`
reports the last *event*, not the current *state*.

### Sending input — where they're most careful

##### The insight: **typing text is not the same as submitting it**, and a silently swallowed Enter is worse

than a loud failure.

```bash
# bin/fm-tmux-lib.sh:389-416
fm_tmux_submit_enter_core() {  # <target> <retries> <enter-sleep>
  local target=$1 retries=$2 sleep_s=$3 i=0 state
  while :; do
    tmux send-keys -t "$target" Enter 2>/dev/null || true
    sleep "$sleep_s"
    state=$(fm_tmux_composer_state "$target")
    case "$state" in
      pending|pending-unproven) ;;
      *) printf '%s' "$state"; return 0 ;;
    esac
    i=$((i + 1))
    [ "$i" -lt "$retries" ] || break
  done
  # Text is typed ONCE by the caller; only Enter is retried here.
  if fm_pane_is_busy "$target"; then printf 'empty'; else printf 'pending'; fi
}
```

- **Text is typed ONCE**; only Enter is retried — retyping a swallowed line would duplicate it.
- Delivery is confirmed **iff** the composer reads exactly `empty`; otherwise `fm-send` exits non-zero.
- Slash commands get a longer 1.2 s pre-Enter settle so completion popups don't eat Enter.

The incident that paid for it: their old check recognised only a bare `>` glyph as an empty composer.
Claude draws its box with box-drawing borders, so *every idle pane* read as "pending input" and away
mode deferred **100% of escalations for 9.5 hours** with no escape.

### Liveness is six-valued

`alive` · `dead` · `missing` · `ambiguous` · `unreadable` · `unverified`

with the stated rule: **only `dead` and `missing` license recovery** (`fm-backend.sh:872-886`). Ours
has no liveness concept at all.

### The guard system

A mistake-prevention layer, not a sandbox — the stated threat model is *agent error*, not adversarial
bypass.

| Guard | Hook point | Denies |
|---|---|---|
| watcher-arm seatbelt | PreToolUse (Bash) | arming the watcher in a way that won't survive — shell `&`, truncating pipes, broad `pkill -f fm-watch` |
| cd-guard | PreToolUse (Bash) | a persistent top-level `cd` in the primary shell |
| delegation guard | PreToolUse (any) | delegation-*shaped* tool names — the agent's own subagent/task/worktree tools |
| turn-end guard | Stop / session.idle | ending a turn blind while work is in flight and no watcher is live |

**Uniform exit contract:** allow = exit 0 silent; deny = exit 2 + shaped JSON; anything malformed =
**fail open**. Rationale stated up front — a guard that breaks the agent is worse than a guard that
occasionally doesn't fire.

The delegation guard is the most interesting conceptually. It denies the primary agent its *own
harness's* subagent tools, because work created that way writes no `state/<id>.meta` — and every other
guard counts in-flight work from those files, so the whole stack would go inert. Their recorded
incident: 73 minutes of dead supervision plus two lost workers. *"The failure is precisely the absence
of that metadata."*

### The backend interface contract (the reusable part)

A backend is one sourced bash file defining `fm_backend_<name>_*` functions, sourced lazily. ~15
mandatory + 12 optional functions. tmux does it in 211 lines.

| Operation | Contract | Implemented by |
|---|---|---|
| `capture` | bounded **plain-text** screen tail; non-zero on unreadable | all 5 |
| `send_key` | one named special key (`Enter`/`Escape`/`C-c`) | all 5 |
| `send_text_submit` | type once, submit + verify, retry **submission only**; echo a proof-carrying verdict | all 5 |
| `kill` | remove endpoint, best-effort | all 5 |
| `busy_state` | → `busy\|idle\|unknown`, native semantic state | **herdr only** |
| `composer_state` | → `empty\|pending\|pending-unproven\|unknown`, pre-submit guard | tmux, herdr, orca, cmux |
| `agent_state` | → 6-valued recovery-grade verdict | **tmux, herdr only** |
| `target_exists` | cheap read-only presence probe that must **not** start a server | all 5 |
| `wait_transition` | **0** = fresh actionable edge · **1** = clean timeout · **2** = unusable, caller sleeps the budget itself | herdr only |

That tri-state `wait_transition` return is the load-bearing detail: it makes the polling fallback
*provably* non-lossy.

---

## 3 · Code we can borrow

### Side-by-side, concern by concern

| Concern | Ours | Theirs | Verdict |
|---|---|---|---|
| spawn | one call, idempotent workspace, `--no-select` | launch template + worktree + brief + meta record | we're fine |
| ready-detect | poll for Claude footer strings | verified per-harness readiness table | they're better |
| **done-detect** | screen-quiet, 4.5 s floor, `idle` fallthrough | turn-end **hooks** + herdr native push | **they're better** |
| blocked-detect | prompt regex, wins over busy-guard | event-sourced; policy is a table, not a regex | they're better |
| **input injection** | blind 200-backspace clear + type + blind Enter | type once, retry Enter only, non-zero on proven swallow | **they're better, decisively** |
| approval | Telegram ✅/♾️/🚫 one-tap, adaptive menu | workers run `--dangerously-skip-permissions`; no prompts exist | **we're better** |
| decision durability | none — `wask.sh` fires and forgets | keyed durable holds, idempotent on retry | they solve a gap |
| notification | Telegram, off-machine, chunked, dedup-marked | in-pane batched digest + durable buffer + wedge alarm | **we're better** |
| **recovery** | **none** | 5-rung ladder: peek → answer → interrupt → relaunch → report failed | **they solve a gap** |
| **liveness** | none; `idle` fallthrough | 6-valued verdict; only 2 values license recovery | **they solve a gap** |
| state | 4 config files, no per-worker record | `<id>.meta` + event log + reconciler | they're better |
| multi-backend | agterm only | 5 adapters + runtime auto-detect | don't want it |
| multi-harness | Claude assumed everywhere | 7 verified adapters | they're better |
| scheduling / quota | none | quota-aware model routing | don't need it |
| shutdown | graceful drain + `force` escape hatch | teardown + worktree return | we're fine |
| prompt-avoidance | allow-listed script surface, STANDALONE rule | not a problem they have | **genuinely ours** |

### B1 — Kill the ghost prompt-suggestion at the source
**effort: 1 line · value: very high**

Theirs: every Claude worker launches with `CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION=false`
(`fm-spawn.sh:429`). Their recorded finding: the `--prompt-suggestions` flag is print/SDK-mode only and
does *not* suppress the interactive composer ghost — the env var does.

This deletes our documented failure class outright ("Enter does nothing / text won't submit… the box is
actually EMPTY… `session text` can't tell ghost from real"), and it is a **hard prerequisite for
B3/B4**: on plain-text capture you cannot distinguish ghost from typed text, so the only sound fix is
to stop the harness emitting it.

```diff
  # skills/orchestrating-agterm-agents/scripts/agent-ctl.sh
   cwd="${4:?spawn: missing <cwd>}"
   shift 4
-  run="${*:-zsh -ic claude}"
+  # CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION=false: the composer's greyed "ghost" suggestion is
+  # indistinguishable from real typed text in agterm's PLAIN-TEXT session read, so `submit` can
+  # fire \r at an empty box and silently do nothing. The --prompt-suggestions flag is print/SDK
+  # mode only; the env var is what suppresses the interactive ghost. Scoped to workers we spawn.
+  run="${*:-zsh -ic 'CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION=false claude'}"
```

**Risk:** undocumented env var, could change between Claude versions. Scoped to workers we spawn, so it
never touches your own sessions, and it fails open — the ghost comes back and we're where we are today.

### B2 — A liveness verdict; `idle` stops being the fallthrough
**effort: S–M · value: very high**

This fixes **the worst behaviour we have**: a worker that crashed to a bare `zsh` prompt produces a
static screen with no busy / idle / prompt marker → `agent-wait.sh` returns `STATE=idle` → the
orchestrator reports the task **done**. We cannot currently distinguish *finished* from *dead*.

The inputs already exist in agterm and we already read them elsewhere: session absent from
`tree --json` → `missing`; `foreground` vs the session's `initialCommand` → harness gone = `dead`;
anything else = `ambiguous`.

```diff
  # skills/orchestrating-agterm-agents/scripts/agent-wait.sh
  final="${cur:-$start}"
-if printf '%s' "$final" | grep -qE "$wait_re"; then state=waiting; else state=idle; fi
+# A quiet screen is NOT proof of "finished" — a worker that crashed to a bare shell is also quiet.
+# Ask for a liveness verdict before defaulting to idle, so a dead worker is never reported as a
+# completed task. Only dead/missing are asserted; anything unattributable stays `ambiguous` and is
+# never acted on automatically (firstmate's rule: only dead and missing license recovery).
+if printf '%s' "$final" | grep -qE "$wait_re"; then
+  state=waiting
+else
+  case "$("$(dirname "$0")/agent-ctl.sh" alive "$id" 2>/dev/null)" in
+    missing)   state=missing ;;
+    dead)      state=dead ;;
+    ambiguous) state=ambiguous ;;
+    *)         state=idle ;;
+  esac
+fi
```

**Risk:** agterm's `foreground` is a command name — weaker attribution than tmux's process-ancestry
walk. Keep `ambiguous` as a real outcome and never auto-act on it. That is exactly their discipline.

### B4 — Verified submit: prove the postcondition
**effort: M · value: high**

`say` is our single most-used mutating verb and it **currently cannot fail**. Every "the worker never
got the task" mystery becomes a non-zero exit with a reason.

```diff
  # skills/orchestrating-agterm-agents/scripts/agent-ctl.sh
     if [ "$cmd" = say ]; then
+      # Type ONCE (above), then retry ENTER ONLY until the composer proves it drained — never
+      # retype, which would duplicate the line. Reporting success unconditionally is how a
+      # swallowed Enter used to become an unexplained "the worker never got the task".
       sleep 1
-      printf '\r' | "$AGT" session type --stdin --target "$id" >/dev/null
+      verdict=pending
+      for _ in 1 2 3 4 5; do
+        printf '\r' | "$AGT" session type --stdin --target "$id" >/dev/null
+        sleep 0.6
+        verdict="$("$(dirname "$0")/agent-ctl.sh" composer "$id" 2>/dev/null || echo unknown)"
+        [ "$verdict" = pending ] || break
+      done
       "$(dirname "$0")/agterm-clear-status.sh" "$id" 2>/dev/null || true
-      echo "said + submitted -> $id: $text"
+      if [ "$verdict" = empty ]; then
+        echo "said + submitted -> $id: $text"
+      else
+        echo "say: delivery UNCONFIRMED to $id (composer=$verdict) -> $text" >&2
+        exit 1
+      fi
     else
```

**Risk:** depends on B3 for the composer verdict, and on B1 to make B3 sound.

### B3 — Composer-state guard: never blind-clear
**effort: S–M · value: high**

Our own SKILL.md says *"Clearing input eats queued text — `session text` FIRST, don't blind-clear."*
Then `agent-ctl.sh:121` fires 200 backspaces, and `inject.sh` routes *every* inbound Telegram `/o`
through it. It gates on `status == idle`, but **idle ≠ empty composer**.

Their `fm-composer-lib.sh` returns `empty|pending|unknown`, with the safety rule that a bare shell
glyph is only "empty" *inside a bordered composer box* — bare, it's a dead shell and is never
injectable. Their `orca.sh` adapter is the **plain-text** implementation, which proves the approach
works without ANSI — exactly our constraint, since agterm's `session text` returns plain text.

**Risk:** a mis-classified `unknown` makes `/o` feel dead. Mitigate the way their daemon does — bounded
defer, then a loud surfaced stall (`notify.sh --level attention`), never silence.

### B6 — Event-driven waiting: this one overturns our own conclusion
**effort: M–L · value: high**

Our SKILL.md concluded:

> *"Skip the `--run`/`--after` cursor. It removes the race in principle, but the anchor lives in the raw
> `events.read` envelope which the streaming CLI never prints — you would be talking to the control
> socket directly."*

So we shipped a measured **0.3 s settle delay** (missed 0 of 10, versus 9 of 10 with no delay) and
shelved the design.

**firstmate's answer: yes — talk to the socket directly.** That's all `herdr-eventwait.py` is, and it's
**157 lines**. The thing we treated as a blocker is their whole implementation.

Three ideas we don't have:

1. **A deterministic anchor, not a sleep.** Block on the server's `subscription_started` ack before
   doing anything else. The race *disappears* rather than becoming 90% unlikely.
2. **Level reconcile on connect.** After subscribing, read each pane's *current* status once — so a
   worker that went `blocked` before we subscribed is never missed.
3. **Tri-state return.** `0` = actionable edge, `1` = clean timeout, `2` = unusable → caller sleeps the
   budget itself. That makes the polling fallback *provably* non-lossy.

**Verify before building.** The claim that agterm's no-cursor `events.read` returns `{"run":…,"next":N}`
with an empty batch anchored at the current tail comes from a worker reading agterm's docs — *nothing
was run*. Confirm that envelope behaviour first. And keep `agent-wait.sh` as the permanent backstop.

### B7 — Stuck-worker recovery ladder (+ a brief on disk to relaunch with)
**effort: M**

Their five rungs: peek → answer a brief-answerable question → interrupt + one corrective line → exit
and relaunch *with the same brief plus a progress note* → report failed with work preserved. Two
guardrails worth stealing verbatim: *"a low context reading is not wedging"*, and *"prove that no live
agent still owns the recorded task"*.

Our blocker isn't the ladder — it's that we have **no brief**. `spawn` takes a command and the task
arrives later as keystrokes, so there is nothing to relaunch *with*.

**Biggest semantic mismatch in the whole report:** firstmate relaunches into a worktree *it owns*; we
spawn into the user's **live checkout**. A relaunch that races a still-live agent means two Claudes
editing one tree. Their "prove no live agent owns it" step becomes *mandatory* for us — gate on B2
returning `dead`/`missing`, never `ambiguous`.

### B8 — Durable decision hold
**effort: S–M**

`wask.sh` posts Telegram buttons and keeps nothing. Today a context compaction or an orchestrator
restart **silently drops an outstanding question** and nobody knows a worker is still parked.

Cheap fix that composes with our existing button protocol: a `pending/<worker-id>-<key>` file written
by `wask.sh`, removed by `wapprove.sh` (which already knows the id and the choice); `oclose.sh`'s
graceful path lists what's still open.

**Risk:** adds durable state to a deliberately near-stateless skill. Keep it to one flat directory with
self-describing filenames; no schema.

### B5 — per-harness adapter table
Real but lower-leverage. Our harness strings are scattered across `agent-ctl.sh:91`, `:165-167`, and
`agent-wait.sh:52-55`. Hoisting them into one sourced table keyed by a `--harness` flag mostly unblocks
"spawn a Codex worker" without editing three regexes in two files. Codex is already half-supported
(`agent-wait.sh:55` matches `›`).

---

## 4 · Borrowing the workflow, not the code

This is the richer surface. firstmate's real product is its **contract layer** — a 502-line `AGENTS.md`
that is versioned, dispatch-tabled, and *tested* (`tests/fm-captain-translation-contract.test.sh`
asserts the chat-vocabulary rules). Our `SKILL.md` is the same kind of artifact. Everything below is a
prose rule costing no new script.

### W1 — Delegation-first as a hard rule

> *"You do not do project-specific work yourself. **Delegate** coding, investigation, planning, bug
> reproduction, and audits to a crewmate you spawn and supervise."*
>
> Hard rule 1: *"Never write to a project. … firstmate reads projects and crewmates change them."*
>
> — AGENTS.md §1

**The scoping nuance that matters.** firstmate is *not* "always delegate everything." The rule is
narrow — never do **project** work yourself — and §7 immediately counterweights it:

> *"For one-off or infrequent operational work, start with the simplest direct end-to-end path. Do not
> build wrappers, control planes, policy layers, custom verifiers, or automation unless the direct path
> exposes a concrete blocker or repeated need that justifies the added machinery."*

So a blanket "do all work with agents" would overshoot what they do. The borrowable rule is
**delegate the work that touches the subject repo; do operational work directly.**

**Where we already half-have it.** Our SKILL.md already says *"Push any prompt-prone work — codebase
investigation, builds, edits, git — into a worker session you spawn and drive"* — but **only inside
Telegram mode**, and for a narrow reason (protect the orchestrator from a permission dialog it can't
answer remotely). Generalising that from *when remote* to *by default* is the borrow.

| What it buys us | What it costs us — and they don't pay it |
|---|---|
| Orchestrator context stays small and supervisory — never fills with file dumps | Every worker tool call is an **approval round-trip**. This investigation cost ~**20 approvals** across three read-only workers |
| Parallelism becomes the default shape, not a special occasion | They avoid it entirely: workers run `--dangerously-skip-permissions` in **disposable worktrees**. We spawn into the **live checkout** |
| Blast radius per mistake is one worker, not the session holding all state | So W1 is only comfortable **paired with** `spawn --autonomous` for read-only scouts — otherwise the orchestrator is a full-time approval clerk |
| Makes B7 (brief on disk) and W4 (status protocol) worth having rather than ceremony | |

**It fits the existing "no Agent tool unasked" rule rather than fighting it.** firstmate enforces the
same preference structurally — its delegation guard **denies the harness's native subagent/task tools**
and forces work through real terminal sessions, because in-process delegation leaves no durable record.
So the rule to write is *"prefer an agterm worker session over an in-process subagent"* — the same
instruction, stated as a positive.

### W2 — Type the deliverable: *ship* vs *scout*

Theirs: *ship* changes the project; *scout* produces knowledge in a report file, **never a PR, never a
push**. Their sharpest lines: *"A diagnostic request, report, recommendation, or implementation-ready
finding is **evidence, not authorization to change code**."* And *"Never both present a likely-enough
solution and launch a parallel design exercise that is not expected to change it."*

For us: today `spawn` has no notion of what a worker may produce. A `--scout` shape would mean a
read-only brief, a report path, and a natural pairing with `--autonomous` — a read-only worker is
exactly the one that's safe to run unattended. That combination is what makes W1 affordable.

We improvised precisely this during this investigation — three briefs, three report files, no writes
outside them — but none of it is in the skill, so the next run reinvents it.

### W3 — The brief is a file and a contract, not keystrokes

§7: *"Write the task-specific brief … **before** spawning."* §8: *"Steer a worker with short
single-line messages … **put long instructions in a file**."* §11: *"The scaffold is a safety contract,
not a suggestion."*

This is the prose half of B7. Writing the brief to disk and having the worker read it beats typing a
400-character instruction through a terminal — it dodges quoting hazards, survives a relaunch, and is
reviewable before it fires.

### W4 — A worker→orchestrator status channel that isn't the screen

Their generated brief instructs every worker: *"States: `working, needs-decision, blocked, paused,
done, failed`. Each append wakes firstmate, so **report sparingly** — only phase changes a supervisor
would act on. No step-by-step FYI progress lines; firstmate reads your pane for that."*

For us: we have *no* semantic channel at all — every conclusion about a worker is inferred from pixels.
A one-line-append status file per worker, requested in the brief, would give us `needs-decision` and
`blocked` as *facts* rather than regex guesses. It pairs with B2: the status file says *what*, the
liveness verdict says *whether it's still alive*.

Copy their paired discipline verbatim: **a status line is a wake event, not current state** — never
re-escalate an old decision off a stale log line.

### W5 — Escalation etiquette: a vocabulary translation table for Telegram

§9: *"Talk in outcomes, not mechanics"*, plus an explicit ban-list and rewrite table. It even bans
compressed *safety* jargon:

| Internal | What the human gets |
|---|---|
| worktree / checkout | local copy, isolated copy |
| teardown | cleanup |
| wake / watcher / stale | notification, monitoring, stopped responding |
| brief | instructions |
| crewmate | worker — only when naming the helper matters |
| fail-closed | stops safely when something goes wrong |

For us: we have exactly this audience — a human reading on a phone. Today `notify.sh` ships whatever
internal prose the orchestrator wrote, and `tg-agterm.sh`'s Stop hook forwards the raw turn text. Their
rule *"never relay worker reports, status lines, or tool output **verbatim** — read them as evidence,
then send the plain-English outcome"* is a one-paragraph addition to our "Notifying the user" section.

### W6 — Silence is the correct supervision output

§8: *"**Waiting on a healthy supervision cycle is silent**; empty polls, elapsed time, and no-change
updates are not captain-facing progress."* Paired with the opposite guarantee: *"**No turn ends blind**
while work is under way, including turns described as holding or waiting."*

Concretely: during this investigation the orchestrator emitted about fifteen turns of "Approved and
re-armed." Under their contract every one is a violation — routine supervision mechanics, zero decision
content. Our SKILL.md has no rule against it, so nothing stopped it.

Fix: two lines in the supervise-loop section — approve-and-rearm is silent unless the payload needs a
decision; batch what you learned into the next substantive reply.

### W7 — Parallelism rule: overlap is a risk signal, not a reason to wait

§7: *"Treat file or subsystem overlap as a **risk signal rather than an automatic reason to wait**, and
dispatch isolated work immediately with no concurrency cap … Serialize only for a true semantic
dependency, shared mutable external state, incompatible concurrent migration, or another concrete
condition that makes independent progress unsafe; **same-file editing alone is insufficient**."*

Note the honest asymmetry: their workers are in *separate worktrees*, so same-file parallelism is
genuinely safe. Ours share the checkout, which **inverts** the rule for *write* tasks — same-file
overlap **is** a reason to serialize until we have worktree isolation. It transfers cleanly for
read-only scouts.

### W8 — A situation → skill dispatch table

§13 is a routing table from situation to skill, with triggers stated at the point of need: *"Load
`stuck-crewmate-recovery` after a stale wake, looping or confused pane, answered-by-brief question,
unresponsive worker, or failed steer."* The contract layer stays thin because it *delegates to
procedures* instead of inlining them.

For us: our SKILL.md is one flat ~300-line document that must be read whole. The borrow is structural —
keep the always-loaded part to rules plus a trigger table, and move procedures (spawn lifecycle,
recovery ladder, Telegram setup) into sections loaded on demand.

### W9 — Self-recursion refusal

Because firstmate validates its own repo, an agent can end up running inside a checkout that contains
firstmate's identity. So `fm-spawn`, `fm-send`, and `fm-teardown` detect that context and **exit 3
before any fleet mutation**.

The same shape exists for us: our skill's scripts live in a git repo that we spawn workers *against* —
worker 3 read `agent-ctl.sh` from the very repo it was launched from. A worker told to "fix
agent-wait.sh" could drive its own siblings. Worth at least a documented rule, if not a check.

---

## What is explicitly NOT worth borrowing

**The backend abstraction layer itself** (980 l + 4,400 l of adapters). Its entire cost is paid to make
five *multiplexers* interchangeable. We have one backend and it's a native app with a typed control
socket — adopting `fm_backend_*` dispatch would mean writing an adapter for the only backend we have.
Two concrete frictions: their selector grammar reserves `:` as the raw-endpoint escape hatch, which
collides with the `<session>:<pane>` addressing agterm's `--pane` invites; and
`remove_worktree`/`worktree_path` exist only for Orca, so the contract is already not uniform.

*Could agterm be a firstmate backend?* Checked verb by verb: technically yes — agterm covers `capture`,
`send_key`, `kill`, `target_exists` natively and would be the *second* push-capable backend after
herdr, and the only one with a restart-safe cursor. Practically no — the real blocker is that agterm is
a macOS **GUI app**, and every one of their adapters has a CI-runnable smoke test.

**The away-mode daemon** (1,515 l). It exists because *their* escalation target is an LLM in a pane
where every wake costs a turn — so batching is worth a daemon, an injection-marker protocol, a busy
guard, a composer guard, a durable buffer, and a wedge alarm. **Our escalation target is a human on a
phone**, which is strictly better for the same job and costs nothing per message. Take only its two
portable ideas — the composer guard (→ B3) and max-defer-then-alarm (→ B3's mitigation).

**`--dangerously-skip-permissions` on workers by default.** It *would* fix our documented "~18 approval
prompts in one read-only investigation" row in one character, and it's why they have no
worker-approval machinery at all. But it deletes the human judgment our Safety section is built on, and
our workers run in the user's **real checkout**. Offer it as an explicit opt-in `spawn --autonomous`
for read-only investigation workers; never make it the default. (Deliberate judgment call — reasonable
people could rank it candidate #1 by raw value/effort.)

**Their vocabulary and state root.** `fm-crew-state.sh` exists to reconcile a status log against
`no-mistakes` run steps — a validation pipeline we don't have. Borrowing the words without the pipeline
is cargo cult: take B7/B8's *files*, not their nouns.

**The ANSI ghost-stripper** (~120 lines of awk doing SGR-2 and truecolor-luminance analysis). agterm's
`session text` returns **plain text, no ANSI** — nothing to strip and no way to get the styling. A
genuine "can't", and it's fine: B1 solves the same problem at the source, which is what their own
plain-text adapters rely on.

**`quota-array-dispatch`.** Presupposes multiple harnesses, multiple provider accounts, and a data
source we don't have. We drive 1–5 workers on one account.

**Their tone contract** — the mandatory "captain" address and nautical seasoning. Load-bearing for their
identity, pure overhead for ours.

---

## Recommendation

Two halves, and they reinforce each other.

**Workflow first** (prose in `SKILL.md`, no new scripts, no new failure modes):
**W1 + W2 + W3** — delegation-first scoped to subject-repo work, typed deliverables (ship/scout), and
briefs on disk. One coherent change that alters every future run, and it's what makes the code borrows
worth building rather than speculative. Add **W6** while you're in there; it's two lines.

**Then code** (~80 lines, no new architecture):
**B1 + B2 + B4** — they convert our two most-used verbs from confident guesses into checked claims.
B1 is a one-line prerequisite; B2 fixes the worst behaviour we have; B4 makes `say` capable of failing.

**One dependency to be aware of:** W1 realistically needs `spawn --autonomous` (read-only scouts only)
alongside it, or the orchestrator spends its life approving read-only probes — ~20 of them in this
investigation alone.

Everything else on both lists is optional.
