#!/bin/bash
# agent-wait.sh — block until an agterm session's screen goes QUIET (static for a few polls),
# then print a small payload and exit. Harness-agnostic: it only detects that the agent STOPPED
# producing output. Deciding what the stop MEANS (approve / answer / next task / escalate) is the
# orchestrator's job — read the payload, and if the tail isn't enough, re-read the session with
# `agent-ctl.sh read <session> <lines>` (or `session text --all`).
#
#   agent-wait.sh <session> [--stable N] [--interval S] [--timeout T] [--tail L]
#     --stable    consecutive identical polls to call it quiet   (default 3)
#     --interval  seconds between polls                          (default 1.5)
#     --timeout   give up waiting after this many seconds        (default 600)
#     --tail      lines of the final screen to return           (default 20)
#
# Why quiescence works: while an agent runs, the elapsed timer / token counter / spinner tick every
# second, so the screen text keeps changing. "Text identical across polls" therefore means it is NOT
# producing output — i.e. waiting at a prompt, waiting for an answer, or done. All three are exactly
# when the orchestrator should wake and look.
#
# Output (stdout):
#   STATE=waiting|idle|gone|unreadable  ENDED=prompt|stable|timeout|gone  POLLS=n
#   Exit: 0 normal (incl. timeout) | 2 STATE=gone | 4 STATE=unreadable (alive, never saw its screen)
#     ENDED=prompt -> returned early because a permission/choice prompt is up (STATE=waiting);
#                     this wins over the "still working" busy-guard so parallel-task spinners
#                     (e.g. "ctrl+b to run in background") can't mask a pending approval.
#     ENDED=gone   -> the session left the session tree while we waited (exit 2). Waiting for a
#                     screen that no longer exists can only ever end in a useless timeout.
#   Unreadable polls (surface not ready / socket refused) do NOT count toward stability: an
#   unreadable read used to yield the literal "null" every time, which is trivially "static", so
#   three of them in a row reported the worker as finished when we had simply never seen it.
#   --- new since wait start (set-diff) ---   # lines that appeared during the wait (deduped)
#   ...
#   --- tail (L) ---                          # last L non-blank lines of the final screen
#   ...
set -uo pipefail
export PATH="/opt/homebrew/bin:$PATH"
# shellcheck source=_agt-common.sh
. "$(dirname "$0")/_agt-common.sh"   # defines $AGT, agt_text/agt_alive/agt_dbg

targ="${1:?usage: agent-wait.sh <session> [--stable N] [--interval S] [--timeout T] [--tail L]}"; shift
stable=3; interval=1.5; timeout=600; tail=20
while [ $# -gt 0 ]; do
  case "$1" in
    --stable)   stable="$2";   shift 2 ;;
    --interval) interval="$2"; shift 2 ;;
    --timeout)  timeout="$2";  shift 2 ;;
    --tail)     tail="$2";     shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

id=$("$AGT" tree --json | jq -r --arg t "$targ" \
      '.result.tree.workspaces[].sessions[] | select(.name==$t) | .id' | head -1)
[ -n "$id" ] || id="$targ"

screen=""
grab() { if agt_text "$id" "wait" ; then screen="$AGT_TEXT"; return 0; fi; screen=""; return 1; }
native() { "$AGT" tree --json 2>/dev/null | jq -r --arg id "$id" \
    '.result.tree.workspaces[].sessions[] | select(.id==$id) | (.status // "")' | head -1; }

# spinner / active-work markers — a safety net for the rare no-timer tool run; the ticking timer
# usually resets stability on its own.
work_re='to run in background|[Ee]sc to interrupt|↓ [0-9]|· ↓'
# choice affordance shared across harnesses (Claude "❯ 1. Yes", Codex "› 1. Yes, proceed"), plus
# permission-prompt phrasing that shows even when the cursor line is off-screen.
wait_re='(❯|›|>)[[:space:]]*[0-9]+[.)][[:space:]]*(Yes|Allow|Proceed|No)|Do you want to proceed|requires approval|don.?t ask again'

