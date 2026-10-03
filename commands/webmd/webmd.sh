#!/bin/zsh
# webmd.sh — read markdown rendered, inside the terminal.
#
# Renders a markdown file to a self-contained HTML page and shows it with
# terminal-browser, either in an overlay over the current session or in a new
# session of its own.
#
# Subcommands:
#   webmd.sh last <agterm-session-id> [--side|--new]   The LAST message of the
#                                                     claude running in that session.
#   webmd.sh file <path.md> [SID] [--side|--new]      Any markdown file.
#
# Placement, default a 95% overlay over the session:
#   --side  run it in the session's right split pane, creating or revealing that pane
#           as needed. A pane already running something is left alone and the page
#           falls back to a new session.
#   --new   always a new session, never the pane.
#
# A new session is named "<current session name> last" for the last-message flow, and
# after the file for `file`, so a sidebar full of these says which conversation or
# document each one holds. The overlay dies with its command; a pane and a session
# both persist.
#
# Bound in keymap.conf:
#   ctrl+b>c>w        ->  webmd.sh last "$AGT_SESSION_ID"
#   ctrl+b>c>shift+w  ->  webmd.sh last "$AGT_SESSION_ID" --side
#
# Same front half as last-message-typora.sh (ctrl+b>c>t): resolve the claude
# session from the live process (agterm-claude-session -> ~/.claude/sessions/<pid>.json,
# so /clear is handled) and pull the final stop message out of the transcript.
# Where c>t hands the markdown to Typora as an editable file, this one renders it
# read-only and never leaves the terminal.
#
# Files land in ~/.local/share/agterm/webmd/msg-<stamp>.{md,html}, one pair per
# press, so an earlier message stays readable after the next one.
#
# This is the stopgap for the Go `webmd` (repos/webmd/task.md). When that exists,
# it replaces the render + open half of this script.
set -eu

AGTERMCTL=/opt/homebrew/bin/agtermctl
OPSBIN="$HOME/.local/bin"
OUTDIR="$HOME/.local/share/agterm/webmd"
RENDER="$(dirname "$0")/webmd-render.py"
SIZE=95             # overlay panel, percent of the pane
SIDE_PRIMARY=0.34   # divider when webmd creates the split: the browser gets the rest, ~2/3

# The GUI PATH a chord runs under does not include ~/.local/bin, so every binary
# is named absolutely. The launcher script is preferred; the app's own copy is the
# fallback when only the vendored install exists.
TB="$HOME/.local/bin/terminal-browser"
[ -x "$TB" ] || TB="$HOME/.local/share/terminal-browser/app/bin/terminal-browser"

TITLE="webmd"
origin=""
mode=overlay        # overlay | side | session

fail() {
  local -a target
  target=()
  [ -n "$origin" ] && target=(--target "$origin")
  "$AGTERMCTL" notify "$1" --title "$TITLE" "${target[@]}" || true
  exit 1
}

# Three words about the origin session's split pane: hasSplit, shown, busy.
# A pane counts as busy when the tree reports a foreground process for it, which it
# does for a hidden split too. A pane sitting at its shell prompt reports none.
split_state() {  # split_state <session-id>
  "$AGTERMCTL" tree --json 2>/dev/null | /usr/bin/env python3 -c '
import json, sys
try:
    tree = json.load(sys.stdin)["result"]["tree"]
except Exception:
    print("no no no"); raise SystemExit(0)
for ws in tree.get("workspaces", []):
    for s in ws.get("sessions", []):
        if s.get("id") == sys.argv[1]:
            print("yes" if s.get("hasSplit") else "no",
                  "yes" if s.get("split") else "no",
                  "yes" if s.get("splitForeground") else "no")
            raise SystemExit(0)
print("no no no")
' "$1" 2>/dev/null
}

# The page the right pane is already showing, when what runs there is one of ours.
# The argv of the pane's foreground process carries the file, and a path under OUTDIR
# is proof webmd put it there — anything else in that pane belongs to the user.
webmd_pane_file() {  # webmd_pane_file <session-id>
  "$AGTERMCTL" tree --json 2>/dev/null | /usr/bin/env python3 -c '
import json, sys
sid, outdir = sys.argv[1], sys.argv[2]
try:
    tree = json.load(sys.stdin)["result"]["tree"]
except Exception:
    raise SystemExit(0)
for ws in tree.get("workspaces", []):
    for s in ws.get("sessions", []):
        if s.get("id") == sid:
            for arg in s.get("splitForeground") or []:
                if arg.startswith(outdir) and arg.endswith(".html"):
                    print(arg)
                    raise SystemExit(0)
' "$1" "$OUTDIR" 2>/dev/null
}

