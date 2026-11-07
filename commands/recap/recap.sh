#!/bin/zsh
# recap.sh — what happened lately in a Claude session, summarized by a local Ollama model.
#
#   recap.sh [<session>] [--model M] [--turns N]   overlay over that session: the summary is
#                                                  written live, then shown rendered
#   recap.sh --print [<session>] [--model M] [--turns N]   write it to this terminal instead
#
# <session> is an agterm session id, a unique id prefix, a session name or `active`; without
# one, the session this runs in. The claude running there is found through
# agterm-claude-session (~/.claude/sessions/<pid>.json), so /clear and a split pane's own
# claude resolve to the right transcript.
#
# recap.py builds the digest (user messages, the user's answers to the agent's questions,
# assistant text, one line per tool call; the last text of each turn nearly whole) from the
# last N user turns, default 6, and streams the model's summary. Model: --model, else
# $RECAP_MODEL, else gemma4:e4b.
#
# Each recap is kept as ~/.local/share/agterm/recap/recap-<stamp>.md.
#
# Bound in keymap.conf: ctrl+b>c>s -> recap.sh "$AGT_SESSION_ID"
set -u

AGTERMCTL=/opt/homebrew/bin/agtermctl
RESOLVE="$HOME/.local/bin/agterm-claude-session"
HERE="${0:A:h}"                 # resolves a symlink, so recap.py is found beside the real file
SELF="${0:A}"
OUTDIR="$HOME/.local/share/agterm/recap"
SIZE=85                         # overlay, percent of the pane

mode=overlay; session=""; extra=(); show_file=""
while [ $# -gt 0 ]; do
  case "$1" in
    --print) mode=print ;;
    --run)   mode=run ;;       # internal: the overlay's own command
    --show)  mode=show; session="$2"; show_file="$3"; shift 2 ;;  # internal: from the detached reopen
    --model|--turns) extra+=("$1" "$2"); shift ;;
    -h|--help) sed -n '2,20p' "$SELF" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) session="$1" ;;
  esac
  shift
done

# The run in the overlay closes with its command, so a failure there waits for a key:
# an error that flashes for a frame is no error message at all.
fail() {
  print -u2 -- "recap: $*"
  if [ "$mode" = run ]; then print -n -- "\nнажми любую клавишу…"; read -k1 -s; fi
  exit 1
}

# One call gives both the agterm id (the overlay's target) and the transcript.
resolve() {
  local -a arg; arg=()
  [ -n "$session" ] && arg=("$session")
  "$RESOLVE" "${arg[@]}" --json 2>/dev/null | /usr/bin/env python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    raise SystemExit(1)
print(d.get("agtermSessionId") or "")
print(d.get("transcript") or "")
'
}

session_name() {
  "$AGTERMCTL" tree --json 2>/dev/null | /usr/bin/env python3 -c '
import json, sys
for ws in json.load(sys.stdin)["result"]["tree"].get("workspaces", []):
    for s in ws.get("sessions", []):
        if s.get("id") == sys.argv[1]:
            print(s.get("name") or "")
' "$1" 2>/dev/null
}

# The rendered overlay replaces the live one only once that one is gone: agterm answers
# "overlay already open" until the closing overlay's command has fully exited.
if [ "$mode" = show ]; then
  for i in {1..20}; do
    sleep 0.5
    "$AGTERMCTL" session overlay open --markdown "$show_file" --size-percent "$SIZE" \
      --target "$session" --json 2>&1 | grep -q '"ok":true' && exit 0
  done
  print -- "$(date '+%F %T') could not show $show_file on $session" >> "$OUTDIR/overlay.log"
  exit 1
fi

lines=("${(@f)$(resolve)}")
sid="${lines[1]:-}"; transcript="${lines[2]:-}"
[ -n "$sid" ] && [ -f "$transcript" ] || fail "no live claude in ${session:-this session}"

case "$mode" in
  overlay)
    # A running overlay is never replaced, so close whatever is there first.
    "$AGTERMCTL" session overlay close --target "$sid" >/dev/null 2>&1
    "$AGTERMCTL" session overlay open "$SELF --run $sid ${extra[*]}" \
      --size-percent "$SIZE" --target "$sid" >/dev/null || fail "could not open an overlay on $sid"
    ;;
  print)
    /usr/bin/env python3 "$HERE/recap.py" "$transcript" "${extra[@]}" || exit 1
    ;;
  run)
    name="$(session_name "$sid")"
    mkdir -p "$OUTDIR" || fail "cannot create $OUTDIR"
    out="$OUTDIR/recap-$(date +%Y-%m-%d_%H%M%S).md"
    print -P "%B%F{cyan}Recap · ${name:-$sid}%f%b"
    print -P "%F{8}${transcript:t} · ${extra[*]:-default model}%f\n"
    /usr/bin/env python3 "$HERE/recap.py" "$transcript" --out "$out" --title "${name:-$sid}" "${extra[@]}" \
      || fail "the model run failed"
    # This overlay ends with this script, and an open overlay cannot be replaced, so the
    # rendered one is opened from a detached process once this one is gone.
    nohup "$SELF" --show "$sid" "$out" >/dev/null 2>&1 &!
    ;;
esac