# Baseline. A just-spawned session reads back unreadable for a few seconds, and the set-diff below is
# only meaningful against a screen we actually saw — so give the first read a short grace period.
for _ in 1 2 3 4 5 6 7 8 9 10; do grab && break; sleep 0.5; done
start="$screen"; cur="$start"; prev="$start"; same=0; ended=timeout; polls=0; unreadable=0
maxpolls=$(awk -v t="$timeout" -v i="$interval" 'BEGIN{printf "%d", (t/i)+1}')

while [ "$polls" -lt "$maxpolls" ]; do
  polls=$((polls+1))
  if ! grab; then
    # Could not see the screen. Only the session tree may declare death; anything else is transient
    # and must NOT advance stability (an unreadable read is "static" for the wrong reason).
    if [ "$(agt_alive "$id")" = gone ]; then ended=gone; break; fi
    unreadable=$((unreadable+1)); same=0
    agt_dbg "wait: unreadable poll $polls (session alive) — stability reset"
    sleep "$interval"; continue
  fi
  cur="$screen"
  # A choice / permission prompt is an unambiguous, actionable stop — return AT ONCE, even if a
  # parallel sub-agent task still shows a "to run in background" spinner marker (which would
  # otherwise keep work_re resetting stability forever and mask the pending prompt). Confirm with
  # one short re-grab so a half-rendered prompt isn't caught mid-draw.
  if printf '%s' "$cur" | grep -qE "$wait_re"; then
    sleep "$interval"; polls=$((polls+1))
    # Confirm on a screen we actually re-read — an unreadable re-grab must not confirm a prompt.
    if grab && printf '%s' "$screen" | grep -qE "$wait_re"; then cur="$screen"; ended=prompt; break; fi
    [ -n "$screen" ] && cur="$screen"
  fi
  if [ "$cur" = "$prev" ]; then same=$((same+1)); else same=0; prev="$cur"; fi
  printf '%s' "$cur" | grep -qE "$work_re" && same=0   # working -> not quiet
  if [ "$same" -ge "$stable" ]; then
    # Screen quiet — but a scrolled-up viewport (e.g. the user peeking via remote control)
    # freezes the scrape while the agent is still mid-turn, causing a false idle. Native
    # status is the veto: Claude's lifecycle hooks hold "active" for the whole turn. Absent
    # or any other native value → the screen's judgment stands (SSH/plain-shell sessions).
    if [ "$(native)" = active ]; then same=0
    else ended=stable; break; fi
  fi
  sleep "$interval"
done

final="${cur:-$start}"
if [ "$ended" = gone ]; then
  printf 'STATE=gone  ENDED=gone  POLLS=%s\n' "$polls"
  echo "--- session $id left the session tree while waiting (worker exited or was closed) ---"
  printf '%s\n' "$final" | grep -vE '^[[:space:]]*$' | tail -n "$tail"
  exit 2
fi
if [ "$ended" = timeout ] && [ -z "$final" ] && [ "$unreadable" -gt 0 ]; then
  # Alive (the tree says so) but we never once saw its screen — same condition as `agent-ctl.sh`
  # exit 4, and it must not masquerade as "the worker went idle".
  printf 'STATE=unreadable  ENDED=timeout  POLLS=%s\n' "$polls"
  echo "--- never got a readable screen for $id in ${timeout}s ($unreadable unreadable polls) — session is alive, agterm-side surface problem ---"
  exit 4
fi
if printf '%s' "$final" | grep -qE "$wait_re"; then state=waiting; else state=idle; fi

printf 'STATE=%s  ENDED=%s  POLLS=%s\n' "$state" "$ended" "$polls"
[ "$unreadable" -gt 0 ] && echo "--- note: $unreadable unreadable poll(s) skipped (surface not ready / socket busy) ---"
# new lines: present in the final screen, absent from the start screen, IN SCREEN ORDER
echo "--- new since wait start ---"
grep -Fxv -f <(printf '%s\n' "$start") <(printf '%s\n' "$final") \
  | grep -vE '^[[:space:]]*$' | tail -20
echo "--- tail ($tail) ---"
printf '%s\n' "$final" | grep -vE '^[[:space:]]*$' | tail -n "$tail"
exit 0   # explicit: with `pipefail`, a grep that matches nothing above would otherwise leak exit 1
