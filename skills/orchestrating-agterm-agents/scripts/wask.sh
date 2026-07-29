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
# Read the worker's AGENT pane, not whatever pane is on-screen. If the worker is split and a non-agent
# pane (e.g. an nvim tree) has focus, `session text` defaults to that focused pane and the snippet AND
# button-shape would come from the wrong pane. The tree's statusPane names the pane the worker's agent
# lives in ("left" for the main/unsplit pane, "right" when the agent is in the split), so read that pane.
# This is the worker analog of tg-agterm.sh's AGTERM_PANE fix — here we can't use our own env (wrong
# session), so we ask the tree for the worker's agent pane. Empty (older agterm) → default on-screen read.
screen=""
if [ -n "$JQ" ]; then
  pane_arg=()
  sp="$("$AGT" tree --json 2>/dev/null | "$JQ" -r --arg id "$sid" '.. | objects | select(.id? == $id) | .statusPane // empty' | head -1)"
  [ -n "$sp" ] && pane_arg=(--pane "$sp")
  screen="$("$AGT" session text "${pane_arg[@]}" --target "$sid" --json 2>/dev/null | "$JQ" -r '.result.text // ""')"
fi

# Friendly title for the worker (falls back to the raw id).
title="$sid"
if [ -n "$JQ" ]; then
  t="$("$AGT" tree --json 2>/dev/null | "$JQ" -r --arg id "$sid" '.. | objects | select(.id? == $id) | (.title // .name) // empty' | head -1)"
  [ -n "$t" ] && title="$t"
fi

# Sanitized snippet of what's being approved. Keep letters in ANY language (Cyrillic, CJK, …): the old
# `tr -cd '\40-\176'` was ASCII-only and silently deleted every non-Latin character, so a Russian prompt
# arrived as bare English scaffolding. Instead drop only the TUI box-drawing/block frame and control
# chars, and guarantee valid UTF-8 with iconv (Telegram 400s on invalid bytes — the real hazard the
# ASCII strip guarded against). Same treatment as tg-agterm.sh; perl -CSDA does the unicode-range work.
# You have to be able to DECIDE from this text alone, so the bounds are generous: 160 cols clears the
# widest line a full-width pane emits (~130), and 30 lines covers most of a screen.
# `tail`, not `head`: when the text still overruns, drop the OLDEST lines. The bottom carries the
# "Do you want to..." question and the menu — cutting from the top loses scrollback, cutting from the
# bottom loses the decision. 3000 leaves headroom under Telegram's 4096-char cap for the title + note.
# The final iconv -c cleans any partial multi-byte char that `tail -c 3000` may have sliced.
snippet="$(printf '%s' "$screen" \
  | iconv -c -f utf-8 -t utf-8 2>/dev/null \
  | perl -CSDA -ne '
      chomp;
      s/[\x{2500}-\x{259F}]//g;                 # box-drawing + block elements → strip the frame
      s/[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]//g;     # control chars, but keep \t \n \r
      next if /^\s*$/;
      next if /for shortcuts|to interrupt|for agents|session \/|\/hr|tokens/i;
      $_ = substr($_,0,160) if length > 160;
      print "$_\n";
    ' \
  | tail -30 | tail -c 3000 | iconv -c -f utf-8 -t utf-8 2>/dev/null)"

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
