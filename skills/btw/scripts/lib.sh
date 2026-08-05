#!/bin/zsh
# Shared helpers for claude-btw. Sourced by btw-sync.sh, btw-back.sh and
# claude-btw.sh. Every root is overridable so the test suite can redirect it.

AGTERM_TABS_DIR=${AGTERM_TABS_DIR:-$HOME/.claude/agterm/tabs}
BTW_ROOT=${BTW_ROOT:-$HOME/.claude/btw}
AGTERMCTL=${AGTERMCTL:-/opt/homebrew/bin/agtermctl}
CLAUDE_PROJECTS=${CLAUDE_PROJECTS:-$HOME/.claude/projects}

btw_die() { print -ru2 -- "btw: $*"; exit 1 }

btw_need() {
  local c
  for c in "$@"; do
    command -v "$c" >/dev/null 2>&1 || btw_die "missing dependency: $c"
  done
}

# Session ids are UUIDs and globally unique, so never derive the project slug.
btw_transcript() {
  local hits=( $CLAUDE_PROJECTS/*/$1.jsonl(N) )
  (( $#hits )) && print -r -- "$hits[1]"
}

btw_registry() {
  local f=$AGTERM_TABS_DIR/$1.$2
  [[ -f $f ]] && cat -- "$f"
}

btw_meta() {
  local f=$BTW_ROOT/forks/${AGTERM_SESSION_ID:-none}/meta.json
  [[ -f $f ]] || btw_die "this tab is not a btw fork"
  cat -- "$f"
}

btw_pane_for() {
  local hits=( $AGTERM_TABS_DIR/$1.*(N) )
  (( $#hits == 1 )) && print -r -- "${${hits[1]:t}##*.}"
}

btw_verdict() {  # <agterm-sid> <pane> -> ok|moved|ambiguous|gone
  local sid=$1 pane=$2 tree
  tree=$("$AGTERMCTL" tree --json 2>/dev/null) || { print -r -- gone; return }
  print -r -- "$tree" | jq -e --arg s "$sid" '
    [ .result.tree.workspaces[].sessions[]
      | select((.id | ascii_downcase) == ($s | ascii_downcase)) ] | length > 0
  ' >/dev/null 2>&1 || { print -r -- gone; return }

  if [[ -f $AGTERM_TABS_DIR/$sid.$pane ]]; then print -r -- ok; return; fi

  local hits=( $AGTERM_TABS_DIR/$sid.*(N) )
  case $#hits in
    1) print -r -- moved ;;
    0) print -r -- gone ;;
    *) print -r -- ambiguous ;;
  esac
}

# Fail closed: anything we cannot read as an empty prompt counts as occupied.
btw_input_empty() {  # <agterm-sid> <pane>
  local txt line
  txt=$("$AGTERMCTL" session text --lines 6 --target "$1" --pane "$2" --json 2>/dev/null \
        | jq -r '.result.text // ""' 2>/dev/null) || return 1
  line=$(print -r -- "$txt" | grep -- '>' | tail -1)
  [[ -n $line ]] || return 1
  line=${line//[│┃|]/}
  line=${line#*>}
  line=${line//[[:space:]]/}
  [[ -z $line ]]
}
