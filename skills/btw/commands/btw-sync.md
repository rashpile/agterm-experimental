---
description: Pull in what the base session did since this fork was created
---

Run `~/.claude/skills/btw/scripts/btw-sync.sh`. (If running from a checkout of
this repo, `skills/btw/scripts/btw-sync.sh` also works — but a fork inherits
the *base tab's* cwd, the user's project, never this repo, so the installed
path is the general case.) Read its report, then read the digest file it
names.

If the report says `mode: squeezed` and the user passed `--summarize`, dispatch
a subagent to read the `.full.md` file and return a brief instead of reading the
full text into this conversation.

Then answer the user's side question with that context in hand.

$ARGUMENTS
