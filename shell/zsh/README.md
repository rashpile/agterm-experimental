# agterm status badges — for *any* command

A tiny zsh hook that turns each agterm tab's sidebar glyph into a traffic light for whatever you're
running: dim while it works, **green** when it succeeds, **yellow** when it fails.

## What it does

You start a long command in one tab, then wander off to another tab (or another app). Normally you have
no idea when it's done — you keep flipping back to check. This fixes that:

- **While a command runs** → the tab shows a dim, blinking dot. "This tab is busy."
- **When it finishes** → **green** if it worked, **yellow** if it failed (non-zero exit).
- You only *see* the green/yellow on tabs you're **not** looking at. The instant you switch to the tab,
  it clears — because if you're looking at it, you can already see what happened. It's a "come back
  here" signal, not clutter.
- Only commands that ran a while (**≥ 10s** by default) get the green/yellow. A quick `ls` or `cd` just
  blinks for a moment and clears — no noise.

That's the whole thing. A build finishes while you're reading Slack → the tab goes green. A test run
blows up → it goes yellow and you notice.

## Wait — doesn't agterm already do this?

For **agents**, yes. agterm ships a shell integration that lights the glyph while a *coding agent*
(`codex`, `aider`, `gemini`, …) is running, and clears it when the agent stops. It's great, and this
hook doesn't replace it.

**This is for everything else.** Any command — `make`, `npm test`, `cargo build`, `terraform apply`, a
long script — gets the running dot, **and** a success/failure colour when it's done, which the
agent integration doesn't do. If you run the shipped agent integration too, this sits happily alongside
it (it even reuses agterm's agent regex to stay out of its way — agent commands get the running dot but
not a finish colour, so the two don't fight over the glyph).

## What you'll see

| Command | While running | When it finishes |
|---|---|---|
| `make build` (took 40s, succeeded) | dim blinking dot | **green** (on a background tab) |
| `npm test` (took 30s, failed) | dim blinking dot | **blinking yellow** |
| `ls`, `cd`, `git status` (instant) | a blink you won't notice | cleared |
| `codex` / an agent | dim blinking dot | cleared (no colour — left to agterm's agent integration) |
| `vim`, `man`, `ssh`, … | dim blinking dot | cleared (you were looking at it) |

## Install

1. **Drop the file** somewhere on your machine, e.g.:
   ```sh
   cp finish-badge.zsh ~/.config/agterm/finish-badge.zsh
   ```
2. **Source it from your interactive `.zshrc`:**
   ```zsh
   source ~/.config/agterm/finish-badge.zsh
   ```
   Two things to get right:
   - **Source it from the `.zshrc` that actually runs.** If you set `ZDOTDIR`, that's
     `$ZDOTDIR/.zshrc`, *not* `~/.zshrc`. (Quick check: `echo $ZDOTDIR`.)
   - If you also use agterm's **shipped agent integration**, put this line **after** it, so this hook
     reuses its `AGTERM_AGENT_RE` and knows which commands to leave alone.
3. **Open a new tab** (or `source` the file in your current one). Run something that takes >10s in a tab,
   switch away, and watch it go green/yellow.

It's a clean no-op outside agterm, outside zsh, or when disabled — so sourcing it unconditionally is safe.

## Knobs (environment variables)

| Variable | Default | What it does |
|---|---|---|
| `AGTERM_BADGE_DISABLE` | unset | **Off switch.** `export AGTERM_BADGE_DISABLE=1` turns it off *right now* in the current shell (both dots and colours); `unset` turns it back on. Set it in your `.zshrc` to disable for good. |
| `AGTERM_BADGE_MIN_SECS` | `10` | How long a command must run before a *finish* shows green/yellow. |
| `AGTERM_BADGE_RUN_COLOR` | `#a9c1d4` | Running dot (light blue-grey). |
| `AGTERM_BADGE_OK_COLOR` | `#8fce9b` | Success dot (light green). |
| `AGTERM_BADGE_FAIL_COLOR` | `#e6b800` | Failure dot (amber). |
| `AGTERM_BADGE_RUN_BLINK` | `1` | Pulse the running dot. `0` for a steady dot. |
| `AGTERM_BADGE_FAIL_BLINK` | `1` | Pulse the failure dot. `0` for a steady dot. |
| `AGTERM_BADGE_IGNORE_RE` | `vim\|nvim\|less\|man\|ssh\|tmux\|…` | Interactive programs that get the running dot but no finish colour. |
| `AGTERM_AGENT_RE` | agterm's default | Agent commands to leave to agterm's own integration (running dot only, no finish colour). |

The three `*_COLOR` knobs default to **light** shades so a command's badge is distinguishable from an
*agent's* (agents use agterm's default active/completed/blocked colours). Set any of them empty to match
agents exactly. All knobs are read per command, so you can tweak them live in a running shell.

## Which commands get badged

Out of the box: everything **except** agents (`claude`, `codex`, …, whose own status hooks own the
glyph) and interactive TUIs (`vim`, `ssh`, `man`, `yazi`, …). Skipped commands are left completely
alone — no badge is set, and none is cleared.

Two optional files tune that. One bare command name per line; blanks and `#` comments ignored; matched
on the command's **first word** (`bookokrat --foo` → `bookokrat`); read live, so edits apply
immediately with no re-source:

- **`~/.config/agterm-projects/.badgeignore`** — extra commands to never badge:
  ```
  # my long-running TUIs
  bookokrat
  gitui
  ```
- **`~/.config/agterm-projects/.badgeallow`** — an **allowlist**. If it has *any* entry, only the
  commands named in it get badges and everything else is skipped. It's authoritative: the ignore lists
  above no longer apply.
  ```
  make
  npm
  cargo
  ```

Paths are overridable with `AGTERM_BADGE_IGNORE_FILE` / `AGTERM_BADGE_ALLOW_FILE`.

## How it works (the short version)

Two zsh hooks. `preexec` (before a command runs) sets the glyph to agterm's `active` state; `precmd`
(after it finishes) reads the exit code and duration and sets green / yellow / clears it. Both status
writes are **synchronous** on purpose — two async writes from back-to-back commands can land out of
order and leave a stuck glyph. The trade-off is ~2 fast `agtermctl` socket calls per command (turn it
off with `AGTERM_BADGE_DISABLE` if you'd rather not pay that). Full detail is in the comments at the top
of [`finish-badge.zsh`](finish-badge.zsh).

## Requirements

- [agterm](https://agterm.com) with `agtermctl` on your `PATH` (bundled with the Homebrew cask)
- zsh
