# claude-btw

Fork the Claude session running in the current agterm tab into a new tab beside it, so a side
question ("btw...") can be asked without derailing the main task or waiting for it to finish. The
fork opens with the base conversation's full history and an empty prompt; from there, `/btw-sync`
pulls in what the base did while you were on the side question, and `/btw-back` sends the answer
to the base tab's prompt — unsubmitted, so the user reviews and sends it themselves.

See the root [`README.md`](../README.md#claude-btw) for the usage table and a short "how it
works" overview. This file has the complete install steps.

## Layout

Each subdirectory mirrors where it gets installed:

| Path | Installs to | What it is |
|---|---|---|
| [`hooks/`](hooks/) | `~/.claude/hooks/agterm-tabs/` | A Claude Code `SessionStart`/`SessionEnd` hook that maintains the tab registry the hotkey depends on. |
| [`commands/`](commands/) | `~/.config/agterm-projects/` | The `cmd+shift+a` hotkey target and its keymap chord. |
| [`skills/btw/`](skills/btw/) | `~/.claude/skills/btw` | The skill and slash commands (`/btw-sync`, `/btw-back`) that run *inside* the fork. |
| [`tests/`](tests/) | (not installed) | The automated suite. Run by hand: `zsh claude-btw/tests/run.sh`. Never touches agterm or Claude. |
| [`design-notes.md`](design-notes.md) | (not installed) | Historical brainstorm notes, superseded by the spec and plan under `docs/superpowers/`. |

## Install

Install in this order — each step depends on the one before it:

1. **The hook** — the registry the hotkey depends on. Without it the hotkey has nothing to fork,
   so install this first.

   ```bash
   cp -r claude-btw/hooks ~/.claude/hooks/agterm-tabs
   # merge claude-btw/hooks/settings-snippet.json into ~/.claude/settings.json
   # (see claude-btw/hooks/README.md for the exact merge and the <HOME> substitution)
   ```

2. **The hotkey.** A plain `cp`, not a symlink — `claude-btw.sh` looks for `lib.sh` at the
   installed skill location first (`~/.claude/skills/btw/scripts/lib.sh`, which step 3 below
   creates), so a copy resolves correctly once step 3 is done. If you install this step before
   step 3, the script falls back to failing cleanly with a one-line diagnostic rather than
   silently doing nothing.

   ```bash
   mkdir -p ~/.config/agterm-projects
   cp claude-btw/commands/claude-btw.sh ~/.config/agterm-projects/claude-btw.sh
   # merge claude-btw/commands/keymap.conf into ~/.config/agterm/keymap.conf
   ```

3. **The skill + slash commands**, run inside the fork.

   ```bash
   cp -r claude-btw/skills/btw ~/.claude/skills/btw
   ln -s ~/.claude/skills/btw/commands/btw-sync.md ~/.claude/commands/btw-sync.md
   ln -s ~/.claude/skills/btw/commands/btw-back.md ~/.claude/commands/btw-back.md
   ```

- **Fork tabs only survive an agterm restart if "Restore running commands on restart" is on**
  (agterm's General settings; **off by default**). With it on, the registry hook pins each fork
  tab to restore itself (`claude --resume <its-own-id>`) instead of replaying the literal fork
  command, so a restart doesn't silently re-fork the base session again. With it off, both the
  fork and the base tab come back as plain shells on restart — expected behaviour given the
  setting, not a bug in this design.

## Path resolution

`claude-btw/commands/claude-btw.sh` finds `lib.sh` by checking, in order: `$BTW_LIB` if set; the
installed skill location `~/.claude/skills/btw/scripts/lib.sh`; and the checkout-relative
`../skills/btw/scripts/lib.sh` next to the script itself. This means both a `cp` install (step 2
above, once step 3 has run) and running straight out of a checkout work, and a total miss (no
`BTW_LIB`, no installed skill, not running from a checkout) prints one clear diagnostic line and
exits, instead of cascading into `command not found` once execution reaches a `btw_*` function.
