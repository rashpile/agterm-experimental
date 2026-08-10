#!/bin/bash
# agent-ctl-test.sh — behavioural tests for agent-ctl.sh / agent-wait.sh against a stub agtermctl.
# No agterm required. Run: scripts/tests/agent-ctl-test.sh
#
# The suite exists because the original bug was invisible to inspection: `ready` looked like it
# honoured its timeout, but any four consecutive non-ok polls short-circuited it in ~2s and reported a
# live worker as dead. Cases 1/3/7 pin exactly that.
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
CTL="$here/../agent-ctl.sh"
WAIT="$here/../agent-wait.sh"
STUB="$here/stub-agtermctl"
chmod +x "$STUB" "$CTL" "$WAIT" 2>/dev/null || true

pass=0; fail=0
SID="11111111-2222-3333-4444-555555555555"
TREE_ALIVE="{\"ok\":true,\"result\":{\"tree\":{\"workspaces\":[{\"id\":\"W\",\"name\":\"ws\",\"sessions\":[{\"id\":\"$SID\",\"name\":\"worker\",\"status\":null}]}]}}}"
TREE_ALIVE_ACTIVE="${TREE_ALIVE/\"status\":null/\"status\":\"active\"}"
TREE_EMPTY='{"ok":true,"result":{"tree":{"workspaces":[{"id":"W","name":"ws","sessions":[]}]}}}'

SURFACE_ERR='{"ok":false,"error":"failed to read surface buffer"}'
NOSESS_ERR="{\"ok\":false,\"error\":\"no such session: $SID\"}"
SOCKET_ERR='ERR Error: connect(/Users/x/Library/Application Support/agterm/agterm.sock) failed: Connection refused — is agterm running?'
READY_SCREEN='{"ok":true,"result":{"text":"welcome\n⏵⏵ auto mode on (shift+tab to cycle)"}}'
IDLE_SCREEN='{"ok":true,"result":{"text":"some output\n? for shortcuts"}}'
BUSY_SCREEN='{"ok":true,"result":{"text":"working\nesc to interrupt"}}'
BLANK_SCREEN='{"ok":true,"result":{"text":""}}'
BANNER_SCREEN='{"ok":true,"result":{"text":"Claude usage limit reached. Resets at 5pm"}}'
EXPIRY_SCREEN='{"ok":true,"result":{"text":" ⚠ Your login expires in 2 days · run /login to renew\n⏵⏵ auto mode on (shift+tab to cycle)"}}'
TRUST_SCREEN='{"ok":true,"result":{"text":"Do you trust this folder?"}}'

setup() {  # setup <tree-json> <response lines...>
  STUB_DIR="$(mktemp -d)"; export STUB_DIR
  printf '%s\n' "$1" > "$STUB_DIR/tree.json"; shift
  : > "$STUB_DIR/text.responses"
  for r in "$@"; do printf '%s\n' "$r" >> "$STUB_DIR/text.responses"; done
  : > "$STUB_DIR/calls.log"
  export AGENT_CTL_AGTERMCTL="$STUB"
}
teardown() { rm -rf "$STUB_DIR"; }

check() {  # check <name> <expected-exit> <actual-exit> [extra-assert-desc] [assert-result 0/1]
  local name="$1" want="$2" got="$3" desc="${4:-}" ok="${5:-0}"
  if [ "$got" = "$want" ] && [ "$ok" = 0 ]; then
    printf '  ok   %s\n' "$name"; pass=$((pass+1))
  else
    printf '  FAIL %s — exit want=%s got=%s%s\n' "$name" "$want" "$got" "${desc:+ ; $desc}"
    [ -n "${OUT:-}" ] && printf '       output: %s\n' "$(printf '%s' "$OUT" | tr '\n' '|')"
    fail=$((fail+1))
  fi
}

echo "== ready =="

# 1. THE REGRESSION: transient surface errors must not short-circuit the timeout.
setup "$TREE_ALIVE" "$SURFACE_ERR" "$SURFACE_ERR" "$SURFACE_ERR" "$SURFACE_ERR" \
      "$SURFACE_ERR" "$SURFACE_ERR" "$BLANK_SCREEN" "$READY_SCREEN"
t0=$SECONDS; OUT="$("$CTL" ready "$SID" 20 2>&1)"; rc=$?; el=$((SECONDS-t0))
slow=0; [ "$el" -ge 3 ] || slow=1   # 6 transient polls @0.5s => must have waited, not bailed at 2s
check "waits through 6 transient surface errors, then succeeds" 0 "$rc" "elapsed=${el}s (want >=3)" "$slow"
teardown

# 2. A real death is still fast and still exit 2.
setup "$TREE_EMPTY" "$NOSESS_ERR"
t0=$SECONDS; OUT="$("$CTL" ready "$SID" 90 2>&1)"; rc=$?; el=$((SECONDS-t0))
fast=0; [ "$el" -le 3 ] || fast=1
check "dead session -> exit 2 immediately" 2 "$rc" "elapsed=${el}s (want <=3)" "$fast"
teardown

