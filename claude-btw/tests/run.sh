#!/bin/zsh
# Automated suite for claude-btw. Run by hand:
#   claude-btw/tests/run.sh
# Never talks to agterm or Claude. Exits non-zero on any failure.
set -u
setopt extended_glob

HERE=${0:A:h}
REPO=${HERE:h:h}
DIGEST=$REPO/claude-btw/skills/btw/scripts/btw-digest.sh

typeset -g PASS=0 FAIL=0

ok()  { print -r -- "  ok    $1"; (( PASS++ )) }
bad() { print -r -- "  FAIL  $1"; shift; print -r -- "$@" | sed 's/^/        /'; (( FAIL++ )) }

assert_eq() {  # name expected actual
  if [[ "$2" == "$3" ]]; then ok "$1"
  else bad "$1" "expected: $2" "actual:   $3"; fi
}

assert_golden() {  # name actual
  local golden=$HERE/golden/$1.md
  if [[ ! -f $golden ]]; then bad "$1" "no golden file: $golden"; return; fi
  local d
  d=$(diff -u "$golden" =(print -r -- "$2") 2>&1)
  if [[ -z $d ]]; then ok "$1"; else bad "$1" "$d"; fi
}

assert_contains() {  # name haystack needle
  if [[ "$2" == *"$3"* ]]; then ok "$1"
  else bad "$1" "expected to contain: $3" "actual: $2"; fi
}

assert_not_contains() {  # name haystack needle
  if [[ "$2" != *"$3"* ]]; then ok "$1"
  else bad "$1" "expected NOT to contain: $3" "actual: $2"; fi
}

digest_case() {  # fixture from-line [ENV=val ...]
  local name=$1 from=$2; shift 2
  assert_golden "$name" "$(env "$@" "$DIGEST" "$HERE/fixtures/$name.jsonl" "$from" 2>/dev/null)"
}

print -r -- "digest:"
digest_case basic 1
digest_case noise 1
digest_case meta-sidechain 1
digest_case system-reminder 1
digest_case from-line 4
digest_case compact 1
digest_case truncate 1 BTW_ASSISTANT_CHARS=20
digest_case squeeze 1 BTW_SQUEEZE_BYTES=1 BTW_TAIL_MSGS=2 BTW_COLLAPSE_CHARS=20

