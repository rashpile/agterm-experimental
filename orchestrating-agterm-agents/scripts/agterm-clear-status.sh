#!/bin/bash
# agterm-clear-status.sh — smart-clear a stale "blocked" agent-status glyph on an agterm session.
#
# When a session waits on a permission prompt, the agterm Notification hook sets its sidebar status
# to "blocked" (blinking for attention). Answering that prompt is just a keystroke — it does NOT fire
# a Claude lifecycle hook, so the glyph stays stale (still blinking "needs you") until the turn ends,
# even though the agent has resumed. Call this RIGHT AFTER you send the answer: if the target is still
# "blocked", flip it to "active" (it's working again). No-op if the session isn't blocked, so it's
# safe to call after ANY keystroke/submit.
#
#   agterm-clear-status.sh <session-id>
#
# Exit: always 0 (best-effort; must never break the caller's flow).
set -uo pipefail
export PATH="/opt/homebrew/bin:$PATH"
AGT=/opt/homebrew/bin/agtermctl
JQ="$(command -v jq || true)"   # resolves /usr/bin/jq or /opt/homebrew/bin/jq — don't hardcode
[ -n "$JQ" ] || exit 0

id="${1:-}"
[ -n "$id" ] || exit 0

# Only act on a genuinely stale "blocked" glyph — leave idle/active/completed alone.
st="$("$AGT" tree --json 2>/dev/null | "$JQ" -r --arg id "$id" \
      '.result.tree.workspaces[].sessions[] | select(.id==$id) | .status // ""' | head -1)"
[ "$st" = blocked ] && "$AGT" session status active --target "$id" >/dev/null 2>&1
exit 0
