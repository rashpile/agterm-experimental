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

# Verbatim captures from a real agterm pane running Claude Code, ANSI already
# stripped exactly as `agtermctl session text` returns it. The character
# right after the `❯` marker is U+00A0 (non-breaking space, bytes c2 a0), NOT
# a plain space -- a synthetic fixture would never have gotten that detail
# right, which is exactly why these are captures, not hand-written strings.
NBSP=$'\xc2\xa0'
REAL_CAPTURE_GHOST=$'────────────────────────────────────────────────\n'\
$'❯'$NBSP$'what does update-agterm do?\n'\
$'────────────────────────────────────────────────\n'\
$'  Model: Opus 5, high - 46.5k | ⎇ claude-btw-design (+0,-0)\n'\
$'  .../agterm-experimental | koptilin@gmail.com | 📡 ○ | v2.1.222\n'\
$'  ⏵⏵ auto mode on (shift+tab to cycle)'
REAL_CAPTURE_TYPED=$'────────────────────────────────────────────────\n'\
$'❯'$NBSP$'XYZZY\n'\
$'────────────────────────────────────────────────\n'\
$'  Model: Opus 5, high - 46.5k | ⎇ claude-btw-design (+0,-0)'

# Real captures of an idle and a busy base pane, used below for
# btw_base_is_idle. Verbatim, not hand-written -- per the task that
# commissioned this check, assumption-written fixtures for exactly this kind
# of undocumented-string matching are what hid two real bugs (the Ctrl+U
# partial-clear and the `status` field lying about liveness) behind a green
# suite the first time around. Note the busy capture contains BOTH the
# past-tense form (`Cooked for 11s`) and the live form (`Enchanting… (4s
# ...)`) at once -- that's not a typo, it's the trap the live-timer
# discriminator exists to handle.
REAL_CAPTURE_IDLE_PANE=$(cat <<'PANE'
  recaps in /config)
                                                        current: 2.1.222 · latest: 2.1.223
────────────────────────────────────────────────────────────────────────────
❯
────────────────────────────────────────────────────────────────────────────
  Model: Opus 5, high - 51.4k | ⎇ claude-btw-design (+0,-0)
  .../agterm-experimental | koptilin@gmail.com | 📡 ○ | v2.1.222
  ⏵⏵ auto mode on (shift+tab to cycle) · ← 2 agents
PANE
)
REAL_CAPTURE_BUSY_PANE=$(cat <<'PANE'
✻ Cooked for 11s
  some earlier output line
✻ Enchanting… (4s · ↓ 23 tokens)
                                                        current: 2.1.222 · latest: 2.1.223
────────────────────────────────────────────────────────────────────────────
❯
────────────────────────────────────────────────────────────────────────────
  Model: Opus 5, high - 51.5k | ⎇ claude-btw-design (+0,-0)
PANE
)

prompt_text_case() {  # name pane-text expected
  local name=$1 text=$2 expected=$3
  local actual
  actual=$(
    AGTERMCTL=$HERE/fake-agtermctl.sh FAKE_PANE_TEXT=$text \
      zsh -c "source '$LIB'; btw_prompt_text 'sid1' 'left'"
  )
  assert_eq "$name" "$expected" "$actual"
}

print -r -- ""
print -r -- "prompt text (real captures):"
prompt_text_case prompt-text-typed      $REAL_CAPTURE_TYPED "XYZZY"
prompt_text_case prompt-text-ghost      $REAL_CAPTURE_GHOST "what does update-agterm do?"
prompt_text_case prompt-text-noprompt   $'nothing here\n' ""

# --- failure injection ------------------------------------------------------
# btw_verdict is the fail-closed contract on the most safety-critical path in
# the project (deciding whether it's safe to type into a live tab at all): if
# agtermctl is unreachable or errors, it must fail CLOSED to `gone` — never
# treat "I don't know" as "safe". btw_prompt_text carries no such contract
# any more (it's a read, not a verdict) but must still degrade to an empty
# read rather than erroring out.
print -r -- ""
print -r -- "failure injection (agtermctl unreachable):"

actual=$(
  AGTERMCTL=$HERE/fake-agtermctl.sh FAKE_FAIL=1 \
  zsh -c "source '$LIB'; btw_verdict 'sid1' 'left'"
)
assert_eq verdict-agtermctl-fails gone "$actual"

