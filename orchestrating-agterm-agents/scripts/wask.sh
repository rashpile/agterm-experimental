#!/bin/bash
# wask.sh — post a Telegram approval request (Yes / [Always] / No buttons) for a WORKER session's
# live permission prompt. The orchestrator calls this INSTEAD of `notify.sh --level attention` when a
# supervised worker hits a prompt it should not silently auto-approve. It is the worker analog of
# tg-agterm.sh: same generic act:<command>:<arg> button protocol, but the arg carries the worker's
# session id, so clicking runs the `wapprove` command → wapprove.sh injects the keystroke into THAT
# worker (not the orchestrator).
#
#   wask.sh <worker-session-id> [note...]
#
# Exit: 0 sent · 2 usage · 3 telegram not configured · 1 send failed.
set -uo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
AGT=/opt/homebrew/bin/agtermctl
JQ="$(command -v jq || true)"
cfgdir="$HOME/.config/orchestrating-agterm-agents"

sid="${1:-}"; [ -n "$sid" ] || { echo "usage: wask.sh <worker-session-id> [note...]"; exit 2; }
shift || true
note="${*:-}"

token="$([ -r "$cfgdir/token" ]   && tr -d '[:space:]' < "$cfgdir/token")"
chat="${TG_CHAT_ID:-$([ -r "$cfgdir/chat_id" ] && tr -d '[:space:]' < "$cfgdir/chat_id")}"
[ -n "$token" ] && [ -n "$chat" ] || { echo "wask.sh: telegram not configured (need $cfgdir/token + chat_id)" >&2; exit 3; }

# Read the worker's pending-prompt screen once — used to detect the menu shape and build the snippet.
screen=""
[ -n "$JQ" ] && screen="$("$AGT" session text --target "$sid" --json 2>/dev/null | "$JQ" -r '.result.text // ""')"

# Friendly title for the worker (falls back to the raw id).
title="$sid"
if [ -n "$JQ" ]; then
  t="$("$AGT" tree --json 2>/dev/null | "$JQ" -r --arg id "$sid" '.. | objects | select(.id? == $id) | (.title // .name) // empty' | head -1)"
  [ -n "$t" ] && title="$t"
fi

# Short, sanitized snippet of what's being approved — ASCII-only and capped so a Telegram send can't
# be broken by multi-byte box-drawing (same reasoning as tg-agterm.sh / os.yaml).
snippet="$(printf '%s' "$screen" \
  | LC_ALL=C tr -cd '\11\12\15\40-\176' \
  | grep -vE '^[[:space:]]*$' \
  | grep -viE 'for shortcuts|to interrupt|for agents|session /|/hr|tokens' \
  | tail -10 | cut -c1-90 | head -c 800)"

text="⚠️ Worker needs approval — ${title}"
[ -n "$note" ]    && text="${text}
${note}"
[ -n "$snippet" ] && text="${text}

${snippet}"

# Buttons ride the generic act:<command>:<arg> protocol. arg = "<session-id>:<choice>", which
# ParseActionCallback (SplitN ":",3) hands to the wapprove command intact.
yes_btn="{\"text\":\"✅ Yes\",\"callback_data\":\"act:wapprove:${sid}:yes\"}"
no_btn="{\"text\":\"🚫 No\",\"callback_data\":\"act:wapprove:${sid}:no\"}"
always_btn="{\"text\":\"♾️ Always\",\"callback_data\":\"act:wapprove:${sid}:always\"}"

# ADAPTIVE: only offer "Always" when the live menu actually has a don't-ask-again option (3-option
# prompt). Anchor on the last "Do you want to..." line so a stale menu in scrollback can't skew it.
menu="$(printf '%s' "$screen" | awk 'tolower($0) ~ /do you want to/ {cap=""} {cap=cap $0 "\n"} END {print cap}')"
if printf '%s' "$menu" | grep -qE '(^|[^0-9])3\.[[:space:]]'; then
  kb="{\"inline_keyboard\":[[${yes_btn},${always_btn},${no_btn}]]}"
else
  kb="{\"inline_keyboard\":[[${yes_btn},${no_btn}]]}"
fi

resp="$(curl -sS -m 15 -X POST "https://api.telegram.org/bot${token}/sendMessage" \
  --data-urlencode "chat_id=${chat}" \
  --data-urlencode "text=${text}" \
  --data-urlencode "reply_markup=${kb}" 2>&1)"

case "$resp" in
  *'"ok":true'*) echo "wask: sent for $sid"; exit 0 ;;
  *) echo "wask: FAILED: $resp" >&2; exit 1 ;;
esac
