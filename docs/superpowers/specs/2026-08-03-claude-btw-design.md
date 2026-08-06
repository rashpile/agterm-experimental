# claude-btw — design spec

**Date:** 2026-08-03
**Status:** design approved, not implemented
**Supersedes:** `commands/claude-btw/design-notes.md` (mid-brainstorm handoff notes)

## Goal

Fork the Claude session running in the current agterm tab so a side question ("btw…") can be asked
in a new tab without derailing the main task. The fork starts with the base conversation's full
history, can later pull in what the base did since the fork, and can push a short answer back into
the base tab's prompt.

## Constraints and verified facts

Checked against the running system on 2026-08-03. Do not re-derive.

**Claude Code**

- `claude --resume <id> --fork-session` forks into a new session id with full history.
  `--fork-session` requires `--resume`/`--continue`.
- `--resume` resolves the id against the **cwd's** project directory, so the forked tab must be
  spawned with the base tab's cwd.
- Transcripts live at `~/.claude/projects/<cwd-slug>/<session-id>.jsonl` and are append-only.
  Session ids are UUIDs and globally unique, so the transcript is found by globbing
  `~/.claude/projects/*/<id>.jsonl` — **never derive the slug from a cwd**.
- Compaction keeps the same session id and the same transcript file: it appends a
  `type:"system"`, `subtype:"compact_boundary"` entry plus an `isCompactSummary` user entry.
  `/clear` was checked live on 2026-08-06: it also kept the same session id and the same
  transcript file (35 lines before, 55 after, same `<id>.jsonl`) — it behaves like compaction for
  our purposes here, not like a fresh start. This is version-dependent and could change on a later
  Claude Code build; do not re-derive from memory, re-check live if it matters. What actually
  mints a new session id, observed the same day, is the Claude process in the pane being replaced
  by a different one (quit and relaunch `claude`).
- Transcript entry types observed in the wild: `assistant`, `user`, `system`, and the non-message
  types `attachment`, `last-prompt`, `mode`, `permission-mode`, `ai-title`,
  `file-history-snapshot`, `file-history-delta`, `queue-operation`, `agent-name`,
  `bridge-session`, `custom-title`, `pr-link`, `frame-link`.
- A user entry's `message.content` is sometimes a plain string, sometimes a block array.
- `isMeta: true` marks skill injections and local-command caveats. `isSidechain: true` marks
  subagent turns.
- A `SessionStart` hook receives the live session id as `session_id` on **stdin JSON**. Hooks
  inherit `AGTERM_SESSION_ID`, `AGTERM_PANE`, `AGTERM_PANE_ID`, `AGTERM_SOCKET`.

**agterm**

- Custom commands receive `$AGT_SESSION_ID`, `$AGT_PANE`, `$AGT_SESSION_PWD`, `$AGT_SESSION_NAME`.
- `$AGT_PANE` and the shell's `$AGTERM_PANE` share one vocabulary: `left` (main) / `right` (split)
  / `scratch`. `$AGTERM_PANE_ID` is a *different* thing — an opaque stable spawn token — and is
  not usable as a join key between the hook side and the hotkey side.
- `agtermctl session type <text>` injects real keystrokes and **turns each newline into Return**.
  A multi-line brief would submit itself line by line. Single-line output is a safety constraint,
  not a style choice.
- `agtermctl session text --lines N` returns a pane's visible screen as plain text.
- `agtermctl session status blocked --blink --auto-reset` is the documented attention signal for a
  finished result; it is persistent state that clears itself when the session is visited. The
  reference explicitly prefers it over `notify` for agentic attention.
- `agtermctl session restore <line>` pins a shell line a pane re-runs on the **next** launch,
  overriding agterm's auto-captured foreground. It is sticky and persists in the window state file
  until cleared, and is readable back as `restoreCommand` in `tree --json`.
- `session new --command` runs **argv-style with no shell**, so shell operators are not interpreted
  and `PATH` is the launchd default without `/opt/homebrew/bin`. It must be wrapped:
  `--command "zsh -lc '…'"`.
- `--json` is a global flag; without it, mutations print `ok`. It is required to read the new
  session id back from `session new`.
- **All restore behaviour is gated on the "Restore running commands on restart" setting** (General
  settings, **off by default**). While it is off, `session restore` succeeds but nothing runs on
  relaunch, and a persisted `--command` does not re-run either. The override also fires only on an
  app-launch restore — not on Reopen Closed Item or reopening a closed window.

