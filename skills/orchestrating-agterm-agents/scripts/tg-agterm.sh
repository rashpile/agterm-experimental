#!/bin/bash
# tg-agterm.sh — Telegram approval bridge for the agterm orchestrator (Claude Code Notification hook).
# When the REGISTERED orchestrator session hits a permission prompt, post a Telegram message with
# Yes / Always / No buttons. The buttons use pako-telegram's GENERIC action-button protocol
# (callback_data "act:<command>[:<arg>]"): clicking runs the `oapprove` command, which calls
# oapprove.sh to inject the keystroke back into the session. No agterm logic lives in the bot.
#
# It also reports turn completion: on Stop, it posts the turn's final answer so an unattended job says
# when it is done. Wire it up under BOTH events in settings.json (Notification matcher
# "permission_prompt", and Stop).
#
# Reads the hook JSON on stdin. Fail-open: exits 0 (does nothing) unless ALL hold:
#   - hook_event_name == Notification or Stop
#   - this session IS the registered orchestrator (AGTERM_SESSION_ID == orchestrator_session)
#   - Telegram is configured (token + chat_id)
#   - Notification only: it is a permission request (not the 60s idle nudge)
#   - Stop only: not a stop-hook continuation, and the agent did not already report via notify.sh
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

# --- event routing: Notification = approvals, Stop = turn finished. ---
case "$event" in
  Notification|Stop) : ;;
  *) exit 0 ;;
esac

# Only act for the registered orchestrator session.
sid="${AGTERM_SESSION_ID:-}"
reg="$([ -r "$regfile" ] && tr -d '[:space:]' < "$regfile")"
[ -n "$sid" ] && [ -n "$reg" ] || exit 0
[ "$(printf '%s' "$sid" | tr 'A-Z' 'a-z')" = "$(printf '%s' "$reg" | tr 'A-Z' 'a-z')" ] || exit 0

# Telegram configured?
token="$([ -r "$cfgdir/token" ] && tr -d '[:space:]' < "$cfgdir/token")"
chat="${TG_CHAT_ID:-$([ -r "$cfgdir/chat_id" ] && tr -d '[:space:]' < "$cfgdir/chat_id")}"
[ -n "$token" ] && [ -n "$chat" ] || exit 0

