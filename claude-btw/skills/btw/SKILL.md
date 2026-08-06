---
name: btw
description: Use when working in a btw fork tab — pull in what the base session did since the fork (btw-sync), or send a short answer back to the base tab's prompt (btw-back). Triggers - btw, btw sync, btw back, catch up on the base session, send this back, what did the main task do, fork sync.
---

# btw

## What a fork is

A btw fork is a new agterm tab created by pressing `cmd+shift+a` in another tab: it forks the
Claude session running in that *base* tab (`claude --resume <base-id> --fork-session`), so it
opens beside the base tab with the base conversation's full history already loaded and an empty
prompt. This session's own agterm tab id is `$AGTERM_SESSION_ID`. Its relationship to the base —
the base's Claude session id, its agterm tab and pane, its transcript path, and a watermark line
number — lives at `$BTW_ROOT/forks/$AGTERM_SESSION_ID/meta.json` (`BTW_ROOT` defaults to
`~/.claude/btw`). Both scripts below read that file; nothing else identifies which base tab a
fork belongs to.

## btw-sync — pull in base activity

Run `~/.claude/skills/btw/scripts/btw-sync.sh` (no arguments) — a fork inherits the *base tab's*
cwd, which is the user's project, never this repo, so the repo-relative path only resolves when
you happen to be running from a checkout of this repo itself (then `claude-btw/skills/btw/scripts/btw-sync.sh`
also works). It resolves the base tab from `meta.json`, reads the base's transcript forward from
the recorded watermark, and writes two files under `$BTW_ROOT/forks/$AGTERM_SESSION_ID/`:
`sync-NNN.md` (the digest to read) and `sync-NNN.full.md` (the uncollapsed rendering, always
written regardless of size).

The script's stdout is a short **report**, never the transcript body — that's deliberate: an
oversized sync must not flood this conversation's context by accident. Read the report first:

```
btw-sync: 6 new messages from base
mode: squeezed (14 older messages collapsed; full text in sync-004.full.md)
digest: /Users/you/.claude/btw/forks/<sid>/sync-004.md (876 bytes)
base: alive
next: read the digest file
```

Only after reading the report should you read the file it names (`digest:`, usually `sync-NNN.md`).
Do **not** cat the digest into the conversation blindly. If there is nothing new since the fork or
last sync, the script just prints `btw-sync: nothing new since <timestamp>` and writes no file.

- `mode: verbatim` — `.md` and `.full.md` are the same content, every new message rendered in
  full.
- `mode: squeezed` — the verbatim rendering exceeded the size threshold (24 KB by default,
  `BTW_SQUEEZE_BYTES`) **and** there are more than `BTW_TAIL_MSGS` (20 by default) messages to
  render. Both conditions must hold: a handful of very large messages under that count stays
  `verbatim` even when oversized. When squeezed, `.md` collapses everything except the most recent
  `BTW_TAIL_MSGS` messages into one-line summaries and keeps only that tail in full. `.full.md` is
  unaffected by squeezing — it always holds every message, in both modes. It is *uncollapsed*, not
  literally complete: every assistant message, in `.full.md` too, is still individually truncated
  past `BTW_ASSISTANT_CHARS` (1200 chars by default) with a `…[+N chars]` marker.
- `base:` reports what happened to the base session since the fork or last sync — `alive` (same
  session, nothing unusual), `reset` (the base was `/clear`ed or restarted; the digest chains the
  old tail onto the new session's activity with a `--- base session was reset here (/clear or
  restart) ---` marker), or `gone` (the base tab or its Claude session is no longer in the
  registry; the transcript on disk is still readable, so the digest is still produced).

**`--summarize`.** If the user asked for a summary and the report says `mode: squeezed`, do not
read the (possibly large) `.full.md` into this conversation's own context. Dispatch a **subagent**
whose only job is to read `.full.md` and return a brief. That is what `--summarize` means: it
moves the cost of reading the complete text out of the fork's context and into a disposable
subagent — which is exactly why `btw-sync` always writes the full rendering even when the digest
handed to you is squeezed. Without that full file, `--summarize` would have nothing complete to
read.

## btw-back — send the answer to the base prompt

Compose the answer to the side question as markdown, plus one short sentence that summarizes it.
Write the answer body to stdin via a heredoc and pass the sentence as `--line`:

```bash
~/.claude/skills/btw/scripts/btw-back.sh --line "Auth validates JWTs in Gin middleware, not per-handler" <<'EOF'
## Where auth happens

