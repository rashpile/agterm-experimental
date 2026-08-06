#!/bin/zsh
# Send the fork's answer back to the base tab's prompt — unsubmitted.
#
#   btw-back.sh --line "<one sentence>" [--dry-run]      < answer-body.md
#   btw-back.sh --compose "<text>" --answer-path <path>  # composer only, for tests
#
# Two artifacts: the full answer goes to a file, and ONE line goes into the
# base prompt. `agtermctl session type` turns every newline into Return, so a
# multi-line brief would submit itself line by line — single line is a safety
# constraint, not a style rule.
#
# Whether the base prompt gets CLEARED first or the line gets APPENDED to it
# depends on whether the base looks idle. This used to always clear with
# Ctrl+U first, on the theory that `session type` appends rather than
# replacing so clearing sidesteps ever having to tell an empty box from an
# occupied one. Live testing against a real Claude session proved that
# design wrong on its own terms:
#   - Ctrl+U only kills one *visual row*, not the whole input. A line
#     wrapping to two rows needed two presses; a three-row line was never
#     fully cleared even after eight spaced presses.
#   - Esc does nothing. Ctrl+A then Ctrl+K made it worse (kills the first
#     row, leaves the rest).
#   - Ctrl+C clears the box completely and reliably — but it also
#     interrupts a running turn, and this whole feature exists to work
#     while the base is mid-turn (that's why it's a keybinding rather than
#     a slash command: it has to work when you can't type into the base at
#     all).
# So: Ctrl+C only when the base is idle; append without clearing when it's
# busy. `btw_base_is_idle` in lib.sh decides which, and fails toward BUSY on
# any failure or ambiguity — a false "busy" here just leaves an untidy
# appended prompt, but a false "idle" would send Ctrl+C into a live turn and
# destroy work the user was waiting on. That check costs ~1.3s of latency
# (it re-samples the pane twice looking for spinner animation); accepted,
# see lib.sh for why.
#
# Either way, whatever was in the box beforehand (typed text, or an
# indistinguishable dimmed "ghost" suggestion — `session text` strips ANSI,
# so the two can't be told apart) is read and reported in this script's own
# output before anything is sent, so it's recoverable by copy-paste
# regardless of which path was taken.
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

dry=0 line=""
while (( $# )); do
  case $1 in
    --dry-run) dry=1; shift ;;
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

# Read whatever is currently in the box — typed text or an indistinguishable
# ghost suggestion, see the header comment — so it's recoverable from this
# script's own output regardless of which path (clear or append) is taken
# below.
prev=$(btw_prompt_text "$base_sid" "$base_pane")

out=$(btw_compose_line "$line" "$answer")

# Costs ~1.3s: samples the pane twice looking for spinner animation. See
# btw_base_is_idle in lib.sh for the two signals and why this must fail
# toward busy, never toward idle.
if btw_base_is_idle "$base_sid" "$base_pane"; then idle=1; else idle=0; fi

if (( dry )); then
  print -r -- "verdict: $verdict"
  if (( idle )); then
    if [[ -n $prev ]]; then
      print -r -- "btw-back: base idle -- would clear prompt (Ctrl+C; was: $prev) and send"
    else
      print -r -- "btw-back: base idle -- would clear prompt (Ctrl+C) and send"
    fi
    print -r -- "$AGTERMCTL session type <ctrl-c> --target $base_sid --pane $base_pane"
  else
    if [[ -n $prev ]]; then
      print -r -- "btw-back: base busy -- would append without clearing (prompt already has: $prev)"
    else
      print -r -- "btw-back: base busy -- would append without clearing"
    fi
  fi
  print -r -- "$AGTERMCTL session type <line> --target $base_sid --pane $base_pane"
  print -r -- "$out"
  exit 0
fi

if (( idle )); then
  "$AGTERMCTL" session type $'\x03' --target "$base_sid" --pane "$base_pane" >/dev/null \
    || refuse "clear failed"
fi

"$AGTERMCTL" session type "$out" --target "$base_sid" --pane "$base_pane" >/dev/null \
  || refuse "type failed"

"$AGTERMCTL" session status blocked --blink --auto-reset \
  --target "$base_sid" --pane "$base_pane" >/dev/null 2>&1

if (( idle )); then
  if [[ -n $prev ]]; then
    print -r -- "btw-back: base was idle -- cleared prompt (Ctrl+C; was: $prev) and sent"
  else
    print -r -- "btw-back: base was idle -- cleared prompt (Ctrl+C) and sent"
  fi
else
  if [[ -n $prev ]]; then
    print -r -- "btw-back: base was busy -- appended without clearing (prompt already had: $prev); it now has two things in it"
  else
    print -r -- "btw-back: base was busy -- appended without clearing"
  fi
fi
print -r -- "btw-back: sent to base tab (glyph raised, not submitted)"
print -r -- "answer: $answer"
