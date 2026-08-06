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

base_state=alive
body=""
if [[ -z $now_id ]]; then
  # Tab or Claude gone. The transcript is still on disk, so degrade.
  base_state=gone
  body=$($DIGEST "$transcript" "$watermark" 2>/tmp/btw-report.$$)
  report=$(< /tmp/btw-report.$$); rm -f /tmp/btw-report.$$
  new_transcript=$transcript
  new_id=$base_id
elif [[ $now_id == $base_id ]]; then
  body=$($DIGEST "$transcript" "$watermark" 2>/tmp/btw-report.$$)
  report=$(< /tmp/btw-report.$$); rm -f /tmp/btw-report.$$
  new_transcript=$transcript
  new_id=$base_id
else
  # The base was /clear'ed or restarted: chain the old tail onto the new head.
  base_state=reset
  new_transcript=$(btw_transcript "$now_id")
  [[ -n $new_transcript ]] || btw_die "base moved to $now_id but its transcript is missing"
  old=$($DIGEST "$transcript" "$watermark" 2>/dev/null)
  new=$($DIGEST "$new_transcript" 1 2>/tmp/btw-report.$$)
  report=$(< /tmp/btw-report.$$); rm -f /tmp/btw-report.$$
  body="$old

--- base session was reset here (/clear or restart) ---

$new"
  new_id=$now_id
fi

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
from line: $watermark
captured: $(date -u +%Y-%m-%dT%H:%M:%SZ)
"

# The full rendering is always written, so --summarize has something complete
# to read even when the squeezed version is what landed in context.
full=$dir/sync-$n.full.md
print -r -- "$header" > "$full"
BTW_SQUEEZE_BYTES=99999999 $DIGEST "$new_transcript" \
  "$([[ $base_state == reset ]] && print 1 || print $watermark)" 2>/dev/null >> "$full"

digest=$dir/sync-$n.md
if [[ $mode == squeezed ]]; then
  { print -r -- "$header"; print -r -- "$body" } > "$digest"
else
  cp -- "$full" "$digest"
fi

new_watermark=$(wc -l < "$new_transcript" | tr -d ' ')
print -r -- "$meta" | jq \
  --argjson wm "$new_watermark" --argjson c "$(( count + 1 ))" \
  --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --arg tr "$new_transcript" --arg id "$new_id" \
  '.watermark = $wm | .sync_count = $c | .synced_at = $ts
   | .transcript = $tr | .base_claude_id = $id' > "$dir/meta.json"

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
