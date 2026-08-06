#!/bin/zsh
# Send the fork's answer back to the base tab's prompt — unsubmitted.
#
#   btw-back.sh --line "<one sentence>" [--dry-run] [--force]  < answer-body.md
#   btw-back.sh --compose "<text>" --answer-path <path>        # composer only, for tests
#
# Two artifacts: the full answer goes to a file, and ONE line goes into the
# base prompt. `agtermctl session type` turns every newline into Return, so a
# multi-line brief would submit itself line by line — single line is a safety
# constraint, not a style rule.
set -u
# REQUIRED for the `[[:space:]]#` trims below — `#` is a zero-or-more
# quantifier only under extended globbing. Without it both trims are silent
# no-ops and stray whitespace reaches the user's live prompt.
setopt extended_glob

source "${0:A:h}/lib.sh"

MAXLINE=${BTW_MAX_LINE:-400}

btw_compose_line() {  # <text> <answer-path>
  local text=$1 path=$2
  # Trim BEFORE collapsing, not after: a leading or trailing newline would
  # otherwise already have become a " · " separator, which no whitespace trim
  # can remove. [[:space:]] covers \n, so this handles both.
  text=${text##[[:space:]]#}
  text=${text%%[[:space:]]#}
  # Collapse every newline run; nothing multi-line may reach `session type`.
  text=${text//$'\n'/ · }
  while [[ $text == *' ·  · '* ]]; do text=${text//' ·  · '/' · '}; done
  text=${text%.}

  local prefix="btw: " suffix=". Full notes: $path"
  local room=$(( MAXLINE - ${#prefix} - ${#suffix} ))
  if (( ${#text} > room )); then
    text="${text[1,$(( room - 1 ))]}…"
  fi
  print -r -- "${prefix}${text}${suffix}"
}

if [[ ${1:-} == --compose ]]; then
  btw_compose_line "$2" "$4"
  exit 0
fi

dry=0 force=0 line=""
while (( $# )); do
  case $1 in
    --dry-run) dry=1; shift ;;
    --force)   force=1; shift ;;
    --line)    (( $# >= 2 )) || btw_die "--line requires a value"
               line=$2; shift 2 ;;
    *) btw_die "unknown argument: $1" ;;
  esac
done
[[ -n $line ]] || btw_die "--line is required"

btw_need jq

meta=$(btw_meta) || exit 1
dir=$(btw_fork_dir)
base_sid=$(print -r -- "$meta"  | jq -r '.base_agterm_sid')
base_pane=$(print -r -- "$meta" | jq -r '.base_pane')

# Write the answer file FIRST: every refusal below must leave the work intact.
body=$(cat)
[[ -n ${body//[[:space:]]/} ]] || btw_die "no answer body on stdin"
mkdir -p -- "$dir"
n=1
while [[ -f $dir/answer-$(printf '%03d' $n).md ]]; do (( n++ )); done
answer=$dir/answer-$(printf '%03d' $n).md
print -r -- "$body" > "$answer"

refuse() {
  print -ru2 -- "btw-back: refused ($1)"
  print -ru2 -- "answer kept at: $answer"
  exit 1
}

verdict=$(btw_verdict "$base_sid" "$base_pane")
case $verdict in
  ok) ;;
  moved)
    base_pane=$(btw_pane_for "$base_sid")
    [[ -n $base_pane ]] || refuse ambiguous
    print -r -- "btw-back: base pane moved to '$base_pane'"
    ;;
  *) refuse "$verdict" ;;
esac

# `session type` APPENDS to whatever is already in the box, so injecting into
# half-typed text would mangle it. Fail closed when we cannot tell.
if (( ! force )) && ! btw_input_empty "$base_sid" "$base_pane"; then
  refuse busy-input
fi

out=$(btw_compose_line "$line" "$answer")

if (( dry )); then
  print -r -- "verdict: $verdict"
  print -r -- "$AGTERMCTL session type <line> --target $base_sid --pane $base_pane"
  print -r -- "$out"
  exit 0
fi

"$AGTERMCTL" session type "$out" --target "$base_sid" --pane "$base_pane" >/dev/null \
  || refuse "type failed"

"$AGTERMCTL" session status blocked --blink --auto-reset \
  --target "$base_sid" --pane "$base_pane" >/dev/null 2>&1

print -r -- "btw-back: sent to base tab (glyph raised, not submitted)"
print -r -- "answer: $answer"
