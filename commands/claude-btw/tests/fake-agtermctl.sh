#!/bin/zsh
# Canned agtermctl for verdict tests. Answers `tree --json` from $FAKE_TREE_IDS
# (space-separated session ids) and `session text` from $FAKE_PANE_TEXT.
set -u

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
