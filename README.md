# agterm-experimental

Experiments and tooling for **[agterm](https://agterm.com)** — a native macOS terminal that can be
driven programmatically over a local control socket (`agtermctl`).

This repo collects what I've built while researching **multi-agent orchestration on agterm**: running
and supervising Claude (and other) agents across real terminal sessions, and steering the whole thing
remotely from Telegram.

> ⚠️ **Experimental & personal.** Conventions, absolute paths, and bot wiring are tuned to my setup —
> read before you copy. agterm itself is beta, so expect churn.

## Contents

| Path | What it is |
|------|-----------|
| [`orchestrating-agterm-agents/`](orchestrating-agterm-agents/) | A Claude Code **skill**: one orchestrator agent spawns, reads, and drives worker agents in separate agterm sessions — plus a Telegram remote-control loop. |
| [`commands/`](commands/) | Small standalone **agterm commands** — shell scripts bound to keymap chords via `agtermctl`. See [Commands](#commands). |

The remote-control side is transport-agnostic — the skill just needs *something* on the Mac that can run
a shell command. I use **[pako-telegram](https://github.com/rashpile/pako-telegram)** (a bot that runs
allow-listed shell commands) as that transport, so I can drive the orchestrator from my phone.

## Why agterm orchestration

agterm's control socket lets one shell command **spawn** a new terminal session, **read** another
session's screen, and **type** into it. That's the entire loop needed to run a *worker* agent — a Claude
in its own session — and supervise it to completion. A real multi-agent system, on one Mac, in visible
terminals you can watch and take over at any time. Layer a Telegram bridge on top and you can drive it
from your phone.

## Commands

Standalone shell scripts under [`commands/`](commands/), each a small self-contained agterm tool driven
through `agtermctl`. Every command lives in its own folder with its script and setup.

### quickloc — quick session locations

Harpoon-style session marks: pin up to 5 sessions to slots `1`–`5` and jump between them with two
keystrokes. Files: [`commands/quickloc/`](commands/quickloc/).

**Usage**

| Chord | Action |
|-------|--------|
| `ctrl+b` `m` `N` | Mark the current session as slot **N** (1–5) |
| `ctrl+b` `N` | Jump to the session marked slot **N** |

**How it works.** The mark *is* the session name — slot N shows as a `[N]` tag prefixed to the name
(e.g. `top` → `[1] top`), so marks are visible in the sidebar, survive restarts, and need no state
file. Marking uses a nested leader (`ctrl+b` → `m` → digit); jumping uses the plain two-key
`ctrl+b` → digit.

- **Re-marking moves the tag.** Marking slot N on a new session strips `[N]` from the previous holder
  and adds it to the current one — each slot points at exactly one session.
- **Multi-slot is allowed.** A session pinned to two slots merges and sorts its tags (`[1][2] top`).
- **Names are the source of truth.** Renaming a session to include `[3]` makes it slot 3; removing the
  tag unmarks it. Non-slot brackets (e.g. `[On]`) are left untouched.

## Requirements

- macOS with [agterm](https://agterm.com) **≥ 0.5.0** (needed for `session text`)
- `agtermctl` (bundled with the agterm Homebrew cask)
- [Claude Code](https://claude.ai/code) — for the skill
- *(optional)* any transport that can run a shell command on the Mac — e.g.
  [pako-telegram](https://github.com/rashpile/pako-telegram) — for remote control

## Credits

Enormous thanks to **[Umputun](https://github.com/umputun)** for building
**[agterm](https://github.com/umputun/agterm)** — none of this exists without it. A native macOS
terminal with a scriptable control socket is exactly the primitive that makes agent orchestration like
this possible, and it's a genuine pleasure to hack on. 🙏

## Status

Personal research project, shared in case it's useful. No stability guarantees; no license yet — ask
before reusing.
