#!/bin/zsh
# Pull in what the base tab did since the fork (or since the last sync).
#
#   btw-sync.sh
#
# Writes a digest file and prints a short report. NEVER prints the payload:
# an oversize sync must not flood the fork's context by accident.
set -u

source "${0:A:h}/lib.sh"
DIGEST=${0:A:h}/btw-digest.sh

btw_need jq

meta=$(btw_meta)
dir=$BTW_ROOT/forks/$AGTERM_SESSION_ID

base_id=$(print -r -- "$meta"   | jq -r '.base_claude_id')
base_sid=$(print -r -- "$meta"  | jq -r '.base_agterm_sid')
base_pane=$(print -r -- "$meta" | jq -r '.base_pane')
transcript=$(print -r -- "$meta" | jq -r '.transcript')
watermark=$(print -r -- "$meta" | jq -r '.watermark')
count=$(print -r -- "$meta"     | jq -r '.sync_count')

# Which Claude is in the base pane NOW? The registry is the source of truth;
# meta is only what we remembered at fork time.
reg=$(btw_registry "$base_sid" "$base_pane")
now_id=$(print -r -- "$reg" | jq -r '.claude_session_id // empty' 2>/dev/null)

# The watermark counts lines already consumed, and `tail -n +N` inside
# btw-digest.sh is INCLUSIVE of line N — so the first unread line is
# watermark + 1. Passing the watermark itself re-reports the last message of
# every previous sync and makes the "nothing new" branch unreachable.
from=$(( watermark + 1 ))

rpt=$(mktemp "${TMPDIR:-/tmp}/btw-sync.XXXXXX") || btw_die "cannot create a temp file"
trap 'rm -f -- "$rpt"' EXIT INT TERM

base_state=alive
if [[ -z $now_id ]]; then
  # Tab or Claude gone. The transcript is still on disk, so degrade.
  base_state=gone
  new_transcript=$transcript
  new_id=$base_id
elif [[ $now_id == $base_id ]]; then
  new_transcript=$transcript
  new_id=$base_id
else
  # The base was /clear'ed or restarted: chain the old tail onto the new head.
  base_state=reset
  new_transcript=$(btw_transcript "$now_id")
  [[ -n $new_transcript ]] || btw_die "base moved to $now_id but its transcript is missing"
  new_id=$now_id
fi

# One renderer for both files, so the reset chain and its marker survive into
# BOTH. Rendering `.full.md` straight from the new transcript would silently
# drop the pre-reset tail, and copying `.full.md` over `.md` in verbatim mode
# would drop it from there too.
render() {  # <squeeze-byte-threshold> -> body on stdout, digest report into $rpt
  local sq=$1 old new
  if [[ $base_state == reset ]]; then
    old=$(BTW_SQUEEZE_BYTES=$sq $DIGEST "$transcript" "$from" 2>/dev/null)
    new=$(BTW_SQUEEZE_BYTES=$sq $DIGEST "$new_transcript" 1 2>"$rpt")
    if [[ -n ${old//[[:space:]]/} ]]; then
      print -r -- "$old"
      print -r -- ""
      print -r -- "--- base session was reset here (/clear or restart) ---"
      print -r -- ""
    fi
    print -r -- "$new"
  else
    BTW_SQUEEZE_BYTES=$sq $DIGEST "$transcript" "$from" 2>"$rpt"
  fi
}

body=$(render "${BTW_SQUEEZE_BYTES:-24576}")
report=$(< "$rpt")               # the squeezed run's report is the one we report
full_body=$(render 99999999)

mode=${${(M)${=report}:#mode=*}#mode=}
collapsed=${${(M)${=report}:#collapsed=*}#collapsed=}
messages=${${(M)${=report}:#messages=*}#messages=}

if [[ -z ${body//[[:space:]]/} ]]; then
  synced=$(print -r -- "$meta" | jq -r '.synced_at // .forked_at')
  print -r -- "btw-sync: nothing new since $synced"
  exit 0
fi

n=$(printf '%03d' $(( count + 1 )))
mkdir -p -- "$dir"

header="# Base session activity — sync $n
base: $new_id (agterm tab $base_sid, pane $base_pane)
from line: $from
captured: $(date -u +%Y-%m-%dT%H:%M:%SZ)
"

# The full rendering is always written, so --summarize has something complete
# to read even when the squeezed version is what landed in context.
full=$dir/sync-$n.full.md
{ print -r -- "$header"; print -r -- "$full_body" } > "$full"

digest=$dir/sync-$n.md
if [[ $mode == squeezed ]]; then
  { print -r -- "$header"; print -r -- "$body" } > "$digest"
else
  cp -- "$full" "$digest"
fi

# meta.json is the fork's only durable state. Write it via a temp file and
# rename, so a jq failure cannot leave a truncated record behind.
new_watermark=$(wc -l < "$new_transcript" | tr -d ' ')
if print -r -- "$meta" | jq \
     --argjson wm "$new_watermark" --argjson c "$(( count + 1 ))" \
     --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
     --arg tr "$new_transcript" --arg id "$new_id" \
     '.watermark = $wm | .sync_count = $c | .synced_at = $ts
      | .transcript = $tr | .base_claude_id = $id' > "$dir/meta.json.new"
then
  mv -f -- "$dir/meta.json.new" "$dir/meta.json"
else
  rm -f -- "$dir/meta.json.new"
  btw_die "could not update meta.json — digest written to $digest, watermark NOT advanced"
fi

size=$(wc -c < "$digest" | tr -d ' ')
print -r -- "btw-sync: $messages new messages from base"
if [[ $mode == squeezed ]]; then
  print -r -- "mode: squeezed ($collapsed older messages collapsed; full text in $(basename $full))"
else
  print -r -- "mode: verbatim"
fi
print -r -- "digest: $digest ($size bytes)"
print -r -- "base: $base_state"
print -r -- "next: read the digest file"
