#!/bin/bash
# register.sh — record THIS agterm session as the orchestrator that Telegram (/o) reaches.
# Run once when the orchestrating-agterm-agents skill activates. "Newer wins": plain overwrite.
#
#   register.sh            # writes $AGTERM_SESSION_ID -> orchestrator_session
#
# Exit: 0 registered · 1 not inside an agterm session (nothing written).
set -uo pipefail

cfgdir="$HOME/.config/orchestrating-agterm-agents"
sid="${AGTERM_SESSION_ID:-}"

if [ -z "$sid" ]; then
  echo "register.sh: \$AGTERM_SESSION_ID is empty — not inside an agterm session; cannot register." >&2
  exit 1
fi

mkdir -p "$cfgdir"
printf '%s\n' "$sid" > "$cfgdir/orchestrator_session"
chmod 600 "$cfgdir/orchestrator_session"
echo "registered orchestrator session: $sid"
