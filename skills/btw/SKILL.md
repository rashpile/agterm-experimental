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

Run `skills/btw/scripts/btw-sync.sh` (no arguments). It resolves the base tab from `meta.json`,
reads the base's transcript forward from the recorded watermark, and writes two files under
`$BTW_ROOT/forks/$AGTERM_SESSION_ID/`: `sync-NNN.md` (the digest to read) and `sync-NNN.full.md`
(the complete rendering, always written in full regardless of size).

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
  `BTW_SQUEEZE_BYTES`), so `.md` collapses everything except the most recent messages (20 by
  default, `BTW_TAIL_MSGS`) into one-line summaries and keeps only that tail in full. `.full.md` is
  unaffected — it always holds the complete, uncollapsed rendering, in both modes.
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
skills/btw/scripts/btw-back.sh --line "Auth validates JWTs in Gin middleware, not per-handler" <<'EOF'
## Where auth happens

`internal/api/middleware.go:41` installs a Gin middleware that validates the
JWT once per request...
EOF
```

The script always writes the full answer to `$BTW_ROOT/forks/$AGTERM_SESSION_ID/answer-NNN.md`
**first** — so the answer is never lost, even on a refusal. It then checks whether the base tab
can safely receive text (the verdicts below); if it can, it composes the `--line` sentence into a
single capped-length line and types just that one line into the base tab's prompt with `agtermctl
session type`. It does **not** press Enter — the user reviews and submits it — and it does **not**
switch you to the base tab; you stay in the fork. On refusal, the answer file is kept — tell the
user its path.

### Refusal verdicts

| Verdict | Meaning | What the user should do |
|---|---|---|
| `gone` | Either the base tab's agterm session no longer exists (closed, or agterm restarted without "Restore running commands on restart"), or the tab is still there but no Claude session is currently registered in *any* of its panes. | Nothing to send to. The answer is saved at the printed path; there is no live tab to receive it. |
| `ambiguous` | The base agterm session still exists, but more than one pane in it currently has a Claude session registered (e.g. after a re-split), so which pane was the original base pane can't be determined. | Not something `--force` fixes. The user picks the right pane by hand, or re-forks. |
| `busy-input` | The base tab was resolved, but its prompt already has text in it. `agtermctl session type` **appends** rather than replacing, so injecting now would mangle whatever is already there. | Clear the base tab's prompt first, or re-run `btw-back.sh` with `--force` — it skips **only** this check, never `gone` or `ambiguous`. |

## What not to do

- Don't run `btw-sync` unprompted on the fork's very first message — wait until the user actually
  asks for base context, or it's clearly needed for the side question.
- Don't close the fork tab — that's the user's call, not yours.
- Don't switch to the base tab yourself — `btw-back` deliberately leaves that to the user.
