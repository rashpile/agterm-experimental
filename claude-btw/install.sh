#!/bin/zsh
# Installs claude-btw: the tab-registry hook, the cmd+shift+a hotkey target,
# and the btw skill + its two slash commands — then patches the two live
# config files those pieces need to be registered in. See
# claude-btw/README.md for what each step does and why the order matters
# (hook first: the hotkey has nothing to fork without the registry).
#
#   install.sh [--dry-run] [--link]
#
# --dry-run   print every action prefixed "would " and change nothing.
# --link      symlink instead of copy (for working out of a checkout). The
#             default is a plain copy.
#
# Honors HOME, AGTERM_CONFIG_DIR ($HOME/.config/agterm) and
# AGTERM_PROJECTS_DIR ($HOME/.config/agterm-projects) so the test suite can
# redirect every path this script touches. Never run this against a real
# HOME without --dry-run first.
set -u

SRC=${0:A:h}

dry=0
link=0
for arg in "$@"; do
  case $arg in
    --dry-run) dry=1 ;;
    --link)    link=1 ;;
    -h|--help)
      print -r -- "usage: install.sh [--dry-run] [--link]"
      exit 0 ;;
    *)
      print -ru2 -- "install.sh: unknown option: $arg"
      exit 1 ;;
  esac
done

: ${HOME:?HOME must be set}
AGTERM_CONFIG_DIR=${AGTERM_CONFIG_DIR:-$HOME/.config/agterm}
AGTERM_PROJECTS_DIR=${AGTERM_PROJECTS_DIR:-$HOME/.config/agterm-projects}

CLAUDE_DIR=$HOME/.claude
HOOKS_DST=$CLAUDE_DIR/hooks/agterm-tabs
SKILL_DST=$CLAUDE_DIR/skills/btw
COMMANDS_DIR=$CLAUDE_DIR/commands
SETTINGS=$CLAUDE_DIR/settings.json
KEYMAP=$AGTERM_CONFIG_DIR/keymap.conf
CLAUDE_BTW_SH_DST=$AGTERM_PROJECTS_DIR/claude-btw.sh
HOOK_CONTROL_CONFIG=$CLAUDE_DIR/hooks/hook-control/config.yaml

# Claude Code scans every directory under $CLAUDE_DIR/skills as a skill and
# every *.md file under $CLAUDE_DIR/commands as a slash command. Backing up
# an existing install *in place* (the old `path.bak.<timestamp>` scheme,
# still used everywhere else below) inside either tree therefore registers
# as a phantom duplicate skill/command -- observed live after a reinstall,
# where the session's skill list carried both `btw` and
# `btw.bak.20260806-214526` with identical descriptions. Backups that would
# land inside those two trees are redirected here instead, mirroring the
# relative path so what got displaced is still obvious from the backup
# path alone. One timestamp per run so every backup from a single
# install.sh invocation lands together.
BACKUP_ROOT=$CLAUDE_DIR/claude-btw-backups

MARKER="agterm-tabs/register-tab.sh"
KEYMAP_START="# >>> claude-btw >>>"
KEYMAP_END="# <<< claude-btw <<<"

die()  { print -ru2 -- "install.sh: $*"; exit 1 }
note() { print -r -- "$*" }
action() { if (( dry )); then note "would $1"; else note "$1"; fi }

timestamp() { date +%Y%m%d-%H%M%S }
RUN_TS=$(timestamp)

