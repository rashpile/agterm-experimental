#!/bin/zsh
# Fork the Claude session running in this pane into a new tab beside it.
# Bound to a chord in keymap.conf; see commands/claude-btw/design-notes.md and
# docs/superpowers/specs/2026-08-03-claude-btw-design.md.
#
#   claude-btw.sh [--dry-run] <AGT_SESSION_ID> <AGT_PANE> <AGT_SESSION_PWD> <AGT_SESSION_NAME>
set -u

source "${0:A:h}/../../skills/btw/scripts/lib.sh"

dry=0
if [[ ${1:-} == --dry-run ]]; then dry=1; shift; fi

sid=${1:?missing session id}
pane=${2:-left}
base_pwd=${3:-$HOME}
base_name=${4:-session}

# Under --dry-run this prints instead of posting: a desktop notification is a
# real side effect, and "print what you would do" has to mean all of it.
notify() {
  if (( dry )); then
    print -r -- "notify: $1"
  else
    [[ -x $AGTERMCTL ]] && "$AGTERMCTL" notify "$1" --title "claude-btw" >/dev/null 2>&1
  fi
}

btw_need jq
[[ -x $AGTERMCTL ]] || { notify "agtermctl not found"; btw_die "agtermctl not found: $AGTERMCTL" }

# 1. Prune registry entries whose agterm session no longer exists.
#    Skipped under --dry-run: deleting files is a side effect, and --dry-run
#    means print what you would do instead of doing it. Skipping costs no
#    fidelity — prune only removes entries for DEAD agterm sessions, while
#    resolution below looks up the current pane's sid, which is live by
#    definition, so the two never interact.
tree=$( (( dry )) || "$AGTERMCTL" tree --json 2>/dev/null )
if [[ -n $tree ]]; then
  live=( ${(f)"$(print -r -- "$tree" | jq -r '.result.tree.workspaces[].sessions[].id | ascii_downcase')"} )
  for f in $AGTERM_TABS_DIR/*(N); do
    base=${${f:t}%.*}
    (( ${live[(Ie)${base:l}]} )) || rm -f -- "$f"
  done
fi

# 2. Resolve the Claude running in this pane.
reg=$(btw_registry "$sid" "$pane")
if [[ -z $reg ]]; then
  notify "no Claude registered in this pane"
  exit 0
fi
base_id=$(print -r -- "$reg"  | jq -r '.claude_session_id // empty')
base_cwd=$(print -r -- "$reg" | jq -r '.cwd // empty')
[[ -n $base_cwd ]] || base_cwd=$base_pwd
[[ -n $base_id ]] || { notify "registry entry has no session id"; exit 1 }

# 3. Locate the transcript. A tab that opens onto a --resume error is worse
#    than no tab, so refuse before spawning.
transcript=$(btw_transcript "$base_id")
if [[ -z $transcript ]]; then
  notify "no transcript for $base_id"
  btw_die "no transcript found for $base_id"
fi

# 4. Watermark.
watermark=$(wc -l < "$transcript" | tr -d ' ')

# 5. Spawn. --command is argv-style with no shell and without Homebrew on
#    PATH, so wrap it in a login shell.
cmd="zsh -lc 'claude --resume $base_id --fork-session'"
if (( dry )); then
  print -r -- "$AGTERMCTL session new --json --cwd $base_cwd --after $sid --name 'btw: $base_name' --command \"$cmd\""
  print -r -- "watermark=$watermark transcript=$transcript base_id=$base_id"
  exit 0
fi

new=$("$AGTERMCTL" session new --json \
        --cwd "$base_cwd" --after "$sid" \
        --name "btw: $base_name" \
        --command "$cmd" 2>/dev/null | jq -r '.result.id // empty')
[[ -n $new ]] || { notify "could not create the fork tab"; btw_die "session new returned no id" }

# 6. Record fork metadata, keyed by the NEW tab's agterm session id.
dir=$BTW_ROOT/forks/$new
mkdir -p -- "$dir"
jq -n \
  --arg bc "$base_id" --arg ba "$sid" --arg bp "$pane" --arg bcwd "$base_cwd" \
  --arg tr "$transcript" --argjson wm "$watermark" \
  --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  '{base_claude_id: $bc, base_agterm_sid: $ba, base_pane: $bp, base_cwd: $bcwd,
    transcript: $tr, watermark: $wm, forked_at: $ts, sync_count: 0, synced_at: null}' \
  > "$dir/meta.json"
