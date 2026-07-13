#!/bin/bash
# open-nvim-zed.sh — quick-look file tree, TOGGLE. Adds a RIGHT split to the CURRENT session running
# a tree-only nvim (nvim-zed, role=tree); the left/main pane is untouched. Selecting a file opens it
# in a 90% agterm overlay (see open-nvim-zed-preview.sh). Bound to ctrl+b>z.
#
# Pressing it again while the tree is showing HIDES the split but keeps nvim running, so the next
# press REVEALS the same already-drawn tree instantly (no relaunch flash). nvim persists hidden until
# the session closes; the self-heal below reaps it if its pane ever dies.
#   open-nvim-zed.sh <session-id> <cwd>
#
# The right pane is shared with the tmux-ide split (ctrl+b>ctrl+b>t), so "is the tree open?" cannot be
# answered from the split flag alone — agterm's tree JSON reports `split` as VISIBILITY, and this build
# emits no `splitForeground`. Instead we look for a live nvim whose environment carries this session's
# id, AGTERM_PANE=right and NVIM_ZED_ROLE=tree. A tmux-ide split never matches, so `z` leaves it alone.
set -uo pipefail
export PATH="/opt/homebrew/bin:$PATH"
AGT=/opt/homebrew/bin/agtermctl
JQ="$(command -v jq || true)"
RUN="$HOME/.config/agterm-projects/nvim-zed-tree-run.sh"   # the pane entrypoint (owns the marker)
STATE="$HOME/.config/agterm-projects/state"                # tree-<session-id> markers live here

sid="${1:?usage: open-nvim-zed.sh <session-id> <cwd>}"
cwd="${2:-$HOME}"

# pid of THIS session's nvim-zed tree, or empty. Two nvim processes exist per tree (main + embed);
# either one answering is enough, and quitting the main takes both down.
zed_pid() {
  local pid env
  for pid in $(pgrep -x nvim 2>/dev/null); do
    env=$(ps eww -o command= -p "$pid" 2>/dev/null | tr ' ' '\n')
    grep -qxF "AGTERM_SESSION_ID=$sid" <<<"$env" || continue
    grep -qxF "AGTERM_PANE=right"      <<<"$env" || continue
    grep -qxF "NVIM_ZED_ROLE=tree"     <<<"$env" || continue
    printf '%s' "$pid"
    return 0
  done
  return 1
}

# Is the split pane currently SHOWN? (hidden and absent both report false)
split_visible() {
  [ -n "$JQ" ] || return 1
  [ "$("$AGT" tree --json 2>/dev/null | "$JQ" -r --arg id "$sid" \
      'first(.. | objects | select(.id? == $id)) | (.split == true)')" = "true" ]
}

# Is $1 an ORPHANED tree nvim — process alive but its pane host died, so it reparented to
# launchd (PPID 1)? A genuinely hidden split keeps a live parent, so PPID != 1. One cheap ps,
# no agterm round-trip. This is what tells a stale ghost apart from a togglable hidden tree.
is_orphan() {
  [ "$(ps -o ppid= -p "$1" 2>/dev/null | tr -d ' ')" = "1" ]
}

# Does the session have only ONE pane? Since agterm 0.12 the session JSON carries a `surfaces` array;
# a split session lists two (kind left + right) whether or not both are visible, so a length of 1 means
# the split is GONE, not merely hidden. `split` alone cannot say this — it reports visibility, and is
# false for both "hidden" and "promoted".
single_pane() {
  [ -n "$JQ" ] || return 1
  [ "$("$AGT" tree --json 2>/dev/null | "$JQ" -r --arg id "$sid" \
      'first(.. | objects | select(.id? == $id)) | ((.surfaces | length) == 1)')" = "true" ]
}

# Kill this session's tree (both procs — main + embed) and drop its marker, so nothing restores it.
# A tree pane is a file browser with no unsaved edits, so a hard kill is safe.
kill_tree() {
  kill "$1" 2>/dev/null
  for _ in $(seq 1 20); do
    local p; p="$(zed_pid || true)"
    [ -z "$p" ] && break
    kill -9 "$p" 2>/dev/null
    sleep 0.05
  done
  rm -f "$STATE/tree-$sid"
}

pid="$(zed_pid || true)"