actual=$(
  AGTERMCTL=$HERE/fake-agtermctl.sh FAKE_FAIL=1 \
  zsh -c "source '$LIB'; btw_prompt_text 'sid1' 'left'"
)
assert_eq prompt-text-agtermctl-fails "" "$actual"

# --- btw_base_is_idle -------------------------------------------------------
# Positive-evidence-only: idle must be justified twice over (samples static
# AND no live-timer line); everything else -- a difference, a live-timer
# line, a read failure, an unrecognisable pane -- must come back busy. A
# false "idle" here is the one way this whole change could be worse than
# doing nothing (see btw-back.sh's header comment), so this is the
# safety-critical surface of this round, same as btw_verdict was last round.
#
# BTW_IDLE_SAMPLE_DELAY is overridden to keep this suite fast; the fake
# doesn't care how much real time passed between its two calls.
idle_case() {  # name expected-exit [env=val ...]
  local name=$1 expected=$2; shift 2
  env "$@" AGTERMCTL=$HERE/fake-agtermctl.sh BTW_IDLE_SAMPLE_DELAY=0.05 \
    zsh -c "source '$LIB'; btw_base_is_idle 'sid1' 'left'"
  assert_eq "$name" "$expected" "$?"
}

print -r -- ""
print -r -- "btw_base_is_idle (real captures):"

# Samples identical, no live-timer line -> idle (0).
idle_case idle-samples-identical-no-timer 0 \
  FAKE_PANE_TEXT=$REAL_CAPTURE_IDLE_PANE

# Samples identical (FAKE_PANE_TEXT is static across both calls, so an
# equality-only check would say "idle") but the busy capture's live-timer
# line is present -> busy (1). This is signal 2 catching what signal 1, on
# its own, would have missed -- the exact trap called out in the task: the
# busy pane contains BOTH the past-tense and the live form at once, so the
# discriminator has to be the live form's presence, not the past-tense
# form's absence.
idle_case idle-samples-identical-with-timer 1 \
  FAKE_PANE_TEXT=$REAL_CAPTURE_BUSY_PANE

# Two DIFFERENT real captures, neither containing a live-timer line (both
# are the pre-existing "typed"/"ghost" idle captures from the prompt-text
# tests above) -> busy (1) purely from signal 1 (the samples differ), with
# signal 2 not in play at all. Isolates signal 1 from signal 2, the reverse
# of the case above.
idle_seq_isolate_s1="${REAL_CAPTURE_TYPED}"$'\x01'"${REAL_CAPTURE_GHOST}"
idle_seq_state=$(mktemp); rm -f "$idle_seq_state"
idle_case idle-samples-differ 1 \
  FAKE_PANE_TEXT_SEQ=$idle_seq_isolate_s1 FAKE_PANE_TEXT_SEQ_STATE=$idle_seq_state
rm -f "$idle_seq_state"

# `session text` failing -> busy (1). Fail-toward-busy, same contract as
# btw_verdict failing toward `gone`.
idle_case idle-agtermctl-fails 1 FAKE_FAIL=1

# No `❯`/`>` anywhere in the sample -> not recognisably a Claude Code prompt
# box at all -> busy (1).
idle_case idle-no-prompt-block 1 \
  FAKE_PANE_TEXT=$'nothing here\nno prompt marker either'

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

# --- btw-back end to end -----------------------------------------------------
# Exercises the real refusal/clear/send path through btw-back.sh itself,
# never the real agtermctl (per the task's safety rule) -- always the fake.
# Registry has BOTH sid1.left and sid1.right so that a meta.json pointing at
# an unregistered pane ("mid") resolves to `ambiguous` (2 hits) rather than
# `moved` (1 hit), without disturbing the `ok` case, which is decided by an
# exact-match check before the hit-count logic ever runs.
back_root=$(mktemp -d)
mkdir -p "$back_root/tabs"
: > "$back_root/tabs/sid1.left"
: > "$back_root/tabs/sid1.right"