# 3. Alive but permanently unreadable -> the NEW exit 4, and only at the real deadline.
setup "$TREE_ALIVE" "$SURFACE_ERR"
t0=$SECONDS; OUT="$("$CTL" ready "$SID" 3 2>&1)"; rc=$?; el=$((SECONDS-t0))
waited=0; [ "$el" -ge 2 ] || waited=1
check "alive+unreadable -> exit 4 at the deadline" 4 "$rc" "elapsed=${el}s (want >=2)" "$waited"
teardown

# 4. Usage-limit / login banner keeps its own code.
setup "$TREE_ALIVE" "$BANNER_SCREEN"
OUT="$("$CTL" ready "$SID" 10 2>&1)"; rc=$?
check "usage-limit banner -> exit 3" 3 "$rc"
teardown

# 4b. "Your login expires in N days · run /login to renew" is a WARNING, not a block: claude is
#     usable, and treating it as exit 3 silently killed every spawn in the days before expiry.
setup "$TREE_ALIVE" "$EXPIRY_SCREEN"
OUT="$("$CTL" ready "$SID" 5 2>&1)"; rc=$?
check "login-expiry warning is not a block -> exit 0" 0 "$rc"
teardown

# 5. Readable screen that never gets ready -> plain timeout, NOT 4.
setup "$TREE_ALIVE" "$BLANK_SCREEN"
OUT="$("$CTL" ready "$SID" 2 2>&1)"; rc=$?
check "readable but never ready -> exit 1" 1 "$rc"
teardown

# 6. Trust prompt is still auto-dismissed.
setup "$TREE_ALIVE" "$TRUST_SCREEN" "$READY_SCREEN"
OUT="$("$CTL" ready "$SID" 10 2>&1)"; rc=$?
typed=0; grep -q 'session type' "$STUB_DIR/calls.log" || typed=1
check "trust-folder prompt answered, then ready" 0 "$rc" "no 'session type' in calls.log" "$typed"
teardown

# 7. Socket refusal (stderr + non-zero exit, not JSON) is transient too.
setup "$TREE_ALIVE" "$SOCKET_ERR" "$SOCKET_ERR" "$SOCKET_ERR" "$SOCKET_ERR" "$SOCKET_ERR" "$READY_SCREEN"
OUT="$("$CTL" ready "$SID" 20 2>&1)"; rc=$?
check "socket refusal ride-out -> exit 0" 0 "$rc"
teardown

# 8. Debug hook surfaces the raw response.
setup "$TREE_ALIVE" "$SURFACE_ERR" "$READY_SCREEN"
OUT="$(AGENT_CTL_DEBUG=1 "$CTL" ready "$SID" 10 2>&1)"; rc=$?
dbg=0; printf '%s' "$OUT" | grep -q 'failed to read surface buffer' || dbg=1
check "AGENT_CTL_DEBUG=1 prints the raw agtermctl response" 0 "$rc" "raw response missing from stderr" "$dbg"
teardown

echo "== read =="

# 9. Retry across a transient error instead of failing the first shot.
setup "$TREE_ALIVE" "$SURFACE_ERR" "$SURFACE_ERR" "$IDLE_SCREEN"
OUT="$("$CTL" read "$SID" 5 2>&1)"; rc=$?
got=0; printf '%s' "$OUT" | grep -q '? for shortcuts' || got=1
check "read retries past transient errors" 0 "$rc" "screen text missing" "$got"
teardown

# 10. Gone -> exit 2 (was an undifferentiated exit 1).
setup "$TREE_EMPTY" "$NOSESS_ERR"
OUT="$("$CTL" read "$SID" 5 2>&1)"; rc=$?
check "read on a dead session -> exit 2" 2 "$rc"
teardown

# 11. Alive but unreadable past the budget -> exit 4.
setup "$TREE_ALIVE" "$SURFACE_ERR"
OUT="$(AGENT_CTL_READ_RETRY_SECS=1 "$CTL" read "$SID" 5 2>&1)"; rc=$?
check "read on an unreadable-but-alive session -> exit 4" 4 "$rc"
teardown

# 12. The line-count argument still works (it is positional after the session).
setup "$TREE_ALIVE" '{"ok":true,"result":{"text":"l1\nl2\nl3\nl4"}}'
OUT="$("$CTL" read "$SID" 2 2>&1)"; rc=$?
lines=0; [ "$(printf '%s\n' "$OUT" | grep -c '')" = 2 ] || lines=1
check "read <n> honours the line count" 0 "$rc" "got $(printf '%s\n' "$OUT" | grep -c '') lines" "$lines"
teardown

echo "== status =="

# 13. Unreadable + no native status must NOT be reported as idle.
setup "$TREE_ALIVE" "$SURFACE_ERR"
OUT="$("$CTL" status "$SID" 2>&1)"; rc=$?
st="$(printf '%s\n' "$OUT" | head -1)"
bad=0; [ "$st" = unknown ] || bad=1
check "unreadable surface -> 'unknown', not 'idle'" 0 "$rc" "line1='$st'" "$bad"
teardown