## Approved decisions

1. **Trigger is an agterm keybinding**, not a slash command — it must work while base Claude is
   mid-turn. Cost: the firing shell does not see `CLAUDE_CODE_SESSION_ID`, so the base session id
   comes from a registry.
2. **Press behaviour is a bare fork.** A new tab opens beside the base tab running the forked
   Claude with full history, sitting at an empty prompt. No input UI, no placement picker.
3. **Merge payload is a filtered replay** of the base transcript past a watermark.
4. **Answer-back is in v1.** The fork types a one-line brief into the base tab's prompt,
   unsubmitted. The fork is not auto-closed.
5. **Oversize digests are squeezed mechanically**, not summarized by a model, with a report line
   naming what was left out. `--summarize` opts into a subagent summary.
6. **btw-back does not switch tabs.** It raises the base tab's status glyph and leaves you where
   you are.
7. **A fork tab restores to itself** across agterm restarts, not to a plain shell.
8. **Packaged as one skill plus two slash commands**, all shipped in-repo.
9. **Tests cover the digest and the refusal verdicts**; everything agterm-side is a manual
   checklist.

## Components

```
hooks/agterm-tabs/
  register-tab.sh          SessionStart + SessionEnd hook: maintain the tab registry
  README.md                registry file format, for other consumers
  settings-snippet.json    the two hook entries to paste into ~/.claude/settings.json

commands/claude-btw/
  claude-btw.sh            hotkey target: resolve base, record fork meta, spawn the tab
  keymap.conf              chord binding
  tests/
    run.sh                 the whole automated suite
    fixtures/*.jsonl       synthetic transcripts, one per hazard
    golden/*.md            expected digest output
    fake-agtermctl.sh      canned tree/text responses for verdict tests

skills/btw/
  SKILL.md                 how and when to sync and send back
  scripts/lib.sh           shared: resolve fork meta, read registry, compute verdicts
  scripts/btw-digest.sh    transcript slice -> markdown
  scripts/btw-sync.sh      digest + write file + report
  scripts/btw-back.sh      compose one-liner, verdict-check, type into base
  commands/btw-sync.md     slash command wrapper
  commands/btw-back.md     slash command wrapper
```

**Packaging.** One skill holds the content and the shared code; two slash commands make firing
deterministic rather than depending on model trigger-matching. Both command files ship in-repo
under `skills/btw/commands/`, so nothing about btw lives outside this repository — installation
copies or symlinks them into `~/.claude/commands/`, the same way the hook's settings snippet is
pasted rather than auto-installed.

`hooks/agterm-tabs/` is a new top-level directory. The registry is a general facility — "which
Claude runs in which agterm tab and pane" — not a btw implementation detail, so it does not live
under `commands/claude-btw/`. `claude-btw` is its first consumer, not its owner. It is also not an
agterm command, which is what this repo's `commands/` means.

### Environment overrides

Every script reads its roots from the environment so tests can redirect them:

| Variable | Default |
|---|---|
| `AGTERM_TABS_DIR` | `~/.claude/agterm/tabs` |
| `BTW_ROOT` | `~/.claude/btw` |
| `AGTERMCTL` | `/opt/homebrew/bin/agtermctl` |

## The registry

One file per Claude-bearing pane:

```
~/.claude/agterm/tabs/<AGTERM_SESSION_ID>.<AGTERM_PANE>
```

`<AGTERM_PANE>` is `left` / `right` / `scratch`, which is what both the hook and the hotkey can
see. Contents:

```json
{
  "claude_session_id": "7a3f…",
  "agterm_session_id": "b41c…",
  "pane": "left",
  "pane_id": "…",
  "cwd": "/Users/pkoptilin/workspace/repos/foo",
  "updated_at": "2026-08-03T13:58:00Z"
}
```

`pane_id` is recorded but not used as a key — it is there for future consumers that need to
survive a promote/re-split, which changes a pane's role.

### `register-tab.sh`

Installed as a plain Claude hook on `SessionStart` (matcher `""`) and `SessionEnd` (matcher `""`).
The repo ships the settings snippet; wrapping it in a hook manager is the user's business.

**On SessionStart:**

1. Exit 0 immediately if `$AGTERM_SESSION_ID` is unset — outside agterm this costs nothing.
2. Read `session_id` from stdin JSON. If empty, exit 0 without writing. Never write a partial
   record.
