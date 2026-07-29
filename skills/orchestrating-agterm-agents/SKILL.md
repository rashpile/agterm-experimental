---
name: orchestrating-agterm-agents
description: Use when one Claude agent needs to run and supervise OTHER agents in separate agterm terminal sessions on macOS — multi-agent orchestration where an orchestrator delegates a task to a worker agent in its own session and drives it to completion. Triggers - multi-agent orchestration, subagent in a real terminal, control another agterm session, drive/babysit another agent, auto-approve agent permission prompts, answer another agent's AskUserQuestion, session.text, session.type, session.new, agtermctl, run parallel agents across workspaces.
---

# Orchestrating agterm agents

## Overview
agterm's control socket (`agtermctl`) lets one agent programmatically **spawn**, **read**, and **type
into** other terminal sessions. That is the complete loop to run and supervise worker agents:
`session new` launches a Claude in its own session, `session text` reads its screen, `session type`
answers its prompts or sends new instructions. So an orchestrator agent can delegate a task to a worker
agent in a separate session and drive it to completion — a real multi-agent system on one macOS machine.

Requires: running inside agterm (`AGTERM_ENABLED=1`), agterm **≥ 0.5.0** (for `session text`),
`agtermctl` at `/opt/homebrew/bin/agtermctl`. `agtermctl` has **no `--version`**; check the app build with
`defaults read /Applications/agterm.app/Contents/Info.plist CFBundleShortVersionString`.
**REQUIRED BACKGROUND:** the `agterm` skill — full `agtermctl` reference, the window/workspace/session
model, and hard-won gotchas (TTY shell-wrap, bare-PATH, id-not-name).

## When to use
- Delegate independent tasks to worker agents, each in its own terminal, and collect results.
- Babysit a long task: approve permission prompts, answer AskUserQuestion, nudge with follow-ups.
- Parallelize across projects/workspaces (one worker per workspace) and supervise them all.

**Not for:** single-agent work; when the in-process Task/subagent tool suffices (no terminal needed);
non-macOS / non-agterm environments.

## Quick reference
**Drive through the allow-listed `agent-ctl.sh` verbs, not raw `agtermctl`** — every raw call (or
`…|jq` pipeline) is a novel command that re-prompts for permission (and bumps the session's red
notification badge). The verbs below keep the whole loop allow-listed.

| Goal | Command |
|---|---|
| resolve a session name → id | `scripts/agent-ctl.sh id <name>` (replaces the ad-hoc `tree\|jq`) |
| spawn a worker agent | `scripts/agent-ctl.sh spawn [--create-workspace] <ws-id\|name> <name> <cwd> [command]` → prints new id. `--create-workspace` makes/reuses the workspace **by name** (idempotent) so you never touch raw `workspace new` |
| wait until a fresh worker is ready | `scripts/agent-ctl.sh ready <id> [secs]` — dismisses the first-run "trust folder" prompt, returns when the input box is up (no hand-rolled `session text` polling) |
| read its screen | `scripts/agent-ctl.sh read <id> [N]` (replaces raw `session text`) |
| classify a session's state | `scripts/agent-ctl.sh status <id>` → `idle\|busy\|waiting` + screen tail (reads agterm **native** status first; screen-scrapes only for SSH agents) |
| send a full instruction | `scripts/agent-ctl.sh say <id> <text>` (clear box → type → submit), or `type` → **verify via `read`** → `submit` |
| answer a prompt / menu | `scripts/agent-ctl.sh submit <id>` (Enter) · `key <id> down\|up\|esc\|enter` for a multi-choice menu |
| wait for the worker to stop | `scripts/agent-wait.sh <session-id>` (returns `STATE=waiting\|idle` + tail) |
| ping the human on Telegram | `scripts/notify.sh --level done\|attention\|info <message>` |
| ask the human to approve a **worker** (Telegram buttons) | `scripts/wask.sh <worker-id> [note]` — posts adaptive ✅ Yes / ♾️ Always / 🚫 No buttons that approve **that** worker via the `wapprove` bot command (needs a one-time `/reload`) |
| register THIS session as `/o` target (**only when the user explicitly asks**) | `scripts/register.sh` — or the user presses **`cmd+shift+r`** in any session (toggles: again in the bound one = unbind) |
| (inbound) Telegram `/o <msg>` injects `[tg] <msg>` here | handled by pako-telegram → `scripts/inject.sh` |
| (rare) bring a session to the foreground | `scripts/agent-ctl.sh select <id>` — steals focus; `read`/`status` populate the buffer without it, so you rarely need this |

