#!/usr/bin/env bash
# scratch.sh — quick scratch files for agterm, opened in a full overlay via nvim (no nvim-tree).
#
# Subcommands:
#   scratch.sh new     Pick a type (txt/json/html/markdown), create scratches/scratch_<N>.<ext>,
#                      open it in nvim.
#   scratch.sh open    fzf-pick an existing scratch file and open it in nvim.
#   scratch.sh last    Reopen the most recently opened scratch (no picker).
#
# Meant to run inside an agterm overlay (real TTY for fzf/nvim). Bound to cmd+shift+n>n / >o / >l.
set -uo pipefail

DIR="$HOME/.config/agterm-projects/scratches"
LAST="$DIR/.last"   # hidden pointer to the last-opened file (dotfile → not listed by the open picker)
mkdir -p "$DIR"

# nvim without nvim-tree: the default config doesn't auto-open the tree (that's the nvim-ide config),
# but set the plugin guard anyway so a scratch edit is always a plain editor. Every open is recorded
# to $LAST so `last` can reopen it (mtime is no good — merely opening a file doesn't change it).
#   edit <file> [extra nvim args...]   (e.g. -c startinsert to land in INSERT mode)
edit() { local f="$1"; shift; printf '%s\n' "$f" > "$LAST"; nvim --cmd 'let g:loaded_nvim_tree=1' "$@" -- "$f"; }

# Next free N across ALL extensions, so scratch_<N> is unique regardless of type.
next_n() {
  local n=0 f b num
  for f in "$DIR"/scratch_*.*; do
    [ -e "$f" ] || continue
    b="${f##*/}"; num="${b#scratch_}"; num="${num%%.*}"
    case "$num" in ''|*[!0-9]*) continue ;; esac
    [ "$num" -gt "$n" ] && n="$num"
  done
  echo $(( n + 1 ))
}

cmd_new() {
  local choice ext n file
  # Type picker. label -> extension (markdown -> md, the rest map to themselves).
  choice="$(printf '%s\n' txt json html markdown \
    | fzf --prompt='scratch type> ' --height=100% --no-multi)" || exit 0
  [ -n "$choice" ] || exit 0
  case "$choice" in
    markdown) ext=md ;;
    txt|json|html) ext="$choice" ;;
    *) echo "scratch: unknown type: $choice" >&2; exit 1 ;;
  esac
  n="$(next_n)"
  file="$DIR/scratch_${n}.${ext}"
  : > "$file"
  edit "$file" -c startinsert   # new scratch → land in INSERT mode, ready to type
}

cmd_open() {
  local sel
  # Newest first; preview with bat when available, else cat. cd so fzf sees bare filenames.
  cd "$DIR" || exit 1
  if ! ls -1t 2>/dev/null | grep -q .; then
    echo "scratch: no scratch files yet in $DIR — create one with 'scratch.sh new'." >&2
    sleep 1.5
    exit 0
  fi
  sel="$(ls -1t | fzf --prompt='open scratch> ' --height=100% --no-multi \
    --preview 'bat --style=plain --color=always {} 2>/dev/null || cat {}')" || exit 0
  [ -n "$sel" ] || exit 0
  edit "$DIR/$sel"
}

cmd_last() {
  local f
  f="$([ -r "$LAST" ] && head -1 "$LAST")"
  if [ -n "$f" ] && [ -e "$f" ]; then
    edit "$f"
  else
    # No recorded/valid last file — fall back to the picker instead of a dead end.
    echo "scratch: no last-opened scratch (yet) — pick one." >&2
    sleep 0.8
    cmd_open
  fi
}

case "${1:-}" in
  new)  cmd_new ;;
  open) cmd_open ;;
  last) cmd_last ;;
  *)    echo "usage: scratch.sh {new|open|last}" >&2; exit 2 ;;
esac