back_meta() {  # fork-sid base-pane
  mkdir -p "$back_root/btw/forks/$1"
  jq -n --arg ba sid1 --arg bp "$2" \
    '{base_agterm_sid: $ba, base_pane: $bp, base_claude_id: "x", transcript: "/dev/null",
      watermark: 0, forked_at: "x", sync_count: 0, synced_at: null}' \
    > "$back_root/btw/forks/$1/meta.json"
}

fork_ok=fork-ok0-0000-0000-0000-000000000000
fork_gone=fork-gone-0000-0000-0000-000000000000
fork_amb=fork-amb0-0000-0000-0000-000000000000
back_meta "$fork_ok"   left
back_meta "$fork_gone" left
back_meta "$fork_amb"  mid

print -r -- ""
print -r -- "btw-back end to end:"

# Every invocation below that reaches the idle check overrides
# BTW_IDLE_SAMPLE_DELAY -- same reasoning as the btw_base_is_idle section
# above: the fake doesn't care how much real time passed between its two
# `session text` calls, so there's no reason to pay the real ~1.3s here.

# --- dry-run against a resolvable, IDLE pane (FAKE_PANE_TEXT is static, so
#     both of btw_base_is_idle's samples come back identical, and the typed
#     capture carries no busy markers): reports it would clear with Ctrl+C
#     and send, instead of refusing on busy-input (which no longer exists at
#     all). ---
out_dry=$(
  print -r -- "answer body one" | \
  AGTERM_SESSION_ID=$fork_ok AGTERM_TABS_DIR=$back_root/tabs BTW_ROOT=$back_root/btw \
  AGTERMCTL=$HERE/fake-agtermctl.sh FAKE_TREE_IDS=sid1 FAKE_PANE_TEXT=$REAL_CAPTURE_TYPED \
  BTW_IDLE_SAMPLE_DELAY=0.05 \
    zsh "$BACK" --line "Answer one" --dry-run 2>&1
)
assert_contains back-dryrun-would-clear "$out_dry" "base idle -- would clear prompt (Ctrl+C; was: XYZZY) and send"
assert_not_contains back-dryrun-not-refused "$out_dry" "refused"

# --- gone: still refuses, answer file still kept. ---
out_gone=$(
  print -r -- "answer body two" | \
  AGTERM_SESSION_ID=$fork_gone AGTERM_TABS_DIR=$back_root/tabs BTW_ROOT=$back_root/btw \
  AGTERMCTL=$HERE/fake-agtermctl.sh FAKE_TREE_IDS="" \
    zsh "$BACK" --line "Answer two" 2>&1
)
status_gone=$?
assert_contains back-gone-refuses "$out_gone" "refused (gone)"
assert_eq       back-gone-exit    1 "$status_gone"
path_gone=$(print -r -- "$out_gone" | grep '^answer kept at: ' | sed 's/^answer kept at: //')
if [[ -n $path_gone && -f $path_gone ]]; then ok back-gone-keeps-answer
else bad back-gone-keeps-answer "no answer file kept: $out_gone"; fi

# --- ambiguous: still refuses, answer file still kept. ---
out_amb=$(
  print -r -- "answer body three" | \
  AGTERM_SESSION_ID=$fork_amb AGTERM_TABS_DIR=$back_root/tabs BTW_ROOT=$back_root/btw \
  AGTERMCTL=$HERE/fake-agtermctl.sh FAKE_TREE_IDS=sid1 \
    zsh "$BACK" --line "Answer three" 2>&1
)
status_amb=$?
assert_contains back-ambiguous-refuses "$out_amb" "refused (ambiguous)"
assert_eq       back-ambiguous-exit    1 "$status_amb"
path_amb=$(print -r -- "$out_amb" | grep '^answer kept at: ' | sed 's/^answer kept at: //')
if [[ -n $path_amb && -f $path_amb ]]; then ok back-ambiguous-keeps-answer
else bad back-ambiguous-keeps-answer "no answer file kept: $out_amb"; fi

