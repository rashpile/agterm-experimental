# _agt-common.sh — shared session-read helpers. SOURCED by agent-ctl.sh / agent-wait.sh, not run.
#
# Why this exists: `agtermctl session text` has THREE failure shapes that look identical to a naive
# `.ok == true` test but mean completely different things —
#
#   {"ok":false,"error":"no such session: <id>"}          the session is really gone      -> FATAL
#   {"ok":false,"error":"failed to read surface buffer"}  surface not realized YET        -> TRANSIENT
#   Error: connect(.../agterm.sock) failed: Connection…   control socket briefly refused  -> TRANSIENT
#
# The last two are routine right after a spawn: a fresh session reads back empty for ~4s, and while
# several sessions spawn at once the socket itself intermittently refuses connections (observed live:
# 7 consecutive refusals in 0.5s on a 10-session parallel spawn). Treating those as death made `ready`
# declare a healthy worker dead ~2s into a 90s wait.
#
# Liveness is therefore decided by `tree` — the SESSION LIST, not the error string. That is what keeps
# a retry loop from hanging on a genuinely dead session while still riding out a not-yet-ready surface.
#
# Set AGENT_CTL_DEBUG=1 to dump every raw agtermctl response (and its classification) to stderr.
# Set AGENT_CTL_AGTERMCTL=/path/to/agtermctl to point the scripts at a stub (used by the tests).

AGT="${AGENT_CTL_AGTERMCTL:-/opt/homebrew/bin/agtermctl}"
AGT_ERRFILE="${TMPDIR:-/tmp}/agt-common.$$.err"
trap 'rm -f "$AGT_ERRFILE"' EXIT

# Globals set by agt_text(): AGT_TEXT (screen), AGT_CLASS (ok|fatal|transient|unknown), AGT_RAW (blob).
AGT_TEXT=""; AGT_CLASS=""; AGT_RAW=""

agt_dbg() {
  [ "${AGENT_CTL_DEBUG:-0}" = 1 ] || return 0
  printf '[agt %s] %s\n' "$(date +%H:%M:%S)" "$*" >&2
}

agt_classify() {  # <blob> -> fatal | transient | unknown
  case "$1" in
    *"no such session"*|*"no such workspace"*|*"no such surface"*)
      printf 'fatal' ;;
    *"failed to read surface buffer"*|*"surface not available"*|*"no active surface"*|\
    *"not realized"*|*"window is not open"*|*"window not open"*|\
    *"Connection refused"*|*"is agterm running"*)
      printf 'transient' ;;
    *)
      printf 'unknown' ;;
  esac
}

# Read a session's screen. Returns 0 with AGT_TEXT set, or 1 with AGT_CLASS/AGT_RAW set.
# stderr is CAPTURED, not discarded — socket-level failures print there and used to vanish.
agt_text() {  # <id> [debug-tag] [extra agtermctl args...]
  local id="$1" tag="${2:-text}" out err
  shift; [ $# -gt 0 ] && shift   # drop <id>, then <debug-tag> if it was given
  out="$("$AGT" session text --target "$id" --json "$@" 2>"$AGT_ERRFILE")"
  err="$(cat "$AGT_ERRFILE" 2>/dev/null)"; : > "$AGT_ERRFILE"
  AGT_RAW="$(printf '%s %s' "$out" "$err")"
  if printf '%s' "$out" | jq -e '.ok == true' >/dev/null 2>&1; then
    AGT_TEXT="$(printf '%s' "$out" | jq -r '.result.text // ""')"
    AGT_CLASS=ok
    agt_dbg "$tag $id -> ok, ${#AGT_TEXT} bytes"
    return 0
  fi
  AGT_TEXT=""
  AGT_CLASS="$(agt_classify "$AGT_RAW")"
  agt_dbg "$tag $id -> NOT ok, class=$AGT_CLASS :: $(printf '%s' "$AGT_RAW" | tr '\n' ' ' | cut -c1-300)"
  return 1
}

# One-line rendering of the last failure, for the human-facing error messages.
agt_err_line() {
  printf '%s' "$AGT_RAW" | jq -c '.error // .' 2>/dev/null || printf '%s\n' "$AGT_RAW"
}

# Liveness by session list — the authority. Never infer death from an error string alone.
# A tree we cannot read is NOT evidence of death, so it answers "unknown" (i.e. keep waiting).
agt_alive() {  # <id-or-name-or-prefix> -> prints alive | gone | unknown
  local t
  t="$("$AGT" tree --json 2>/dev/null)"
  if ! printf '%s' "$t" | jq -e '.ok == true' >/dev/null 2>&1; then
    agt_dbg "alive $1 -> unknown (tree unreadable)"
    printf 'unknown'; return
  fi
  # Mirror resolve()'s matching: exact id, exact name, or unique id prefix.
  if printf '%s' "$t" | jq -e --arg id "$1" '
        [ .result.tree.workspaces[].sessions[]
          | select(.id == $id or .name == $id
                   or (.id | ascii_downcase | startswith($id | ascii_downcase))) ] | length > 0
      ' >/dev/null 2>&1; then
    agt_dbg "alive $1 -> alive"
    printf 'alive'
  else
    agt_dbg "alive $1 -> gone (absent from tree)"
    printf 'gone'
  fi
}
