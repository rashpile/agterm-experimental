---
description: Send this fork's answer back to the base tab's prompt
---

Compose the answer to the side question as markdown, and a single summarising
sentence. Then run:

```bash
skills/btw/scripts/btw-back.sh --line "<one sentence>" <<'EOF'
<the full answer as markdown>
EOF
```

The script writes the answer to a file, refuses if the base tab is gone,
ambiguous, or has half-typed text in its prompt, and otherwise types one line
into the base prompt without submitting it. Report what it printed. On refusal,
tell the user where the answer file was kept.

$ARGUMENTS