report_case() {  # name fixture from-line expected-report-without-bytes [ENV=val ...]
  local name=$1 fixture=$2 from=$3 expected=$4; shift 4
  local actual
  actual=$(env "$@" "$DIGEST" "$HERE/fixtures/$fixture.jsonl" "$from" 2>&1 >/dev/null)
  actual=${actual//bytes=[0-9]## /}      # drop the byte count
  assert_eq "$name" "$expected" "$actual"
}

print -r -- ""
print -r -- "report:"
report_case report-verbatim basic 1 \
  "messages=3 markers=0 mode=verbatim collapsed=0"
report_case report-squeezed squeeze 1 \
  "messages=6 markers=0 mode=squeezed collapsed=4" \
  BTW_SQUEEZE_BYTES=1 BTW_TAIL_MSGS=2 BTW_COLLAPSE_CHARS=20
report_case report-empty noise 15 \
  "messages=0 markers=0 mode=verbatim collapsed=0"

# Regression guard: `mode=squeezed` requires bytes > SQUEEZE_BYTES **and**
# total > TAIL_MSGS. Every other squeeze test here also satisfies the
# message-count condition independently, so dropping `&& (( total > TAIL ))`
# from btw-digest.sh would break nothing else in this suite. `basic` has only
# 3 messages (well under the default TAIL=20) but BTW_SQUEEZE_BYTES=1 makes
# the byte condition trivially true — if the count guard were ever dropped,
# this alone would flip to squeezed.
report_case report-bytes-alone-stays-verbatim basic 1 \
  "messages=3 markers=0 mode=verbatim collapsed=0" \
  BTW_SQUEEZE_BYTES=1

# --- verdicts -------------------------------------------------------------
LIB=$REPO/claude-btw/skills/btw/scripts/lib.sh

verdict_case() {  # name tree-ids registry-keys pane expected
  local name=$1 ids=$2 keys=$3 pane=$4 expected=$5
  local root; root=$(mktemp -d)
  mkdir -p "$root/tabs"
  local k
  for k in ${=keys}; do
    jq -n --arg c "cl-$k" '{claude_session_id: $c}' > "$root/tabs/$k"
  done
  local actual
  actual=$(
    AGTERM_TABS_DIR=$root/tabs BTW_ROOT=$root/btw \
    AGTERMCTL=$HERE/fake-agtermctl.sh FAKE_TREE_IDS=$ids \
    zsh -c "source '$LIB'; btw_verdict 'sid1' '$pane'"
  )
  rm -rf "$root"
  assert_eq "$name" "$expected" "$actual"
}

print -r -- ""
print -r -- "verdicts:"
verdict_case verdict-ok        "sid1 sid2" "sid1.left"            left ok
verdict_case verdict-moved     "sid1 sid2" "sid1.right"           left moved
verdict_case verdict-ambiguous "sid1 sid2" "sid1.right sid1.scratch" left ambiguous
verdict_case verdict-gone-tab  "sid2"      "sid1.left"            left gone
verdict_case verdict-gone-noclaude "sid1 sid2" ""                 left gone

input_case() {  # name pane-text expected-exit
  local name=$1 text=$2 expected=$3
  local actual
  AGTERMCTL=$HERE/fake-agtermctl.sh FAKE_PANE_TEXT=$text \
    zsh -c "source '$LIB'; btw_input_empty 'sid1' 'left'"
  actual=$?
  assert_eq "$name" "$expected" "$actual"
}

print -r -- ""
print -r -- "input box:"
input_case input-empty     $'│ > │\n' 0
input_case input-typed     $'│ > half typed │\n' 1
input_case input-noprompt  $'nothing here\n' 1

# --- failure injection ------------------------------------------------------
# This is the fail-closed contract on the most safety-critical path in the
# project (btw-back deciding whether it's safe to type into a live tab), and
# until now it had no coverage at all: if agtermctl is unreachable or errors,
# both helpers must fail CLOSED — never treat "I don't know" as "safe".
print -r -- ""
print -r -- "failure injection (agtermctl unreachable):"

actual=$(
  AGTERMCTL=$HERE/fake-agtermctl.sh FAKE_FAIL=1 \
  zsh -c "source '$LIB'; btw_verdict 'sid1' 'left'"
)
assert_eq verdict-agtermctl-fails gone "$actual"

AGTERMCTL=$HERE/fake-agtermctl.sh FAKE_FAIL=1 \
  zsh -c "source '$LIB'; btw_input_empty 'sid1' 'left'"
actual=$?
assert_eq input-agtermctl-fails 1 "$actual"

# --- one-liner composition ------------------------------------------------
BACK=$REPO/claude-btw/skills/btw/scripts/btw-back.sh

compose_case() {  # name line path expected
  local name=$1 line=$2 path=$3 expected=$4
  assert_eq "$name" "$expected" "$("$BACK" --compose "$line" --answer-path "$path")"
}

print -r -- ""
print -r -- "one-liner:"
compose_case compose-plain \
  "Auth uses JWT" "/tmp/a.md" \
  "btw: Auth uses JWT. Full notes: /tmp/a.md"
compose_case compose-newlines \
  $'first\nsecond\n\nthird' "/tmp/a.md" \
  "btw: first · second · third. Full notes: /tmp/a.md"
compose_case compose-trailing-period \
  "Auth uses JWT." "/tmp/a.md" \
  "btw: Auth uses JWT. Full notes: /tmp/a.md"
compose_case compose-leading-blank \
  $'\n\nAuth uses JWT' "/tmp/a.md" \
  "btw: Auth uses JWT. Full notes: /tmp/a.md"
compose_case compose-trailing-blank \
  $'Auth uses JWT  \n' "/tmp/a.md" \
  "btw: Auth uses JWT. Full notes: /tmp/a.md"

long=$(printf 'x%.0s' {1..500})
composed=$("$BACK" --compose "$long" --answer-path "/tmp/a.md")

assert_eq compose-capped-length 400 "${#composed}"
assert_eq compose-capped-lines   1   "$(print -r -- "$composed" | wc -l | tr -d ' ')"
if [[ $composed == *". Full notes: /tmp/a.md" ]]; then ok compose-capped-path
else bad compose-capped-path "does not end with the answer path: $composed"; fi

# --- btw-sync state machine -------------------------------------------------
# btw-sync.sh talks to no agtermctl at all — it only reads meta.json, the
# registry, and a transcript, and writes files. Fully testable with temp
# directories, yet until now it had zero coverage, and it's where both of
# this branch's real bugs were found. Build a synthetic fork end to end and
# walk it through: first sync, a no-op second sync, an incremental third
# sync (the off-by-one regression guard), and a base reset.
SYNC=$REPO/claude-btw/skills/btw/scripts/btw-sync.sh

sync_mkuser() { jq -nc --arg t "$1" '{type: "user", message: {role: "user", content: $t}}' }
sync_mkasst() { jq -nc --arg t "$1" '{type: "assistant", message: {role: "assistant", content: [{type: "text", text: $t}]}}' }

sync_root=$(mktemp -d)
fork_sid=fork0000-0000-0000-0000-000000000000
base_sid=base0000-0000-0000-0000-000000000000
base_pane=left
old_claude_id=aaaa0000-0000-0000-0000-000000000001
new_claude_id=bbbb0000-0000-0000-0000-000000000002

mkdir -p "$sync_root/tabs" "$sync_root/btw/forks/$fork_sid" "$sync_root/projects/proj1"
old_transcript=$sync_root/projects/proj1/$old_claude_id.jsonl
new_transcript=$sync_root/projects/proj1/$new_claude_id.jsonl

# Two lines already in the transcript at "fork time" — never expected in any
# sync output, since they were part of the fork's initial context.
{ sync_mkuser "PRE0-marker before the fork"; sync_mkasst "PRE0-marker reply before the fork" } \
  > "$old_transcript"

jq -n \
  --arg bc "$old_claude_id" --arg ba "$base_sid" --arg bp "$base_pane" --arg bcwd "/tmp" \
  --arg tr "$old_transcript" --argjson wm 2 --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  '{base_claude_id: $bc, base_agterm_sid: $ba, base_pane: $bp, base_cwd: $bcwd,
    transcript: $tr, watermark: $wm, forked_at: $ts, sync_count: 0, synced_at: null}' \
  > "$sync_root/btw/forks/$fork_sid/meta.json"

