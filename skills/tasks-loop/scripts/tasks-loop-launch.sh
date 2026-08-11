#!/bin/sh
# tasks-loop-launch.sh — agterm keymap command: start a tasks-loop run in a NEW session.
#
# Cascading native picker that accumulates flags:
#   menu 1:  default | auto | keep | list | resume | remote — "default" runs bare /tasks-loop immediately
#   pick an item -> next menu: run (with selection so far) | the items not yet added | clear
#   "run" executes; "clear" resets to menu 1; cancelling any menu aborts silently.
# auto/keep/list/resume become /tasks-loop flags; "remote" instead spawns the orchestrator with Remote
# Control on, so it is steerable from claude.ai/phone.
# Then: spawn a fresh claude session through the AGENT DRIVER in the current workspace at the
# current session's cwd, put it on high effort, submit "/tasks-loop <flags>", select it.
#
# The driver is the same one the skill runs on (config.md `agent-driver-cmd`) and it owns what
# this script used to hand-roll: the prompt-suggestion kill, spawn-blocks-until-ready, and a
# CONFIRMED submit (it re-presses Enter when the `/rc · Enter to view` affordance eats the
# first one — which is exactly what a --remote orchestrator draws).
#
# Keymap line (leader chord, palette name "Tasks loop"):
#   command "Tasks loop"  ctrl+b>a>l  $HOME/.claude/skills/tasks-loop/scripts/tasks-loop-launch.sh
#
# Failures surface as a desktop notification (a keymap command has no terminal).

PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
DRIVER="${TASKS_LOOP_DRIVER:-$HOME/.config/agent-driver/bash-claude-agterm/claude-agterm}"

fail() {
  agtermctl notify "$1" --title "tasks-loop" 2>/dev/null
  exit 1
}

# The driver answers one JSON line; a failure carries {"error":…,"message":…}. A notification has
# no room for the envelope — lift the message out, fall back to the whole line.
drv_fail() {  # drv_fail <what> <driver output>
  msg=$(printf '%s' "$2" | sed -n 's/.*"message":"\([^"]*\)".*/\1/p' | head -1)
  fail "$1: ${msg:-$2}"
}

flags=""
run_label="default"
has_auto=0
has_keep=0
has_list=0
has_resume=0
has_remote=0
remote_tag=""

while :; do
  items="[{\"id\":\"run\",\"label\":\"$run_label\",\"subtitle\":\"/tasks-loop$flags$remote_tag\"}"
  [ "$has_auto" = 0 ] && items="$items,{\"id\":\"auto\",\"label\":\"auto\",\"subtitle\":\"add --auto: skip human checkpoints (e.g. plan review)\"}"
  [ "$has_keep" = 0 ] && items="$items,{\"id\":\"keep\",\"label\":\"keep\",\"subtitle\":\"add --keep: keep worker sessions + worktrees\"}"
  [ "$has_list" = 0 ] && items="$items,{\"id\":\"list\",\"label\":\"list\",\"subtitle\":\"add --list: show actionable issues, pick which to process\"}"
  [ "$has_resume" = 0 ] && items="$items,{\"id\":\"resume\",\"label\":\"resume\",\"subtitle\":\"add --resume: continue the unfinished local session\"}"
  [ "$has_remote" = 0 ] && items="$items,{\"id\":\"remote\",\"label\":\"remote\",\"subtitle\":\"start claude with --remote-control (steer from claude.ai / phone)\"}"
  [ -n "$flags$remote_tag" ] && items="$items,{\"id\":\"clear\",\"label\":\"clear\",\"subtitle\":\"drop selection, back to the first menu\"}"
  items="$items]"

  choice=$(printf '%s' "$items" | agtermctl pick --prompt "tasks-loop$flags$remote_tag") || exit 0  # cancelled -> no-op

  case "$(printf '%s' "$choice" | sed -n 's/.*"id":"\([^"]*\)".*/\1/p')" in
    run)    break ;;
    auto)   flags="$flags --auto"; has_auto=1; run_label="run" ;;
    keep)   flags="$flags --keep"; has_keep=1; run_label="run" ;;
    list)   flags="$flags --list"; has_list=1; run_label="run" ;;
    resume) flags="$flags --resume"; has_resume=1; run_label="run" ;;
    remote) has_remote=1; remote_tag=" · remote"; run_label="run" ;;
    clear)  flags=""; has_auto=0; has_keep=0; has_list=0; has_resume=0; has_remote=0; remote_tag=""; run_label="default" ;;
    *)      exit 0 ;;
  esac