# Where a backup of $1 should land. Paths under $CLAUDE_DIR/skills or
# $CLAUDE_DIR/commands are redirected to $BACKUP_ROOT (see its comment
# above); everything else keeps the plain `path.bak.<timestamp>` sibling.
backup_target() {  # path
  local p=$1
  if [[ $p == $CLAUDE_DIR/skills/* || $p == $CLAUDE_DIR/commands/* ]]; then
    print -r -- "$BACKUP_ROOT/$RUN_TS/${p#$CLAUDE_DIR/}"
  else
    print -r -- "${p}.bak.$(timestamp)"
  fi
}

# Timestamped backup of an existing file/dir/symlink before it's mutated.
backup_existing() {  # path
  local p=$1
  [[ -e $p || -L $p ]] || return 0
  local b
  b=$(backup_target "$p")
  if (( dry )); then
    note "would back up $p -> $b"
  else
    mkdir -p -- "${b:h}"
    mv -- "$p" "$b"
    note "backed up $p -> $b"
  fi
}

# Copy (default) or symlink (--link) a file or directory. Skips the whole
# operation — no backup, no write — when the destination already matches,
# so reruns don't churn backup files.
install_tree() {  # src dst
  local src=$1 dst=$2
  if (( link )); then
    if [[ -L $dst && "$(readlink -- "$dst")" == "$src" ]]; then
      note "up to date (symlink): $dst"; return
    fi
  else
    if [[ -e $dst && ! -L $dst ]] && diff -rq -- "$src" "$dst" >/dev/null 2>&1; then
      note "up to date: $dst"; return
    fi
  fi
  backup_existing "$dst"
  if (( link )); then
    action "symlink $dst -> $src"
    (( dry )) || { mkdir -p -- "${dst:h}"; ln -s -- "$src" "$dst" }
  else
    action "copy $src -> $dst"
    (( dry )) || { mkdir -p -- "${dst:h}"; cp -R -- "$src" "$dst" }
  fi
}

# Always a symlink, regardless of --link — this is how the two slash
# commands are wired in, same as skills/btw/commands/*.md -> ~/.claude/commands.
install_symlink() {  # target linkpath
  local target=$1 linkpath=$2
  if [[ -L $linkpath && "$(readlink -- "$linkpath")" == "$target" ]]; then
    note "up to date (symlink): $linkpath"; return
  fi
  backup_existing "$linkpath"
  action "symlink $linkpath -> $target"
  (( dry )) || { mkdir -p -- "${linkpath:h}"; ln -s -- "$target" "$linkpath" }
}

strip_keymap_block() {  # content -> content with the sentinel block removed
  print -r -- "$1" | awk -v s="$KEYMAP_START" -v e="$KEYMAP_END" '
    $0 == s { skip=1; next }
    $0 == e { skip=0; next }
    skip { next }
    { print }
  '
}

# Merge the two hook entries into settings.json's SessionStart/SessionEnd
# arrays. Never overwrites the hooks object or those arrays wholesale:
# existing entries are preserved, our own (identified by the
# register-tab.sh path) are replaced rather than duplicated. jq runs against
# a copy in memory; the file is only touched after a successful parse, via a
# temp file + rename, so a jq failure leaves the original untouched.
patch_settings() {
  local existing='{}'
  if [[ -f $SETTINGS ]]; then
    existing=$(< "$SETTINGS")
    [[ -n $existing ]] || existing='{}'
    print -r -- "$existing" | jq -e . >/dev/null 2>&1 \
      || die "$SETTINGS exists but is not valid JSON; refusing to touch it"
  fi

  local snippet
  snippet=$(< "$SRC/hooks/settings-snippet.json")
  snippet=${snippet//<HOME>/$HOME}
  print -r -- "$snippet" | jq -e . >/dev/null 2>&1 \
    || die "internal error: settings-snippet.json did not parse after <HOME> substitution"

  local start_entry end_entry
  start_entry=$(print -r -- "$snippet" | jq -c '.hooks.SessionStart[0]')
  end_entry=$(print -r -- "$snippet"   | jq -c '.hooks.SessionEnd[0]')

  local canon merged
  canon=$(print -r -- "$existing" | jq .)
  merged=$(print -r -- "$existing" | jq \
    --argjson start "$start_entry" --argjson end "$end_entry" --arg marker "$MARKER" '
    def rm_marker:
      map(.hooks |= map(select((.command // "") | contains($marker) | not)))
      | map(select((.hooks | length) > 0));
    .hooks = (.hooks // {}) |
    .hooks.SessionStart = ((.hooks.SessionStart // []) | rm_marker) + [$start] |
    .hooks.SessionEnd   = ((.hooks.SessionEnd   // []) | rm_marker) + [$end]
  ') || die "jq failed while merging hook entries into $SETTINGS; left untouched"
  [[ -n $merged ]] || die "jq produced no output while merging hook entries into $SETTINGS; left untouched"
  print -r -- "$merged" | jq -e . >/dev/null 2>&1 \
    || die "jq produced invalid JSON while merging hook entries into $SETTINGS; left untouched"

  if [[ "$merged" == "$canon" ]]; then
    note "up to date: $SETTINGS (hook entries already present)"
    return
  fi

  [[ -f $SETTINGS ]] && backup_existing "$SETTINGS"
  action "merge SessionStart/SessionEnd hook entries into $SETTINGS"
  if (( ! dry )); then
    mkdir -p -- "$CLAUDE_DIR"
    local tmp="${SETTINGS}.tmp.$$"
    print -r -- "$merged" > "$tmp" && mv -- "$tmp" "$SETTINGS"
  fi
}

# Append the chord from commands/keymap.conf inside a sentinel block.
# Idempotent: strips any existing block first (wherever it is) and
# re-appends, so a rerun leaves exactly one block, not two.
patch_keymap() {
  local existing=""
  [[ -f $KEYMAP ]] && existing=$(< "$KEYMAP")

  local chord
  chord=$(< "$SRC/commands/keymap.conf")
  local block="${KEYMAP_START}"$'\n'"${chord}"$'\n'"${KEYMAP_END}"

  local stripped new_content
  stripped=$(strip_keymap_block "$existing")
  if [[ -n $stripped ]]; then
    new_content="${stripped}"$'\n\n'"${block}"
  else
    new_content="${block}"
  fi

  if [[ -f $KEYMAP && "$existing" == "$new_content" ]]; then
    note "up to date: $KEYMAP (chord already present)"
    return
  fi

  [[ -f $KEYMAP ]] && backup_existing "$KEYMAP"
  action "write claude-btw chord block into $KEYMAP"
  if (( ! dry )); then
    mkdir -p -- "${KEYMAP:h}"
    print -r -- "$new_content" > "$KEYMAP"
  fi
}

# --- preflight ---------------------------------------------------------
command -v jq >/dev/null 2>&1 || die "jq is required but not found on PATH"
for f in "$SRC/hooks/register-tab.sh" "$SRC/hooks/settings-snippet.json" \
         "$SRC/commands/claude-btw.sh" "$SRC/commands/keymap.conf" \
         "$SRC/skills/btw/SKILL.md" \
         "$SRC/skills/btw/commands/btw-sync.md" "$SRC/skills/btw/commands/btw-back.md"; do
  [[ -e $f ]] || die "expected source file missing: $f"
done

# --- install -------------------------------------------------------------
note "claude-btw install$([[ $link -eq 1 ]] && print -r -- ' (--link)')$([[ $dry -eq 1 ]] && print -r -- ' (--dry-run)')"
note ""

# 1. hook — the registry the hotkey depends on; install first.
install_tree "$SRC/hooks" "$HOOKS_DST"

# 2. hotkey target.
install_tree "$SRC/commands/claude-btw.sh" "$CLAUDE_BTW_SH_DST"

# 3. skill + slash commands.
install_tree "$SRC/skills/btw" "$SKILL_DST"
install_symlink "$SKILL_DST/commands/btw-sync.md" "$COMMANDS_DIR/btw-sync.md"
install_symlink "$SKILL_DST/commands/btw-back.md" "$COMMANDS_DIR/btw-back.md"

# 4. patch settings.json — merge, never replace.
patch_settings

# 5. patch keymap.conf — sentinel block.
patch_keymap

note ""
if (( dry )); then
  note "dry run complete -- nothing was written. Re-run without --dry-run to apply."
else
  note "claude-btw install complete."
fi

if [[ -f $HOOK_CONTROL_CONFIG ]]; then
  note ""
  note "WARNING: hook-control detected at $HOOK_CONTROL_CONFIG."
  note "  $SETTINGS looks like it is generated by hook-control's 'manage.py"
  note "  sync', which itself runs on SessionStart. The SessionStart/SessionEnd"
  if (( dry )); then
    note "  hook entries this would merge into $SETTINGS may be silently overwritten on"
  else
    note "  hook entries just merged into $SETTINGS may be silently overwritten on"
  fi
  note "  the next sync, un-registering claude-btw's tab registry hook -- the"
  note "  hotkey would then report 'no Claude registered in this pane' with no"
  note "  obvious cause."
  note "  The durable fix is a claude-btw tool entry in $HOOK_CONTROL_CONFIG"
  note "  itself (this script does not edit that file -- that's your call)."
fi