3. Write the registry file for `$AGTERM_SESSION_ID.$AGTERM_PANE`.
4. If `$BTW_ROOT/forks/$AGTERM_SESSION_ID/meta.json` exists — i.e. this session is a fork — pin
   `agtermctl session restore "claude --resume <session_id>" --target "$AGTERM_SESSION_ID"
   --pane-id "$AGTERM_PANE_ID"`.

Step 4 is gated on being a fork so that non-fork sessions keep whatever restore behaviour they
have today. It must run on **every** start, because a restored fork gets a new session id each
time; a set-once pin would go stale immediately. Without it, agterm's auto-capture would restore
the literal `claude --resume <base> --fork-session` line and re-fork the base on every launch.

**This whole step is conditional on the user's "Restore running commands on restart" setting.**
With it off (the default) nothing is restored at all: the re-fork bug does not exist, the pin is
inert, and a fork tab comes back as a plain shell. With it on, the pin is what stands between a
restart and a silently re-forked base. So the pin is harmless when the setting is off and
essential when it is on — write it unconditionally and let the setting decide.

**On SessionEnd:** delete the registry file for this session and pane.

## Fork flow

`claude-btw.sh "$AGT_SESSION_ID" "$AGT_PANE" "$AGT_SESSION_PWD" "$AGT_SESSION_NAME"`

1. **Prune** registry files whose agterm session id is absent from `agtermctl tree --json`.
2. **Resolve.** Read `$AGTERM_TABS_DIR/<sid>.<pane>`. Missing → `notify` "no Claude registered in
   this pane", exit 0. Nothing else happens.
3. **Locate the transcript** by globbing `~/.claude/projects/*/<claude_session_id>.jsonl`. Missing
   → `notify`, exit non-zero, do not spawn. A tab that opens onto a `--resume` error is worse than
   no tab.
4. **Watermark.** Record the transcript's current line count.
5. **Spawn.**
   `agtermctl session new --cwd <base cwd> --after <sid> --name "btw: <base name>" --command
   "zsh -lc 'claude --resume <base id> --fork-session'" --json`, capturing the new agterm session
   id.
6. **Record** `$BTW_ROOT/forks/<new-agterm-sid>/meta.json`:

```json
{
  "base_claude_id": "7a3f…",
  "base_agterm_sid": "b41c…",
  "base_pane": "left",
  "base_cwd": "/Users/pkoptilin/workspace/repos/foo",
  "transcript": "/Users/pkoptilin/.claude/projects/…/7a3f….jsonl",
  "watermark": 412,
  "forked_at": "2026-08-03T14:02:11Z",
  "sync_count": 0,
  "synced_at": null
}
```

7. The user lands in the new tab with full history and an empty prompt.

The fork never needs to know its own Claude session id. Both skill scripts identify themselves by
`$AGTERM_SESSION_ID`, which the Bash tool inherits.

`--dry-run` prints the `session new` line instead of running it.

**Chord:** `cmd+shift+b`, subject to change if it collides with an existing binding.

**Known race, accepted:** lines appended to the base transcript between step 4 and step 5 land
after the watermark but before the fork, so the first sync replays context the fork already has.
Harmless duplication; not worth a lock.

## `btw-sync`

Pull in what the base tab did since the fork.

The script never prints the payload to stdout. It writes a digest file and prints a short report,
so an oversize sync cannot flood the fork's context by accident.

### Resolution

1. `$AGTERM_SESSION_ID` → `$BTW_ROOT/forks/<sid>/meta.json`. Missing → "this tab is not a btw
   fork", exit non-zero.
2. Read the registry for `base_agterm_sid.base_pane` → the Claude id running there **now**.
3. **Same id** → read `transcript` from `watermark` to EOF.
4. **Different id** → the Claude session in the base pane has been replaced by a different one
   (observed trigger: quit and relaunch `claude` in that pane — **not** `/clear`, which was
   checked live and keeps the same id; see Constraints). Read the old transcript from `watermark`
   to EOF, then the new one from line 1 to EOF, with
   `--- base session was reset here (/clear or restart) ---` between them. Rewrite `transcript`
   and `base_claude_id` in meta.
5. **Registry entry missing** → the base tab is gone. The transcript is still on disk, so sync
   from the recorded path and report `base: gone`.

### Filter

