#!/bin/zsh
# Claude SessionStart / SessionEnd hook: maintain the agterm tab registry.
#
#   register-tab.sh start   < hook-json-on-stdin
#   register-tab.sh end     < hook-json-on-stdin
#
# Writes $AGTERM_TABS_DIR/<agterm-session-id>.<pane>, a generic map of which
# Claude session runs in which agterm tab and pane. Not btw-specific; btw is
# its first consumer.
#
# Outside agterm this exits immediately and costs nothing.
set -u

[[ -n ${AGTERM_SESSION_ID:-} ]] || exit 0
command -v jq >/dev/null 2>&1 || exit 0

tabs_dir=${AGTERM_TABS_DIR:-$HOME/.claude/agterm/tabs}
pane=${AGTERM_PANE:-left}
file=$tabs_dir/$AGTERM_SESSION_ID.$pane

payload=$(cat)
sid=$(print -r -- "$payload" | jq -r '.session_id // empty' 2>/dev/null)

if [[ ${1:-start} == end ]]; then
  # Only delete our own registration. On /clear the ending and starting hooks
  # can interleave; deleting unconditionally would drop the fresh entry.
  cur=$(jq -r '.claude_session_id // empty' -- "$file" 2>/dev/null)
  [[ -n $sid && $sid == $cur ]] && rm -f -- "$file"
  exit 0
fi

# Never write a partial record.
[[ -n $sid ]] || exit 0

cwd=$(print -r -- "$payload" | jq -r '.cwd // empty' 2>/dev/null)
[[ -n $cwd ]] || cwd=$PWD

mkdir -p -- "$tabs_dir" || exit 0
jq -n \
  --arg cs "$sid" --arg as "$AGTERM_SESSION_ID" --arg p "$pane" \
  --arg pid "${AGTERM_PANE_ID:-}" --arg cwd "$cwd" \
  --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  '{claude_session_id: $cs, agterm_session_id: $as, pane: $p,
    pane_id: $pid, cwd: $cwd, updated_at: $ts}' > "$file"

# A fork tab must restore to ITSELF. Without this pin, agterm's auto-capture
# restores the literal `claude --resume <base> --fork-session` line and
# re-forks the base on every launch. Rewritten every start because a restored
# fork gets a new session id each time. Inert unless the user has agterm's
# "Restore running commands on restart" setting on.
btw_root=${BTW_ROOT:-$HOME/.claude/btw}
if [[ -f $btw_root/forks/$AGTERM_SESSION_ID/meta.json ]]; then
  agtermctl=${AGTERMCTL:-/opt/homebrew/bin/agtermctl}
  if [[ -x $agtermctl ]]; then
    if [[ -n ${AGTERM_PANE_ID:-} ]]; then
      "$agtermctl" session restore "claude --resume $sid" \
        --target "$AGTERM_SESSION_ID" --pane-id "$AGTERM_PANE_ID" >/dev/null 2>&1
    else
      "$agtermctl" session restore "claude --resume $sid" \
        --target "$AGTERM_SESSION_ID" >/dev/null 2>&1
    fi
  fi
fi
exit 0