# --- Stop: the registered session finished a turn → report the result, so an unattended job doesn't
# just go quiet. Every turn fires this, which is why binding is a toggle: unbind (cmd+shift+r) when you
# are sitting at the keyboard and don't want the running commentary. ---
if [ "$event" = Stop ]; then
  # A Stop hook that CONTINUES the turn re-enters Stop; don't post twice for one turn.
  [ "$(printf '%s' "$input" | "$JQ" -r '.stop_hook_active // false')" = true ] && exit 0

  # Dedup: if the agent already reported via notify.sh during THIS turn, stay quiet — its curated
  # message beats an automatic dump of the same thing. "This turn" = since the previous Stop, which is
  # exactly what comparing the two markers' mtimes asks. Per-session, so a worker's notify.sh can't
  # silence the orchestrator's ping. First ever Stop has no smark → posts, then the pairing is armed.
  nmark="$cfgdir/last_notify-$sid"
  smark="$cfgdir/last_stop-$sid"
  reported=0
  [ -f "$nmark" ] && [ -f "$smark" ] && [ "$nmark" -nt "$smark" ] && reported=1
  : > "$smark" 2>/dev/null || true   # close the turn whether or not we post
  [ "$reported" = 1 ] && exit 0

  # The turn's final answer, handed to us directly by the harness. Do NOT read transcript_path for
  # this — the hooks docs are explicit:
  #   "The transcript file is written asynchronously and may lag the in-memory conversation, so it may
  #    not yet include the current turn's most recent messages when a hook fires. Hooks that need the
  #    final assistant text of the current turn should use last_assistant_message on Stop and
  #    SubagentStop instead of reading the transcript"
  # Measured here: the transcript lags ~250ms, so reading it posted the PREVIOUS text block — the
  # narration written before the last tool call — on every single turn, while looking like it worked.
  # No polling, no race, no heuristics: the field IS the turn's final message by definition.
  result="$(printf '%s' "$input" | "$JQ" -r '.last_assistant_message // ""')"
  [ -n "$result" ] || exit 0   # empty on an interrupted/abort turn — silence beats posting the wrong text

  # Guard against posting the same text twice if Stop fires more than once for one turn. A GUARD, not a
  # trigger: an earlier version keyed the whole decision on "is this text new?", which fired happily on
  # a mid-turn narration line because that is new too.
  sentmark="$cfgdir/last_sent-$sid"
  hash="$(printf '%s' "$result" | shasum | cut -d' ' -f1)"
  [ -r "$sentmark" ] && [ "$(cat "$sentmark")" = "$hash" ] && exit 0
  printf '%s' "$hash" > "$sentmark" 2>/dev/null || true

  # The prose is written for a terminal, so Telegram shows its markdown raw: asterisks, backticks, and
  # pipe-tables that are unreadable on a phone. Strip it to plain text.
  #
  # NOT parse_mode: Telegram's parser is strict and this text reliably breaks it — identifiers like
  # last_notify-* read as italics, and any unbalanced marker returns a 400 that would lose the message
  # entirely. Stripping is lossy; a 400 is silent data loss. Prefer lossy.
  plain="$(printf '%s' "$result" | sed -E \
    -e '/^[[:space:]]*```/d' \
    -e '/^[[:space:]]*\|[-|: [:space:]]*\|[[:space:]]*$/d' \
    -e 's/^#{1,6}[[:space:]]+//' \
    -e 's/\[([^]]*)\]\([^)]*\)/\1/g' \
    -e 's/\*\*([^*]+)\*\*/\1/g' \
    -e 's/\*([^*]+)\*/\1/g' \
    -e 's/`([^`]+)`/\1/g' \
    -e 's/^[[:space:]]*\|[[:space:]]*//' \
    -e 's/[[:space:]]*\|[[:space:]]*$//')"

  # Keep the HEAD here, unlike the approval snippet below: an answer leads with its conclusion, so an
  # overrun should lose trailing detail, not the verdict. `head -c` can slice a UTF-8 char in half and
  # Telegram rejects invalid UTF-8 — `iconv -c` drops any such partial tail.
  body="$(printf '%s' "$plain" | head -c 3000 | iconv -c -f utf-8 -t utf-8 2>/dev/null)"
  [ "${#plain}" -gt "${#body}" ] && body="${body}…"

  curl -sS -m 15 -X POST "https://api.telegram.org/bot${token}/sendMessage" \
    --data-urlencode "chat_id=${chat}" \
    --data-urlencode "text=✅ ${body}" >/dev/null 2>&1

  # Restore the cyan "this session owns Telegram" badge. register.sh sets it at bind time, but the glyph
  # is shared state that Claude's own lifecycle hooks overwrite every turn, so it never survives on its
  # own. Re-asserting it HERE is what makes it effectively persistent: this hook is last in the Stop
  # chain, so it runs after agterm-agent-status.sh's `completed`, and the badge then sits there through
  # the idle gap — exactly when someone glances at the sidebar to ask "where is Telegram pointed?".
  # --auto-reset keeps it honest: visiting the session clears it.
  # Stop only, deliberately: doing this on the approval path too would overwrite the `blocked` glyph and
  # hide that a session needs a decision — the one signal worth more than this one.
  "$AGT" session status active --color '#00d7d7' --auto-reset --target "$sid" >/dev/null 2>&1
  exit 0
fi

# --- Notification: only permission/approval prompts — skip the "waiting for your input" idle nudge. ---
printf '%s' "$message" | grep -qiE 'permission|needs your|approv' || exit 0

# Read the pending-prompt screen once — used both to detect the menu shape and to build the snippet.
screen="$("$AGT" session text --target "$sid" --json 2>/dev/null | "$JQ" -r '.result.text // ""')"

# A sanitized snippet of what's being approved. ASCII-only so the Telegram send can't be broken by
# multi-byte box-drawing (same reasoning as os.yaml), then bounded to fit one message. Bounds match
# wask.sh — see the rationale there; in short, `tail -c` (not `head -c`) so an overrun drops the oldest
# lines rather than the "Do you want to..." question and menu at the bottom.
snippet="$(printf '%s' "$screen" \
  | LC_ALL=C tr -cd '\11\12\15\40-\176' \
  | grep -vE '^[[:space:]]*$' \
  | grep -viE 'for shortcuts|to interrupt|for agents|session /|/hr|tokens' \
  | tail -30 | cut -c1-160 | tail -c 3000)"

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
