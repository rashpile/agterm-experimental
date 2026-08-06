#!/bin/zsh
# Canned agtermctl for verdict tests. Answers `tree --json` from $FAKE_TREE_IDS
# (space-separated session ids) and `session text` from $FAKE_PANE_TEXT.
# FAKE_FAIL=1 makes every subcommand fail closed (nonzero exit, no stdout),
# simulating agtermctl being unreachable or erroring — the fail-closed
# contract that btw_verdict (-> gone) depends on, and the empty-read contract
# btw_prompt_text depends on.
#
# If $FAKE_CALL_LOG is set, every invocation's full argv is appended to it
# (one call per line, args tab-separated) — so tests can assert not just what
# this fake returned, but that a specific call (e.g. the Ctrl+U clear) was
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
    jq -n --arg t "${FAKE_PANE_TEXT:-}" '{result: {text: $t}}'
    ;;
  *)
    print -r -- '{"result":"ok"}'
    ;;
esac