jq -n --arg cs "$old_claude_id" '{claude_session_id: $cs}' > "$sync_root/tabs/$base_sid.$base_pane"

run_sync() {
  AGTERM_SESSION_ID=$fork_sid AGTERM_TABS_DIR=$sync_root/tabs BTW_ROOT=$sync_root/btw \
  CLAUDE_PROJECTS=$sync_root/projects \
    zsh "$SYNC"
}

fork_dir=$sync_root/btw/forks/$fork_sid

print -r -- ""
print -r -- "btw-sync state machine:"

# --- sync 1: first activity since the fork ---
{ sync_mkuser "BTW1-marker user"; sync_mkasst "BTW1-marker asst" } >> "$old_transcript"
out1=$(run_sync)
assert_contains sync1-reports-count "$out1" "btw-sync: 2 new messages from base"
d1=$(cat -- "$fork_dir/sync-001.md" 2>/dev/null)
assert_contains    sync1-digest-has-new     "$d1" "BTW1-marker"
assert_not_contains sync1-digest-omits-pre0 "$d1" "PRE0-marker"
m1=$(cat -- "$fork_dir/meta.json")
assert_eq sync1-watermark 4 "$(print -r -- "$m1" | jq -r '.watermark')"
assert_eq sync1-count     1 "$(print -r -- "$m1" | jq -r '.sync_count')"
if [[ "$(print -r -- "$m1" | jq -r '.synced_at')" != null ]]; then ok sync1-synced-at-set
else bad sync1-synced-at-set "synced_at is still null"; fi