# --- real (non-dry-run) send, base IDLE (FAKE_PANE_TEXT static, no busy
#     markers): Ctrl+C is actually issued before the composed line, and the
#     previous prompt text is reported as cleared. Asserted against the
#     fake's own call log, not just this script's stdout -- this is the
#     "samples identical and no live-timer line -> idle -> Ctrl+C IS issued
#     before the type" case from the task, exercised end to end rather than
#     just at the lib.sh level. ---
call_log=$(mktemp)
out_real=$(
  print -r -- "answer body four" | \
  AGTERM_SESSION_ID=$fork_ok AGTERM_TABS_DIR=$back_root/tabs BTW_ROOT=$back_root/btw \
  AGTERMCTL=$HERE/fake-agtermctl.sh FAKE_TREE_IDS=sid1 FAKE_PANE_TEXT=$REAL_CAPTURE_TYPED \
  FAKE_CALL_LOG=$call_log BTW_IDLE_SAMPLE_DELAY=0.05 \
    zsh "$BACK" --line "Answer four" 2>&1
)
status_real=$?
assert_eq       back-real-exit             0 "$status_real"
assert_contains back-real-cleared-report   "$out_real" "base was idle -- cleared prompt (Ctrl+C; was: XYZZY) and sent"
assert_contains back-real-sent-report      "$out_real" "sent to base tab"
assert_contains back-real-not-submitted    "$out_real" "not submitted"

call_log_text=$(<"$call_log")
if [[ $call_log_text == *"session"$'\t'"type"$'\t'$'\x03'* ]]; then ok back-real-ctrlc-issued
else bad back-real-ctrlc-issued "no Ctrl+C session-type call in log:" "$call_log_text"; fi
if [[ $call_log_text == *"session"$'\t'"type"$'\t'"btw: Answer four"* ]]; then ok back-real-line-issued
else bad back-real-line-issued "composed line not sent in log:" "$call_log_text"; fi

rm -f "$call_log"

# --- real send, base BUSY via signal 1 (animation): the pane sampled twice
#     comes back different. Sequenced from three real captures -- the typed
#     prompt (consumed by the prior-text read), then the idle pane, then the
#     busy pane (consumed as btw_base_is_idle's two samples) -- so this is
#     also the "animation differs between samples -> busy -> appends, no
#     Ctrl+C issued" case, and simultaneously proves the prior prompt text
#     ("XYZZY") is reported on the busy path too, not just the idle one. ---
call_log=$(mktemp)
seq_state=$(mktemp); rm -f "$seq_state"
busy_seq="${REAL_CAPTURE_TYPED}"$'\x01'"${REAL_CAPTURE_IDLE_PANE}"$'\x01'"${REAL_CAPTURE_BUSY_PANE}"
out_busy=$(
  print -r -- "answer body five" | \
  AGTERM_SESSION_ID=$fork_ok AGTERM_TABS_DIR=$back_root/tabs BTW_ROOT=$back_root/btw \
  AGTERMCTL=$HERE/fake-agtermctl.sh FAKE_TREE_IDS=sid1 \
  FAKE_PANE_TEXT_SEQ=$busy_seq FAKE_PANE_TEXT_SEQ_STATE=$seq_state \
  FAKE_CALL_LOG=$call_log BTW_IDLE_SAMPLE_DELAY=0.05 \
    zsh "$BACK" --line "Answer five" 2>&1
)
status_busy=$?
assert_eq       back-busy-exit           0 "$status_busy"
assert_contains back-busy-appended-report "$out_busy" "base was busy -- appended without clearing (prompt already had: XYZZY); it now has two things in it"
assert_contains back-busy-sent-report    "$out_busy" "sent to base tab"
assert_contains back-busy-not-submitted  "$out_busy" "not submitted"

call_log_text=$(<"$call_log")
if [[ $call_log_text != *"session"$'\t'"type"$'\t'$'\x03'* ]]; then ok back-busy-no-ctrlc
else bad back-busy-no-ctrlc "Ctrl+C session-type call found in log for a BUSY base:" "$call_log_text"; fi
if [[ $call_log_text == *"session"$'\t'"type"$'\t'"btw: Answer five"* ]]; then ok back-busy-line-issued
else bad back-busy-line-issued "composed line not sent in log:" "$call_log_text"; fi

rm -f "$call_log" "$seq_state"

