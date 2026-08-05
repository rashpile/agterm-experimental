#!/bin/zsh
# Automated suite for claude-btw. Run by hand:
#   commands/claude-btw/tests/run.sh
# Never talks to agterm or Claude. Exits non-zero on any failure.
set -u
setopt extended_glob

HERE=${0:A:h}
REPO=${HERE:h:h:h}
DIGEST=$REPO/skills/btw/scripts/btw-digest.sh

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

print -r -- ""
print -r -- "$PASS passed, $FAIL failed"
(( FAIL == 0 ))
