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

The remote-control side is transport-agnostic — the skill just needs *something* on the Mac that can run
a shell command. I use **[pako-telegram](https://github.com/rashpile/pako-telegram)** (a bot that runs
allow-listed shell commands) as that transport, so I can drive the orchestrator from my phone.

## Why agterm orchestration

agterm's control socket lets one shell command **spawn** a new terminal session, **read** another
session's screen, and **type** into it. That's the entire loop needed to run a *worker* agent — a Claude
in its own session — and supervise it to completion. A real multi-agent system, on one Mac, in visible
terminals you can watch and take over at any time. Layer a Telegram bridge on top and you can drive it
from your phone.

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
