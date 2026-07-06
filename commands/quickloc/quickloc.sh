#!/bin/zsh
# Quick session locations (harpoon-style marks), slots 1-5. Bound in keymap.conf:
#   ctrl+b>shift+N  ->  quickloc.sh set N "$AGT_SESSION_ID"   (mark current session)
#   ctrl+b>N        ->  quickloc.sh go  N                      (jump to slot N)
#
# The mark IS the session name: slot N shows as a "[N]" tag prefixed to the name
# (e.g. "top" -> "[1] top"). The name is the single source of truth — no state
# file — so marks survive restarts and are visible in the sidebar. A session can
# hold several slots ("[1][2] top", tags sorted & merged); non-slot brackets such
# as "[On]" are left untouched. Re-marking a slot moves it: the previous holder's
# "[N]" tag is stripped and re-added to the current session.
set -eu

AGTERMCTL=/opt/homebrew/bin/agtermctl

action="${1:-}"     # set | go
slot="${2:-}"       # 1..5
cur="${3:-}"        # current session id (keymap passes $AGT_SESSION_ID); set only

case "$slot" in 1|2|3|4|5) ;; *) exit 0 ;; esac

tree=$("$AGTERMCTL" tree --json)

# id of the session currently tagged [slot], if any (first match wins).
find_tagged() {
  printf '%s' "$tree" | jq -r --arg n "$1" \
    'first(.result.tree.workspaces[].sessions[] | select(.name | test("\\[" + $n + "\\]")) | .id) // empty'
}

# name of a session by id.
name_of() {
  printf '%s' "$tree" | jq -r --arg i "$1" \
    'first(.result.tree.workspaces[].sessions[] | select((.id|ascii_downcase)==($i|ascii_downcase)) | .name) // empty'
}

# rebuild a name with slot $2 added ("add") or removed ("del"); prints new name.
retag() { # $1=name  $2=add|del  $3=slot
  jq -rn --arg name "$1" --arg op "$2" --arg s "$3" '
    $name as $n
    | [ $n | scan("\\[([1-5])\\]") | .[0] ] as $found
    | ($n | gsub("\\[[1-5]\\]"; "") | gsub("^\\s+|\\s+$"; "")) as $base
    | ( (if $op=="add" then ($found + [$s]) else ($found - [$s]) end)
        | map(tonumber) | unique | sort | map("[" + tostring + "]") | join("") ) as $tags
    | if   $tags=="" then $base
      elif $base=="" then $tags
      else $tags + " " + $base end'
}

if [ "$action" = "go" ]; then
  t=$(find_tagged "$slot")
  [ -n "$t" ] && exec "$AGTERMCTL" session select --target "$t"
  exit 0
fi

if [ "$action" = "set" ]; then
  # current session: arg ($AGT_SESSION_ID) wins, else the active session from the tree.
  if [ -z "$cur" ]; then
    cur=$(printf '%s' "$tree" | jq -r 'first(.result.tree.workspaces[].sessions[] | select(.active==true) | .id) // empty')
  fi
  [ -n "$cur" ] || exit 0

  # strip [slot] from the previous holder (if different session).
  prev=$(find_tagged "$slot")
  if [ -n "$prev" ] && [ "$prev:l" != "$cur:l" ]; then
    newprev=$(retag "$(name_of "$prev")" del "$slot")
    [ -n "$newprev" ] && "$AGTERMCTL" session rename "$newprev" --target "$prev"
  fi

  # add [slot] to the current session (idempotent if already tagged).
  newcur=$(retag "$(name_of "$cur")" add "$slot")
  [ -n "$newcur" ] && exec "$AGTERMCTL" session rename "$newcur" --target "$cur"
  exit 0
fi

exit 0
