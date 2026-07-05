#!/bin/bash
# oa.sh — send exact keystrokes to the registered orchestrator session: raw, WITHOUT clearing the
# input box and WITHOUT an automatic Enter. For answering a live prompt/menu from Telegram.
#
#   /oa <text...>     types the text as-is (NO Enter) — e.g. "/oa 1" selects option 1
#   bare word ENTER   is sent as a real Enter keypress — e.g. "/oa 2 ENTER" or "/oa ENTER"
#
# Why a bare "ENTER" word and not the literal "<Enter>": pako-telegram appends command args to
# the shell UNQUOTED, so "<Enter>"'s < > would parse as redirection and break. "ENTER" is safe.
#
# Exit: 0 sent · 2 no text · 3 not registered.
set -uo pipefail
export PATH="/opt/homebrew/bin:$PATH"
AGT=/opt/homebrew/bin/agtermctl

regfile="$HOME/.config/orchestrating-agterm-agents/orchestrator_session"
target="$([ -r "$regfile" ] && tr -d '[:space:]' < "$regfile")"
[ -n "$target" ] || { echo "orchestrator not registered — start the orchestrating-agterm-agents skill."; exit 3; }
[ $# -ge 1 ] || { echo "usage: /oa <text>   (bare word ENTER = an Enter keypress)"; exit 2; }

# Build the raw payload: words join with single spaces; a bare ENTER word becomes a carriage return.
payload=""
fresh=1
for a in "$@"; do
  case "$a" in
    ENTER|enter|Enter) payload="${payload}"$'\r'; fresh=1 ;;
    *)
      if [ "$fresh" = 1 ]; then payload="${payload}${a}"; fresh=0
      else payload="${payload} ${a}"; fi ;;
  esac
done

printf '%s' "$payload" | "$AGT" session type --stdin --target "$target"
echo "sent to orchestrator: $*"
