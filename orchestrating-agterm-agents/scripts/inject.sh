#!/bin/bash
# inject.sh — inject a Telegram-originated message into the registered orchestrator session.
# Called by the pako-telegram /o command. Prefixes "[tg] " so the orchestrator knows the
# message arrived remotely and must reply via notify.sh (the user is not at the keyboard).
#
#   inject.sh <text...>
#
# Exit: 0 injected · 2 empty message · 3 not registered · 4 registered session gone.
set -uo pipefail
export PATH="/opt/homebrew/bin:$PATH"

here="$(cd "$(dirname "$0")" && pwd)"
cfgdir="$HOME/.config/orchestrating-agterm-agents"
regfile="$cfgdir/orchestrator_session"
AGT=/opt/homebrew/bin/agtermctl
JQ="$(command -v jq || true)"   # resolves /usr/bin/jq or /opt/homebrew/bin/jq — don't hardcode
[ -n "$JQ" ] || { echo "inject.sh: jq not found on PATH." >&2; exit 5; }

text="$*"
if [ -z "$text" ]; then
  echo "inject.sh: empty message — nothing to send." >&2
  exit 2
fi

target="$([ -r "$regfile" ] && tr -d '[:space:]' < "$regfile")"
if [ -z "$target" ]; then
  echo "inject.sh: orchestrator not registered — start the orchestrating-agterm-agents skill so it registers its session (expected file: $regfile)." >&2
  exit 3
fi

# Fail loudly if the registered session has since closed, instead of injecting into the void.
if ! "$AGT" tree --json | "$JQ" -e --arg id "$target" \
      '.result.tree.workspaces[].sessions[] | select(.id==$id)' >/dev/null 2>&1; then
  echo "inject.sh: registered orchestrator session $target no longer exists — re-run register.sh in the orchestrator." >&2
  exit 4
fi

# Status-aware injection: never blindly push into a busy session or a live prompt. Pushing text +
# Enter into an AskUserQuestion / permission menu makes the message become the menu's answer (that
# exact bug corrupted a real reply). So read the orchestrator's state first and give the user honest
# Telegram feedback instead of silently mis-firing.
status_out="$("$here/agent-ctl.sh" status "$target")"
state="$(printf '%s\n' "$status_out" | head -1)"
context="$(printf '%s\n' "$status_out" | sed -n '3,14p')"

case "$state" in
  idle)
    exec "$here/agent-ctl.sh" say "$target" "[tg] $text" ;;
  waiting)
    printf '⚠️ Orchestrator is waiting on a prompt — your message was NOT sent.\nIt is asking:\n%s\n\nReply with your choice to answer it, or resend once it moves on.\n' "$context"
    exit 6 ;;
  busy)
    printf '⏳ Orchestrator is busy right now — message NOT sent. Please resend in a moment.\n'
    exit 7 ;;
  *)
    # unknown state — best-effort inject rather than drop the message
    exec "$here/agent-ctl.sh" say "$target" "[tg] $text" ;;
esac