# --- real send, base BUSY via signal 2 alone (live-timer line, samples
#     otherwise identical): the busy capture's own prompt line is empty, so
#     this also covers "no prior text to report" on the busy path. ---
call_log=$(mktemp)
out_busy2=$(
  print -r -- "answer body six" | \
  AGTERM_SESSION_ID=$fork_ok AGTERM_TABS_DIR=$back_root/tabs BTW_ROOT=$back_root/btw \
  AGTERMCTL=$HERE/fake-agtermctl.sh FAKE_TREE_IDS=sid1 FAKE_PANE_TEXT=$REAL_CAPTURE_BUSY_PANE \
  FAKE_CALL_LOG=$call_log BTW_IDLE_SAMPLE_DELAY=0.05 \
    zsh "$BACK" --line "Answer six" 2>&1
)
status_busy2=$?
assert_eq       back-busy2-exit          0 "$status_busy2"
assert_contains back-busy2-appended-report "$out_busy2" "base was busy -- appended without clearing"
assert_not_contains back-busy2-no-prev-claim "$out_busy2" "prompt already had"

call_log_text=$(<"$call_log")
if [[ $call_log_text != *"session"$'\t'"type"$'\t'$'\x03'* ]]; then ok back-busy2-no-ctrlc
else bad back-busy2-no-ctrlc "Ctrl+C session-type call found in log for a BUSY base:" "$call_log_text"; fi

rm -f "$call_log"
rm -rf "$back_root"

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

# --- install.sh / uninstall.sh ----------------------------------------------
# Both scripts are exercised only against a scratch HOME + AGTERM_CONFIG_DIR +
# AGTERM_PROJECTS_DIR -- never the real ones (a stray real run would rewrite
# this machine's live settings.json and keymap.conf). `zsh -f` skips this
# machine's own zshenv/zprofile so output is deterministic.
INSTALL=$REPO/claude-btw/install.sh
UNINSTALL=$REPO/claude-btw/uninstall.sh

run_install() {  # root [args...]
  local root=$1; shift
  HOME=$root/home AGTERM_CONFIG_DIR=$root/home/.config/agterm \
    AGTERM_PROJECTS_DIR=$root/home/.config/agterm-projects \
    zsh -f "$INSTALL" "$@" 2>&1
}

run_uninstall() {  # root [args...]
  local root=$1; shift
  HOME=$root/home AGTERM_CONFIG_DIR=$root/home/.config/agterm \
    AGTERM_PROJECTS_DIR=$root/home/.config/agterm-projects \
    zsh -f "$UNINSTALL" "$@" 2>&1
}

print -r -- ""
print -r -- "install.sh / uninstall.sh:"

# --- 1. --dry-run writes nothing at all, and its own output cannot be
#     misread as a completed install/uninstall: no dry-run claims work it
#     did not do, in either tense (the closing banner, or the hook-control
#     warning's "just merged" phrasing). Regression guard for a real
#     defect: the first version of these scripts always printed
#     "claude-btw install complete." / "claude-btw uninstall complete." even
#     under --dry-run, so someone who ran --dry-run first (the cautious,
#     correct thing to do) would read that line and reasonably believe the
#     hook was registered when nothing had been written. ---
root=$(mktemp -d); mkdir -p "$root/home"
before=$(cd "$root" && find . | sort)
out=$(run_install "$root" --dry-run)
after=$(cd "$root" && find . | sort)
assert_eq install-dryrun-tree-untouched "$before" "$after"
assert_contains install-dryrun-says-would "$out" "would "
assert_not_contains install-dryrun-no-false-complete "$out" "install complete."
assert_contains install-dryrun-says-dry-run-complete "$out" "dry run complete -- nothing was written"
rm -rf "$root"

# Same false-completion defect class, on the uninstall side.
root=$(mktemp -d); mkdir -p "$root/home"
out=$(run_uninstall "$root" --dry-run)
assert_not_contains uninstall-dryrun-no-false-complete "$out" "uninstall complete."
assert_contains uninstall-dryrun-says-dry-run-complete "$out" "dry run complete -- nothing was removed"
rm -rf "$root"