# --- sync 2: immediately again, nothing changed ---
out2=$(run_sync)
assert_contains sync2-nothing-new "$out2" "btw-sync: nothing new since"
if [[ ! -f $fork_dir/sync-002.md ]]; then ok sync2-writes-no-file
else bad sync2-writes-no-file "sync-002.md was written on a no-op sync"; fi

# --- sync 3: more activity — the off-by-one regression guard. Must contain
#     EXACTLY the new messages and none of sync 1's, which is what broke
#     before (watermark passed instead of watermark+1 re-reported the last
#     message of every previous sync). ---
{ sync_mkuser "BTW2-marker user"; sync_mkasst "BTW2-marker asst" } >> "$old_transcript"
out3=$(run_sync)
assert_contains sync3-reports-count "$out3" "btw-sync: 2 new messages from base"
d3=$(cat -- "$fork_dir/sync-002.md" 2>/dev/null)
assert_contains     sync3-digest-has-new    "$d3" "BTW2-marker"
assert_not_contains sync3-digest-no-repeat  "$d3" "BTW1-marker"
assert_not_contains sync3-digest-omits-pre0 "$d3" "PRE0-marker"
m3=$(cat -- "$fork_dir/meta.json")
assert_eq sync3-watermark 6 "$(print -r -- "$m3" | jq -r '.watermark')"
assert_eq sync3-count     2 "$(print -r -- "$m3" | jq -r '.sync_count')"

# --- sync 4: the base was /clear'ed or restarted. One more, unsynced line
#     lands in the OLD transcript first (the pre-reset tail), then the
#     registry is swapped to a different Claude session id. ---
{ sync_mkuser "BTW3PRE-marker unsynced tail" } >> "$old_transcript"
{ sync_mkuser "BTW4-marker user"; sync_mkasst "BTW4-marker asst" } > "$new_transcript"
jq -n --arg cs "$new_claude_id" '{claude_session_id: $cs}' > "$sync_root/tabs/$base_sid.$base_pane"

out4=$(run_sync)
assert_contains sync4-reports-count "$out4" "btw-sync: 3 new messages from base"
assert_contains sync4-reports-reset "$out4" "base: reset"
d4=$(cat -- "$fork_dir/sync-003.md" 2>/dev/null)
f4=$(cat -- "$fork_dir/sync-003.full.md" 2>/dev/null)
marker="--- base session was reset here (/clear or restart) ---"
assert_contains sync4-digest-has-marker "$d4" "$marker"
assert_contains sync4-full-has-marker   "$f4" "$marker"
assert_contains sync4-digest-has-pretail "$d4" "BTW3PRE-marker"
assert_contains sync4-full-has-pretail   "$f4" "BTW3PRE-marker"
assert_contains sync4-digest-has-newhead "$d4" "BTW4-marker"
assert_contains sync4-full-has-newhead   "$f4" "BTW4-marker"
m4=$(cat -- "$fork_dir/meta.json")
assert_eq sync4-transcript-rewritten "$new_transcript" "$(print -r -- "$m4" | jq -r '.transcript')"
assert_eq sync4-base-id-rewritten    "$new_claude_id"  "$(print -r -- "$m4" | jq -r '.base_claude_id')"
assert_eq sync4-watermark            2                 "$(print -r -- "$m4" | jq -r '.watermark')"
assert_eq sync4-count                3                 "$(print -r -- "$m4" | jq -r '.sync_count')"

rm -rf "$sync_root"

print -r -- ""
print -r -- "$PASS passed, $FAIL failed"
(( FAIL == 0 ))
