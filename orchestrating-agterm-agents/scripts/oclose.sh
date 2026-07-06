#!/bin/bash
# oclose.sh — wind down the orchestrator. Invoked by the pako-telegram /oclose (graceful) and the
# force command (runs on the BOT, outside agterm; reaches the control socket like inject.sh).
#
#   oclose.sh            # GRACEFUL (default): ask the orchestrator to drain itself — close its workers,
#                        #   summarize to Telegram, then clear its registration + close itself
#                        #   (via oselfclose.sh). This ONLY posts a prompt; it kills nothing directly.
#   oclose.sh force      # HARD: delete the "orchestrator" workspace immediately + clear registration.
#                        #   Escape hatch for a stuck/unresponsive orchestrator (or when self-close was
#                        #   refused because it was the window's only workspace).
#
# Exit: 0 ok / requested · 1 jq missing · 2 delete failed · 3 not registered / nothing to close.
set -uo pipefail
export PATH="/opt/homebrew/bin:$PATH"
AGT=/opt/homebrew/bin/agtermctl
JQ="$(command -v jq || true)"   # resolves /usr/bin/jq or /opt/homebrew/bin/jq — don't hardcode
[ -n "$JQ" ] || { echo "oclose: jq not found on PATH."; exit 1; }
here="$(cd "$(dirname "$0")" && pwd)"

cfgdir="$HOME/.config/orchestrating-agterm-agents"
regfile="$cfgdir/orchestrator_session"
mode="${1:-graceful}"

# ── HARD teardown (the original behavior) ───────────────────────────────────────────────────────────
if [ "$mode" = force ]; then
  wsid="$("$AGT" tree --json 2>/dev/null | "$JQ" -r \
      '.result.tree.workspaces[] | select(.name=="orchestrator") | .id' | head -1)"
  if [ -z "$wsid" ]; then
    rm -f "$regfile"
    echo "No 'orchestrator' workspace open — nothing to close. Cleared registration."
    exit 0
  fi
  if err="$("$AGT" workspace delete --target "$wsid" 2>&1)"; then
    rm -f "$regfile"
    echo "Force-closed the orchestrator workspace and cleared registration. Use /ostart for a new one."
  else
    echo "Could not close the orchestrator workspace: ${err:-unknown error}"
    echo "(agterm keeps at least one workspace per window — if it's the only one, add another first.)"
    exit 2
  fi
  exit 0
fi

# ── GRACEFUL (default): post a shutdown prompt; the orchestrator drains itself ───────────────────────
target="$([ -r "$regfile" ] && tr -d '[:space:]' < "$regfile")"
[ -n "$target" ] || { echo "Orchestrator not registered — nothing to close. (Use /ostart to start one.)"; exit 3; }

# Don't fire text into a busy session or an open menu (same rule as inject.sh). Only post when idle.
state="$("$here/agent-ctl.sh" status "$target" 2>/dev/null | head -1)"
case "$state" in
  busy)    echo "Orchestrator is busy — not interrupting. Resend /oclose when it's idle, or use the force close to kill it now."; exit 0 ;;
  waiting) echo "Orchestrator has a prompt open — answer it (/oa) first, then /oclose. Or force-close to kill it now."; exit 0 ;;
esac

"$here/agent-ctl.sh" say "$target" \
  "[tg] Graceful shutdown requested. Follow the skill's \"Graceful shutdown\" procedure: close every worker session you spawned (agent-ctl.sh close <id>), send a short session summary via notify.sh --level done, then run scripts/oselfclose.sh (clears your registration and closes your workspace). This ends the session." \
  >/dev/null 2>&1

echo "Shutdown requested — the orchestrator is winding down; it'll send a summary when done. (Force-close if it doesn't.)"
