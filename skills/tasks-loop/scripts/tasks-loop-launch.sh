#!/bin/sh
# tasks-loop-launch.sh — agterm keymap command: start a tasks-loop run in a NEW session.
#
# Cascading native picker that accumulates flags:
#   menu 1:  default | auto | keep | remote   — "default" runs bare /tasks-loop immediately
#   pick an item -> next menu: run (with selection so far) | the items not yet added | clear
#   "run" executes; "clear" resets to menu 1; cancelling any menu aborts silently.
# auto/keep become /tasks-loop flags; "remote" instead starts claude itself with
# --remote-control tasks-loop, so the orchestrator is steerable from claude.ai/phone.
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
has_remote=0
remote_tag=""

while :; do
  items="[{\"id\":\"run\",\"label\":\"$run_label\",\"subtitle\":\"/tasks-loop$flags$remote_tag\"}"
  [ "$has_auto" = 0 ] && items="$items,{\"id\":\"auto\",\"label\":\"auto\",\"subtitle\":\"add --auto: skip human checkpoints (e.g. plan review)\"}"
  [ "$has_keep" = 0 ] && items="$items,{\"id\":\"keep\",\"label\":\"keep\",\"subtitle\":\"add --keep: keep worker sessions + worktrees\"}"
  [ "$has_remote" = 0 ] && items="$items,{\"id\":\"remote\",\"label\":\"remote\",\"subtitle\":\"start claude with --remote-control (steer from claude.ai / phone)\"}"
  [ -n "$flags$remote_tag" ] && items="$items,{\"id\":\"clear\",\"label\":\"clear\",\"subtitle\":\"drop selection, back to the first menu\"}"
  items="$items]"

  choice=$(printf '%s' "$items" | agtermctl pick --prompt "tasks-loop$flags$remote_tag") || exit 0  # cancelled -> no-op

  case "$(printf '%s' "$choice" | sed -n 's/.*"id":"\([^"]*\)".*/\1/p')" in
    run)    break ;;
    auto)   flags="$flags --auto"; has_auto=1; run_label="run" ;;
    keep)   flags="$flags --keep"; has_keep=1; run_label="run" ;;
    remote) has_remote=1; remote_tag=" · remote"; run_label="run" ;;
    clear)  flags=""; has_auto=0; has_keep=0; has_remote=0; remote_tag=""; run_label="default" ;;
    *)      exit 0 ;;
  esac
done

ws="${AGT_WORKSPACE_ID:-active}"
cwd="${AGT_SESSION_PWD:-$HOME}"

if [ "$has_remote" = 1 ]; then
  # agent-ctl's default spawn command (prompt-suggestion triple-kill) + --remote-control.
  # Keep in sync with agent-ctl.sh spawn.
  sid=$("$CTL" spawn "$ws" "tasks-loop" "$cwd" \
    "zsh -ic 'CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION=false claude --settings \"{\\\"promptSuggestionEnabled\\\": false}\" --prompt-suggestions false --remote-control tasks-loop'") \
    || fail "spawn failed (see agent-ctl.sh)"
else
  sid=$("$CTL" spawn "$ws" "tasks-loop" "$cwd") || fail "spawn failed (see agent-ctl.sh)"
fi
"$CTL" ready "$sid" >/dev/null || fail "claude not ready in tasks-loop session (usage limit / login?)"
"$CTL" say "$sid" "/tasks-loop$flags" >/dev/null || fail "could not submit /tasks-loop"
agtermctl session select "$sid" >/dev/null 2>&1
