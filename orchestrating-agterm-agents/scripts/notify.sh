#!/bin/bash
# notify.sh — send a Telegram message to the user from the orchestrator.
# Use it to ping a human when a supervised worker needs attention or a long
# unattended job is done. Standalone: it touches no agterm session, only curl.
#
#   notify.sh [--level done|attention|info] <message...>
#     --level  prepend a glance-emoji: done ✅ · attention ⚠️ · info ℹ️ (default info)
#
# Config (dedicated to this skill, seeded once from the pako-telegram bot):
#   ~/.config/orchestrating-agterm-agents/token     — bot token, one line
#   ~/.config/orchestrating-agterm-agents/chat_id   — target chat id, one line
#   TG_CHAT_ID env var overrides the chat_id file (e.g. to hit a group instead).
#
# curl is /usr/bin/curl — present under agterm's bare PATH, so this needs no PATH fix.
set -uo pipefail

cfgdir="$HOME/.config/orchestrating-agterm-agents"

level=info
case "${1:-}" in
  --level) level="${2:?--level needs a value}"; shift 2 ;;
esac
[ $# -ge 1 ] || { echo "usage: notify.sh [--level done|attention|info] <message...>" >&2; exit 2; }

case "$level" in
  done)      icon="✅" ;;
  attention) icon="⚠️" ;;
  info|*)    icon="ℹ️" ;;
esac

msg="$*"
token="$([ -r "$cfgdir/token" ]   && tr -d '[:space:]' < "$cfgdir/token")"
chat="${TG_CHAT_ID:-$([ -r "$cfgdir/chat_id" ] && tr -d '[:space:]' < "$cfgdir/chat_id")}"

# Not-configured is a setup problem, not a runtime one — and notify.sh IS the channel to the user,
# so it can't Telegram this. Print an actionable, copy-pasteable hint (exit 3) so the orchestrator
# relays it to the user in chat instead of retrying.
if [ -z "$token" ] || [ -z "$chat" ]; then
  {
    echo "notify.sh: not configured — Telegram notifications need a bot token + chat id."
    echo "Create these one-line files (get them from @BotFather and @userinfobot):"
    echo "  mkdir -p $cfgdir"
    echo "  printf '%s\\n' '<BOT_TOKEN>' > $cfgdir/token   && chmod 600 $cfgdir/token"
    echo "  printf '%s\\n' '<CHAT_ID>'   > $cfgdir/chat_id && chmod 600 $cfgdir/chat_id"
    [ -z "$token" ] || echo "(token OK; missing chat_id — or set TG_CHAT_ID)"
    [ -z "$chat" ]  || echo "(chat_id OK; missing token)"
  } >&2
  exit 3
fi

resp="$(curl -sS -m 15 -X POST "https://api.telegram.org/bot${token}/sendMessage" \
  --data-urlencode "chat_id=${chat}" \
  --data-urlencode "text=${icon} ${msg}" 2>&1)"

case "$resp" in
  *'"ok":true'*)
    echo "notify: sent"
    # Best-effort: clear the orchestrator's own unread-notification badge (session.seen, agterm
    # 0.8.3+). It just pinged the user via Telegram, so the desktop badge is redundant — keep it from
    # climbing while the session sits unfocused. Reads the same regfile inject.sh writes; never
    # affects notify's exit status.
    reg="$cfgdir/orchestrator_session"
    if [ -r "$reg" ]; then
      oid="$(tr -d '[:space:]' < "$reg")"
      [ -n "$oid" ] && /opt/homebrew/bin/agtermctl session seen --target "$oid" >/dev/null 2>&1 || true
    fi
    exit 0 ;;
  *) echo "notify: FAILED: $resp" >&2; exit 1 ;;
esac