# --- 2. a clean install lands all five artifacts + valid settings.json -----
root=$(mktemp -d); mkdir -p "$root/home"
h=$root/home
run_install "$root" >/dev/null
if [[ -f $h/.claude/hooks/agterm-tabs/register-tab.sh ]]; then ok install-hook-file
else bad install-hook-file "missing $h/.claude/hooks/agterm-tabs/register-tab.sh"; fi
if [[ -f $h/.config/agterm-projects/claude-btw.sh ]]; then ok install-hotkey-file
else bad install-hotkey-file "missing $h/.config/agterm-projects/claude-btw.sh"; fi
if [[ -f $h/.claude/skills/btw/SKILL.md ]]; then ok install-skill-file
else bad install-skill-file "missing $h/.claude/skills/btw/SKILL.md"; fi
if [[ -L $h/.claude/commands/btw-sync.md ]]; then ok install-symlink-sync
else bad install-symlink-sync "missing symlink $h/.claude/commands/btw-sync.md"; fi
if [[ -L $h/.claude/commands/btw-back.md ]]; then ok install-symlink-back
else bad install-symlink-back "missing symlink $h/.claude/commands/btw-back.md"; fi
if jq -e . "$h/.claude/settings.json" >/dev/null 2>&1; then ok install-settings-valid-json
else bad install-settings-valid-json "$h/.claude/settings.json is not valid JSON"; fi
assert_eq install-keymap-has-block 1 "$(grep -c '^# >>> claude-btw >>>$' "$h/.config/agterm/keymap.conf")"
rm -rf "$root"

# --- 3. install into a settings.json / keymap.conf that already carry
#     unrelated SessionStart/SessionEnd hooks and unrelated keymap lines --
#     those must survive completely untouched. This is the one that matters
#     most: a merge bug here means a stray reinstall silently eats someone
#     else's hooks. ---
root=$(mktemp -d); mkdir -p "$root/home/.claude" "$root/home/.config/agterm"
h=$root/home
cat > "$h/.claude/settings.json" <<'EOF'
{
  "hooks": {
    "SessionStart": [
      {"matcher": "", "hooks": [{"type": "command", "command": "/opt/homebrew/bin/some-other-tool.sh start", "timeout": 5}]}
    ],
    "SessionEnd": [
      {"matcher": "", "hooks": [{"type": "command", "command": "/opt/homebrew/bin/some-other-tool.sh end", "timeout": 5}]}
    ],
    "PreToolUse": [
      {"matcher": "Bash", "hooks": [{"type": "command", "command": "/opt/homebrew/bin/guard.sh", "timeout": 5}]}
    ]
  }
}
EOF
unrelated_start_before=$(jq -c '.hooks.SessionStart[0]' "$h/.claude/settings.json")
unrelated_end_before=$(jq -c '.hooks.SessionEnd[0]' "$h/.claude/settings.json")
unrelated_pretool_before=$(jq -c '.hooks.PreToolUse' "$h/.claude/settings.json")
cat > "$h/.config/agterm/keymap.conf" <<'EOF'
command "Split" cmd+d $HOME/scripts/split.sh
command "Other thing" cmd+e $HOME/scripts/other.sh
EOF
keymap_before=$(< "$h/.config/agterm/keymap.conf")

run_install "$root" >/dev/null

assert_eq install-preserves-unrelated-sessionstart "$unrelated_start_before" "$(jq -c '.hooks.SessionStart[0]' "$h/.claude/settings.json")"
assert_eq install-preserves-unrelated-sessionend   "$unrelated_end_before"   "$(jq -c '.hooks.SessionEnd[0]'   "$h/.claude/settings.json")"
assert_eq install-preserves-unrelated-pretooluse   "$unrelated_pretool_before" "$(jq -c '.hooks.PreToolUse' "$h/.claude/settings.json")"
assert_eq install-adds-one-sessionstart 2 "$(jq '.hooks.SessionStart | length' "$h/.claude/settings.json")"
assert_eq install-adds-one-sessionend   2 "$(jq '.hooks.SessionEnd | length'   "$h/.claude/settings.json")"
if grep -qF 'command "Split" cmd+d' "$h/.config/agterm/keymap.conf" \
   && grep -qF 'command "Other thing" cmd+e' "$h/.config/agterm/keymap.conf"; then
  ok install-preserves-unrelated-keymap-lines
else
  bad install-preserves-unrelated-keymap-lines "unrelated keymap lines missing after install"
fi

