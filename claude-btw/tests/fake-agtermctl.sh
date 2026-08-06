#!/bin/zsh
# Canned agtermctl for verdict tests. Answers `tree --json` from $FAKE_TREE_IDS
# (space-separated session ids) and `session text` from $FAKE_PANE_TEXT.
# FAKE_FAIL=1 makes every subcommand fail closed (nonzero exit, no stdout),
# simulating agtermctl being unreachable or erroring — the fail-closed
# contract that btw_verdict (-> gone) depends on, and the empty-read contract
# btw_prompt_text depends on.
#
# For simulating animation (btw_base_is_idle samples the pane twice and
# compares), set $FAKE_PANE_TEXT_SEQ to successive `session text` responses
# joined by a single SOH byte ($'\x01') and $FAKE_PANE_TEXT_SEQ_STATE to a
# scratch file path this script can use as a call counter. Each `session
# text` call consumes the next response in the list; once the list is
# exhausted, the last response repeats. Takes priority over $FAKE_PANE_TEXT
# when set. Each invocation of this script is a fresh process, hence the
# counter file instead of an in-memory index.
#
# If $FAKE_CALL_LOG is set, every invocation's full argv is appended to it
# (one call per line, args tab-separated) — so tests can assert not just what
# this fake returned, but that a specific call (e.g. the Ctrl+C clear) was
# actually made.
set -u

if [[ -n ${FAKE_CALL_LOG:-} ]]; then
  { for a in "$@"; do print -rn -- "$a"$'\t'; done; print -r -- } >> "$FAKE_CALL_LOG"
fi

if [[ -n ${FAKE_FAIL:-} ]]; then
  exit 1
fi

case "${1:-}:${2:-}" in
  tree:*)
    ids=(${=FAKE_TREE_IDS:-})
    if (( $#ids )); then
      jq -n --argjson s "$(printf '%s\n' "${ids[@]}" | jq -R . | jq -s .)" \
            '{result: {tree: {workspaces: [{sessions: ($s | map({id: .}))}]}}}'
    else
      jq -n '{result: {tree: {workspaces: [{sessions: []}]}}}'
    fi
    ;;
  session:text)
    if [[ -n ${FAKE_PANE_TEXT_SEQ:-} ]]; then
      [[ -n ${FAKE_PANE_TEXT_SEQ_STATE:-} ]] \
        || { print -ru2 -- "fake-agtermctl: FAKE_PANE_TEXT_SEQ requires FAKE_PANE_TEXT_SEQ_STATE"; exit 1; }
      seq=("${(@ps:\x01:)FAKE_PANE_TEXT_SEQ}")
      idx=0
      [[ -f $FAKE_PANE_TEXT_SEQ_STATE ]] && idx=$(<"$FAKE_PANE_TEXT_SEQ_STATE")
      (( idx >= $#seq )) && idx=$(( $#seq - 1 ))
      txt=$seq[$(( idx + 1 ))]
      print -r -- $(( idx + 1 )) > "$FAKE_PANE_TEXT_SEQ_STATE"
      jq -n --arg t "$txt" '{result: {text: $t}}'
    else
      jq -n --arg t "${FAKE_PANE_TEXT:-}" '{result: {text: $t}}'
    fi
    ;;
  *)
    print -r -- '{"result":"ok"}'
    ;;
esac
