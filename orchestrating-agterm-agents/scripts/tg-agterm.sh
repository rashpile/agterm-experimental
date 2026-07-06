#!/bin/bash
# tg-agterm.sh — Telegram approval bridge for the agterm orchestrator (Claude Code Notification hook).
# When the REGISTERED orchestrator session hits a permission prompt, post a Telegram message with
# Yes / Always / No buttons. The buttons use pako-telegram's GENERIC action-button protocol
# (callback_data "act:<command>[:<arg>]"): clicking runs the `oapprove` command, which calls
# oapprove.sh to inject the keystroke back into the session. No agterm logic lives in the bot.
#
# Phase 1: approvals only (Notification event). Stop-based flows are a documented future seam below.
#
# Reads the hook JSON on stdin. Fail-open: exits 0 (does nothing) unless ALL hold:
#   - hook_event_name == Notification
#   - this session IS the registered orchestrator (AGTERM_SESSION_ID == orchestrator_session)
#   - the notification is a permission request (not the 60s idle nudge)
#   - Telegram is configured (token + chat_id)
set -uo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
AGT=/opt/homebrew/bin/agtermctl
JQ="$(command -v jq || true)"   # resolves /usr/bin/jq or /opt/homebrew/bin/jq — don't hardcode
[ -n "$JQ" ] || exit 0          # no jq → can't parse; fail open (silent)

cfgdir="$HOME/.config/orchestrating-agterm-agents"
regfile="$cfgdir/orchestrator_session"

input="$(cat)"
event="$(printf '%s' "$input" | "$JQ" -r '.hook_event_name // ""')"
message="$(printf '%s' "$input" | "$JQ" -r '.message // ""')"

# --- event routing. Phase 1 = Notification/approvals only. ---
case "$event" in
  Notification) : ;;
  # Stop) ... ;;   # FUTURE SEAM: turn-complete / idle flows attach here in a later phase.
  *) exit 0 ;;
esac

# Only act for the registered orchestrator session.
sid="${AGTERM_SESSION_ID:-}"
reg="$([ -r "$regfile" ] && tr -d '[:space:]' < "$regfile")"
[ -n "$sid" ] && [ -n "$reg" ] || exit 0
[ "$(printf '%s' "$sid" | tr 'A-Z' 'a-z')" = "$(printf '%s' "$reg" | tr 'A-Z' 'a-z')" ] || exit 0

# Only permission/approval notifications — skip the "waiting for your input" idle nudge.
printf '%s' "$message" | grep -qiE 'permission|needs your|approv' || exit 0

# Telegram configured?
token="$([ -r "$cfgdir/token" ] && tr -d '[:space:]' < "$cfgdir/token")"
chat="${TG_CHAT_ID:-$([ -r "$cfgdir/chat_id" ] && tr -d '[:space:]' < "$cfgdir/chat_id")}"
[ -n "$token" ] && [ -n "$chat" ] || exit 0

# Read the pending-prompt screen once — used both to detect the menu shape and to build the snippet.
screen="$("$AGT" session text --target "$sid" --json 2>/dev/null | "$JQ" -r '.result.text // ""')"

# A short, sanitized snippet of what's being approved. ASCII-only and capped so the Telegram send
# can't be broken by multi-byte box-drawing (same reasoning as os.yaml).
snippet="$(printf '%s' "$screen" \
  | LC_ALL=C tr -cd '\11\12\15\40-\176' \
  | grep -vE '^[[:space:]]*$' \
  | grep -viE 'for shortcuts|to interrupt|for agents|session /|/hr|tokens' \
  | tail -10 | cut -c1-90 | head -c 800)"

text="⚠️ Orchestrator needs approval
${message}"
[ -n "$snippet" ] && text="${text}

${snippet}"

# Buttons ride the generic act:<command>:<arg> protocol → the `oapprove` command → oapprove.sh.
# ADAPTIVE: only offer "Always" when the menu actually has a don't-ask-again / always-allow option
# (the 3-option prompt). A bare Yes/No prompt gets just Yes/No — no misleading Always button.
yes_btn='{"text":"✅ Yes","callback_data":"act:oapprove:yes"}'
no_btn='{"text":"🚫 No","callback_data":"act:oapprove:no"}'
always_btn='{"text":"♾️ Always","callback_data":"act:oapprove:always"}'
# Isolate the CURRENT permission menu: everything from the last "Do you want to ..." line to the
# bottom. Anchoring on the live menu (not the whole buffer) means a stale menu left in scrollback by
# an earlier prompt can't skew the button set, and a "3." inside the command text above is ignored.
menu="$(printf '%s' "$screen" | awk 'tolower($0) ~ /do you want to/ {cap=""} {cap=cap $0 "\n"} END {print cap}')"
# A 3-option prompt exposes an option "3." (Yes / Yes+always / No); a bare Yes/No prompt stops at "2.".
if printf '%s' "$menu" | grep -qE '(^|[^0-9])3\.[[:space:]]'; then
  kb="{\"inline_keyboard\":[[${yes_btn},${always_btn},${no_btn}]]}"
else
  kb="{\"inline_keyboard\":[[${yes_btn},${no_btn}]]}"
fi

curl -sS -m 15 -X POST "https://api.telegram.org/bot${token}/sendMessage" \
  --data-urlencode "chat_id=${chat}" \
  --data-urlencode "text=${text}" \
  --data-urlencode "reply_markup=${kb}" >/dev/null 2>&1

exit 0
