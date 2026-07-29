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
#   agent-ctl.sh id     <name>               # resolve a session name -> id (kills the ad-hoc tree|jq)
#   agent-ctl.sh select <session>            # bring a session to the foreground (steals focus; rare)
#   agent-ctl.sh close  <session>            # close a session (e.g. a spawned worker) — allow-listed teardown
#   agent-ctl.sh ready  <session> [secs]     # block until the worker's Claude box is up (dismisses the
#                                            #   first-run "trust folder" prompt); default 15s. So the
#                                            #   orchestrator never hand-rolls raw `session text` polling.
#   agent-ctl.sh spawn  [--create-workspace] <workspace> <name> <cwd> [command...]  # new session, prints id
#          workspace = id | exact name | name-prefix (e.g. "ops" → "ops ⎇ main")
#          --create-workspace : reuse the workspace NAMED <workspace> if it exists, else create it
#                 (idempotent) — so you never drop to a raw `agtermctl workspace new` (which re-prompts).
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
  create_ws=0
  if [ "${2:-}" = --create-workspace ]; then create_ws=1; shift; fi
  ws="${2:?spawn: missing <workspace> (id | name | name-prefix)}"
  name="${3:?spawn: missing <name>}"
  cwd="${4:?spawn: missing <cwd>}"
  shift 4
  run="${*:-zsh -ic claude}"
  # --no-select (agterm ≥0.15.2): create the worker in the BACKGROUND. Delegation is the whole point —
  # spawning used to yank the user into the worker's workspace, mid-task, once per worker. Safe for the
  # supervise loop: a never-selected session's buffer is still live, so `ready`/`read`/`status`/
  # `agent-wait` all work without it ever being focused (verified). Use `agent-ctl.sh select <id>` on
  # the rare occasion you actually want it foregrounded.
  if [ "$create_ws" = 1 ]; then
    # Idempotent: agterm reuses the workspace NAMED "$ws" if present, creates it if absent. Keeps the
    # whole spawn (incl. workspace creation) inside this one allow-listed path — no raw `workspace new`.
    "$AGT" session new --workspace-name "$ws" --create-workspace --no-select \
        --name "$name" --cwd "$cwd" --command "$run" --json | jq -r '.result.id'
    exit "${PIPESTATUS[0]:-$?}"
  fi
  wsid=$("$AGT" tree --json | jq -r --arg w "$ws" \
      '.result.tree.workspaces[] | select(.id==$w or .name==$w or (.name|startswith($w))) | .id' | head -1)
  [ -n "$wsid" ] || wsid="$ws"
  "$AGT" session new --workspace "$wsid" --no-select --name "$name" --cwd "$cwd" --command "$run" --json \
    | jq -r '.result.id'
  exit "${PIPESTATUS[0]:-$?}"
fi

targ="${2:?missing <session>}"
id=$(resolve "$targ")
shift 2

