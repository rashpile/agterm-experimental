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
| Goal | Command |
|---|---|
| find a session id by name | `agtermctl tree --json \| jq -r '.result.tree.workspaces[].sessions[]\|select(.name=="agent").id'` |
| find a workspace id | `agtermctl tree --json \| jq -r '.result.tree.workspaces[]\|select(.name\|startswith("ops")).id'` |
| spawn a worker agent | `scripts/agent-ctl.sh spawn <ws-id\|name-prefix> <name> <cwd> [command]` → prints new id (allow-listed; avoids the ad-hoc `$(agtermctl…\|jq)` pipeline that re-prompts) |
| view it | `agtermctl session select --target <id>` |
| read its screen | `agtermctl session text --target <id> [--lines N \| --all] [--pane left\|right]` |
| classify a session's state | `scripts/agent-ctl.sh status <id>` → `idle\|busy\|waiting` + screen tail (reads agterm **native** session status first; screen-scrapes only for SSH agents) |
| answer a prompt / menu | `printf '\r' \| agtermctl session type --stdin --target <id>` |
| navigation keys | `\r`=Enter, `\x1b[B`=↓, `\x1b[A`=↑, `\x03`=Ctrl-C, `\x7f`=Backspace |
| send a full instruction | clear input (backspaces) → type → **verify via `session text`** → `\r` |
| wait for the worker to stop | `scripts/agent-wait.sh <session-id>` (returns `STATE=waiting\|idle` + tail) |
| ping the human on Telegram | `scripts/notify.sh --level done\|attention\|info <message>` |
| register THIS session as the Telegram `/o` target | `scripts/register.sh` |
| (inbound) Telegram `/o <msg>` injects `[tg] <msg>` here | handled by pako-telegram → `scripts/inject.sh` |

Always target by the quoted `--target <id>`, and call `agtermctl` by absolute path in scripts.

## Bundled helper scripts (allow-list these, not ad-hoc pipelines)
Compound one-liners (`printf … | agtermctl … | jq …`) are a *novel* command each time, so a permission
allow-list can't match them — they re-prompt. The fix is stable script paths. Two are bundled in
`scripts/`; allow-list them once as `Bash(/abs/path/scripts/<name>:*)`:
- **`agent-ctl.sh <read|submit|key|type|say> <session> [args]`** — the driving verbs:
  `read <s> [N]` (tail screen), `submit <s>` (Enter), `key <s> enter|down|up|ctrl-c|esc`,
  `type <s> <text>` (clear box + type, no submit), `say <s> <text>` (type + submit). Prefer these over
  raw `agtermctl session type/text` so the calls stay allow-listable.
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
- **`register.sh`** — records this session's `$AGTERM_SESSION_ID` in
  `~/.config/orchestrating-agterm-agents/orchestrator_session` so the Telegram `/o` command can inject
  into you. Run once on activation. Allow-listed, so it runs without a prompt. `inject.sh` (the inbound
  half) is invoked by pako-telegram, not by you.

## Notifying the user
The orchestrator runs unattended, so **surface anything a human must see** instead of silently waiting.
Pair it with the supervise loop: after `agent-wait.sh` returns, decide, and if the stop needs the user,
`notify.sh` them. Only reach out for real signal (done / needs-a-decision / blocked) — don't narrate
routine progress.
```
agent-wait.sh <id>                                              # blocks until the worker stops
notify.sh --level attention "worker X needs approval: <tail>"   # waiting on something you won't auto-approve
notify.sh --level done      "worker X finished: <result>"       # idle → task complete
```

## Remote control via Telegram (inbound)
`notify.sh` is outbound; this is the return path so the user can drive you from their phone.
- **On activation, register yourself:** run `scripts/register.sh` once. It records this session's
  `$AGTERM_SESSION_ID` so the Telegram `/o` command knows which session to inject into. "Newer wins" —
  if you re-run it (or a new orchestrator starts), the latest registration is the live target.
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

## The orchestration loop
1. **Spawn** — `session new --workspace <ws-ID> … --command "zsh -ic claude"` (shell-wrapped for a TTY),
   capture `.result.id`. `--workspace` needs the workspace **id** (or its full name incl. any ` ⎇ branch`
   suffix) — a bare base name like `ops` fails; resolve the id from `tree --json` first.
2. **Prompt** — clear the worker's input box with backspaces, type the task, **read it back with
   `session text` to confirm it rendered**, then a *separate* `\r` to submit. (Verify before firing.)
