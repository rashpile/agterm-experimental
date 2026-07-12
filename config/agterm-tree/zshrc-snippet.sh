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
  if [[ -f $__agt_tree_marker ]]; then
    exec "$HOME/.config/agterm-projects/nvim-zed-tree-run.sh" "$(<$__agt_tree_marker)"
  fi
  unset __agt_tree_marker
fi