# The terminal-browser instance key for the browser launched with a given file.
#
# Matching is by TTY, not by the tab's url. The launch argv never changes, but the tab
# does: after one refresh the browser shows a newer page than the file it was started
# with, and a url match would miss it from the second press onward.
browser_key_for() {  # browser_key_for <launch-file>
  local tty
  tty="$(ps -Ao tty=,command= | grep -F "$1" | grep -v grep | awk '$1 != "??" {print $1; exit}')"
  [ -n "$tty" ] || return 0
  "$TB" ls --all --json 2>/dev/null | /usr/bin/env python3 -c '
import json, sys
want = "/dev/" + sys.argv[1]
try:
    data = json.load(sys.stdin)
except Exception:
    raise SystemExit(0)
for b in data.get("browsers", []):
    if b.get("tty") == want:
        print(b.get("key", ""))
        raise SystemExit(0)
' "$tty" 2>/dev/null
}

open_side() {  # open_side <html> -> 0 when the browser was started in the right pane
  local html="$1" has shown busy i created=0 current key
  [ -n "$origin" ] || return 1
  read -r has shown busy <<< "$(split_state "$origin")"

  if [ "$busy" = yes ]; then
    # A webmd page already in the pane is refreshed in place: same pane, same browser,
    # new document. Anything else running there is the user's, so the caller falls back
    # to a new session rather than disturbing it.
    current="$(webmd_pane_file "$origin")"
    [ -n "$current" ] || return 1
    key="$(browser_key_for "$current")"
    [ -n "$key" ] || return 1
    # location.replace, not `action -- open`: open adds a TAB, so every press left
    # another one behind and the pane's tab strip grew without end. replace swaps the
    # document in the tab that is already there and adds no history entry either.
    "$TB" action --browser "$key" -- eval "location.replace('file://$html')" >/dev/null 2>&1 || return 1
    return 0
  fi

  # Create the pane, or reveal it when it exists but is hidden. Both are the same
  # command; a fresh pane needs a moment before its shell can be typed into.
  [ "$has" = no ] && created=1
  if [ "$has" = no ] || [ "$shown" = no ]; then
    "$AGTERMCTL" session split on --target "$origin" >/dev/null 2>&1 || return 1
  fi
  if [ "$created" -eq 1 ]; then
    for i in 1 2 3 4 5 6 7 8 9 10; do
      read -r has shown busy <<< "$(split_state "$origin")"
      [ "$has" = yes ] && break
      sleep 0.2
    done
    [ "$has" = yes ] || return 1
    sleep 0.3
    # Give the browser two thirds. A markdown page needs the width more than a
    # prompt does. Only a pane this run created is resized; an existing pane keeps
    # the divider where the user put it.
    "$AGTERMCTL" session resize --split-ratio "$SIDE_PRIMARY" --target "$origin" >/dev/null 2>&1 || true
  fi

  # session type drives the pane's shell, so the command is quoted for it.
  #
  # A pane this run created is ours: `; exit` ends its shell when the browser quits,
  # and the pane goes with it, so closing the page leaves the session as it was found.
  # A pane that already existed keeps its shell — its cwd and history are the user's,
  # not ours to end.
  if [ "$created" -eq 1 ]; then
    printf "'%s' open '%s'; exit\n" "$TB" "$html"
  else
    printf "'%s' open '%s'\n" "$TB" "$html"
  fi | "$AGTERMCTL" session type --stdin --target "$origin" --pane right >/dev/null 2>&1 \
    || return 1
}

open_session() {  # open_session <html> <label>
  local html="$1" label="$2"
  local -a place
  place=()
  [ -n "$origin" ] && place=(--after "$origin")
  "$AGTERMCTL" session new --name "$label" "${place[@]}" \
    --command "$TB open $html" >/dev/null || fail "could not open a session for $html"
}

