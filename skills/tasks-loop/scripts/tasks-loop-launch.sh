#!/bin/sh
# tasks-loop-launch.sh — agterm keymap command: start a tasks-loop run in a NEW session.
#
# Cascading native picker that accumulates flags:
#   menu 1:  default | auto | keep      — "default" runs bare /tasks-loop immediately
#   pick a flag -> next menu: run (with flags so far) | the flags not yet added
#   "run" executes; cancelling any menu aborts silently.
# Then: spawn a fresh claude session in the current workspace at the current session's
# cwd (via agent-ctl.sh, which carries the prompt-suggestion kill and ready-marker wait),
# submit "/tasks-loop <flags>", select the new session.
#
# Keymap line (leader chord, palette name "Tasks loop"):
#   command "Tasks loop"  ctrl+b>a>l  $HOME/.claude/skills/tasks-loop/scripts/tasks-loop-launch.sh
#
# Failures surface as a desktop notification (a keymap command has no terminal).

PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
CTL="$HOME/.claude/skills/orchestrating-agterm-agents/scripts/agent-ctl.sh"

fail() {
  agtermctl notify "$1" --title "tasks-loop" 2>/dev/null
  exit 1
}

flags=""
run_label="default"
has_auto=0
has_keep=0

while :; do
  items="[{\"id\":\"run\",\"label\":\"$run_label\",\"subtitle\":\"/tasks-loop$flags\"}"
  [ "$has_auto" = 0 ] && items="$items,{\"id\":\"auto\",\"label\":\"auto\",\"subtitle\":\"add --auto: skip human checkpoints (e.g. plan review)\"}"
  [ "$has_keep" = 0 ] && items="$items,{\"id\":\"keep\",\"label\":\"keep\",\"subtitle\":\"add --keep: keep worker sessions + worktrees\"}"
  [ -n "$flags" ] && items="$items,{\"id\":\"clear\",\"label\":\"clear\",\"subtitle\":\"drop selected flags, back to the first menu\"}"
  items="$items]"

  choice=$(printf '%s' "$items" | agtermctl pick --prompt "tasks-loop$flags") || exit 0  # cancelled -> no-op

  case "$(printf '%s' "$choice" | sed -n 's/.*"id":"\([^"]*\)".*/\1/p')" in
    run)   break ;;
    auto)  flags="$flags --auto"; has_auto=1; run_label="run" ;;
    keep)  flags="$flags --keep"; has_keep=1; run_label="run" ;;
    clear) flags=""; has_auto=0; has_keep=0; run_label="default" ;;
    *)     exit 0 ;;
  esac
done

ws="${AGT_WORKSPACE_ID:-active}"
cwd="${AGT_SESSION_PWD:-$HOME}"

sid=$("$CTL" spawn "$ws" "tasks-loop" "$cwd") || fail "spawn failed (see agent-ctl.sh)"
"$CTL" ready "$sid" >/dev/null || fail "claude not ready in tasks-loop session (usage limit / login?)"
"$CTL" say "$sid" "/tasks-loop$flags" >/dev/null || fail "could not submit /tasks-loop"
agtermctl session select "$sid" >/dev/null 2>&1
