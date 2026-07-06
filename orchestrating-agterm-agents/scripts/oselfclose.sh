#!/bin/bash
# oselfclose.sh — the orchestrator's FINAL graceful-shutdown act: clear its own Telegram registration
# and delete its own workspace (which closes THIS session). Pre-approved / allow-listed so the
# orchestrator can run it WITHOUT a permission prompt — essential in Telegram mode, where a /oclose
# arrives remotely and the orchestrator cannot approve a dialog on itself.
#
# Run it LAST, after closing your workers (agent-ctl.sh close) and sending the summary (notify.sh).
# Nothing runs after it — the process is torn down with the workspace.
#
#   oselfclose.sh
#
# Exit: 0 done (this session is about to die) · 2 could not delete workspace or close own session.
set -uo pipefail
export PATH="/opt/homebrew/bin:$PATH"
AGT=/opt/homebrew/bin/agtermctl
JQ="$(command -v jq || true)"   # resolves /usr/bin/jq or /opt/homebrew/bin/jq — don't hardcode

cfgdir="$HOME/.config/orchestrating-agterm-agents"
regfile="$cfgdir/orchestrator_session"

# 3. Clear the Telegram (/o) registration so no stale target is left pointing at a dead session.
rm -f "$regfile"

# 4. Close myself: delete the workspace CONTAINING this session. Resolve it from $AGTERM_SESSION_ID
#    (the robust "close myself" — doesn't rely on the workspace being named "orchestrator"), with a
#    name fallback. UUID case can differ between the env var and the tree, so compare case-insensitively.
me="${AGTERM_SESSION_ID:-}"
wsid=""
if [ -n "$JQ" ] && [ -n "$me" ]; then
  wsid="$("$AGT" tree --json 2>/dev/null | "$JQ" -r --arg id "$me" '
      .result.tree.workspaces[]
      | select(any(.sessions[]?; (.id|ascii_downcase) == ($id|ascii_downcase)))
      | .id' | head -1)"
fi
if [ -z "$wsid" ] && [ -n "$JQ" ]; then
  wsid="$("$AGT" tree --json 2>/dev/null | "$JQ" -r \
      '.result.tree.workspaces[] | select(.name=="orchestrator") | .id' | head -1)"
fi

# Try the full-workspace delete first (also sweeps up any leftover sessions in it).
if [ -n "$wsid" ] && "$AGT" workspace delete --target "$wsid" >/dev/null 2>&1; then
  exit 0
fi

# keep-at-least-one refused it (orchestrator was the window's only workspace) — close just my own
# session instead; the (now-empty) workspace stays, and /oclose force can finish it if needed.
if [ -n "$me" ] && "$AGT" session close --target "$me" >/dev/null 2>&1; then
  exit 0
fi

echo "oselfclose: could not delete the workspace or close own session (id=${me:-<unset>})" >&2
exit 2
