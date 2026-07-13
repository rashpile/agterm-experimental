# agterm-tree — restore hook. Append to the END of ~/.zshrc (or ~/.config/zsh/.zshrc).
#
# Why: agterm restores a split pane on restart (isSplit/splitCwd/splitRatio persist) but NOT the
# program running in it — "restore running commands" captures only the MAIN pane's foreground
# command, and even a captured `nvim` would come back without NVIM_APPNAME/NVIM_ZED_ROLE, i.e. as a
# plain editor rather than the tree.
#
# So the tree re-launches itself: while it runs, nvim-zed-tree-run.sh keeps a marker file naming its
# session and holding its cwd. A restored right-pane shell finds that marker and re-execs the tree,
# env intact. Quitting the tree removes the marker — "I don't want it back next launch".
#
# Must be LAST in the file: this replaces the shell, so nothing after it would run.
if [[ ${AGTERM_PANE:-} == right && -n ${AGTERM_SESSION_ID:-} ]]; then
  __agt_tree_marker="$HOME/.config/agterm-projects/state/tree-$AGTERM_SESSION_ID"

  # …but only if this session has no tree ALREADY. The marker says "this session wants a tree", not
  # "this session has none" — so a *second* right-pane shell born while the first tree still runs would
  # exec a duplicate off the same marker. Reachable since agterm 0.12: it promotes a surviving pane into
  # the main slot, and a later `split on` opens a fresh right pane beside the promoted tree. Restore
  # after a restart is unaffected — there the old tree is gone, so nothing matches and this stays out
  # of the way.
  __agt_tree_live=""
  for __agt_p in ${(f)"$(pgrep -x nvim 2>/dev/null)"}; do
    [[ -n $__agt_p ]] || continue
    __agt_e="$(ps eww -o command= -p $__agt_p 2>/dev/null | tr ' ' '\n')"
    if grep -qxF "AGTERM_SESSION_ID=$AGTERM_SESSION_ID" <<<"$__agt_e" \
       && grep -qxF "NVIM_ZED_ROLE=tree" <<<"$__agt_e"; then
      __agt_tree_live=1
      break
    fi
  done

  if [[ -f $__agt_tree_marker && -z $__agt_tree_live ]]; then
    exec "$HOME/.config/agterm-projects/nvim-zed-tree-run.sh" "$(<$__agt_tree_marker)"
  fi
  unset __agt_tree_marker __agt_tree_live __agt_p __agt_e
fi