Always target by the quoted `--target <id>`, and call `agtermctl` by absolute path in scripts.

## Bundled helper scripts (allow-list these, not ad-hoc pipelines)
Compound one-liners (`printf … | agtermctl … | jq …`) are a *novel* command each time, so a permission
allow-list can't match them — they re-prompt. The fix is stable script paths. Several are bundled in
`scripts/`; allow-list them once as `Bash(/abs/path/scripts/<name>:*)`:
- **`agent-ctl.sh <spawn|ready|id|read|status|type|say|submit|key> <session> [args]`** — the driving
  verbs (usage in the Quick reference above). Two behaviours worth knowing: after an *answering* verb
  (`submit`, `say`, `key enter|esc`) it auto-clears a stale **blocked** glyph on the target (see
  `agterm-clear-status.sh` below); and `id` prints the input unchanged when no name matched, so a caller
  can tell resolve-miss from hit.
- **`oselfclose.sh`** — the orchestrator's final graceful-shutdown act (see "Graceful shutdown"): clears
  its Telegram registration and deletes its own workspace, closing this session. Allow-listed so it runs
  without a prompt over Telegram.
- **`agterm-clear-status.sh <session-id>`** — smart-clear a stale agterm agent-status. Answering a
  permission prompt is just a keystroke, so no Claude lifecycle hook fires and the session's sidebar
  glyph stays **blocked** (blinking "needs you") even though the agent already resumed. This flips a
  *blocked* session to **active**; it no-ops on any other status, so it's safe after any keystroke.
  Wired into `agent-ctl.sh` (answering verbs), `oapprove.sh` (Telegram approval), and `oa.sh` (when the
  keystroke batch includes Enter) — so a remotely-approved prompt clears its own attention glyph.
- **`agent-wait.sh <session> [--stable N] [--interval S] [--timeout T] [--tail L]`** — blocks until the
  worker's screen goes **quiet** (static for N polls), then prints `STATE=waiting|idle`, the new lines
  since the wait started, and the tail. Run it in the background; its exit wakes you to decide. This is
  the **harness-agnostic** supervise primitive — it detects that the agent *stopped*, and you (the LLM)
  read the payload and choose the action. If the tail isn't enough, re-read with `agent-ctl.sh read`.
- **`notify.sh [--level done|attention|info] <message>`** — sends a Telegram message to the user. Use
  it to pull a human in when a long unattended job finishes (`done`) or a supervised worker hits a
  prompt you shouldn't auto-approve (`attention`). Touches no session (just `curl`); reads its bot
  token + chat id from `~/.config/orchestrating-agterm-agents/{token,chat_id}` (`TG_CHAT_ID` overrides
  the target). Allow-listed, so it sends without a prompt. **If those files are missing it exits 3 with
  a setup hint** (it can't Telegram its own failure) — relay that hint to the user in chat and stop,
  don't retry.
- **`wask.sh <worker-session-id> [note...]`** — the **buttoned** counterpart to `notify.sh` for
  *worker* approvals. Reads the worker's live prompt screen and posts a Telegram message with **✅ Yes /
  ♾️ Always / 🚫 No** buttons (Always shown only on a real 3-option prompt). The buttons ride
  pako-telegram's generic `act:<command>:<arg>` protocol with the worker's id in the arg
  (`act:wapprove:<id>:<choice>`), so a tap runs the **`wapprove`** bot command → **`wapprove.sh`**
  injects the keystroke into *that* worker (Enter / Down+Enter / Esc) and clears its blocked glyph. It is
  the worker analog of the orchestrator's own `tg-agterm.sh` → `oapprove.sh` flow (which only ever
  targets the *registered* session). Use it in the supervise loop when a worker hits a prompt you won't
  silently auto-approve but the user can decide with one tap from their phone. **Caveat:** the bot loads
  new commands only on **`/reload`** (no file watcher) — after adding `wapprove.yaml`, have the user send
  `/reload` once or taps show an "Unknown command" toast. Falls back to a plain `notify.sh` alert if the
  buttoned send fails (e.g. Telegram misconfig).
