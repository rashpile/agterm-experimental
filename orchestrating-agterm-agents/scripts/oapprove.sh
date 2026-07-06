#!/bin/bash
# oapprove.sh — answer the orchestrator's live permission prompt from a Telegram button.
# Maps an approval choice to the right keystroke and injects it (raw, no status-gating) into the
# registered orchestrator agterm session. Called by the pako-telegram bot's approve: callback handler.
#
#   oapprove.sh yes|always|no
#     yes    → Enter        accept the highlighted default (option 1 "Yes")
#     always → Down, Enter  option 2 ("Yes, and don't ask again")
#     no     → Esc          cancel/deny — robust regardless of how many options the menu has
#
# Exit: 0 sent · 2 bad/no choice · 3 not registered · 4 session gone.
set -uo pipefail
export PATH="/opt/homebrew/bin:$PATH"
AGT=/opt/homebrew/bin/agtermctl
JQ="$(command -v jq || true)"   # resolves /usr/bin/jq or /opt/homebrew/bin/jq — don't hardcode

regfile="$HOME/.config/orchestrating-agterm-agents/orchestrator_session"
target="$([ -r "$regfile" ] && tr -d '[:space:]' < "$regfile")"
[ -n "$target" ] || { echo "orchestrator not registered"; exit 3; }

case "${1:-}" in
  yes)    keys=$'\r' ;;
  always) keys=$'\x1b[B\r' ;;
  no)     keys=$'\x1b' ;;
  *) echo "usage: oapprove.sh yes|always|no"; exit 2 ;;
esac

# Bail loudly if the registered session has since closed, rather than injecting into the void.
if [ -n "$JQ" ] && ! "$AGT" tree --json | "$JQ" -e --arg id "$target" \
    '.result.tree.workspaces[].sessions[] | select(.id==$id)' >/dev/null 2>&1; then
  echo "orchestrator session $target gone"; exit 4
fi

printf '%s' "$keys" | "$AGT" session type --stdin --target "$target"

# We just answered the orchestrator's prompt, so clear its stale "blocked" attention glyph.
"$(dirname "$0")/agterm-clear-status.sh" "$target" 2>/dev/null || true

echo "approval '$1' sent to orchestrator"
