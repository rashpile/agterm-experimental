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
ambiguous, and otherwise sends the composed line into the base tab's prompt
without submitting it. Whether it clears the prompt first depends on whether
the base looks idle: `session type` appends rather than replacing, so typing
in without clearing risks mangling whatever's already there — but the only
reliable way to fully clear the box is Ctrl+C (Ctrl+U only kills one visual
row at a time, so a wrapped line survives it), and Ctrl+C also interrupts a
running turn. So: Ctrl+C and clear only when the base is idle; append without
clearing when it's busy — costing about 1.3s to sample the pane twice and
check for animation. Whatever was in the box beforehand is printed either
way, so a half-typed thought stays recoverable by copy-paste even on the
append path. Report which path was taken — if it appended, say so plainly,
since the user's prompt now has two things in it. On refusal, tell the user
where the answer file was kept.

$ARGUMENTS
