#!/bin/bash
# agent-ctl.sh — thin, allow-listable wrapper around agtermctl for driving agent sessions.
# One stable command path so it can be allow-listed once (Bash(.../agent-ctl.sh:*)) instead of
# re-approving ad-hoc compound pipelines (variables + echo + pipes) every time.
#
#   agent-ctl.sh read   <session> [lines]    # print last N lines of the session (default 20)
#   agent-ctl.sh submit <session>            # press Enter — submit whatever is in the input box
#   agent-ctl.sh key    <session> <key>      # send one key: enter | down | up | ctrl-c | esc
#   agent-ctl.sh type   <session> <text...>  # clear input box, type <text>, do NOT submit
#   agent-ctl.sh say    <session> <text...>  # clear input box, type <text>, then submit
#   agent-ctl.sh spawn  <workspace> <name> <cwd> [command...]   # new session, prints its id
#          workspace = id | exact name | name-prefix (e.g. "ops" → "ops ⎇ main")
#          command defaults to `zsh -ic claude`. Encapsulated so orchestrator spawns stay
#          allow-listable (no ad-hoc `$(agtermctl … | jq …)` pipeline that re-prompts).
#
# <session> = exact session name OR id / unique prefix (prefer an id — names can repeat).
# SAFETY: `submit`/`say` push input into a live agent (may launch real work). Read first.
set -uo pipefail
export PATH="/opt/homebrew/bin:$PATH"
AGT=/opt/homebrew/bin/agtermctl

resolve() {  # exact session name -> id, else pass through as id/prefix
  local t="$1" id
  id=$("$AGT" tree --json | jq -r --arg t "$t" \
        '.result.tree.workspaces[].sessions[] | select(.name==$t) | .id' | head -1)
  [ -n "$id" ] && printf '%s' "$id" || printf '%s' "$t"
}

cmd="${1:?usage: agent-ctl.sh read|submit|key|type|say|spawn <target> [args]}"

# spawn is workspace-targeted — handle before the session-resolution below.
if [ "$cmd" = spawn ] || [ "$cmd" = new ]; then
  ws="${2:?spawn: missing <workspace> (id | name | name-prefix)}"
  name="${3:?spawn: missing <name>}"
  cwd="${4:?spawn: missing <cwd>}"
  shift 4
  run="${*:-zsh -ic claude}"
  wsid=$("$AGT" tree --json | jq -r --arg w "$ws" \
      '.result.tree.workspaces[] | select(.id==$w or .name==$w or (.name|startswith($w))) | .id' | head -1)
  [ -n "$wsid" ] || wsid="$ws"
  "$AGT" session new --workspace "$wsid" --name "$name" --cwd "$cwd" --command "$run" --json \
    | jq -r '.result.id'
  exit "${PIPESTATUS[0]:-$?}"
fi

targ="${2:?missing <session>}"
id=$(resolve "$targ")
shift 2

case "$cmd" in
  read)
    "$AGT" session text --target "$id" --json | jq -r '.result.text' | tail -n "${1:-20}" ;;
  submit)
    printf '\r' | "$AGT" session type --stdin --target "$id" >/dev/null && echo "submitted Enter -> $id" ;;
  key)
    case "${1:-}" in
      enter)  s=$'\r' ;;   down) s=$'\x1b[B' ;;   up) s=$'\x1b[A' ;;
      right)  s=$'\x1b[C' ;; left) s=$'\x1b[D' ;;   # right accepts a ghost prompt-suggestion
      ctrl-c) s=$'\x03' ;; esc)  s=$'\x1b' ;;
      *) echo "unknown key: '${1:-}' (enter|down|up|left|right|ctrl-c|esc)"; exit 1 ;;
    esac
    printf '%s' "$s" | "$AGT" session type --stdin --target "$id" >/dev/null && echo "sent '$1' -> $id" ;;
  type|say)
    text="$*"; [ -n "$text" ] || { echo "missing text"; exit 1; }
    # clear any queued text (200 backspaces) then type ours
    { printf '\x7f%.0s' $(seq 1 200); printf '%s' "$text"; } \
      | "$AGT" session type --stdin --target "$id" >/dev/null
    if [ "$cmd" = say ]; then
      sleep 1
      printf '\r' | "$AGT" session type --stdin --target "$id" >/dev/null
      echo "said + submitted -> $id: $text"
    else
      echo "typed (NOT submitted; verify then 'agent-ctl.sh submit') -> $id: $text"
    fi ;;
  status)
    # Classify a single screen snapshot: line 1 = idle|busy|waiting, then a short tail for context.
    # Reuses agent-wait.sh's markers so inject.sh can branch without blindly pushing into a prompt.
    txt="$("$AGT" session text --target "$id" --json 2>/dev/null | jq -r '.result.text')"
    work_re='to run in background|[Ee]sc to interrupt|↓ [0-9]|· ↓'
    wait_re='(❯|›|>)[[:space:]]*[0-9]+[.)][[:space:]]*(Yes|Allow|Proceed|No)|Do you want to proceed|requires approval|don.?t ask again'
    if   printf '%s' "$txt" | grep -qE "$wait_re"; then st=waiting
    elif printf '%s' "$txt" | grep -qE "$work_re"; then st=busy
    else st=idle; fi
    echo "$st"
    echo "--- screen tail ---"
    printf '%s\n' "$txt" | grep -vE '^[[:space:]]*$' | tail -12 ;;
  *) echo "unknown subcommand: '$cmd'"; exit 1 ;;
esac