# --- 4. running install twice is idempotent: exactly one keymap block,
#     exactly one pair of hook entries (continues on the same tree as #3,
#     which already carries unrelated entries -- so this also proves the
#     second pass doesn't re-duplicate those). ---
out2=$(run_install "$root")
assert_eq install-idempotent-sessionstart-count 2 "$(jq '.hooks.SessionStart | length' "$h/.claude/settings.json")"
assert_eq install-idempotent-sessionend-count   2 "$(jq '.hooks.SessionEnd | length'   "$h/.claude/settings.json")"
assert_eq install-idempotent-keymap-block-count 1 "$(grep -c '^# >>> claude-btw >>>$' "$h/.config/agterm/keymap.conf")"
assert_contains install-idempotent-reports-up-to-date "$out2" "up to date"

# --- 5. uninstall removes our artifacts/entries and leaves the unrelated
#     hooks and unrelated keymap lines byte-identical to what they were
#     before install ever ran. ---
run_uninstall "$root" >/dev/null
if [[ ! -e $h/.claude/hooks/agterm-tabs ]]; then ok uninstall-removes-hook
else bad uninstall-removes-hook "still present: $h/.claude/hooks/agterm-tabs"; fi
if [[ ! -e $h/.config/agterm-projects/claude-btw.sh ]]; then ok uninstall-removes-hotkey
else bad uninstall-removes-hotkey "still present: $h/.config/agterm-projects/claude-btw.sh"; fi
if [[ ! -e $h/.claude/skills/btw ]]; then ok uninstall-removes-skill
else bad uninstall-removes-skill "still present: $h/.claude/skills/btw"; fi
if [[ ! -e $h/.claude/commands/btw-sync.md && ! -L $h/.claude/commands/btw-sync.md ]]; then ok uninstall-removes-symlink-sync
else bad uninstall-removes-symlink-sync "still present: $h/.claude/commands/btw-sync.md"; fi
if [[ ! -e $h/.claude/commands/btw-back.md && ! -L $h/.claude/commands/btw-back.md ]]; then ok uninstall-removes-symlink-back
else bad uninstall-removes-symlink-back "still present: $h/.claude/commands/btw-back.md"; fi

assert_eq uninstall-preserves-unrelated-sessionstart "$unrelated_start_before" "$(jq -c '.hooks.SessionStart[0]' "$h/.claude/settings.json")"
assert_eq uninstall-preserves-unrelated-sessionend   "$unrelated_end_before"   "$(jq -c '.hooks.SessionEnd[0]'   "$h/.claude/settings.json")"
assert_eq uninstall-preserves-unrelated-pretooluse   "$unrelated_pretool_before" "$(jq -c '.hooks.PreToolUse' "$h/.claude/settings.json")"
assert_eq uninstall-removes-our-sessionstart-count 1 "$(jq '.hooks.SessionStart | length' "$h/.claude/settings.json")"
assert_eq uninstall-removes-our-sessionend-count   1 "$(jq '.hooks.SessionEnd | length'   "$h/.claude/settings.json")"
assert_eq uninstall-keymap-byte-identical "$keymap_before" "$(< "$h/.config/agterm/keymap.conf")"
rm -rf "$root"

# --- 6. uninstall leaves runtime state alone by default; --purge removes it -
root=$(mktemp -d); mkdir -p "$root/home/.claude/agterm/tabs" "$root/home/.claude/btw"
h=$root/home
print -r -- "registry entry" > "$h/.claude/agterm/tabs/dummy.left"
print -r -- "my own notes"    > "$h/.claude/btw/answer-001.md"
run_install "$root" >/dev/null
out_nopurge=$(run_uninstall "$root")
if [[ -f $h/.claude/btw/answer-001.md ]]; then ok uninstall-no-purge-keeps-btw-state
else bad uninstall-no-purge-keeps-btw-state "answer-001.md was removed without --purge"; fi
if [[ -f $h/.claude/agterm/tabs/dummy.left ]]; then ok uninstall-no-purge-keeps-tabs
else bad uninstall-no-purge-keeps-tabs "tabs registry was removed without --purge"; fi
assert_contains uninstall-no-purge-mentions-purge "$out_nopurge" "--purge"
run_uninstall "$root" --purge >/dev/null
if [[ ! -e $h/.claude/btw ]]; then ok uninstall-purge-removes-btw-state
else bad uninstall-purge-removes-btw-state "still present: $h/.claude/btw"; fi
if [[ ! -e $h/.claude/agterm/tabs ]]; then ok uninstall-purge-removes-tabs
else bad uninstall-purge-removes-tabs "still present: $h/.claude/agterm/tabs"; fi
rm -rf "$root"