3. **Supervise (wake-on-quiescence).** Run `agent-wait.sh <id>` in the background; it returns when the
   worker's screen goes quiet, with `STATE=waiting|idle` + the new lines + tail. Read it and act:
   `waiting` → inject `\r` (or `↓`×N + `\r` for a real multi-choice); `idle` → the task is done. Relaunch
   `agent-wait.sh` after each action, until done. **No prompt-wording match needed** — it keys on the
   screen going static, so it works across Claude, Codex, etc. unchanged. (Proven: slept through 20s of
   Codex streaming, `POLLS=13`, then returned idle; woke as `waiting` on Codex's approval prompt.)
4. **Collect** — the `idle` payload's tail is the result; `session text --all` for the full transcript.

Why not blind auto-approve? A loop that just presses Enter on any confirmation is cheaper but only safe
for trusted yes/no, and it has to match each harness's prompt wording. Wake-on-quiescence puts *your*
judgment at each stop instead, and needs no per-harness strings.

## Safety — blind approval is dangerous
`Enter` approves **whatever is highlighted** — normally the safe *Yes, this once*, **not** the blanket
"don't ask again". Auto-approving read-only/inspect and reversible steps is fine; for
destructive/outward-facing prompts (service restart, `rm`, `git push`, deploy) **read the `waiting`
payload first and decide** — don't reflexively press Enter. Target a specific session **id**, never
`active`. Only drive tasks you would approve yourself; you are accountable for the worker's actions.

## Common mistakes
| Mistake | Fix |
|---|---|
| `agtermctl`/`jq` silently "not found" | agterm & the allow-list run scripts with a **bare PATH** (`/usr/bin:/bin`). Use the absolute `/opt/homebrew/bin/agtermctl`, or `export PATH="/opt/homebrew/bin:$PATH"` at the top of scripts. |
| Wrong session driven | Several sessions are named `agent` — resolve and target by **id**, not name. |
| `no such workspace` on spawn | Workspace names carry a ` ⎇ <branch>` suffix (e.g. `ops ⎇ main`), so `--workspace ops` fails. Pass the workspace **id** from `tree --json`, or the exact full name. |
| Expecting a `waiting` that never comes | Read-only/allow-listed commands (e.g. `git log`) run with no confirmation, so `agent-wait` returns `idle`, not `waiting` — correct, nothing to approve. |
| `agent-wait` wakes early / mid-run | A tool that runs without ticking output can look static. `agent-wait` guards with spinner markers (`to run in background\|esc to interrupt\|↓ [0-9]`) and needs `--stable` consecutive identical polls (default 3) — keep it ≥3 so a momentary lull isn't read as done. |
| `agent-wait` **never** returns while a prompt is up (loops to timeout) | When the worker runs **parallel** sub-agent tasks, the "❯ 1. Yes" approval prompt shows *alongside* a still-running task's `(ctrl+b to run in background)` marker. That marker matches the busy-guard, resetting stability forever so the pending prompt is masked. Fixed in `agent-wait.sh`: a choice/permission prompt (`wait_re`, incl. "Do you want to proceed"/"requires approval") now **wins over** the busy-guard and returns immediately as `STATE=waiting ENDED=prompt`. If you see an old copy hang, update the script. |
| Heavy read-only investigation = **many** approval prompts | A worker that fans out to a research sub-agent generates a distinct compound `Bash(...)` per probe, each needing approval (~18 in one backup-enumeration run). This is the wake-on-quiescence loop working as designed — approve each `waiting` (read the payload, it's read-only) until `idle`. Don't pick the blanket "don't ask again for `docker system`/`docker image`/`docker volume` *" option: those globs also cover destructive `prune`/`rm`. |
| Clearing input eats queued text | The worker's box may hold text the user typed — `session text` FIRST, don't blind-clear. |
| `Enter` does nothing / text won't submit | The box text may be a greyed **prompt suggestion** (ghost), not real input — the box is actually EMPTY, so `\r` submits nothing. `session text` can't tell ghost from real. Don't rely on `submit`; **type the text for real** (`say`) or accept the suggestion first (`key <s> right`, i.e. `\x1b[C`) then `submit`. |
| Keystrokes dropped | Injecting mid-render/mid-init loses keys — small `sleep` after spawn and between steps. |
| Worker vanishes on exit | Run workers in real **sessions** (persistent), not overlays (auto-close when the program exits). |
| `status`/`inject` misread the **orchestrator** as "waiting on a prompt" | Don't classify a Claude session by scraping its screen — its transcript quotes worker prompt strings (`Do you want to proceed`, `❯ 1. Yes`) that a scraper reads as a *live* prompt, wrongly blocking real `/o` injects. **First-class fix:** `agent-ctl.sh status` reads agterm's **native** session status (`idle\|active\|blocked\|completed`, published by the Stop/UserPromptSubmit/Notification hooks in `~/.config/agterm/agent-status/agterm-agent-status.sh` → mapped `idle/completed→idle`, `active→busy`, `blocked→waiting`). |
| SSH worker always screen-scraped, never native | An **SSH** agent (session `foreground=ssh`) runs Claude on a *remote* host with no `AGTERM_SESSION_ID` and no local socket, so it can't publish native status. `status` detects `foreground=ssh` and falls back to the screen heuristic (anchored on the bottom footer: idle=`? for shortcuts`, busy=`esc to interrupt`, prompt=`Esc to cancel`). Local agents get native; only SSH needs the scrape. |

## Real-world impact
Drove a worker agent through a full bug-fix → PR merge → remote Docker deploy → healthcheck: 6 permission
prompts auto-approved unattended, container recreated **healthy**, result read back over the socket — the
orchestrator never touched the worker's keyboard by hand.
