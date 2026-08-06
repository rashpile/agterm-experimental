---
description: Send this fork's answer back to the base tab's prompt
---

Compose the answer to the side question as markdown, and a single summarising
sentence. Then run:

```bash
~/.claude/skills/btw/scripts/btw-back.sh --line "<one sentence>" <<'EOF'
<the full answer as markdown>
EOF
```

(If running from a checkout of this repo, `claude-btw/skills/btw/scripts/btw-back.sh` also works. A fork
inherits the *base tab's* cwd — the user's project, never this repo — so the repo-relative path
is not the general case.)

The script writes the answer to a file, refuses if the base tab is gone or
ambiguous, and otherwise clears the base tab's prompt (Ctrl+U — `session type`
appends rather than replacing, and an empty box can't be reliably told apart
from Claude's dimmed "ghost" suggestion, so it clears unconditionally rather
than guessing) before typing one line into it without submitting it, and
raises the base tab's sidebar glyph (it clears itself the next time the user
visits that tab). Whatever was in the box before clearing is printed too, so
a half-typed thought is recoverable by copy-paste. Report what it printed. On
refusal, tell the user where the answer file was kept.

$ARGUMENTS