- **`wapprove.sh <session-id>:<choice>`** — the callback handler the `wapprove` bot command runs when a
  `wask.sh` button is tapped: parses the worker id + `yes|always|no` from the single callback arg, maps
  it to a keystroke, injects into that worker id, and clears its stale blocked glyph. Worker analog of
  `oapprove.sh`. Not called by you directly — the bot invokes it via the button.
- **`register.sh [--toggle] [session-id]`** — records a session's id in
  `~/.config/orchestrating-agterm-agents/orchestrator_session` so the Telegram `/o` command can inject
  into it. Bare, it binds THIS session (`$AGTERM_SESSION_ID`). **Run ONLY when the user explicitly asks
  to make THIS session the Telegram target** — never automatically on skill load. The `/ostart` command
  already registers the orchestrator it bootstraps, so the normal path needs no manual call. "Newer
  wins": the latest registration is the live target. Allow-listed, so it runs without a prompt.
  `inject.sh` (the inbound half) is invoked by pako-telegram, not by you.
  - The user can also bind whatever session they are sitting in with **`cmd+shift+r`** (keymap →
    `register.sh --toggle "$AGT_SESSION_ID"`), which is how notifications follow them between agents.
    It TOGGLES: pressed in the session that already owns Telegram it UNBINDS, which mutes the bridge
    globally (`/o` then errors, approval buttons stop posting) rather than falling back to another
    session. Acks with a one-shot status badge — cyan bound, grey unbound. `--toggle` is opt-in
    precisely so `ostart.sh` and skill activation can never unbind by accident.
  - A bound session pings Telegram when its turn ends — see `tg-agterm.sh` below.
- **`tg-agterm.sh`** — the Claude Code hook that IS the outbound bridge for the REGISTERED session.
  Wired in `~/.claude/settings.json` under two events; never call it yourself. Fail-open: any gate that
  does not hold exits 0 silently.
  - **Notification** (matcher `permission_prompt`) — the bound session hit a permission prompt → posts
    it with ✅ Yes / ♾️ Always / 🚫 No buttons (`act:oapprove:*` → `oapprove.sh`). `wask.sh` is the
    worker analog of this.
  - **Stop** — the bound session finished a turn → posts the turn's final answer, so an unattended job
    reports itself. This is the real payoff for binding a session that is NOT Telegram-aware (a worker,
    a plain Claude session): those never call `notify.sh`, so they used to finish in silence.
  - Skipped when the agent already reported via `notify.sh` that turn — a curated message beats an
    automatic dump of the same thing.
  - Markdown is STRIPPED, and `parse_mode` is deliberately not set: the prose routinely contains
    underscores (`last_notify-*`) and unbalanced markers that make Telegram return 400, which would
    lose the message outright. Lossy beats silent data loss.
  - EVERY turn fires Stop — which is what makes the `cmd+shift+r` toggle load-bearing: unbind when
    sitting at the keyboard, bind when walking away.
  - After posting, Stop RE-ASSERTS the cyan bound-badge. The glyph is shared state that Claude's own
    lifecycle hooks overwrite every turn, so `register.sh` setting it once never survives; re-asserting
    it here does, because this hook runs last in the Stop chain and the session then sits idle. Verified
    to persist on an unfocused session until it is visited (`--auto-reset`). Stop only — on the approval
    path it would overwrite `blocked` and hide that a session needs a decision.
  - The final text comes from **`last_assistant_message` in the Stop hook's stdin JSON** — never from
    `transcript_path`. The docs: *"The transcript file is written asynchronously and may lag the
    in-memory conversation... Hooks that need the final assistant text of the current turn should use
    `last_assistant_message` on Stop and SubagentStop instead of reading the transcript."* Measured
    here at ~250ms of lag, which made a transcript read post the previous block — the narration written
    before the last tool call — every turn, while looking like it worked. If you are tempted to poll the
    transcript, don't: that bug was found and fixed the expensive way.
  - Per-session state in `~/.config/orchestrating-agterm-agents/`: `last_notify-<id>` (notify.sh
    reported this turn — written by `notify.sh`), `last_stop-<id>` (previous turn boundary),
    `last_sent-<id>` (hash of the last text posted — a GUARD against Stop firing twice for one turn,
    never a trigger; keying the decision on "is this text new?" fires on narration, which is new too).