# 14. Unreadable + native status -> native wins (unchanged behaviour).
setup "$TREE_ALIVE_ACTIVE" "$SURFACE_ERR"
OUT="$("$CTL" status "$SID" 2>&1)"; rc=$?
st="$(printf '%s\n' "$OUT" | head -1)"
bad=0; [ "$st" = busy ] || bad=1
check "unreadable surface + native active -> 'busy'" 0 "$rc" "line1='$st'" "$bad"
teardown

# 15/16. Screen classification unchanged.
for pair in "idle:$IDLE_SCREEN" "busy:$BUSY_SCREEN"; do
  want="${pair%%:*}"; scr="${pair#*:}"
  setup "$TREE_ALIVE" "$scr"
  OUT="$("$CTL" status "$SID" 2>&1)"; rc=$?
  st="$(printf '%s\n' "$OUT" | head -1)"
  bad=0; [ "$st" = "$want" ] || bad=1
  check "status reads '$want' off the footer" 0 "$rc" "line1='$st'" "$bad"
  teardown
done

echo "== other verbs (non-regression) =="

setup "$TREE_ALIVE" "$IDLE_SCREEN"
OUT="$("$CTL" id worker 2>&1)"; rc=$?
bad=0; [ "$OUT" = "$SID" ] || bad=1
check "id resolves a name to its session id" 0 "$rc" "got '$OUT'" "$bad"
teardown

setup "$TREE_ALIVE" "$IDLE_SCREEN"
OUT="$("$CTL" type worker hello there 2>&1)"; rc=$?
bad=0; grep -q 'session type --stdin' "$STUB_DIR/calls.log" || bad=1
check "type sends keystrokes and does not submit" 0 "$rc" "no type call logged" "$bad"
teardown

setup "$TREE_ALIVE" "$IDLE_SCREEN"
OUT="$("$CTL" key worker enter 2>&1)"; rc=$?
check "key enter" 0 "$rc"
teardown

setup "$TREE_ALIVE" "$IDLE_SCREEN"
OUT="$("$CTL" key worker bogus 2>&1)"; rc=$?
check "key rejects an unknown key" 1 "$rc"
teardown

setup "$TREE_ALIVE" "$IDLE_SCREEN"
OUT="$("$CTL" close worker 2>&1)"; rc=$?
bad=0; grep -q 'session close' "$STUB_DIR/calls.log" || bad=1
check "close" 0 "$rc" "no close call logged" "$bad"
teardown

setup "$TREE_ALIVE" "$IDLE_SCREEN"
OUT="$("$CTL" select worker 2>&1)"; rc=$?
check "select" 0 "$rc"
teardown

setup "$TREE_ALIVE" "$IDLE_SCREEN"
OUT="$("$CTL" spawn ws newname /tmp 2>&1)"; rc=$?
bad=0; [ "$OUT" = "NEW-SESSION-ID" ] || bad=1
check "spawn prints the new session id" 0 "$rc" "got '$OUT'" "$bad"
teardown

setup "$TREE_ALIVE" "$IDLE_SCREEN"
OUT="$("$CTL" nosuchverb worker 2>&1)"; rc=$?
check "unknown subcommand" 1 "$rc"
teardown

echo "== agent-wait =="

# Unreadable polls must not count as "static" — the old grab() returned the literal "null" forever,
# which three polls in a row read as "the worker finished".
setup "$TREE_ALIVE" "$SURFACE_ERR"
OUT="$("$WAIT" "$SID" --stable 2 --interval 0.2 --timeout 3 2>&1)"; rc=$?
bad=0; printf '%s' "$OUT" | grep -q 'STATE=unreadable' || bad=1
check "unreadable polls never report a false idle" 4 "$rc" "got: $(printf '%s' "$OUT" | head -1)" "$bad"
teardown

# A session that leaves the tree ends the wait at once instead of burning the whole timeout.
setup "$TREE_EMPTY" "$NOSESS_ERR"
t0=$SECONDS; OUT="$("$WAIT" "$SID" --stable 2 --interval 0.2 --timeout 30 2>&1)"; rc=$?; el=$((SECONDS-t0))
bad=0; printf '%s' "$OUT" | grep -q 'STATE=gone' || bad=1
[ "$el" -le 10 ] || bad=1
check "session gone -> STATE=gone, exit 2, no timeout burn" 2 "$rc" "elapsed=${el}s" "$bad"
teardown

# Normal quiescence still works.
setup "$TREE_ALIVE" "$IDLE_SCREEN"
OUT="$("$WAIT" "$SID" --stable 2 --interval 0.2 --timeout 10 2>&1)"; rc=$?
bad=0; printf '%s' "$OUT" | grep -q 'STATE=idle  ENDED=stable' || bad=1
check "quiet screen -> STATE=idle ENDED=stable" 0 "$rc" "got: $(printf '%s' "$OUT" | head -1)" "$bad"
teardown

echo
printf 'passed=%s failed=%s\n' "$pass" "$fail"
[ "$fail" = 0 ]
