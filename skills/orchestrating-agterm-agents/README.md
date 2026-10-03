# orchestrating-agterm-agents

A [Claude Code](https://claude.ai/code) skill for **multi-agent orchestration on
[agterm](https://agterm.com)**: one *orchestrator* agent spawns other agents in their own macOS
terminal sessions, reads their screens, answers their prompts, and drives them to completion — plus a
Telegram bridge to supervise the orchestrator from your phone.

> `SKILL.md` is the agent-facing definition (what Claude Code loads). This README is the human tour.

## The idea

agterm's control socket (`agtermctl`) gives one shell three superpowers over *other* sessions:

- **spawn** — launch a Claude in a brand-new session (`session new`)
- **read** — capture another session's screen text (`session text`)
- **type** — send it keystrokes (`session type`)

Those three are the complete loop to delegate a task to a worker agent and see it through: spawn it,
read its screen, answer its prompts, nudge it — all from an orchestrator agent, on one machine, in real
terminals you can watch.

## Supervise loop — wake-on-quiescence

The orchestrator doesn't blindly press Enter on every confirmation. It supervises by watching for the
worker's screen to go **quiet**:

1. **Spawn** the worker with its task.
2. **Wait** — `agent-wait.sh` blocks until the worker's screen is static for a few polls, then reports
   whether it's `waiting` on a prompt or `idle` (done), plus whatever new output appeared.
3. **Decide** — approve a read-only/reversible step, answer a question, pause on anything
   destructive, or collect the final result.
4. Repeat until done.

Because it keys on the screen going *static* — not on any particular prompt wording — the same loop
supervises Claude, Codex, and other agent CLIs unchanged.

## Scripts

| Script | Purpose |
|--------|---------|
| `agent-ctl.sh` | The driving verbs — `read`, `submit`, `key`, `type`, `say`, `spawn`, `status`. One stable, allow-listable entry point so the orchestrator's actions don't re-prompt. |
| `agent-wait.sh` | Block until a session goes quiet; return `waiting`/`idle` + new output. The harness-agnostic supervise primitive. |
| `register.sh` | Record a session as the orchestrator so Telegram knows where to inject. `cmd+shift+r` binds whichever session you're in — press it again there to unbind — so notifications follow you between agents. |
| `tg-agterm.sh` | The outbound hook for the registered session: permission prompts go out as Yes/Always/No buttons, and a finished turn reports its result. Wired in `settings.json`, never called by hand. |
| `inject.sh` | Route an inbound Telegram message into the orchestrator's input — **status-aware**: won't fire into a busy session or corrupt a live menu, and reports back why if it can't. A message that opens with `<name>:` goes to the live session `tavern-<name>` instead, leaving the registration alone; `<name>!:` also makes that session the default target — so several tavern chats share one Telegram chat. |
| `oa.sh` | Send exact keystrokes (to answer a prompt) with **no** automatic Enter; the bare word `ENTER` sends an Enter keypress. |
| `notify.sh` | Send a message *out* to Telegram (job done / needs a decision). |

## Remote control (transport-agnostic)

The remote side is deliberately thin, so any transport works:

- **outbound** — `notify.sh` sends a message *out* (it's just a `curl`).
- **inbound** — anything that can run a shell command on the Mac can drive the orchestrator by calling
  `inject.sh` (send a message) or `oa.sh` (send raw keystrokes).

As an **example transport**, I wire this to Telegram with
**[pako-telegram](https://github.com/rashpile/pako-telegram)** (a bot that runs allow-listed shell
commands), which gives a full-duplex loop from my phone:

- *plain message* → typed into the orchestrator and submitted
- `/o <msg>` → same, explicit
- `/os` → a read-only snapshot of the orchestrator's screen (watch progress, send nothing)
- `/oa <keys>` → raw keystrokes to answer a prompt (`/oa 1`, `/oa ENTER`, …)

Messages that arrive over the transport are tagged `[tg]`, so the orchestrator knows to reply back the
same way rather than only to the terminal. In this "remote mode" it also avoids interactive selection
menus (a remote user can't drive a highlighted list) and never triggers a permission prompt on
*itself* — it delegates prompt-prone work to a worker whose prompts it approves.

## Requirements

- Running **inside** agterm (`AGTERM_ENABLED=1`), agterm **≥ 0.5.0**
- `agtermctl` at `/opt/homebrew/bin/agtermctl`
- `jq` available on `PATH`
- Recommended companion: the `agterm` skill (full `agtermctl` reference + hard-won gotchas)

## Safety

`Enter` approves **whatever is highlighted** — usually a safe "Yes, this once", but not always. The
orchestrator reads each `waiting` payload and decides: auto-approve read-only and reversible steps,
but stop and surface anything destructive or outward-facing (a service restart, `rm`, `git push`, a
deploy). Only drive tasks you would approve yourself — you are accountable for a worker's actions.

## Credits

Built on **[agterm](https://github.com/umputun/agterm)** by [Umputun](https://github.com/umputun) —
thank you for the scriptable terminal that makes all of this possible. 🙏
