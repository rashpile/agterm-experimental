#!/bin/bash
# wapprove.sh — answer a WORKER session's live permission prompt from a Telegram button.
# The worker analog of oapprove.sh: instead of the registered orchestrator session, it targets an
# arbitrary worker session whose id rides in the button's callback_data. Called by the pako-telegram
# bot's act:wapprove:<id>:<choice> action callback (see wask.sh for the sender).
#
#   wapprove.sh <session-id>:<choice>      # single arg, exactly as delivered by the act: callback
#     yes    → Enter        accept the highlighted default (option 1 "Yes")
#     always → Down, Enter  option 2 ("Yes, and don't ask again")
#     no     → Esc          cancel/deny — robust regardless of how many options the menu has
#
# Exit: 0 sent · 2 bad/no choice · 4 session gone.
set -uo pipefail
export PATH="/opt/homebrew/bin:$PATH"
AGT=/opt/homebrew/bin/agtermctl
JQ="$(command -v jq || true)"   # resolves /usr/bin/jq or /opt/homebrew/bin/jq — don't hardcode

spec="${1:-}"
sid="${spec%%:*}"       # session id = before the first colon (UUIDs contain none)
choice="${spec#*:}"     # choice     = after  the first colon
[ -n "$sid" ] && [ "$sid" != "$spec" ] || { echo "usage: wapprove.sh <session-id>:<yes|always|no>"; exit 2; }

case "$choice" in
  yes)    keys=$'\r' ;;
  always) keys=$'\x1b[B\r' ;;
  no)     keys=$'\x1b' ;;
  *) echo "bad choice '$choice' (want yes|always|no)"; exit 2 ;;
esac

# Bail loudly if the worker session has since closed, rather than injecting into the void.
if [ -n "$JQ" ] && ! "$AGT" tree --json | "$JQ" -e --arg id "$sid" \
    '.result.tree.workspaces[].sessions[] | select(.id==$id)' >/dev/null 2>&1; then
  echo "worker session $sid gone"; exit 4
fi

printf '%s' "$keys" | "$AGT" session type --stdin --target "$sid"

# We just answered the worker's prompt, so clear its stale "blocked" attention glyph.
"$(dirname "$0")/agterm-clear-status.sh" "$sid" 2>/dev/null || true

echo "approval '$choice' sent to worker $sid"