done

[ -x "$DRIVER" ] || fail "agent driver not found or not executable: $DRIVER"

# A keymap command is launched by agterm, not by a session's shell, so it gets $AGT_WORKSPACE_ID
# and NOT the $AGTERM_WORKSPACE_ID the driver defaults to. Pass it when we have it; when this is
# run from a shell inside a session instead, leave it out and let that default apply.
ws="${AGT_WORKSPACE_ID:-}"
cwd="${AGT_SESSION_PWD:-$PWD}"

# The driver addresses a worker by NAME and resolves it across the whole tree, first match wins.
# One "tasks-loop" session per project is the normal state here, so a fixed name would have sent
# this run's turn into the OLDEST one — another repo's orchestrator — with nothing on screen to
# say so. Claim a name no live session answers to.
name="tasks-loop"
taken=$(agtermctl tree --json 2>/dev/null | jq -r '.result.tree.workspaces[].sessions[].name' 2>/dev/null)
n=1
while printf '%s\n' "$taken" | grep -qxF "$name"; do
  n=$((n + 1)); name="tasks-loop-$n"
done

set -- spawn --name "$name" --cwd "$cwd"
[ -n "$ws" ] && set -- "$@" --workspace "$ws"
# An explicit label, because the driver's default is worker:<name>: this session ORCHESTRATES
# workers, and on a phone it has to read apart from the workers it spawns.
[ "$has_remote" = 1 ] && set -- "$@" --remote "$name"

out=$("$DRIVER" "$@" 2>&1) || drv_fail "spawn failed" "$out"

# The orchestrator runs at HIGH effort — the skill says why, and it is about which mistakes have
# no undo, not about output length. Sent as a slash command because the driver cannot set it at
# spawn (`spawn --model M[:effort]` is agent-driver #15); when that lands, this moves into the
# spawn line above and the model goes with it.
#
# Two caveats, both deliberate:
#  - /effort ALSO rewrites the user's saved default for new sessions. There is no session-scoped
#    form, and a loop that thinks properly is worth that; it is called out here so it is a known
#    cost rather than a surprise.
#  - best effort, and NOT fatal: a run at the session's default effort is worse, not broken.
#    Aborting a launch over it would be the bigger failure.
"$DRIVER" say "$name" "/effort high" >/dev/null 2>&1 \
  || agtermctl notify "effort not set — orchestrator is on the session default" \
       --title "tasks-loop" 2>/dev/null

# /effort is handled locally and should not make the session busy — but `say` into a BUSY session
# queues the turn instead of submitting it (driver contract), which would land /tasks-loop late
# and unreported. Settle on `status`, which is non-blocking; `wait --for idle` would spend its
# 10s last-recorded grace here for nothing, since a local command records no answer.
i=0
while [ "$i" -lt 10 ]; do
  "$DRIVER" status "$name" 2>/dev/null | grep -q '"state":"busy"' || break
  sleep 0.5
  i=$((i + 1))
done

out=$("$DRIVER" say "$name" "/tasks-loop$flags" 2>&1) \
  || drv_fail "session '$name' is up but /tasks-loop was not submitted" "$out"

# spawn hands back the name — agterm ids are the driver's business, not its contract — and
# agtermctl targets ids only (a name is "no such session"). Resolve the one we just named; it is
# unique by construction. Best effort: the run is already under way either way.
sid=$(agtermctl tree --json 2>/dev/null \
  | jq -r --arg n "$name" '[.result.tree.workspaces[].sessions[] | select(.name == $n) | .id][0] // empty' 2>/dev/null)
[ -n "$sid" ] && agtermctl session select --target "$sid" >/dev/null 2>&1
exit 0   # the run is started; a missed lookup is not a failed launch
