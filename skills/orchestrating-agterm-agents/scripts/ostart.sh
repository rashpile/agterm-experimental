#!/bin/bash
# ostart.sh — one-tap orchestrator bootstrap for Telegram. Creates a fresh agterm session running
# Claude, registers it as the /o transport target, and kicks it into orchestrator + Telegram mode.
# Invoked by the pako-telegram /ostart command, which runs on the BOT — outside agterm — and reaches
# the agterm control socket exactly like inject.sh does.
#
#   ostart.sh [dir]      # orchestrator session cwd (default ~/workspace/ops)
#
# Exit: 0 started · 1 jq missing / session-create failed.
set -uo pipefail
export PATH="/opt/homebrew/bin:$PATH"
AGT=/opt/homebrew/bin/agtermctl
JQ="$(command -v jq || true)"   # resolves /usr/bin/jq or /opt/homebrew/bin/jq — don't hardcode
[ -n "$JQ" ] || { echo "ostart: jq not found on PATH."; exit 1; }

here="$(cd "$(dirname "$0")" && pwd)"
cfgdir="$HOME/.config/orchestrating-agterm-agents"
regfile="$cfgdir/orchestrator_session"
DIR="${1:-$HOME/workspace/ops}"
[ -d "$DIR" ] || DIR="$HOME"

# 1. Create the orchestrator session. --create-workspace reuses the "orchestrator" workspace when it
#    exists and creates it when absent (idempotent); a fresh session lands inside it each call.
id="$("$AGT" session new --workspace-name "orchestrator" --create-workspace \
        --name "orchestrator" --cwd "$DIR" --command "zsh -ic claude" --json 2>/dev/null \
        | "$JQ" -r '.result.id')"
if [ -z "$id" ] || [ "$id" = null ]; then
  echo "ostart: failed to create orchestrator session."
  exit 1
fi

# 2. Register it as the Telegram (/o /os /oa) target — "newer wins", plain overwrite.
mkdir -p "$cfgdir"
printf '%s\n' "$id" > "$regfile"
chmod 600 "$regfile"

# 2b. Move the "orchestrator" workspace to the top of the sidebar so it's always the first tab.
wsid="$("$AGT" tree --json 2>/dev/null | "$JQ" -r '.result.tree.workspaces[] | select(.name=="orchestrator") | .id' | head -1)"
[ -n "$wsid" ] && "$AGT" workspace move --to top --target "$wsid" >/dev/null 2>&1

# 3. Bring it to the foreground (also populates the screen buffer so `session text` isn't empty).
"$AGT" session select --target "$id" >/dev/null 2>&1

# 4. Wait for Claude to be ready (~12s). Dismiss the first-run "trust this folder" prompt if it
#    appears (accept the highlighted "Yes, I trust"); stop once the input box footer is up.
trusted=0
for _ in $(seq 1 24); do
  sleep 0.5
  txt="$("$AGT" session text --target "$id" --json 2>/dev/null | "$JQ" -r '.result.text // ""')"
  if [ "$trusted" = 0 ] && printf '%s' "$txt" | grep -qi 'trust this folder'; then
    printf '\r' | "$AGT" session type --stdin --target "$id" >/dev/null 2>&1
    trusted=1
    continue
  fi
  printf '%s' "$txt" | grep -q '? for shortcuts' && break   # idle input box → ready
done

# 5. Kick it into orchestrator + Telegram mode. The [tg] prefix also trips the auto-load hook, and
#    `say` clears the box, types, then submits.
"$here/agent-ctl.sh" say "$id" \
  "[tg] Run the orchestrating-agterm-agents skill — we are in Telegram mode. This session is already registered as the Telegram /o target (ostart did it); reply to me via notify.sh." \
  >/dev/null 2>&1

# 6. Confirm to Telegram.
printf 'Orchestrator started and registered.\n  session: %s\n  cwd: %s\nSend /o <msg> to drive it, /os to peek at its screen.\n' "$id" "$DIR"
