# agterm-tabs: the tab registry

A Claude Code hook that answers one question a keybinding-driven shell command
can't answer on its own: *which Claude session is running in this agterm tab
and pane?*

A keybinding fires a shell command directly — it never goes through Claude,
so it has no way to know the Claude session id running in the pane it was
fired from. This hook runs as a Claude `SessionStart` / `SessionEnd` hook,
inside the Claude session itself, and writes that fact down to a small file
registry that any later shell command (bound to a key or not) can read.

The registry is a generic, standalone concept — not specific to any one
consumer. It lives under its own top-level `hooks/agterm-tabs/` rather than
inside a particular tool's directory. `claude-btw` (see `commands/claude-btw/`
and `skills/btw/`) is its first consumer, reading it via `btw_registry()` in
`skills/btw/scripts/lib.sh`, but nothing about the registry format is
btw-specific.

## The registry

One file per Claude-bearing pane, at:

```
$AGTERM_TABS_DIR/<agterm-session-id>.<pane>
```

(`AGTERM_TABS_DIR` defaults to `~/.claude/agterm/tabs`.)

Contents:

```json
{
  "claude_session_id": "7a3f...",
  "agterm_session_id": "b41c...",
  "pane": "left",
  "pane_id": "...",
  "cwd": "/Users/you/workspace/repos/foo",
  "updated_at": "2026-08-05T13:58:00Z"
}
```

`pane` is one of `left` / `right` / `scratch`. This is the same vocabulary
used by the shell's `$AGTERM_PANE` (what this hook reads to build the
filename) and by a custom agterm command's `$AGT_PANE` — the two things that
need to find this file by name. The registry key is `<agterm-session-id>.
<pane>`, not `pane_id`.

`pane_id` (from `$AGTERM_PANE_ID`) is recorded in the file body but is
deliberately **not** part of the key. It's a different, opaque spawn token
that a custom command invoked by a keybinding cannot see, so it can't be used
to look the record up. It's stored for future consumers that need to survive
a pane promote/re-split, which changes a pane's role (and therefore its
`pane` value) without changing its `pane_id`.

## Lifecycle

- **SessionStart**: writes/overwrites the record for
  `$AGTERM_SESSION_ID.$AGTERM_PANE`. Exits without writing if `$AGTERM_SESSION_ID`
  is unset (i.e. outside agterm), if `jq` isn't on `PATH`, or if the hook's
  stdin JSON has an empty or missing `session_id` — a partial record (no
  Claude session id) is never written.
- **SessionEnd**: deletes the record for `$AGTERM_SESSION_ID.$AGTERM_PANE`,
  but only if the ending session's `session_id` still matches the
  `claude_session_id` currently on disk. On `/clear`, the outgoing session's
  `SessionEnd` hook and the incoming session's `SessionStart` hook can run in
  either order; comparing ids means an ending hook never deletes a record a
  newer session already wrote.

Because entries are removed on `SessionEnd`, in the common case the registry
only ever holds live sessions. But **consumers should still tolerate
staleness** — a killed terminal, a crashed Claude process, or any path that
skips the hook (network loss, `kill -9`, etc.) can leave a record behind with
no live session backing it. Treat a hit as "was here as of `updated_at`", not
as a live-session guarantee.

## Fork-restore pin

If the session is a fork of another (`$BTW_ROOT/forks/$AGTERM_SESSION_ID/meta.json`
exists), `SessionStart` also calls:

```
agtermctl session restore "claude --resume <session_id>" --target "$AGTERM_SESSION_ID" [--pane-id "$AGTERM_PANE_ID"]
```

This pins what agterm's "Restore running commands on restart" feature will
replay for this tab. Without it, that feature would capture and replay the
literal command line that launched the fork — `claude --resume <base>
--fork-session` — which re-forks the base session again on every restart.
The pin is rewritten on *every* `SessionStart`, not just once, because a
restored fork gets a brand-new Claude session id each time it's resumed; a
pin set once would go stale on the very next restart. This step is a no-op
if `agtermctl` isn't found at `$AGTERMCTL` (default
`/opt/homebrew/bin/agtermctl`) or isn't executable, and it does nothing
observable unless the user has that agterm setting turned on. Non-fork
sessions are untouched — they keep whatever restore behavior they already
had.

## Install

1. Copy this directory into place:

   ```
   cp -r hooks/agterm-tabs ~/.claude/hooks/agterm-tabs
   ```

2. Merge `settings-snippet.json` into `~/.claude/settings.json`, under the
   top-level `"hooks"` key (merge `SessionStart` / `SessionEnd` arrays with
   anything already there rather than overwriting them).

3. In the merged snippet, replace `<HOME>` with your real home directory
   path (e.g. `/Users/you`). Claude Code does not expand `~` or `$HOME` in
   hook `command` strings, so the path must be literal.

## Environment

| Variable | Default | Purpose |
|---|---|---|
| `AGTERM_TABS_DIR` | `~/.claude/agterm/tabs` | where registry files live |
| `BTW_ROOT` | `~/.claude/btw` | where fork metadata (`forks/<sid>/meta.json`) is checked, to decide whether to set the restore pin |
| `AGTERMCTL` | `/opt/homebrew/bin/agtermctl` | the agterm control CLI, used only for the restore pin |

All three are read from the environment (never hardcoded) so tests and
alternate installs can redirect them.
