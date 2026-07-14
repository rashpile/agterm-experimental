---
name: agterm-check-update
description: Use when agterm has just been updated to a new version and you need to see what changed upstream and decide whether the user's custom agterm configs and scripts (keymap.conf, ghostty.conf, ~/.config/agterm-projects) need updating or simplifying. Tracks the last-reviewed version so each run only covers the delta.
---

# agterm-check-update

## Overview

After the user updates agterm, review the upstream changelog for the **delta** since
the configs were last validated, then check whether their custom customizations can
adopt new built-ins, drop now-redundant scripts, or need fixes for renamed/removed
commands. On finish, record the validated version so the next run starts from there.

The last-validated version is stored in `~/.config/agterm-projects/agterm-migration`
(one line: `<version>\t<date>`). This is the whole point — never re-review versions
already signed off.

## Flow

1. **Status** — `scripts/agterm-migrate.sh status`. If installed == last validated,
   report "up to date" and stop.
2. **First run only** (no state file): ask the user which version they upgraded FROM,
   and pass it as the baseline in step 3.
3. **Changelog** — `scripts/agterm-migrate.sh changelog [FROM]`. Prints release notes
   for every version newer than the baseline up to the installed one.
4. **Summarize** what changed for the user (features / fixes), grouped by version.
5. **Research the configs** — this is the core of the skill, not an afterthought. For
   each new built-in / token / flag in the delta, actively scan our custom configs and
   scripts (method below), read the overlap candidates, and reach a grounded verdict per
   lens. Propose concrete edits; **do not invent work** where the changelog entry is
   GUI/app-internal — say "nothing needed" plainly.
6. **Apply** the edits the user approves. After editing `keymap.conf`, run
   `agtermctl keymap reload` (0 diagnostics = clean); after `ghostty.conf`, `agtermctl config reload`.
7. **Record** — on finish, `scripts/agterm-migrate.sh record` writes the installed
   version + today's date to the migration file.

## Research method (step 5)

Don't eyeball the changelog and guess. For each new native capability in the delta,
grep the configs + `~/.config/agterm-projects/*.sh` for custom code that reinvents it,
then **read the candidates** before ruling. Signals that caught real overlaps before:

```bash
cd ~/.config/agterm-projects
grep -lE 'tree --json' *.sh                 # hand-parsed tree — does a new native verb replace it?
grep -lnE 'osascript|terminal-notifier' *.sh # manual notify vs `agtermctl notify`
grep -lnE 'kitty|base64|\\033_G' *.sh        # hand-rolled image display vs bundled show-image.sh / `session background image`
grep -lnE 'scratch (on|off|toggle)|session split|quick (show|toggle)' *.sh  # reimplemented pane mgmt
grep -nE 'session (type|status|text|reveal)|--pane|AGT_' ~/.config/agterm/keymap.conf  # tokens/flags to adopt
```

A hit is a *candidate*, not a verdict: open the script. Most hand-parse the tree to
resolve a session by name/slot (no native verb for that) or do more than the built-in
(e.g. `img-view.sh` is a gallery, not one-shot display) — those stay. Only flag a script
that a native verb genuinely replaces or simplifies.

## Review lenses

Read the delta changelog, then check each item against the configs:

1. **New custom-command tokens / flags** → adopt in `keymap.conf` where they simplify a
   custom command. (e.g. `$AGT_PANE` routes `session type`/`session status` back into
   the exact pane a shortcut fired from.)
2. **New built-in `agtermctl` command or GUI action that supersedes a custom script** →
   replace the script with the built-in. (e.g. `session reveal` vs `open $AGT_SESSION_PWD`;
   `dashboard` / `⌘⇧D`; `session go next/prev-attention`.)
3. **Renamed / removed commands or flags** → fix anything in `keymap.conf` or the scripts
   that would now break or silently no-op.
4. **Changed default behavior** of a command we rely on → adjust the config to match.

Not every changelog entry touches config — GUI polish, app-internal fixes, and new
opt-in GUI features usually need no change. Say so plainly rather than inventing work.

## Where to look

- `~/.config/agterm/keymap.conf` — custom commands, chords, `{AGT_*}` tokens (the main surface)
- `~/.config/agterm/ghostty.conf`, `agent-status/`, `restore-denylist.conf`
- `~/.config/agterm-projects/*.sh` — custom scripts (IDE, tmux, hist, games, pickers)

**Verify exact names before editing.** Confirm a new token/command/flag against the
shipped agterm skill's `reference.md` and `examples.md` (`~/.claude/skills/agterm/`) —
that skill is updated from the site and is the source of truth for spelling and semantics.

## Migration file format

```
0.13.0	2026-07-14
```

Single line, tab-separated: validated version, date recorded. `status`/`changelog` read
the version (first field); `record` overwrites the whole line.

## Common mistakes

- **Re-reviewing old versions.** The state file exists so you only cover the delta. Trust it.
- **Forcing a change per changelog line.** Most entries are GUI/internal. Only lens 1–4 hits
  warrant a config edit.
- **Editing configs from memory of a command name.** Confirm against the shipped agterm skill first.
- **Forgetting to record.** If you don't `record` at the end, the next run re-does everything.
