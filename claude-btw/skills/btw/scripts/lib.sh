#!/bin/zsh
# Shared helpers for claude-btw. Sourced by btw-sync.sh, btw-back.sh and
# claude-btw.sh. Every root is overridable so the test suite can redirect it.
set -u

AGTERM_TABS_DIR=${AGTERM_TABS_DIR:-$HOME/.claude/agterm/tabs}
BTW_ROOT=${BTW_ROOT:-$HOME/.claude/btw}
AGTERMCTL=${AGTERMCTL:-/opt/homebrew/bin/agtermctl}
CLAUDE_PROJECTS=${CLAUDE_PROJECTS:-$HOME/.claude/projects}

btw_die() { print -ru2 -- "btw: $*"; exit 1 }

# Session ids are UUIDs end to end. Check the shape before any id is
# interpolated into a string that will itself be run as a shell command
# (rather than passed as a separate argv element) — defense in depth, since
# both ids in this codebase come from Claude Code's own hook payload.
btw_is_uuid() {
  [[ $1 =~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$' ]]
}

# The fork metadata directory for THIS tab, keyed by AGTERM_SESSION_ID. The
# case of that env var and the case of `.result.id` from `session new --json`
# (what claude-btw.sh keys the directory by at fork time) have been observed
# to differ, so both write and read sides normalize to lowercase here.
btw_fork_dir() { print -r -- "$BTW_ROOT/forks/${${AGTERM_SESSION_ID:-none}:l}" }

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
  local f=$(btw_fork_dir)/meta.json
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

# Reads the base pane's last prompt line and returns whatever follows the
# prompt marker, with box-drawing characters and surrounding whitespace
# stripped. Empty if no prompt line can be found (including on any agtermctl
# failure). Matches both the real Claude Code marker `❯` (U+276F) and a
# plain `>`, so this keeps working if the glyph ever changes.
#
# This is a READ, not a verdict. There used to be a `btw_input_empty` here
# that tried to judge "safe to type into" from this same text — it never
# worked (it only ever matched ASCII `>`, which Claude Code doesn't render,
# so it failed closed on every call), and worse, it couldn't have worked even
# with the right glyph: Claude Code renders a dimmed "ghost" suggestion in a
# genuinely empty box, `session text` strips ANSI before returning it, and a
# ghost suggestion is textually indistinguishable from something the user
# actually typed. So callers must not infer "empty" from an empty result
# here — there is no reliable way to tell "nothing typed" from "something
# typed" by reading the box. btw-back.sh now decides whether it's safe to
# clear at all via btw_base_is_idle below, and uses this function only to
# recover whatever was in the box beforehand for the user to see, on
# whichever path (clear or append) actually gets taken.
btw_prompt_text() {  # <agterm-sid> <pane>
  local txt line nbsp=$'\xc2\xa0'
  txt=$("$AGTERMCTL" session text --lines 6 --target "$1" --pane "$2" --json 2>/dev/null \
        | jq -r '.result.text // ""' 2>/dev/null) || { print -r -- ""; return }
  line=$(print -r -- "$txt" | grep -E -- '❯|>' | tail -1)
  [[ -n $line ]] || return
  line=${line//[│┃|]/}
  if [[ $line == *❯* ]]; then
    line=${line#*❯}
  else
    line=${line#*>}
  fi
  while [[ $line == [[:space:]]* || $line == $nbsp* ]]; do line=${line#?}; done
  while [[ $line == *[[:space:]] || $line == *$nbsp ]]; do line=${line%?}; done
  print -r -- "$line"
}

# Raw pane text (ANSI already stripped, as `session text` returns it), for
# the busy/idle check below only — this is a lower-level read than
# btw_prompt_text and returns the whole sampled window, not just the prompt
# line. Prints nothing and returns non-zero on any agtermctl or jq failure,
# so callers fail toward busy the same way btw_verdict fails toward gone.
btw_pane_tail() {  # <agterm-sid> <pane> <lines>
  local sid=$1 pane=$2 lines=$3 raw txt
  raw=$("$AGTERMCTL" session text --lines "$lines" --target "$sid" --pane "$pane" --json 2>/dev/null) \
    || return 1
  txt=$(print -r -- "$raw" | jq -r '.result.text // empty' 2>/dev/null) || return 1
  print -r -- "$txt"
}

# Whether a pane-text sample contains a recognisable prompt line — i.e. this
# looks like an actual Claude Code prompt box, not something else (a shell,
# a different screen, truncated/garbled output). Same markers as
# btw_prompt_text: the real `❯` (U+276F), with a plain `>` as a fallback.
btw_has_prompt_block() {  # <pane-text>
  [[ $1 == *❯* || $1 == *'>'* ]]
}

# Whether a pane-text sample shows the LIVE form of the spinner line: a
# gerund with a trailing ellipsis and a parenthesised elapsed-time counter —
# real captures: `✻ Enchanting… (4s · ↓ 23 tokens)`, `✻ Flambéing… (5s · ↓
# 167 tokens · thought for 1s)`. This form appears ONLY while a turn is
# actively running. A finished turn instead shows a past-tense line with no
# parenthesised timer — `✻ Cooked for 11s`, `✻ Worked for 6s` — which this
# pattern does NOT match. A busy pane commonly shows BOTH lines at once (the
# previous turn's past-tense summary stays on screen while the live one
# animates below it), so the mere presence — or absence — of `✻` proves
# nothing either way; only the ellipsis-plus-timer shape does. Do not key on
# the verb: it varies (Cooked, Worked, Enchanting, Flambéing, and reportedly
# many more) and is not an enumerable set.
btw_has_live_timer() {  # <pane-text>
  print -r -- "$1" | grep -qE '✻.*….*\([0-9]+s'
}

# Positive-evidence-only idle check for the BASE pane. Returns 0 only when
# there is direct evidence the base is idle; non-zero (treat as busy) on
# every failure, ambiguity, or unrecognised state.
#
# This polarity is deliberate and load-bearing, and it is the OPPOSITE of
# btw_verdict's default (which fails toward "gone"): a false "busy" here
# only makes btw-back.sh append instead of clearing — untidy, harmless. A
# false "idle" sends Ctrl+C into a live turn and destroys work the user was
# waiting on. Established by live testing against a real Claude session,
# not assumption — see btw-back.sh's header comment for what was tried and
# ruled out (Ctrl+U, Esc, Ctrl+A+Ctrl+K) before landing here.
#
# Two independent signals, OR'd — either one being ambiguous or unreachable
# means busy:
#   Signal 1 (primary): a running turn animates its spinner, so the pane's
#     last lines change between two samples taken ~1.3s apart; an idle pane
#     is static between them. Doesn't depend on parsing any particular
#     string, which is why it's primary.
#   Signal 2 (secondary): btw_has_live_timer above, checked on every sample.
#     Catches what Signal 1 can miss — e.g. a turn that starts and finishes
#     within the sampling gap would leave the two samples looking alike, but
#     the live-timer line's mere presence in a sample already proves busy.
#
# BTW_IDLE_SAMPLE_DELAY controls the gap between the two samples in
# seconds; the real cost of this whole check. The test suite overrides it to
# stay fast, since the fake agtermctl doesn't care how much real time
# passed. In production this is left at its ~1.3s default because that's
# what live testing showed was needed to reliably see the spinner move.
btw_base_is_idle() {  # <agterm-sid> <pane>
  local sid=$1 pane=$2 delay=${BTW_IDLE_SAMPLE_DELAY:-1.3} s1 s2

  s1=$(btw_pane_tail "$sid" "$pane" 16) || return 1
  btw_has_prompt_block "$s1" || return 1
  btw_has_live_timer "$s1" && return 1

  sleep "$delay"

  s2=$(btw_pane_tail "$sid" "$pane" 16) || return 1
  btw_has_prompt_block "$s2" || return 1
  btw_has_live_timer "$s2" && return 1

  [[ $s1 == $s2 ]]
}
