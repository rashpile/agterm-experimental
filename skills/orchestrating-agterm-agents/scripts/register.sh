#!/bin/bash
# register.sh — record an agterm session as the orchestrator that Telegram (/o /os /oa) reaches.
# "Newer wins": plain overwrite.
#
#   register.sh              # bind THIS session — $AGTERM_SESSION_ID (skill activation, a shell)
#   register.sh <session-id> # bind <session-id>  (the cmd+shift+r chord, and ostart.sh)
#
# The argument exists for the keymap: a custom command runs under /bin/sh from the app, NOT inside the
# session's shell, so it has no $AGTERM_SESSION_ID — only the runner's $AGT_SESSION_ID. Same reason its
# stdout goes nowhere, which is why the status badge below IS the feedback.
#
# The badge is deliberately the ONLY mark: it is transient (Claude's lifecycle hooks own the glyph, and
# a keystroke in the pane clears it), so it acks the press but does not persist. A durable mark would
# have to be a background tint/watermark, which was tried and rejected — it darkens the whole session.
# To ask which session currently owns Telegram, read orchestrator_session.
#
# Exit: 0 registered · 1 no session id (nothing written).
set -uo pipefail
export PATH="/opt/homebrew/bin:$PATH"
AGT=/opt/homebrew/bin/agtermctl

CYAN='#00d7d7'

cfgdir="$HOME/.config/orchestrating-agterm-agents"
sid="${1:-${AGTERM_SESSION_ID:-}}"

if [ -z "$sid" ]; then
  echo "register.sh: no session id — pass one, or run inside an agterm session (\$AGTERM_SESSION_ID)." >&2
  exit 1
fi

mkdir -p "$cfgdir"
printf '%s\n' "$sid" > "$cfgdir/orchestrator_session"
chmod 600 "$cfgdir/orchestrator_session"

# Cyan one-shot badge: "the chord fired, this session now owns Telegram". --auto-reset clears it back
# to idle on the next visit, so it never lingers as a stale signal.
"$AGT" session status active --color "$CYAN" --auto-reset --target "$sid" >/dev/null 2>&1

echo "registered orchestrator session: $sid"
