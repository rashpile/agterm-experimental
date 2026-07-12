#!/bin/bash
# nvim-zed-tree-run.sh — pane entrypoint for the quick-look file tree (ctrl+b>z, open-nvim-zed.sh).
#
# Why this exists rather than typing `exec env NVIM_APPNAME=… nvim` straight into the split:
# agterm restores a split pane (isSplit/splitCwd/splitRatio are persisted) but NOT the program in it —
# "Restore running commands on restart" captures only the MAIN pane's foreground command (this build
# reports splitForeground: null for every split), and even a captured `nvim` would come back WITHOUT
# NVIM_APPNAME/NVIM_ZED_ROLE, i.e. as a plain editor the toggle script cannot recognize.
#
# So the tree re-launches itself: while it runs, this script keeps a marker file naming its agterm
# session and holding its cwd. A restored right-pane shell finds that marker (see the agterm block at
# the end of ~/.config/zsh/.zshrc) and re-execs this script, rebuilding the tree with its env intact.
#
#   nvim-zed-tree-run.sh <cwd>      # AGTERM_SESSION_ID / AGTERM_PANE come from the pane's env
set -u
export PATH="/opt/homebrew/bin:$PATH"

STATE="$HOME/.config/agterm-projects/state"
cwd="${1:-$PWD}"
[ -d "$cwd" ] || cwd="$HOME"

marker=""
if [ -n "${AGTERM_SESSION_ID:-}" ]; then
  mkdir -p "$STATE"
  marker="$STATE/tree-$AGTERM_SESSION_ID"
  printf '%s\n' "$cwd" >"$marker"
fi

cd "$cwd" || cd "$HOME" || exit 1
env NVIM_APPNAME=nvim-zed NVIM_ZED_ROLE=tree nvim

# Quitting the tree means "I don't want it back next launch". A pane killed with the tree still
# running never reaches this line — that marker is the point, it is what brings the tree back.
[ -n "$marker" ] && rm -f "$marker"
exit   # nvim was the pane's reason to exist; leaving drops the split, as `exec nvim` used to
