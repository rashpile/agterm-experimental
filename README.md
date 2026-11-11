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
| [`skills/orchestrating-agterm-agents/`](skills/orchestrating-agterm-agents/) | A Claude Code **skill**: one orchestrator agent spawns, reads, and drives worker agents in separate agterm sessions — plus a Telegram remote-control loop. |
| [`skills/agterm-check-update/`](skills/agterm-check-update/) | A Claude Code **skill**: after an agterm upgrade, diff the upstream changelog for the delta since the configs were last reviewed, then audit whether custom configs/scripts should adopt new built-ins or can be simplified. Tracks the last-validated version so each run covers only the delta. |
| [`skills/make-task/`](skills/make-task/) | A Claude Code **skill**: run one development task end to end in the current session — brainstorm, plan, plan review, task-by-task execution in subagents, a revmux review-fix loop, finalize — with a live agterm dashboard; every question is an `AskUserQuestion` in the terminal. A profile in `~/.config/make-task/profiles/<name>/` holds copies of the companion skills and sets how autonomous the run is. Usage: `/make-task [--profile=<name>] [--update] [task text]`. Notes for editing it: [`skills/make-task/CLAUDE.md`](skills/make-task/CLAUDE.md). |
| [`skills/view/`](skills/view/) | A Claude Code **skill**: render a markdown file (or markdown piped in) to a theme-aware HTML page — highlighted code, GFM tables, inlined images, Mermaid diagrams — and show it in a page companion of the current agterm session, flagged when that session is. `--here`, `--size` or `--pane` show it as an overlay over the session instead. Usage: `/view <file.md>`. |
| [`mcps/go-ask/`](mcps/go-ask/) | An **MCP server** in Go: `sessions` lists agterm panes, `ask` types a message into one and submits it. No reply is awaited. Runs over stdio, or over HTTP with a bearer token for other machines. |
| [`claude-btw/`](claude-btw/) | A self-contained feature: fork the Claude session running in an agterm tab into a new tab beside it. Hook, hotkey, and skill in one directory, each subdirectory mirroring where it installs. See [claude-btw](#claude-btw). |
| [`commands/`](commands/) | Small standalone **agterm commands** — shell scripts bound to keymap chords via `agtermctl`. See [Commands](#commands). |
| [`config/agterm-tree/`](config/agterm-tree/) | A **file tree panel** for agterm — nvim-tree in the right split, files opening into overlays. Built entirely on the control API; no app changes. |
| [`shell/zsh/`](shell/zsh/) | A **zsh status-badge hook** — turns each tab's sidebar glyph into a traffic light for *any* command: dim while running, green on success, yellow on failure. agterm does this for agents; this does it for everything. |

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

### scratch — quick scratch files

Create and reopen throwaway files (`txt` / `json` / `html` / `markdown`) without leaving the keyboard.
Each opens in a full-coverage agterm overlay, edited with `nvim`. Files:
[`commands/scratch/`](commands/scratch/).

**Usage**

| Chord | Action |
|-------|--------|
| `cmd+shift+n` `n` | **New** — pick a type, create `scratch_<N>.<ext>`, open in `nvim` (INSERT mode) |
| `cmd+shift+n` `o` | **Open** — fzf-pick an existing scratch (newest first, `bat` preview) |
| `cmd+shift+n` `l` | **Last** — reopen the most recently opened scratch, no picker |
| `cmd+shift+n` `w` | **New workspace** |

Scratches live in `~/.config/agterm-projects/scratches/`; `scratch_<N>` uses the next free number across
all types.

- **Last-opened is tracked, not guessed.** Every open writes the path to a hidden `.last` pointer, so
  `>l` reopens the file you actually last touched (a file's mtime doesn't change on open). If there's no
  valid last file, `>l` falls back to the picker.
- **Plain editor.** `nvim` launches with the nvim-tree plugin guard set, so a scratch is always a bare
  editor even if your config auto-opens a file tree.

### update-agterm — check for a new release

Check whether a newer agterm release is out before upgrading. Compares the installed version against
the latest GitHub release; if you're behind, it renders the changelog for **every** release in the gap
(via `glow`) and asks before touching Homebrew. Files: [`commands/update-agterm/`](commands/update-agterm/).

**Usage**

| Chord | Action |
|-------|--------|
| `ctrl+b` `u` | Check for an update — show release notes, then ask before upgrading |

- **Check-first, never surprises you.** If already on the latest it just says so and exits; only when
  behind does it prompt `Update to X now? [y/N]`, running `brew update && brew upgrade --cask agterm`
  only on `y`.
- **Full gap changelog.** Uses `sort -V` to select every release newer than what's installed, so several
  versions behind still shows all the notes, not just the newest.
- **Runs in the "processes" workspace, not an overlay** — an overlay auto-closes when its program exits,
  and the y/N prompt needs a persistent TTY. Depends on the `run-in-processes` helper and `glow` +
  `jq`.

### webmd — read the last message rendered, in the terminal

Take the last message of the claude running in this session and read it as rendered markdown
without leaving the terminal. `webmd.sh` converts it to a self-contained HTML page and shows it
with [terminal-browser](https://terminal-browser.sh), which draws a real Chromium into a pane.
Files: [`commands/webmd/`](commands/webmd/).

**Usage**

| Chord | Action |
|-------|--------|
| `ctrl+b` `c` `w` | Last message in a **95% overlay** over this session |
| `ctrl+b` `c` `W` or `opt+a` | Last message in the **right split pane**. A webmd page already there is refreshed in place; a pane busy with anything else gets a new session named `<this session> last` |

It also renders any file: `webmd.sh file <path.md> [session-id] [--new]`.

- **Same lookup as the rest of the `c` group.** `agterm-claude-session` resolves the live claude
  from `~/.claude/sessions/<pid>.json`, so `/clear` is handled and a split pane running its own
  claude resolves to the right one.
- **Overlay, pane, session.** The overlay dies with its command, which is what you want for a
  quick read. `>W` puts the page in the right split pane instead, creating or revealing that
  pane, so the document sits beside the conversation and stays there. A pane already running
  something is never disturbed: the page goes to a new session named `<this session> last`,
  placed right after the current one. That name comes from the sidebar name in the tree, not
  `$AGT_SESSION_NAME`, which carries the pane's terminal title and can be set by a remote
  shell over OSC. Because a running overlay is never replaced, `>w` closes whatever overlay
  is up before opening its own.
- **A pane it created closes with the browser.** The typed line ends in `; exit`, so quitting
  the page ends that shell and the split goes away, leaving the session as it was found. A pane
  that already existed keeps its shell: its cwd and history belong to you, not to webmd.
- **Two thirds of the width**, set with `session resize --split-ratio 0.34` right after the pane
  is created. Prose needs the width more than a prompt does. Change `SIDE_PRIMARY` at the top
  of `webmd.sh` to move the divider; it is the fraction the *left* pane keeps. An existing
  pane is never resized.
- **Pressing it again refreshes the pane** instead of piling up sessions. If the pane already
  shows a page from `~/.local/share/agterm/webmd/`, the new document is loaded into that same
  browser with `terminal-browser action -- open`. The browser is found by the pane's tty, not
  by the tab's url: launch argv never changes, but the tab does, so a url match would miss it
  from the second press onward.
- **One pair of files per press** in `~/.local/share/agterm/webmd/msg-<stamp>.{md,html}`, so an
  earlier message stays readable after the next one.
- **Read-only**, unlike `ctrl+b c t` (Last message Typora), which opens the same markdown in a real
  editor that saves in place. Stopgap until the Go `webmd` exists.

### recap: what a Claude session did lately, from a local model

Summarize the last turns of the claude running in a session with a local Ollama model, without
spending Claude tokens. The summary has four parts: what was done, the user's decisions, the
current state, what is left. Files: [`commands/recap/`](commands/recap/).

**Usage**

| Trigger | Action |
|-------|--------|
| `ctrl+b` `c` `s` | Recap this session in an 85% overlay |
| `recap.sh <session>` | The same for another session: id, id prefix, name or `active` |
| `recap.sh --print [<session>]` | Write the recap to this terminal instead |

`--model` picks the model (default `gemma4:e4b`, or `$RECAP_MODEL`) and `--turns` how many user
turns go in (default 6).

- **The overlay shows the summary while it is written**, then closes and a rendered markdown
  overlay takes its place. On an M4 Pro, `gemma4:e4b` takes about 40 s for 6 turns.
- **The transcript is found from the live process**, through `agterm-claude-session`, as webmd
  does: `/clear` and a split pane's own claude resolve to the right file. A session without a
  live claude has no recap.
- **The digest decides the quality more than the model.** `recap.py` sends the user's messages,
  the user's answers to the agent's questions, one line per tool call, assistant text cut to 600
  characters, and the last text of each turn nearly whole, since that is where the state is
  written down. Tool output is left out. In a test, `gemma3:12b` invented a user decision
  and took twice as long; `gemma4:e4b` got the facts right.
- **One file per recap** in `~/.local/share/agterm/recap/recap-<stamp>.md`.

### claude-btw

Fork the Claude session running in the current tab into a new tab beside it, so a side question
("btw...") can be asked without derailing the main task or waiting for it to finish. The fork
opens with the base conversation's full history and an empty prompt; from there, `/btw-sync` pulls
in what the base did while you were on the side question, and `/btw-back` sends the answer to the
base tab's prompt — unsubmitted, so the user reviews and sends it themselves. Files:
[`claude-btw/`](claude-btw/) — see [`claude-btw/README.md`](claude-btw/README.md) for the full
layout and install steps.

**Usage**

| Trigger | Action |
|-------|--------|
| `cmd+shift+a` | Fork the Claude session in the current pane into a new tab |
| `/btw-sync` (inside the fork) | Pull in what the base session did since the fork (or last sync) |
| `/btw-back` (inside the fork) | Send this fork's answer back to the base tab's prompt |

**How it works.** Three moving parts, and they install in this order because each depends on the
one before it:

1. **[`claude-btw/hooks/`](claude-btw/hooks/)** — a Claude Code `SessionStart`/`SessionEnd` hook
   that records which Claude session is running in which agterm tab and pane, in a small file
   registry. The hotkey fires a plain shell command, never through Claude, so it has no way of
   knowing which Claude session it was pressed from without this registry — install it first, or
   the hotkey has nothing to fork.
2. **[`claude-btw/commands/`](claude-btw/commands/)** — the `cmd+shift+a` hotkey target. Looks the
   current pane up in the registry, spawns the new tab running `claude --resume <base-id>
   --fork-session`, then records fork metadata (base session id, transcript path, a line-number
   watermark) under `$BTW_ROOT/forks/<new-tab-id>/meta.json` — spawn has to come first because
   that directory is keyed by the *new* tab's agterm session id, which doesn't exist until
   `session new` returns it.
3. **[`claude-btw/skills/btw/`](claude-btw/skills/btw/)** — the skill and its two slash commands
   (`/btw-sync`, `/btw-back`), which run *inside* the fork and teach the forked Claude how to catch
   up on base activity and send an answer back without flooding its own context or clobbering the
   base tab's prompt.

Installed with `claude-btw/install.sh` (`--dry-run` to preview, `--link` to symlink instead of
copy) and removed with `claude-btw/uninstall.sh` (`--purge` to also drop runtime state). Both
patch the two live config files these parts need — `~/.claude/settings.json` and
`~/.config/agterm/keymap.conf` — as a merge, with a timestamped backup before every change. Full
details, including the manual step-by-step (in this order) for auditing or doing it by hand, live
in **[`claude-btw/README.md`](claude-btw/README.md#install)**, kept there and only there so they
can't drift out of sync with this overview.

- **Fork tabs only survive an agterm restart if "Restore running commands on restart" is on**
  (agterm's General settings; **off by default**). With it on, the registry hook pins each fork tab
  to restore itself (`claude --resume <its-own-id>`) instead of replaying the literal fork command,
  so a restart doesn't silently re-fork the base session again. With it off, both the fork and the
  base tab come back as plain shells on restart — expected behaviour given the setting, not a bug
  in this design.

## Requirements

- macOS with [agterm](https://agterm.com) **≥ 0.5.0** (needed for `session text`)
- `agtermctl` (bundled with the agterm Homebrew cask)
- [Claude Code](https://claude.ai/code) — for the skills
- `jq` — used throughout, including the **claude-btw** hook and scripts
- `nvim` and [`fzf`](https://github.com/junegunn/fzf) — for the **scratch** command
  (`bat` optional, for the open-picker preview)
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