`internal/api/middleware.go:41` installs a Gin middleware that validates the
JWT once per request...
EOF
```

(If running from a checkout of this repo, `claude-btw/skills/btw/scripts/btw-back.sh` also works — but a
fork inherits the *base tab's* cwd, the user's project, never this repo, so the installed path
is the general case.)

The script always writes the full answer to `$BTW_ROOT/forks/$AGTERM_SESSION_ID/answer-NNN.md`
**first** — so the answer is never lost, even on a refusal. It then checks whether the base tab can
be resolved at all (the verdicts below); if it can, it **clears the base tab's prompt** (sends
Ctrl+U) and then composes the `--line` sentence into a single capped-length line and types just
that one line into the now-empty box with `agtermctl session type`. It does **not** press Enter —
the user reviews and submits it — and it does **not** switch you to the base tab; you stay in the
fork.

The clear-first step is unconditional, not a safety check that can be skipped. `agtermctl session
type` **appends** to whatever is already in the box, so typing in without clearing would mangle any
half-typed text that's there. The obvious alternative — read the box first and only clear it if it
looks occupied — doesn't work: Claude Code renders a dimmed "ghost" suggestion in a genuinely empty
input box, `agtermctl session text` returns plain text with ANSI (and therefore the dimming)
already stripped, and a ghost suggestion is textually indistinguishable from something the user
actually typed. There is no reliable way to read the box and tell "empty" from "occupied," so
`btw-back` doesn't try — it clears every time. To bound the cost of that: right before clearing, it
reads whatever was in the box (typed text or ghost, it can't tell which) and prints it, e.g. `btw-
back: cleared base prompt (was: what does update-agterm do?)`, so a half-typed thought is
recoverable by copy-paste from `btw-back`'s own output even though the box itself is wiped. On
success it also raises the base tab's sidebar glyph to a blinking "blocked" indicator, so the user
notices there's something to look at even if they're not watching the fork; it clears itself
automatically the next time they visit that tab (`--auto-reset`), so nothing needs to be done to
dismiss it. On refusal, the answer file is kept — tell the user its path.

### Refusal verdicts

| Verdict | Meaning | What the user should do |
|---|---|---|
| `gone` | Either the base tab's agterm session no longer exists (closed, or agterm restarted without "Restore running commands on restart"), or the tab is still there but no Claude session is currently registered in *any* of its panes. | Nothing to send to. The answer is saved at the printed path; there is no live tab to receive it. |
| `ambiguous` | The base agterm session still exists, but more than one pane in it currently has a Claude session registered (e.g. after a re-split), so which pane was the original base pane can't be determined. | The user picks the right pane by hand, or re-forks. |

Anything other than these two verdicts (i.e. `ok`, or `moved` after re-resolving the pane) means: the agterm tab exists and a registry entry names a pane in it — not that a Claude process is actually alive and listening there. A crashed session leaves a stale registry entry behind, and `btw-back` clears and types into it just the same. This is bounded — `btw-back` never presses Enter, so nothing is ever submitted on the user's behalf — and accepted as designed, but don't be surprised if the line (and a Ctrl+U) lands in a dead tab's shell instead of a live Claude prompt.

## What not to do

- Don't run `btw-sync` unprompted on the fork's very first message — wait until the user actually
  asks for base context, or it's clearly needed for the side question.
- Don't close the fork tab — that's the user's call, not yours.
- Don't switch to the base tab yourself — `btw-back` deliberately leaves that to the user.