open_overlay() {  # open_overlay <html>
  local html="$1"
  local -a target
  target=()
  [ -n "$origin" ] && target=(--target "$origin")
  # A running overlay is never replaced — a second open answers "overlay already
  # open" — so take down whatever is there first. Pressing the chord twice should
  # show the newer message, not fail.
  "$AGTERMCTL" session overlay close "${target[@]}" >/dev/null 2>&1 || true
  "$AGTERMCTL" session overlay open "$TB open $html" \
    --size-percent "$SIZE" "${target[@]}" >/dev/null \
    || fail "could not open an overlay for $html"
}

show() {  # show <html> <label>
  local html="$1" label="$2"
  [ -x "$TB" ] || fail "terminal-browser is not installed"
  case "$mode" in
    session) open_session "$html" "$label" ;;
    side)    open_side "$html" || open_session "$html" "$label" ;;
    *)       open_overlay "$html" ;;
  esac
}

# The sidebar name of a session, empty when it cannot be resolved. Read from the tree
# rather than $AGT_SESSION_NAME: that token carries the focused pane's terminal title,
# which a remote shell can set over OSC, while the tree's name is the sidebar label the
# user actually sees.
session_name() {  # session_name <session-id>
  [ -n "${1:-}" ] || return 0
  "$AGTERMCTL" tree --json 2>/dev/null | /usr/bin/env python3 -c '
import json, sys
try:
    tree = json.load(sys.stdin)["result"]["tree"]
except Exception:
    raise SystemExit(0)
for ws in tree.get("workspaces", []):
    for s in ws.get("sessions", []):
        if s.get("id") == sys.argv[1]:
            print(s.get("name") or "")
            break
' "$1" 2>/dev/null
}

render() {  # render <src.md> <out.html> <title>
  [ -f "$RENDER" ] || fail "renderer missing: $RENDER"
  /usr/bin/env python3 "$RENDER" "$1" "$2" "$3" || fail "could not render $1"
}

stamp() { date +%Y-%m-%d_%H%M%S; }

cmd_last() {
  origin="${1:-${AGTERM_SESSION_ID:-}}"
  TITLE="Last message webmd"
  [ -n "$origin" ] || fail "no agterm session id given"

  # A split pane can run its own claude under the same agterm id; the newest wins.
  local claude_id
  claude_id=$("$OPSBIN/agterm-claude-session" "$origin" 2>/dev/null | head -1)
  case "$claude_id" in
    [0-9a-f]*-*-*-*-*) ;;
    *) fail "no live claude in this session" ;;
  esac

  mkdir -p "$OUTDIR"
  local base="$OUTDIR/msg-$(stamp)"
  "$OPSBIN/claude-session-md" "$claude_id" --last -o "$base.md" >/dev/null 2>&1 \
    || fail "no finished message yet in $claude_id"
  [ -s "$base.md" ] || fail "last message is empty: $claude_id"

  # "<session> last", so several of these in the sidebar say whose message each one is.
  local name
  name="$(session_name "$origin")"
  [ -n "$name" ] || name="webmd"

  render "$base.md" "$base.html" "$name last"
  show "$base.html" "$name last"
}

cmd_file() {
  local src="${1:-}"
  TITLE="webmd"
  [ -n "$src" ] || fail "usage: webmd.sh file <path.md> [session-id] [--new]"
  [ -f "$src" ] || fail "no such file: $src"
  origin="${2:-${AGTERM_SESSION_ID:-}}"

  mkdir -p "$OUTDIR"
  local name="${src:t:r}"
  local out="$OUTDIR/$name-$(stamp).html"
  render "$src" "$out" "${src:t}"
  show "$out" "$name"
}

# Placement flags may appear anywhere after the subcommand; strip them before dispatch.
args=()
for a in "$@"; do
  case "$a" in
    --side) mode=side ;;
    --new|--new-session) mode=session ;;
    *) args+=("$a") ;;
  esac
done

case "${args[1]:-}" in
  last) cmd_last "${args[2]:-}" ;;
  file) cmd_file "${args[2]:-}" "${args[3]:-}" ;;
  *)
    print -u2 "usage: webmd.sh last <agterm-session-id> [--side|--new]"
    print -u2 "       webmd.sh file <path.md> [session-id] [--side|--new]"
    exit 2
    ;;
esac