## Notifying the user
The orchestrator runs unattended, so **surface anything a human must see** instead of silently waiting.
Pair it with the supervise loop: after `agent-wait.sh` returns, decide, and if the stop needs the user,
`notify.sh` them. Only reach out for real signal (done / needs-a-decision / blocked) — don't narrate
routine progress.
```
agent-wait.sh <id>                                              # blocks until the worker stops
wask.sh   <id> "worker X"                                       # waiting → one-tap ✅/♾️/🚫 buttons approve THAT worker
notify.sh --level attention "worker X needs approval: <tail>"   # (plain-text fallback, no buttons)
notify.sh --level done      "worker X finished: <result>"       # idle → task complete
```
Prefer `wask.sh` over a plain `attention` note when the stop is a *permission prompt* — it lets the user
approve from their phone with one tap instead of texting back a `/o` instruction. Keep `notify.sh` for
decisions that aren't a yes/always/no menu (open questions, blocked-on-info, done).

## Remote control via Telegram (inbound)
`notify.sh` is outbound; this is the return path so the user can drive you from their phone.
- **Registration is opt-in — do NOT auto-register on activation.** The Telegram `/o` target is set by
  `register.sh`, but only run it when the user **explicitly** asks to make this session the target (e.g.
  "register this session for Telegram" / "make this the orchestrator"). The `/ostart` command already
  registers the session it bootstraps, so the standard flow needs no manual registration. Auto-
  registering on every skill load would silently steal `/o` from the session the user actually meant.
  "Newer wins" — the latest registration is the live target.
- **Messages prefixed `[tg] ` came from Telegram**, not the local keyboard. The user is remote, so
  **reply via `notify.sh`** (not just terminal output), and keep replies short for phone reading:
  `notify.sh --level info "<answer>"`. A message with no prefix is a local turn — behave as today.
- **Telegram mode — no selection prompts.** While you are conversing over Telegram (the current turn is
  a `[tg]` message), do **not** use `AskUserQuestion` or any interactive menu to ask the user something —
  the remote user can't drive a highlighted menu, and an injected reply would land *as* the menu answer
  and corrupt it. Instead ask a **plain-text question via `notify.sh`** and let them answer with a normal
  reply. Save multiple-choice menus for when the user is local at the keyboard.
