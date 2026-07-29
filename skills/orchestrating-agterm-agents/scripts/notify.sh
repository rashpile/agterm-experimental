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

# Send the WHOLE message, split into ~2000-char parts (Telegram caps one message at 4096 chars, and a
# long curated note would otherwise 400 and vanish). Split on LINE boundaries so paragraphs stay intact;
# hard-split any single line that itself overruns. `iconv -c` drops invalid UTF-8 (Telegram rejects it).
# ${#s} counts CHARACTERS under a UTF-8 locale, so "~2000 symbols" holds for Cyrillic too. Mirrors
# tg-agterm.sh's Stop-path splitter.
export LC_ALL=en_US.UTF-8
msg="$(printf '%s' "$msg" | iconv -c -f utf-8 -t utf-8 2>/dev/null)"

MAX=2000
chunks=()
buf=""
while IFS= read -r line || [ -n "$line" ]; do
  while [ "${#line}" -gt "$MAX" ]; do
    [ -n "$buf" ] && { chunks+=("$buf"); buf=""; }
    chunks+=("${line:0:$MAX}")
    line="${line:$MAX}"
  done
  if [ -z "$buf" ]; then
    buf="$line"
  elif [ "$(( ${#buf} + 1 + ${#line} ))" -le "$MAX" ]; then
    buf="${buf}"$'\n'"${line}"          # +1 for the rejoined newline
  else
    chunks+=("$buf"); buf="$line"
  fi
done <<< "$msg"
[ -n "$buf" ] && chunks+=("$buf")

# Send each part. FIRST failure wins for reporting and stops the run — half a broken thread beats a full
# one. Prefix: bare icon for a single part, icon [i/n] for multi so ordering/completeness is obvious.
n=${#chunks[@]}
i=0
resp=""
for c in "${chunks[@]}"; do
  i=$((i + 1))
  if [ "$n" -gt 1 ]; then prefix="${icon} [$i/$n]
"; else prefix="${icon} "; fi
  resp="$(curl -sS -m 15 -X POST "https://api.telegram.org/bot${token}/sendMessage" \
    --data-urlencode "chat_id=${chat}" \
    --data-urlencode "text=${prefix}${c}" 2>&1)"
  case "$resp" in *'"ok":true'*) ;; *) break ;; esac   # $resp holds the failing response for the report
  [ "$i" -lt "$n" ] && sleep 0.3
done

case "$resp" in
  *'"ok":true'*)
    echo "notify: sent"
    # Mark that THIS session reported to the user in this turn, so tg-agterm.sh's Stop hook doesn't
    # also auto-post the turn's final text — the agent already said it, better. Keyed by session: a
    # worker calling notify.sh must not silence the orchestrator's turn-done ping (or vice versa).
    # Best-effort only; never affects notify's exit status.
    [ -n "${AGTERM_SESSION_ID:-}" ] && : > "$cfgdir/last_notify-${AGTERM_SESSION_ID}" 2>/dev/null || true
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