case "$cmd" in
  id)
    # Resolve a session name -> id and print it. resolve() already prints the id (or passes the input
    # through unchanged if no name matched) — so this just surfaces it without the ad-hoc tree|jq.
    printf '%s\n' "$id" ;;
  ready)
    # Block until the target's freshly-spawned Claude is ready to receive a task: poll the screen,
    # auto-dismiss the first-run "trust this folder" prompt, and return once the input box footer is up.
    # Keeps the raw `session text` polling INSIDE this allow-listed path (the orchestrator never runs it).
    secs="${1:-15}"; iters=$(( secs * 2 )); trusted=0
    i=0
    while [ "$i" -lt "$iters" ]; do
      i=$(( i + 1 )); sleep 0.5
      txt="$("$AGT" session text --target "$id" --json 2>/dev/null | jq -r '.result.text // ""')"
      if [ "$trusted" = 0 ] && printf '%s' "$txt" | grep -qi 'trust this folder'; then
        printf '\r' | "$AGT" session type --stdin --target "$id" >/dev/null 2>&1
        trusted=1; continue
      fi
      # Idle/ready markers: the shortcuts hint, or the input box footer ("for agents" / a token counter).
      if printf '%s' "$txt" | grep -qE '\? for shortcuts|for agents|[0-9]+ tokens'; then
        echo "ready -> $id"; exit 0
      fi
    done
    echo "ready: timed out after ${secs}s waiting for $id"; exit 1 ;;
  select|view)
    # Bring a session to the foreground (steals focus). Rarely needed — read/status populate the buffer
    # without it — but wrapping it keeps even this allow-listed instead of a raw prompting agtermctl call.
    "$AGT" session select --target "$id" >/dev/null && echo "selected -> $id" ;;
  close)
    # Close a session (e.g. a worker you spawned) — allow-listed so shutdown/cleanup never prompts.
    "$AGT" session close --target "$id" >/dev/null && echo "closed -> $id" ;;
  read)
    "$AGT" session text --target "$id" --json | jq -r '.result.text' | tail -n "${1:-20}" ;;
  submit)
    printf '\r' | "$AGT" session type --stdin --target "$id" >/dev/null && echo "submitted Enter -> $id"
    "$(dirname "$0")/agterm-clear-status.sh" "$id" 2>/dev/null || true ;;
  key)
    case "${1:-}" in
      enter)  s=$'\r' ;;   down) s=$'\x1b[B' ;;   up) s=$'\x1b[A' ;;
      right)  s=$'\x1b[C' ;; left) s=$'\x1b[D' ;;   # right accepts a ghost prompt-suggestion
      ctrl-c) s=$'\x03' ;; esc)  s=$'\x1b' ;;
      *) echo "unknown key: '${1:-}' (enter|down|up|left|right|ctrl-c|esc)"; exit 1 ;;
    esac
    printf '%s' "$s" | "$AGT" session type --stdin --target "$id" >/dev/null && echo "sent '$1' -> $id"
    # enter/esc ANSWER a prompt (navigation keys don't) — clear a stale "blocked" glyph only then.
    case "$1" in enter|esc) "$(dirname "$0")/agterm-clear-status.sh" "$id" 2>/dev/null || true ;; esac ;;
  type|say)
    text="$*"; [ -n "$text" ] || { echo "missing text"; exit 1; }
    # clear any queued text (200 backspaces) then type ours
    { printf '\x7f%.0s' $(seq 1 200); printf '%s' "$text"; } \
      | "$AGT" session type --stdin --target "$id" >/dev/null
    if [ "$cmd" = say ]; then
      sleep 1
      printf '\r' | "$AGT" session type --stdin --target "$id" >/dev/null
      "$(dirname "$0")/agterm-clear-status.sh" "$id" 2>/dev/null || true
      echo "said + submitted -> $id: $text"
    else
      echo "typed (NOT submitted; verify then 'agent-ctl.sh submit') -> $id: $text"
    fi ;;
  status)
    # Classify a session: line 1 = idle|busy|waiting, then a short screen tail for context.
    # We ALWAYS compute BOTH signals and decide on both — neither is reliable alone:
    #
    #   (1) NATIVE — agterm's per-session .status from `tree --json` (idle|active|blocked|completed),
    #       published by a LOCAL agent's Claude Code lifecycle hooks (Stop->completed,
    #       UserPromptSubmit->active, Notification->blocked). Authoritative but can be STALE (a focused
    #       session auto-resets on visit) or ABSENT (an SSH agent runs Claude on a remote host that
    #       can't reach this socket — $AGTERM_SESSION_ID is unset there — so it never sets one).
    #   (2) SCREEN — a footer-anchored read of the LIVE buffer. The bottom footer is definitive and
    #       mutually exclusive: idle shows "? for shortcuts", busy "esc to interrupt", a live prompt
    #       "Esc to cancel" (whose menu can sit above a rendered task list, so scan a wider window for
    #       it). Anchoring on the footer — NOT the whole screen — is what stops the orchestrator's own
    #       transcript (which quotes worker prompt strings) from reading as a live prompt. "unknown"
    #       when the footer shows none of them (e.g. a plain shell, or a self-scrape of a blank frame).
    #       NB: "← for agents" is in BOTH idle and busy footers, so only "? for shortcuts" means idle.
    #
    # DECISION: the LIVE screen wins when it is definitive (it reflects the current frame); native
    # fills the gap when the screen is inconclusive. So an SSH agent (native none) rides on the screen;
    # a focused local whose native went stale rides on its live footer; and a self-scrape that catches
    # a blank frame falls back to native. This never re-blocks a real /o inject (a live idle footer
    # beats a stale native busy) and never injects into a menu (a menu footer is never "? for shortcuts").
    native="$("$AGT" tree --json | jq -r --arg id "$id" '
        .result.tree.workspaces[].sessions[] | select(.id==$id) | (.status // "")' | head -1)"
    case "$native" in
      idle|completed) nat=idle ;;
      active)         nat=busy ;;
      blocked)        nat=waiting ;;
      *)              nat=none ;;
    esac
    txt="$("$AGT" session text --target "$id" --json 2>/dev/null | jq -r '.result.text')"
    nb="$(printf '%s\n' "$txt" | grep -vE '^[[:space:]]*$')"
    foot="$(printf '%s\n' "$nb" | tail -6)"
    region="$(printf '%s\n' "$nb" | tail -20)"
    busy_re='to run in background|[Ee]sc to interrupt|ctrl\+b|· ↓ [0-9]'
    idle_re='\? for shortcuts|accept edits on|bypass permissions on|plan mode on'
    wait_re='Esc to cancel|Do you want to (proceed|create|make)|(❯|›)[[:space:]]*[0-9]+[.)][[:space:]]*(Yes|Allow|Proceed|No)'
    if   printf '%s' "$foot"   | grep -qE "$busy_re"; then scr=busy
    elif printf '%s' "$foot"   | grep -qE "$idle_re"; then scr=idle
    elif printf '%s' "$region" | grep -qE "$wait_re"; then scr=waiting
    else scr=unknown; fi
    if   [ "$scr" != unknown ]; then st="$scr"
    elif [ "$nat" != none ];    then st="$nat"
    else st=idle; fi
    echo "$st"
    echo "--- native=${native:-<none>} screen=$scr -> $st ---"
    printf '%s\n' "$nb" | tail -12 ;;
  *) echo "unknown subcommand: '$cmd'"; exit 1 ;;
esac