Applied by `btw-digest.sh` as a jq program over the line slice:

- Keep only `.type == "user"` and `.type == "assistant"`. Drop every other type outright.
- Drop `isSidechain == true` (subagent turns) and `isMeta == true` (skill injections, local
  command caveats).
- Content blocks: keep `text`. Drop `thinking`, `tool_use`, `tool_result`, `image`.
- A user entry whose blocks are all `tool_result` disappears entirely.
- Handle `message.content` as either a plain string or a block array.
- Strip `<system-reminder>…</system-reminder>` and `<local-command-stdout>…</local-command-stdout>`
  from kept text.
- `isCompactSummary` entries and `compact_boundary` become a single
  `--- base compacted here ---` marker line, not content.
- User text is kept verbatim — it is short and it is the actual intent.
- Assistant text is truncated per message to 1200 characters with an `…[+N chars]` suffix.

### Size guard

Threshold: **24 KB** of rendered digest (roughly 6k tokens).

Under it, the digest is verbatim. Over it, the script re-renders: the last **20 turns** keep the
1200-character truncation, and everything earlier collapses to one line per message — first
sentence, 160 characters max — under an `## earlier (collapsed)` heading. The report always names
what was collapsed.

`--summarize` instead dispatches a subagent to read the full digest file and return a brief, so
the raw text never enters the fork's context. This is opt-in only; the mechanical path is the
default because it is instant, deterministic, and testable.

### Output

`$BTW_ROOT/forks/<sid>/sync-NNN.md`:

```markdown
# Base session activity — sync 001
base: 7a3f… (agterm tab "top", pane left)
range: lines 412–587
captured: 2026-08-03T14:22:10Z

## user
Let's refactor the auth middleware…

## claude
I'll start by reading the middleware…
…[+2140 chars]
```

Report on stdout, at most six lines:

```
btw-sync: 24 new messages from base (lines 412–587)
mode: squeezed (86 KB → 23 KB; 41 older turns collapsed)
digest: ~/.claude/btw/forks/<sid>/sync-001.md (23.1 KB)
base: alive (tab "top")
```

Nothing new → `nothing new since <time>`, exit 0, no file written.

`watermark` advances only after the digest is written. `sync_count` and `synced_at` update.

**Sync is always explicit.** It never runs automatically on fork or on the fork's first message.
The bare fork stays bare.

## `btw-back`

Push the fork's answer into the base tab's prompt.

**Two artifacts.** The fork's Claude writes the full answer to
`$BTW_ROOT/forks/<sid>/answer-NNN.md` — markdown, no length cap, whatever detail the answer
deserves. A single line goes into the base prompt:

```
btw: <one sentence>. Full notes: ~/.claude/btw/forks/<sid>/answer-001.md
```

The line addresses base Claude, because pressing Enter makes it a user message there. Total length
is capped at **400 characters**; the script collapses any run of newlines to ` · ` and truncates
the summary — never the path — so a newline can never reach `session type` and the path always
survives as the fallback for whatever the line could not carry.

`answer-NNN.md` increments, so btw-back can run more than once from a fork.

### Verdicts

Only a specific positive verdict licenses typing.

| Check | Verdict | Action |
|---|---|---|
| `tree --json` contains `base_agterm_sid` | `gone` if absent | refuse |
| registry has `<sid>.<pane>` | `ok` | proceed |
| exact pane key missing, exactly one entry for that sid | `moved` | proceed, note in report |
| exact pane key missing, two or more entries for that sid | `ambiguous` | refuse |
| `session text --lines 6` shows a non-empty prompt box | `busy-input` | refuse |

`moved` exists because a promote/re-split changes a pane's role, so the recorded `left`/`right`
can go stale until the next SessionStart rewrites it.

`busy-input` matters because `session type` **appends** to whatever is already in the box.
Injecting into half-typed text would mangle it.

Every refusal keeps the answer file, prints its path, and exits non-zero. The work is never lost;
it just does not get injected.

### On success

`agtermctl session status blocked --blink --auto-reset --target <base sid> --pane <base pane>`.

btw-back does **not** switch tabs. The base tab may be mid-turn, and pulling the user into a
running session to look at a prompt they cannot submit yet is worse than a glyph that waits. The
fork is not closed and not renamed.

`--dry-run` prints the verdict and the `session type` line instead of running it.

## Failure modes

