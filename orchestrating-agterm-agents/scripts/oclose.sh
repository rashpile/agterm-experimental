#!/bin/bash
# oclose.sh — tear down the orchestrator. Deletes the "orchestrator" workspace (closing every
# session in it, including the live Claude) and clears the Telegram (/o) registration so a stale
# target isn't left behind. Invoked by the pako-telegram /oclose command (runs on the bot).
#
#   oclose.sh
#
# Exit: 0 closed or nothing-to-do · 1 jq missing · 2 delete failed.
set -uo pipefail
export PATH="/opt/homebrew/bin:$PATH"
AGT=/opt/homebrew/bin/agtermctl
JQ="$(command -v jq || true)"   # resolves /usr/bin/jq or /opt/homebrew/bin/jq — don't hardcode
[ -n "$JQ" ] || { echo "oclose: jq not found on PATH."; exit 1; }

cfgdir="$HOME/.config/orchestrating-agterm-agents"
regfile="$cfgdir/orchestrator_session"

# Find the orchestrator workspace by name.
wsid="$("$AGT" tree --json 2>/dev/null | "$JQ" -r '.result.tree.workspaces[] | select(.name=="orchestrator") | .id' | head -1)"

if [ -z "$wsid" ]; then
  # Nothing to close, but still clear any dangling registration.
  rm -f "$regfile"
  echo "No 'orchestrator' workspace open — nothing to close. Cleared registration."
  exit 0
fi

# Delete the workspace (closes all its sessions). "keep-at-least-one": if it is the only workspace
# in its window, agterm refuses — report that instead of failing silently.
if err="$("$AGT" workspace delete --target "$wsid" 2>&1)"; then
  rm -f "$regfile"
  echo "Closed the orchestrator workspace and cleared registration. /o has no target now — use /ostart to start a new one."
else
  echo "Could not close the orchestrator workspace: ${err:-unknown error}"
  echo "(agterm keeps at least one workspace per window — if it's the only one, add another first.)"
  exit 2
fi
