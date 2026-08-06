---
description: Pull in what the base session did since this fork was created
---

Run `skills/btw/scripts/btw-sync.sh` from the repo that holds this skill (or
`~/.claude/skills/btw/scripts/btw-sync.sh` if installed). Read its report, then
read the digest file it names.

If the report says `mode: squeezed` and the user passed `--summarize`, dispatch
a subagent to read the `.full.md` file and return a brief instead of reading the
full text into this conversation.

Then answer the user's side question with that context in hand.

$ARGUMENTS
