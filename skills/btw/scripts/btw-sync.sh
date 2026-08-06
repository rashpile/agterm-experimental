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

meta=$(btw_meta) || exit 1
dir=$(btw_fork_dir)

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
rpt_old=$(mktemp "${TMPDIR:-/tmp}/btw-sync.XXXXXX") || btw_die "cannot create a temp file"
trap 'rm -f -- "$rpt" "$rpt_old"' EXIT INT TERM

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

# Read the watermark HERE, right after the transcript(s) to read from are
# settled and before either render below — not after both renders, which is
# what this line used to do. The base session working during a sync is the
# normal case: lines appended between the renders and a post-render `wc -l`
# would be counted into the watermark but appear in no digest, losing them
# permanently. Reading early instead risks the opposite — a line landing
# between this read and the render being duplicated into the NEXT sync — which
# is the trade the spec already accepts for the fork-time race (benign
# duplication beats silent loss).
new_watermark=$(wc -l < "$new_transcript" | tr -d ' ')

# One renderer for both files, so the reset chain and its marker survive into
# BOTH. Rendering `.full.md` straight from the new transcript would silently
# drop the pre-reset tail, and copying `.full.md` over `.md` in verbatim mode
# would drop it from there too.
render() {  # <squeeze-byte-threshold> -> body on stdout, digest report(s) into $rpt (and $rpt_old on reset)
  local sq=$1 old new ec=0
  : > "$rpt_old"
  if [[ $base_state == reset ]]; then
    old=$(BTW_SQUEEZE_BYTES=$sq $DIGEST "$transcript" "$from" 2>"$rpt_old") || ec=1
    new=$(BTW_SQUEEZE_BYTES=$sq $DIGEST "$new_transcript" 1 2>"$rpt") || ec=1
    if [[ -n ${old//[[:space:]]/} ]]; then
      print -r -- "$old"
      print -r -- ""
      print -r -- "--- base session was reset here (/clear or restart) ---"
      print -r -- ""
    fi
    print -r -- "$new"
    return $ec
  else
    BTW_SQUEEZE_BYTES=$sq $DIGEST "$transcript" "$from" 2>"$rpt"
  fi
}

if ! body=$(render "${BTW_SQUEEZE_BYTES:-24576}"); then
  btw_die "btw-digest failed rendering base activity — cannot tell 'nothing new' from a broken render: $(cat -- "$rpt" "$rpt_old" 2>/dev/null)"
fi
report=$(< "$rpt")
report_old=$(< "$rpt_old")       # only non-empty on the reset path
if ! full_body=$(render 99999999); then
  btw_die "btw-digest failed rendering the full (uncollapsed) pass: $(cat -- "$rpt" "$rpt_old" 2>/dev/null)"
fi

mode=${${(M)${=report}:#mode=*}#mode=}
collapsed=${${(M)${=report}:#collapsed=*}#collapsed=}
messages=${${(M)${=report}:#messages=*}#messages=}

# A missing or malformed report line must not silently read as mode=verbatim
# — that's exactly the empty-body/broken-digest case this is meant to catch.
if [[ $mode != verbatim && $mode != squeezed ]]; then
  btw_die "btw-digest produced an unreadable report line: '$report'"
fi

if [[ $base_state == reset ]]; then
  # The pre-reset tail's own report was previously discarded (2>/dev/null),
  # so `messages` undercounted (new-half only) and `mode` reflected only the
  # post-reset half. If the PRE-reset tail was the oversized half, that made
  # `mode` read verbatim while `body` (rendered at the same squeeze
  # threshold) actually held squeezed content for the old half — and because
  # `mode` drove which file became sync-NNN.md, cp then handed the fork the
  # unsqueezed `full` rendering on exactly the path where content is
  # largest. Combine both halves' reports instead.
  mode_old=${${(M)${=report_old}:#mode=*}#mode=}
  collapsed_old=${${(M)${=report_old}:#collapsed=*}#collapsed=}
  messages_old=${${(M)${=report_old}:#messages=*}#messages=}
  if [[ $mode_old != verbatim && $mode_old != squeezed ]]; then
    btw_die "btw-digest produced an unreadable report line for the pre-reset tail: '$report_old'"
  fi
  messages=$(( messages + messages_old ))
  collapsed=$(( collapsed + collapsed_old ))
  [[ $mode_old == squeezed ]] && mode=squeezed
fi

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
# (new_watermark was read earlier, before either render — see above.)
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