# --- 7. --link installs by symlink, and the installed claude-btw.sh still
#     resolves lib.sh through it (never run without --dry-run: this touches
#     no live agterm session, only the registry lookup, which is empty). ---
root=$(mktemp -d); mkdir -p "$root/home"
h=$root/home
run_install "$root" --link >/dev/null
if [[ -L $h/.claude/skills/btw ]]; then ok link-skill-is-symlink
else bad link-skill-is-symlink "$h/.claude/skills/btw is not a symlink"; fi
if [[ -L $h/.config/agterm-projects/claude-btw.sh ]]; then ok link-hotkey-is-symlink
else bad link-hotkey-is-symlink "$h/.config/agterm-projects/claude-btw.sh is not a symlink"; fi
btw_out=$(HOME=$h AGTERMCTL=$HERE/fake-agtermctl.sh zsh -f "$h/.config/agterm-projects/claude-btw.sh" --dry-run sid1 left /tmp/proj myproj 2>&1)
btw_status=$?
assert_eq link-claude-btw-dry-run-exit-0 0 "$btw_status"
assert_not_contains link-claude-btw-resolves-lib "$btw_out" "cannot find lib.sh"
rm -rf "$root"

# --- 8. reinstalling over an existing install never pollutes the two trees
#     Claude Code auto-scans: every directory under ~/.claude/skills loads
#     as a skill, every *.md under ~/.claude/commands loads as a slash
#     command. The old backup scheme (`path.bak.<timestamp>` sibling, still
#     used for settings.json/keymap.conf below) left the backup INSIDE
#     those trees, so a reinstall registered a second, duplicate skill
#     (`btw.bak.20260806-214526`, observed live) and would do the same to
#     the two commands. Force a real backup+overwrite here (not a same-tree
#     "up to date" skip) by mutating the installed skill tree and replacing
#     one command symlink with an unrelated file before reinstalling. ---
root=$(mktemp -d); mkdir -p "$root/home"
h=$root/home
run_install "$root" >/dev/null

print -r -- "stale content" >> "$h/.claude/skills/btw/SKILL.md"
rm -f "$h/.claude/commands/btw-sync.md"
print -r -- "stale command content" > "$h/.claude/commands/btw-sync.md"

run_install "$root" >/dev/null

skills_entries=$(ls -1 "$h/.claude/skills")
assert_eq install-reinstall-skills-namespace-clean "btw" "$skills_entries"

commands_entries=$(ls -1 "$h/.claude/commands" | sort)
expected_commands=$'btw-back.md\nbtw-sync.md'
assert_eq install-reinstall-commands-namespace-clean "$expected_commands" "$commands_entries"

# The displaced content must actually be recoverable -- a backup that's
# merely hidden from Claude Code is worse than no backup at all.
backup_root_dir="$h/.claude/claude-btw-backups"
if [[ -d $backup_root_dir ]]; then ok install-reinstall-backup-dir-created
else bad install-reinstall-backup-dir-created "no backup directory at $backup_root_dir"; fi

found_skill_backup=$(find "$backup_root_dir" -path '*/skills/btw/SKILL.md' 2>/dev/null | head -1)
if [[ -n $found_skill_backup ]] && grep -q "stale content" -- "$found_skill_backup"; then
  ok install-reinstall-skill-backup-recoverable
else
  bad install-reinstall-skill-backup-recoverable "stale SKILL.md not recovered under $backup_root_dir"
fi

found_cmd_backup=$(find "$backup_root_dir" -path '*/commands/btw-sync.md' 2>/dev/null | head -1)
if [[ -n $found_cmd_backup ]] && grep -q "stale command content" -- "$found_cmd_backup"; then
  ok install-reinstall-command-backup-recoverable
else
  bad install-reinstall-command-backup-recoverable "stale btw-sync.md not recovered under $backup_root_dir"
fi

rm -rf "$root"

print -r -- ""
print -r -- "$PASS passed, $FAIL failed"
(( FAIL == 0 ))
