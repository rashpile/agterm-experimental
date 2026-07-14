#!/bin/bash
# agterm-clear-status.sh — after answering a session's prompt, clear its two stale attention signals:
# the "blocked" agent-status glyph AND the unread-notification badge.
#
# When a session waits on a permission prompt, the agterm Notification hook sets its sidebar status
# to "blocked" (blinking for attention). Answering that prompt is just a keystroke — it does NOT fire
# a Claude lifecycle hook, so the glyph stays stale (still blinking "needs you") until the turn ends,
# even though the agent has resumed. Call this RIGHT AFTER you send the answer: if the target is still
# "blocked", flip it to "active" (it's working again). No-op if the session isn't blocked, so it's
# safe to call after ANY keystroke/submit.
#
# It ALSO clears the sidebar's red unread-notification badge (session.seen, agterm 0.8.3+) on every
# call. That badge (unseen desktop notifications) previously cleared ONLY by visiting the session, so
# a purely socket-driven session — orchestrator / Telegram — was never "visited" and its badge grew
# unbounded. session.seen is independent of the status glyph, idempotent, and focus-free.
#
#   agterm-clear-status.sh <session-id>
#
# Exit: always 0 (best-effort; must never break the caller's flow).
set -uo pipefail
export PATH="/opt/homebrew/bin:$PATH"
AGT=/opt/homebrew/bin/agtermctl
JQ="$(command -v jq || true)"   # resolves /usr/bin/jq or /opt/homebrew/bin/jq — don't hardcode

id="${1:-}"
[ -n "$id" ] || exit 0

# Clear the unread-notification badge unconditionally on any answer — it's independent of the status
# glyph below, needs no jq, is idempotent/focus-free, and is a no-op if already 0. Silently skipped on
# older agterm that lacks the subcommand (pre-0.8.3).
"$AGT" session seen --target "$id" >/dev/null 2>&1 || true

# The status-glyph fix below reads the tree, which needs jq — bail out cleanly if it's unavailable.
[ -n "$JQ" ] || exit 0

# Only act on a genuinely stale "blocked" glyph — leave idle/active/completed alone.
st="$("$AGT" tree --json 2>/dev/null | "$JQ" -r --arg id "$id" \
      '.result.tree.workspaces[].sessions[] | select(.id==$id) | .status // ""' | head -1)"
[ "$st" = blocked ] && "$AGT" session status active --target "$id" >/dev/null 2>&1
exit 0
