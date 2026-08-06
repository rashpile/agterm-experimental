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
  session, nothing unusual), `reset` (the Claude session in the base pane has been replaced by a
  different one — observed trigger: quit and relaunch `claude`, **not** `/clear`, which was
  checked live and keeps the same session id and transcript, same as compaction; the digest
  chains the old tail onto the new session's activity with a `--- base session was reset here
  (/clear or restart) ---` marker), or `gone` (the base tab or its Claude session is no longer in
  the registry; the transcript on disk is still readable, so the digest is still produced).

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
be resolved at all (the verdicts below); if it can, it composes the `--line` sentence into a single
capped-length line and types just that one line into the base prompt with `agtermctl session type`.
It does **not** press Enter — the user reviews and submits it — and it does **not** switch you to
the base tab; you stay in the fork.

Whether the box is **cleared first (Ctrl+C) or the line is appended without clearing** depends on
whether the base looks idle right now. This used to clear unconditionally with Ctrl+U, on the theory
that `agtermctl session type` **appends** rather than replaces, so clearing first sidesteps ever
having to tell an empty box from an occupied one — Claude Code renders a dimmed "ghost" suggestion
in a genuinely empty input box, `agtermctl session text` returns plain text with the ANSI (and
therefore the dimming) already stripped, and a ghost suggestion is textually indistinguishable from
something the user actually typed. That reasoning about *why you can't tell empty from occupied* is
still correct — but live testing against a real Claude session proved Ctrl+U itself doesn't work as
a clearing mechanism: it only kills one *visual row*, not the whole input, so a line that had
wrapped to two rows needed two presses, and a three-row line was never fully cleared no matter how
many spaced presses were tried. Esc does nothing; Ctrl+A then Ctrl+K is worse (kills the first row,
leaves the rest). Only Ctrl+C reliably clears the box completely — but Ctrl+C also **interrupts a
running turn**, and this whole feature exists to work while the base is mid-turn (that's why it's a
keybinding rather than a slash command: it has to work when you can't type into the base at all). So
clearing can't be unconditional either.

The resolution: **clear with Ctrl+C only when the base is idle; append without clearing when it's
busy.** `btw_base_is_idle` in `lib.sh` decides which, from two signals, OR'd together — a running
turn's spinner animates, so two samples of the pane's last lines taken ~1.3s apart differ if busy
and match if idle (primary signal); and a running turn's spinner line has a distinctive live shape —
a gerund plus an ellipsis plus a parenthesised elapsed-timer, e.g. `✻ Enchanting… (4s · ↓ 23
tokens)` — that a finished turn's past-tense line (`✻ Cooked for 11s`) does not have, even though a
busy pane commonly shows both at once (secondary signal, catches what the first can miss). The
check fails **toward busy** on anything else — a read failing, no prompt line found, an
unrecognised pane — because the two outcomes aren't symmetric: a false "busy" only costs an untidy
appended prompt, but a false "idle" sends Ctrl+C into a live turn and destroys work the user was
waiting on. This costs ~1.3s of latency (the two samples), accepted for that reason.

Either way, right before acting, `btw-back` reads whatever was in the box (typed text or ghost, it
can't tell which) and prints it — e.g. `btw-back: base was idle -- cleared prompt (Ctrl+C; was: what
does update-agterm do?) and sent` — so a half-typed thought is recoverable by copy-paste from
`btw-back`'s own output regardless of which path was taken. On the busy path it says so plainly
too — the user's prompt now has two things in it, and they need to know that before they hit enter.
On success it also raises the base tab's sidebar glyph to a blinking "blocked" indicator, so the
user notices there's something to look at even if they're not watching the fork; it clears itself
automatically the next time they visit that tab (`--auto-reset`), so nothing needs to be done to
dismiss it. On refusal, the answer file is kept — tell the user its path.

### Refusal verdicts

| Verdict | Meaning | What the user should do |
|---|---|---|
| `gone` | Either the base tab's agterm session no longer exists (closed, or agterm restarted without "Restore running commands on restart"), or the tab is still there but no Claude session is currently registered in *any* of its panes. | Nothing to send to. The answer is saved at the printed path; there is no live tab to receive it. |
| `ambiguous` | The base agterm session still exists, but more than one pane in it currently has a Claude session registered (e.g. after a re-split), so which pane was the original base pane can't be determined. | The user picks the right pane by hand, or re-forks. |

Anything other than these two verdicts (i.e. `ok`, or `moved` after re-resolving the pane) means: the agterm tab exists and a registry entry names a pane in it — not that a Claude process is actually alive and listening there. A crashed session leaves a stale registry entry behind, and `btw-back` still acts on it — clearing and typing, or appending, per the idle check above, for whatever reads back from that pane. This is bounded — `btw-back` never presses Enter, so nothing is ever submitted on the user's behalf — and accepted as designed, but don't be surprised if the line (and possibly a Ctrl+C) lands in a dead tab's shell instead of a live Claude prompt.

## What not to do

- Don't run `btw-sync` unprompted on the fork's very first message — wait until the user actually
  asks for base context, or it's clearly needed for the side question.
- Don't close the fork tab — that's the user's call, not yours.
- Don't switch to the base tab yourself — `btw-back` deliberately leaves that to the user.
