# claude-btw — design notes (handoff)

> **Superseded.** The brainstorm is finished. The full design lives in
> [`docs/superpowers/specs/2026-08-03-claude-btw-design.md`](../../docs/superpowers/specs/2026-08-03-claude-btw-design.md),
> and its implementation plan — now executed — lives in
> [`docs/superpowers/plans/2026-08-05-claude-btw.md`](../../docs/superpowers/plans/2026-08-05-claude-btw.md).
> These notes are kept only as a record of how the design got there.

Brainstorm started in `~/workspace/ops` on 2026-08-03, continued here.
**Status: complete.** Part 1 was presented and amended; part 2 (btw-sync internals, btw-back,
failure modes, naming, testing) was designed on 2026-08-03 and folded into the spec above.
Next step is an implementation plan.

## Goal

Fork the Claude session running in the current agterm tab so a side question ("btw…") can be
asked in a new tab without derailing the main task. The fork keeps the base session id, can
later pull in what the base session did since the fork, and can push its answer back.

## Decisions made (user-approved)

1. **Trigger: agterm keybinding / custom action**, not a slash command. Rationale: works even
   while base Claude is mid-turn. Cost: the firing shell does NOT see `CLAUDE_CODE_SESSION_ID`,
   so the base session id must come from a registry written by a SessionStart hook.
2. **Press behaviour: bare fork.** New tab opens beside the base tab running the forked Claude
   with full history, sitting at an empty prompt. The user types the side question there. No
   input UI, no placement picker.
3. **Merge payload: filtered replay.** Sync reads the base transcript past a watermark, drops
   `tool_use` / `tool_result` / thinking blocks and sidechain (subagent) entries, keeps user
   messages verbatim and assistant prose (truncated per message). Size-guarded — if still huge,
   fall back to summarizing.
4. **Answer-back included in v1.** A command in the fork writes a short brief and injects it into
   the base tab's input via `agtermctl session type` — unsubmitted, so the user reviews and
   presses Enter. The fork is NOT auto-closed.

## Source location

Lives in `agterm-experimental` (this repo), following its existing conventions:

- `commands/claude-btw/claude-btw.sh` + `commands/claude-btw/keymap.conf` — same shape as
  `commands/quickloc/` and `commands/scratch/` (one folder per command: script + keymap snippet).
- `skills/btw-sync/`, `skills/btw-back/` — alongside `skills/orchestrating-agterm-agents/`.
- The SessionStart hook that maintains the tab registry also lives under this command's folder.
- These notes: `commands/claude-btw/design-notes.md` (this file).

Add a `### claude-btw` section to the repo README's Commands section when the design lands.

## Verified facts (checked, don't re-derive)

- `claude --resume <id> --fork-session` natively forks into a new session id with full history.
  `--fork-session` requires `--resume`/`--continue`.
- `--resume` resolves the id against the **cwd's** project dir, so the forked tab must be spawned
  with the base tab's cwd (`--cwd $AGT_SESSION_PWD`).
- Transcripts: `~/.claude/projects/<cwd-slug>/<session-id>.jsonl`, append-only.
- Claude hooks inherit `AGTERM_SESSION_ID` / `AGTERM_SOCKET` — proven by the existing
  `~/.config/agterm/agent-status/agterm-agent-status.sh` hook, which targets on that var.
- agterm custom commands receive `$AGT_SESSION_ID`, `$AGT_PANE`, `$AGT_SESSION_PWD`,
  `$AGT_SESSION_NAME`.
- `agtermctl session type` lands text at the prompt WITHOUT submitting; single line, no trailing
  newline.
- The Bash tool inside any Claude session inherits `AGTERM_SESSION_ID`, so a skill running in the
  fork can identify its own tab.

## Architecture — part 1 (presented; amended by user)

| Piece | Lives at | Job |
|---|---|---|
| SessionStart hook | `commands/claude-btw/register-tab.sh` | On every session start/resume/clear/compact, write `~/.claude/agterm/tabs/<AGTERM_SESSION_ID>.<AGTERM_PANE_ID>` = current Claude session id + cwd |
| `claude-btw` | `commands/claude-btw/claude-btw.sh` | Hotkey target: resolve base, record fork metadata, spawn the tab |
| `btw-sync` skill | `skills/btw-sync/` | Run in the fork: pull base's new turns in |
| `btw-back` skill | `skills/btw-back/` | Run in the fork: type a brief into base's prompt |
| keymap entry | `~/.config/agterm/keymap.conf` | `command "Fork session (btw)" cmd+shift+b claude-btw "$AGT_SESSION_ID" "$AGT_PANE" "$AGT_SESSION_PWD" "$AGT_SESSION_NAME"` |

**User amendment:** the tab registry is `~/.claude/agterm/tabs/`, NOT `~/.claude/btw/tabs/` — it is
a generic "which Claude session runs in which agterm tab/pane" map, intended for reuse by other
skills and CLIs. btw-specific state (fork metadata) stays under `~/.claude/btw/`.

Registry is keyed by **session + pane** because a tab can hold two panes each running Claude;
`$AGT_PANE` tells `claude-btw` which one fired.

### Fork flow (hotkey press)

1. Read `~/.claude/agterm/tabs/<sid>.<pane>` → base Claude session id + cwd. Missing → notify
   "no Claude registered in this pane" and exit; nothing else happens.
2. Compute base transcript path from cwd slug + id; record the **watermark** = current line count
   of that jsonl.
3. `agtermctl session new --cwd <base pwd> --after <sid> --name "btw:/<base name>" --command
   "zsh -lc 'claude --resume <base-id> --fork-session'"`, capturing the new tab id via `--json`.
4. Write `~/.claude/btw/forks/<new-tab-sid>.json`:
   `{base_claude_id, base_agterm_sid, base_pane, transcript, watermark, forked_at}`.
5. User lands in the new tab with full history and an empty prompt.

The fork never needs its own Claude session id — both skills identify themselves by
`$AGTERM_SESSION_ID`.

## Open — still to design (part 2)

- `btw-sync`: exact filtering/truncation rules, size guard threshold and summarize fallback,
  watermark advance, what happens when base has been compacted or `/clear`ed since the fork
  (session id changes → registry has moved on; fork metadata points at a stale transcript).
- `btw-back`: how the brief is composed and length-capped; single-line constraint of
  `session type`; what if the base tab is gone or its pane changed.
- Failure modes: base tab closed, fork spawned outside agterm, registry stale after restart,
  agterm session restore re-running the `--command` (persisted `initialCommand` would re-fork
  from the same base id on restart — needs a decision).
- Naming of the two commands (`/btw-sync`, `/btw-back`?) and whether they are skills or plugin
  slash commands.
- Testing approach.

## Process

This was started under the `superpowers:brainstorming` skill. Remaining steps of that skill:
finish presenting the design section by section with approval, write the spec to
`docs/superpowers/specs/YYYY-MM-DD-claude-btw-design.md`, self-review it, get user review, then
invoke `superpowers:writing-plans`. Ask one question at a time.
