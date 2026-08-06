#!/bin/zsh
# Reverses install.sh: removes the installed files and symlinks, and undoes
# the two settings.json / keymap.conf patches -- touching only the entries
# claude-btw itself owns. See claude-btw/README.md.
#
#   uninstall.sh [--dry-run] [--purge]
#
# --dry-run   print every action prefixed "would " and change nothing.
# --purge     also remove runtime state (~/.claude/agterm/tabs and
#             ~/.claude/btw). Without it, that state is left alone by
#             default -- ~/.claude/btw/ holds your own answer-NNN.md notes
#             from side conversations, and deleting those by surprise would
#             be data loss.
#
# Honors HOME, AGTERM_CONFIG_DIR ($HOME/.config/agterm) and
# AGTERM_PROJECTS_DIR ($HOME/.config/agterm-projects) so the test suite can
# redirect every path this script touches. Never run this against a real
# HOME without --dry-run first.
set -u

dry=0
purge=0
for arg in "$@"; do
  case $arg in
    --dry-run) dry=1 ;;
    --purge)   purge=1 ;;
    -h|--help)
      print -r -- "usage: uninstall.sh [--dry-run] [--purge]"
      exit 0 ;;
    *)
      print -ru2 -- "uninstall.sh: unknown option: $arg"
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
TABS_DIR=$CLAUDE_DIR/agterm/tabs
BTW_STATE_DIR=$CLAUDE_DIR/btw

# See install.sh's matching comment: anything backed up in place inside
# $CLAUDE_DIR/skills or $CLAUDE_DIR/commands registers as a phantom
# duplicate skill/slash-command, so remove_path's backups of the skill dir
# and the two command symlinks are redirected here instead.
BACKUP_ROOT=$CLAUDE_DIR/claude-btw-backups

MARKER="agterm-tabs/register-tab.sh"
KEYMAP_START="# >>> claude-btw >>>"
KEYMAP_END="# <<< claude-btw <<<"

die()  { print -ru2 -- "uninstall.sh: $*"; exit 1 }
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

backup_existing() {  # path
  local p=$1
  [[ -e $p || -L $p ]] || return 0
  local b
  b=$(backup_target "$p")
  if (( dry )); then
    note "would back up $p -> $b"
  else
    mkdir -p -- "${b:h}"
    if [[ -L $p ]]; then
      # Record the symlink's target rather than following it -- the target
      # (e.g. a --link install's checkout) is not ours to copy or touch.
      readlink -- "$p" > "$b"
    else
      cp -R -- "$p" "$b"
    fi
    note "backed up $p -> $b"
  fi
}

remove_path() {  # path
  local p=$1
  if [[ ! -e $p && ! -L $p ]]; then
    note "not installed: $p"
    return
  fi
  backup_existing "$p"
  action "remove $p"
  (( dry )) || rm -rf -- "$p"
}

strip_keymap_block() {  # content -> content with the sentinel block removed
  print -r -- "$1" | awk -v s="$KEYMAP_START" -v e="$KEYMAP_END" '
    $0 == s { skip=1; next }
    $0 == e { skip=0; next }
    skip { next }
    { print }
  '
}

unpatch_settings() {
  if [[ ! -f $SETTINGS ]]; then
    note "not present: $SETTINGS"
    return
  fi
  local existing
  existing=$(< "$SETTINGS")
  [[ -n $existing ]] || existing='{}'
  print -r -- "$existing" | jq -e . >/dev/null 2>&1 \
    || die "$SETTINGS is not valid JSON; refusing to touch it"

  local canon merged
  canon=$(print -r -- "$existing" | jq .)
  merged=$(print -r -- "$existing" | jq --arg marker "$MARKER" '
    def rm_marker:
      map(.hooks |= map(select((.command // "") | contains($marker) | not)))
      | map(select((.hooks | length) > 0));
    if ((.hooks // {}) | type) == "object" then
      .hooks.SessionStart = ((.hooks.SessionStart // []) | rm_marker) |
      .hooks.SessionEnd   = ((.hooks.SessionEnd   // []) | rm_marker)
    else . end
  ') || die "jq failed while removing hook entries from $SETTINGS; left untouched"
  print -r -- "$merged" | jq -e . >/dev/null 2>&1 \
    || die "jq produced invalid JSON while removing hook entries from $SETTINGS; left untouched"

  if [[ "$merged" == "$canon" ]]; then
    note "no claude-btw hook entries found in $SETTINGS"
    return
  fi

  backup_existing "$SETTINGS"
  action "remove claude-btw hook entries from $SETTINGS"
  if (( ! dry )); then
    local tmp="${SETTINGS}.tmp.$$"
    print -r -- "$merged" > "$tmp" && mv -- "$tmp" "$SETTINGS"
  fi
}

unpatch_keymap() {
  if [[ ! -f $KEYMAP ]]; then
    note "not present: $KEYMAP"
    return
  fi
  local existing stripped
  existing=$(< "$KEYMAP")
  stripped=$(strip_keymap_block "$existing")

  if [[ "$existing" == "$stripped" ]]; then
    note "no claude-btw chord block found in $KEYMAP"
    return
  fi

  backup_existing "$KEYMAP"
  if [[ -z $stripped ]]; then
    action "remove $KEYMAP (only the claude-btw block was left)"
    (( dry )) || rm -f -- "$KEYMAP"
  else
    action "remove claude-btw chord block from $KEYMAP"
    (( dry )) || print -r -- "$stripped" > "$KEYMAP"
  fi
}

# --- preflight ---------------------------------------------------------
command -v jq >/dev/null 2>&1 || die "jq is required but not found on PATH"

# --- uninstall -------------------------------------------------------------
note "claude-btw uninstall$([[ $dry -eq 1 ]] && print -r -- ' (--dry-run)')"
note ""

unpatch_settings
unpatch_keymap

remove_path "$COMMANDS_DIR/btw-sync.md"
remove_path "$COMMANDS_DIR/btw-back.md"
remove_path "$SKILL_DST"
remove_path "$CLAUDE_BTW_SH_DST"
remove_path "$HOOKS_DST"

note ""
if (( purge )); then
  remove_path "$TABS_DIR"
  remove_path "$BTW_STATE_DIR"
else
  note "runtime state left in place (pass --purge to remove):"
  any=0
  [[ -e $TABS_DIR ]] && { note "  $TABS_DIR"; any=1 }
  if [[ -e $BTW_STATE_DIR ]]; then
    note "  $BTW_STATE_DIR (includes your own answer-NNN.md notes from side conversations)"
    any=1
  fi
  (( any )) || note "  (neither is currently present)"
fi

note ""
if (( dry )); then
  note "dry run complete -- nothing was removed. Re-run without --dry-run to apply."
else
  note "claude-btw uninstall complete."
fi

if [[ -f $HOOK_CONTROL_CONFIG ]]; then
  note ""
  note "Note: hook-control is present at $HOOK_CONTROL_CONFIG. If you added a"
  note "claude-btw tool entry there when installing, remove it by hand -- this"
  note "script only touches $SETTINGS, never config.yaml."
fi