# ── agterm 0.12: the tree got PROMOTED into the main slot ─────────────────────
# When the primary pane's shell exits, 0.12 promotes the surviving pane into the main slot (#121)
# instead of leaving a dead half-session. If that survivor is our tree, the session is now a lone file
# browser with no shell — and every signal the branches below rely on still says "hidden tree": the
# process is untouched (same pid, same parent, so is_orphan() is false) and its env still reads
# AGTERM_PANE=right. The "reveal it" branch would then `split on` a FRESH pane beside the tree, whose
# right-pane shell finds the marker and launches a SECOND tree. Two trees, no shell, dead session.
#
# So: recognise it and finish the job the exiting shell started — kill the tree and let the session
# close. That is what `exit` meant; the tree was only keeping the corpse alive.
if [ -n "$pid" ] && single_pane; then
  kill_tree "$pid"
  exit 0
fi

# ── Self-heal: reap a stale ghost ─────────────────────────────────────────────
# zed_pid matches by process env, so an nvim that outlived its pane still "counts" as an open
# tree — and with no split visible, the branch below would try to REVEAL it, opening an empty
# pane forever (the process isn't attached to anything). Detect that (PPID 1 + no split shown),
# kill it, and fall through to a fresh launch. A tree pane is a file browser with no unsaved
# edits, so a hard kill is safe. Cheap ps gate first, so the agterm call only runs for a ghost.
if [ -n "$pid" ] && is_orphan "$pid" && ! split_visible; then
  kill_tree "$pid"   # the marker it drops is rewritten by the fresh launch below
  pid=""
fi

# ── The tree is running ───────────────────────────────────────────────────────
if [ -n "$pid" ]; then
  if ! split_visible; then
    # Hidden (e.g. toggled away from the GUI) — reveal it rather than kill it.
    "$AGT" session split on --target "$sid" >/dev/null 2>&1
    "$AGT" session focus right --target "$sid" >/dev/null 2>&1
    exit 0
  fi

  # Showing → HIDE it, keeping nvim running, so the next ctrl+b>z REVEALS the same drawn tree
  # instantly (no shell prompt, no rebuild flash). nvim persists hidden until the session closes;
  # the self-heal above reaps it if its pane ever dies.
  #
  # `split off` MAXIMIZES the focused pane — so a bare split off while the TREE is focused would
  # leave the tree showing full instead of hiding. Focus the main pane FIRST, then hide, so `z`
  # hides the tree no matter where focus was. Focus is async, so WAIT until it has actually landed
  # on the main pane (splitFocused=false) before hiding — else split off races focus and maximizes
  # the tree anyway.
  "$AGT" session focus left --target "$sid" >/dev/null 2>&1
  if [ -n "$JQ" ]; then
    for _ in $(seq 1 20); do
      [ "$("$AGT" tree --json 2>/dev/null | "$JQ" -r --arg id "$sid" \
          'first(.. | objects | select(.id? == $id)) | (.splitFocused == false)')" = "true" ] && break
      sleep 0.03
    done
  else
    sleep 0.2
  fi
  "$AGT" session split off --target "$sid" >/dev/null 2>&1
  exit 0
fi

# ── No tree of ours. A visible split belongs to something else (tmux-ide) — don't touch it. ──
if split_visible; then
  "$AGT" session focus right --target "$sid" >/dev/null 2>&1
  exit 0
fi

# ── Prune markers of sessions that no longer exist ───────────────────────────
# The tree survives an agterm restart via a marker file per session (nvim-zed-tree-run.sh writes it;
# the agterm block in ~/.config/zsh/.zshrc acts on it). A session closed while its tree ran leaves the
# marker behind — harmless (session ids never repeat), but sweep it here so the dir stays readable.
if [ -n "$JQ" ] && [ -d "$STATE" ]; then
  live=$("$AGT" tree --json 2>/dev/null | "$JQ" -r '[.. | objects | .id? // empty] | join(" ")')
  if [ -n "$live" ]; then
    for m in "$STATE"/tree-*; do
      [ -e "$m" ] || continue
      case " $live " in *" ${m##*/tree-} "*) ;; *) rm -f "$m" ;; esac
    done
  fi
fi

# ── Fresh launch ─────────────────────────────────────────────────────────────
# The pane runs nvim-zed-tree-run.sh, not nvim directly: it owns this session's marker, so the tree
# comes back by itself after an agterm restart (agterm restores the split pane but not its program).
"$AGT" session split on --target "$sid" >/dev/null 2>&1
"$AGT" session resize --split-ratio 0.75 --target "$sid" >/dev/null 2>&1   # tree ~25% on the right
sleep 0.8   # let the fresh split shell come up before typing
printf 'exec %q %q\n' "$RUN" "$cwd" \
  | "$AGT" session type --stdin --pane right --target "$sid" >/dev/null 2>&1
"$AGT" session focus right --target "$sid" >/dev/null 2>&1   # land in the tree to browse