- **Telegram mode — never trigger a permission prompt on YOURSELF.** A `[tg]` turn means the user is
  remote and cannot approve a Bash/edit permission dialog — one pending prompt in *your* session freezes
  the entire remote loop. So in TG mode restrict your own hands to **allow-listed, non-prompting tools**
  (the bundled `agent-ctl.sh` / `agent-wait.sh` / `notify.sh` / `register.sh`, and already-allow-listed
  commands). **Do not run novel `Bash` (ad-hoc `grep`/`find`/pipelines) or edits that could prompt.**
  Push any prompt-prone work — codebase investigation, builds, edits, git — into a **worker session you
  spawn** (`agent-ctl.sh spawn`) and drive: *you* approve the worker's prompts on the user's behalf
  (auto-approve read-only/reversible; for destructive, `notify.sh` and wait). The worker absorbs the
  permission dialogs; your own session stays unblocked and reachable from the phone.
  - **Keep allow-listed calls STANDALONE.** Bundling an allow-listed script into a compound command
    (`notify.sh … && cat >> file`, `cd … ; script | jq`) makes the *whole line* a novel command that
    re-prompts — defeating the allow-list and, in TG mode, freezing the loop right when you're trying to
    message the user. Run `notify.sh`/`agent-ctl.sh` **on their own line**; do ledger/file writes as a
    separate step (the `Edit` tool, not a bash heredoc).
- The path is: user texts `/o <msg>` → pako-telegram → `scripts/inject.sh` → this session's input box
  gets `[tg] <msg>` + Enter. It lands in your normal input, so your usual wake-on-quiescence/approval
  and Safety rules still apply — a `[tg]` request for a destructive action still needs your judgment.