| Situation | Behaviour |
|---|---|
| Hotkey in a pane with no Claude | `notify`, exit 0, nothing spawns |
| Base transcript missing at fork time | `notify`, exit non-zero, do not spawn |
| `jq` or `agtermctl` missing | loud fail, `notify`, exit non-zero |
| Claude exited, registry file left behind | `SessionEnd` deletes it; `claude-btw` also prunes entries whose agterm sid is absent from `tree` |
| Base tab closed while the fork lives | sync degrades to the recorded transcript and reports `base: gone`; btw-back refuses |
| Base session replaced (quit + relaunch `claude`) | sync chains across with a reset marker |
| Base `/clear`ed | same id, same transcript (checked live 2026-08-06, version-dependent) — sync just picks up the new turns, no reset marker |
| Base compacted | same file, same id — one marker line |
| Fork of a fork | allowed, no special case; the fork becomes the base, meta chains one level |
| Two forks off one base | separate agterm sids, separate meta directories; duplicate tab names are fine |
| Base mid-turn at fork time | fine — the transcript is append-only, the watermark is a line count |

Both stale-registry defences are kept. Forking a genuinely dead session is harmless — `--resume`
works on ended sessions — but forking the session that *used to* occupy a reused pane is not, and
that is the case the `SessionEnd` delete prevents.

## Testing

`commands/claude-btw/tests/run.sh` — run by hand, exits non-zero on failure, plain zsh with no
`bats` dependency, matching the repo's zero-dependency style. It never talks to agterm or Claude.

**Digest golden files.** `btw-digest.sh` is a pure function of `(jsonl, from-line, mode)`. One
synthetic fixture per hazard, each a handful of lines, diffed against a known-correct output:

- user content as a bare string vs. a block array
- a user entry that is only `tool_result` (must vanish entirely)
- `isMeta` skill injection
- `isSidechain` subagent turn
- `thinking` blocks
- `compact_boundary` + `isCompactSummary` (must become one marker line)
- the non-message noise types
- an oversize case that trips the 24 KB squeeze, asserting both the collapsed output and the
  report's "N older turns collapsed" line

Fixtures are **synthetic and hand-authored** — never captured from real transcripts, which carry
real project paths and content.

**Verdict table tests.** Each verdict is a pure function of `(meta.json, registry dir, tree json,
pane text)`. The suite builds a temporary state directory, points `BTW_ROOT`, `AGTERM_TABS_DIR`
and `AGTERMCTL` at it and at `fake-agtermctl.sh`, and asserts `btw-back.sh --dry-run` reports
`ok`, `moved`, `ambiguous`, `gone`, and `busy-input` in the right conditions. This is the test
that stops btw-back typing into the wrong place.

**Manual smoke checklist**, run once by hand:

1. Fork from a live session — new tab has full history, empty prompt
2. Sync after base activity — digest contains the base's turns, no tool noise
3. Send an answer back — one line lands in the base prompt, unsubmitted, glyph lights
4. Restart agterm — the fork tab comes back as itself, the base is not re-forked
5. Close the base tab, then sync and send back — degrades and refuses respectively
6. Quit and relaunch `claude` in the base, then sync — reset marker present, new turns included
   (`/clear` alone does not exercise this — see Constraints and the failure-modes table)

## Out of scope

- No input UI or placement picker on the hotkey — bare fork only.
- No auto-close of the fork.
- No auto-sync.
- No `--go` flag on btw-back to follow into the base tab.
- No live end-to-end test that spawns real agterm sessions — slow, flaky, and it leaves stray tabs
  behind when it fails.
- No disambiguation of duplicate `btw: <name>` tab names.
- No cleanup of old fork state directories under `$BTW_ROOT/forks/`.

## Documentation

Add a `### claude-btw` section to the repo README's Commands section, and a `hooks/` row to the
Contents table.

## Open risks

- `cmd+shift+b` may collide with an existing binding; verify before committing to it.
- Fork-tab restore only works if "Restore running commands on restart" is on, which it is not by
  default. Decision 7 is therefore best-effort: with the setting off, a fork tab comes back empty
  and there is nothing the design can do about it short of asking the user to flip a global agterm
  setting, which is out of scope here.
- The transcript JSONL format is Anthropic's and can change without notice. The golden-file tests
  are the early-warning system for exactly this.
- The 24 KB threshold and the 20-turn tail are guesses. They are one-line constants; tune after
  living with them.