- **`inject.sh` is status-aware** (reuses `agent-ctl.sh status` = `agent-wait`'s markers): it only pushes
  when you're **idle**. If you're **busy** it tells the user "resend in a moment"; if a **prompt/menu** is
  up it tells them what's being asked instead of firing text into the menu. So a `[tg]` message you
  receive means you were idle — a clean new turn, never a mid-render keystroke.

## Graceful shutdown (responding to `/oclose`)
`/oclose` (Telegram) posts `[tg] Graceful shutdown requested…` instead of hard-killing you — so wind
down cleanly rather than just stopping:
1. **Close your workers** — `agent-ctl.sh close <worker-id>` for each session you spawned.
2. **Summarize + sign off** — a couple of lines on what got done: `notify.sh --level done "<summary>"`.
3. **Close yourself** — `scripts/oselfclose.sh` clears your Telegram registration and deletes your
   workspace (ending this session). It's your final act; nothing runs after it.

All three are allow-listed, so none prompts — essential, since `/oclose` arrives over Telegram where you
can't approve a dialog on yourself. If `oselfclose.sh` can't delete the workspace (you're the window's
only one) it closes just your session; the user can finish with the force close (`oclose.sh force`), the
escape hatch for a wedged orchestrator.

## The orchestration loop
1. **Spawn** — `agent-ctl.sh spawn [--create-workspace] <ws> <name> <cwd>` (defaults the command to the
   TTY-wrapped `zsh -ic claude`), which prints the new id. Give the worker a **unique name** and then
   **drive it by that name** — every verb resolves name→id internally, so you never hand-retype a 36-char
   UUID (a real source of typos that fire keystrokes into a nonexistent session). Use `--create-workspace`
   when the workspace may not exist yet (idempotent, by name) instead of a raw `workspace new`.
2. **Wait until ready, then prompt** — `agent-ctl.sh ready <name>` (returns once the box is up, trust
   prompt dismissed), then `agent-ctl.sh say <name> <task>` (clear box → type → submit). If you want to
   eyeball the task before it fires, `type` then `read` to confirm it rendered, then `submit`. (Verify
   before firing.)
3. **Supervise (wake-on-quiescence).** Run `agent-wait.sh <id>` in the background; it returns when the
   worker's screen goes quiet, with `STATE=waiting|idle` + new lines + tail. Read it and act: `waiting`
   → `submit` (or `key down`×N then `submit` for a real multi-choice); `idle` → task done. Relaunch
   `agent-wait.sh` after each action, until done. It keys on the screen going static — no prompt-wording
   match — so it works across Claude, Codex, etc. unchanged.
4. **Collect** — the `idle` payload's tail is the result; `read <id> 200` for more of the transcript.

Why not blind auto-approve? A loop that just presses Enter on any confirmation is cheaper but only safe
for trusted yes/no, and it has to match each harness's prompt wording. Wake-on-quiescence puts *your*
judgment at each stop instead, and needs no per-harness strings.

## Safety — blind approval is dangerous
`Enter` approves **whatever is highlighted** — normally the safe *Yes, this once*, **not** the blanket
"don't ask again". Auto-approving read-only/inspect and reversible steps is fine; for
destructive/outward-facing prompts (service restart, `rm`, `git push`, deploy) **read the `waiting`
payload first and decide** — don't reflexively press Enter. Target a specific session **id**, never
`active`. Only drive tasks you would approve yourself; you are accountable for the worker's actions.

## agterm ≥0.10 read-back
`tree --json` gained fields that matter here:
- **`splitFocused`** (per session) — which pane has focus. Check it before `session type` into a split
  worker; previously this was unknowable and text could land in the wrong pane.
- **`quick type` / `quick text`** (0.10.1) — the quick terminal is now drivable like a session: a scratch
  channel that doesn't touch a worker's screen or its notification badge.
- **`idleMs`** (tree-level) is **not** an agent-done signal — it counts ms since the last *user* input in
  the window, so a worker grinding for 10 min still shows a large, growing `idleMs`. `agent-wait.sh`
  stays on screen-quiet polling — but see the next section for the signal that *would* work.

## Event-driven waiting — investigated, NOT implemented (2026-07-21)

`agent-wait.sh` infers "the worker finished" from 3 identical `session text` reads 1.5 s apart. That
gives a **4.5 s floor even when the worker replied instantly**, and it is blind to a tool that runs
without ticking output (see the `agent-wait` row in Common mistakes). `agtermctl events` exposes the
exact signal instead, and on this machine the producer side is **already fully wired**: the user's
Claude hooks set `active` on `UserPromptSubmit`/`PostToolUse`, `completed` on `Stop`, and `blocked` on
`Notification`, via `~/.config/agterm/agent-status/agterm-agent-status.sh`. Nothing consumes them yet —
`agtermctl events` currently appears only in the `agterm` skill's documentation.

If you pick this up, these were measured, not assumed:

- **`agtermctl events` is a blocking stream, not a signal.** Read its stdout until the wanted line
  appears. Use process substitution (`while read … < <(agtermctl events …)`) — a pipeline puts the loop
  in a subshell so its `exit` will not end your script. Closing the fd SIGPIPEs the producer; no `kill`
  needed, and no orphans were left in testing. Wrap it in `timeout` so an event that never comes ends
  the read.
- **The subscribe race is real and sharp.** A no-cursor subscription anchors when its *first read*
  completes, not at process start. Firing an event immediately after starting the listener missed it
  **9 times out of 10**; a 0.3 s settle delay missed **0 of 10**. So the order is
  **subscribe → settle → send → block**, and the awaiter must be backgrounded because the listener has
  to be up before the prompt goes out.
- **Skip the `--run`/`--after` cursor.** It removes the race in principle, but the anchor lives in the
  raw `events.read` envelope (`{"run":…,"next":42}`) which the streaming CLI never prints — you would
  be talking to the control socket directly — and cursor-run changes and expiry are documented as fatal
  errors needing explicit handling.
- **Keep the screen-polling fallback.** Status events depend on the user's hooks being configured in
  that worker's directory. A worker whose hooks never fire, one that dies before `Stop`, or a plain
  non-Claude command emits nothing at all. Event-first with polling as backup degrades to today's
  behaviour; event-only would hang to timeout.

Deferred deliberately — the polling version works today, and the race it would fix has a benign worst
case (fall back to the slow path).

## Common mistakes
| Mistake | Fix |
|---|---|
| Dropping to raw `agtermctl` / `…\|jq` for a spawn-lifecycle step (create a workspace, wait for readiness, resolve a name→id, read the screen) | Each raw call re-prompts (and bumps the target's notification badge). The verbs cover the whole loop — `spawn --create-workspace`, `ready`, `id`, `read`, `status`; only `session select` has no wrapper (rare). |
| Hand-retyping a worker's 36-char UUID (typo → keystrokes fire into a nonexistent session) | Give the worker a **unique name** at spawn and drive it **by name** — every verb resolves name→id internally. Only materialize the id (`agent-ctl.sh id <name>`) when a script genuinely needs the raw value. |
| `agtermctl`/`jq` silently "not found" | agterm & the allow-list run scripts with a **bare PATH** (`/usr/bin:/bin`). Use the absolute `/opt/homebrew/bin/agtermctl`, or `export PATH="/opt/homebrew/bin:$PATH"` at the top of scripts. |
| Wrong session driven | Several sessions are named `agent` — resolve and target by **id**, not name. |
| `no such workspace` on spawn | Workspace names carry a ` ⎇ <branch>` suffix (e.g. `ops ⎇ main`), so `--workspace ops` fails. Pass the workspace **id** from `tree --json`, or the exact full name. |
| Expecting a `waiting` that never comes | Read-only/allow-listed commands (e.g. `git log`) run with no confirmation, so `agent-wait` returns `idle`, not `waiting` — correct, nothing to approve. |
| `agent-wait` wakes early / mid-run | A tool that runs without ticking output can look static. `agent-wait` guards with spinner markers (`to run in background\|esc to interrupt\|↓ [0-9]`) and needs `--stable` consecutive identical polls (default 3) — keep it ≥3 so a momentary lull isn't read as done. |
| `agent-wait` **never** returns while a prompt is up (loops to timeout) | When the worker runs **parallel** sub-agent tasks, the "❯ 1. Yes" approval prompt shows *alongside* a still-running task's `(ctrl+b to run in background)` marker. That marker matches the busy-guard, resetting stability forever so the pending prompt is masked. Fixed in `agent-wait.sh`: a choice/permission prompt (`wait_re`, incl. "Do you want to proceed"/"requires approval") now **wins over** the busy-guard and returns immediately as `STATE=waiting ENDED=prompt`. If you see an old copy hang, update the script. |
| Heavy read-only investigation = **many** approval prompts | A worker fanning out to sub-agents makes a distinct `Bash(...)` per probe, each needing approval (~18 in one run) — the loop working as designed; approve each read-only `waiting` until `idle`. Don't pick a blanket "don't ask again for `docker system/image/volume *`": those globs also cover destructive `prune`/`rm`. |
| Clearing input eats queued text | The worker's box may hold text the user typed — `session text` FIRST, don't blind-clear. |
| `Enter` does nothing / text won't submit | The box text may be a greyed **prompt suggestion** (ghost), not real input — the box is actually EMPTY, so `\r` submits nothing. `session text` can't tell ghost from real. Don't rely on `submit`; **type the text for real** (`say`) or accept the suggestion first (`key <s> right`, i.e. `\x1b[C`) then `submit`. |
| Keystrokes dropped | Injecting mid-render/mid-init loses keys — small `sleep` after spawn and between steps. |
| Worker vanishes on exit | Run workers in real **sessions** (persistent), not overlays (auto-close when the program exits). |
| `status`/`inject` misread the **orchestrator** as "waiting on a prompt" | Screen-scraping a Claude session false-positives on prompt strings it quoted in its own transcript (`Do you want to proceed`, `❯ 1. Yes`), wrongly blocking real `/o` injects. `agent-ctl.sh status` instead reads agterm's **native** session status (published by the Stop/UserPromptSubmit/Notification hooks → `idle/completed→idle`, `active→busy`, `blocked→waiting`). |
| SSH worker always screen-scraped, never native | An **SSH** agent (`foreground=ssh`) runs Claude on a remote host with no `AGTERM_SESSION_ID`/local socket, so it can't publish native status. `status` detects `foreground=ssh` and falls back to the footer heuristic (idle=`? for shortcuts`, busy=`esc to interrupt`, prompt=`Esc to cancel`). Local agents get native; only SSH needs the scrape. |
