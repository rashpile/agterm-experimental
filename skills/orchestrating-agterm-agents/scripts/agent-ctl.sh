#!/bin/bash
# agent-ctl.sh — thin, allow-listable wrapper around agtermctl for driving agent sessions.
# One stable command path so it can be allow-listed once (Bash(.../agent-ctl.sh:*)) instead of
# re-approving ad-hoc compound pipelines (variables + echo + pipes) every time.
#
#   agent-ctl.sh read   <session> [lines]    # print last N lines of the session (default 20)
#   agent-ctl.sh submit <session>            # press Enter — submit whatever is in the input box
#   agent-ctl.sh key    <session> <key>      # send one key: enter | down | up | ctrl-c | esc
#   agent-ctl.sh type   <session> <text...>  # clear input box, type <text>, do NOT submit
#   agent-ctl.sh say    <session> <text...>  # clear input box, type <text>, then submit
#   agent-ctl.sh id     <name>               # resolve a session name -> id (kills the ad-hoc tree|jq)
#   agent-ctl.sh select <session>            # bring a session to the foreground (steals focus; rare)
#   agent-ctl.sh close  <session>            # close a session (e.g. a spawned worker) — allow-listed teardown
#   agent-ctl.sh ready  <session> [secs]     # block until the worker's Claude box is up (dismisses the
#                                            #   first-run "trust folder" prompt); default 15s. So the
#                                            #   orchestrator never hand-rolls raw `session text` polling.
#   agent-ctl.sh spawn  [--create-workspace] <workspace> <name> <cwd> [command...]  # new session, prints id
#          workspace = id | exact name | name-prefix (e.g. "ops" → "ops ⎇ main")
#          --create-workspace : reuse the workspace NAMED <workspace> if it exists, else create it
#                 (idempotent) — so you never drop to a raw `agtermctl workspace new` (which re-prompts).
#          command defaults to `zsh -ic claude`. Encapsulated so orchestrator spawns stay
#          allow-listable (no ad-hoc `$(agtermctl … | jq …)` pipeline that re-prompts).
#
# <session> = exact session name OR id / unique prefix (prefer an id — names can repeat).
# SAFETY: `submit`/`say` push input into a live agent (may launch real work). Read first.
set -uo pipefail
export PATH="/opt/homebrew/bin:$PATH"
AGT=/opt/homebrew/bin/agtermctl

resolve() {  # exact session name -> id, else pass through as id/prefix
  local t="$1" id
  id=$("$AGT" tree --json | jq -r --arg t "$t" \
        '.result.tree.workspaces[].sessions[] | select(.name==$t) | .id' | head -1)
  [ -n "$id" ] && printf '%s' "$id" || printf '%s' "$t"
}

cmd="${1:?usage: agent-ctl.sh read|submit|key|type|say|spawn <target> [args]}"

# spawn is workspace-targeted — handle before the session-resolution below.
if [ "$cmd" = spawn ] || [ "$cmd" = new ]; then
  create_ws=0
  if [ "${2:-}" = --create-workspace ]; then create_ws=1; shift; fi
  ws="${2:?spawn: missing <workspace> (id | name | name-prefix)}"
  name="${3:?spawn: missing <name>}"
  cwd="${4:?spawn: missing <cwd>}"
  shift 4
  # Default command disables prompt suggestions: their ghost text sits in the input box and
  # `session text` cannot tell it from real typed input — an orchestrator reading the screen
  # sees a plausible instruction of unknown origin (observed live: a suggested "merge the PR"
  # read as injected text). Workers don't need suggestions; kill the ambiguity at spawn.
  # NB: --prompt-suggestions false alone is NOT enough — it governs the print/SDK-mode
  # suggestion messages; the interactive ghost text is the settings key promptSuggestionEnabled
  # (verified live: a worker spawned with only the flag still rendered ghosts).
  run="${*:-zsh -ic 'claude --settings \"{\\\"promptSuggestionEnabled\\\": false}\" --prompt-suggestions false'}"
  # --no-select (agterm ≥0.15.2): create the worker in the BACKGROUND. Delegation is the whole point —
  # spawning used to yank the user into the worker's workspace, mid-task, once per worker. Safe for the
  # supervise loop: a never-selected session's buffer is still live, so `ready`/`read`/`status`/
  # `agent-wait` all work without it ever being focused (verified). Use `agent-ctl.sh select <id>` on
  # the rare occasion you actually want it foregrounded.
  # Surface spawn failures LOUDLY: jq on an {"ok":false,...} envelope used to print the literal
  # string "null", which callers then used as a session id. Print the error and exit non-zero instead.
  if [ "$create_ws" = 1 ]; then
    # Idempotent: agterm reuses the workspace NAMED "$ws" if present, creates it if absent. Keeps the
    # whole spawn (incl. workspace creation) inside this one allow-listed path — no raw `workspace new`.
    out="$("$AGT" session new --workspace-name "$ws" --create-workspace --no-select \
        --name "$name" --cwd "$cwd" --command "$run" --json)"
  else
    wsid=$("$AGT" tree --json | jq -r --arg w "$ws" \
        '.result.tree.workspaces[] | select(.id==$w or .name==$w or (.name|startswith($w))) | .id' | head -1)
    [ -n "$wsid" ] || wsid="$ws"
    out="$("$AGT" session new --workspace "$wsid" --no-select --name "$name" --cwd "$cwd" --command "$run" --json)"
  fi
  sid="$(printf '%s' "$out" | jq -r '.result.id // empty' 2>/dev/null)"
  if [ -n "$sid" ]; then printf '%s\n' "$sid"; exit 0; fi
  echo "spawn FAILED for '$name' in workspace '$ws':" >&2
  printf '%s' "$out" | jq -c '.error // .' >&2 2>/dev/null || printf '%s\n' "$out" >&2
  exit 1
fi

targ="${2:?missing <session>}"
id=$(resolve "$targ")
shift 2

case "$cmd" in
  id)
    # Resolve a session name -> id and print it. resolve() already prints the id (or passes the input
    # through unchanged if no name matched) — so this just surfaces it without the ad-hoc tree|jq.
    printf '%s\n' "$id" ;;
  ready)
    # Block until the target's freshly-spawned Claude is ready to receive a task: poll the screen,
    # auto-dismiss the first-run "trust this folder" prompt, and return once the input box footer is up.
    # Keeps the raw `session text` polling INSIDE this allow-listed path (the orchestrator never runs it).
    secs="${1:-15}"; iters=$(( secs * 2 )); trusted=0; gone=0
    i=0
    while [ "$i" -lt "$iters" ]; do
      i=$(( i + 1 )); sleep 0.5
      raw="$("$AGT" session text --target "$id" --json 2>/dev/null)"
      # Fail FAST if the session died (claude exited on a usage-limit/login error and took the
      # shell with it) — polling a gone session to timeout used to read as "cold start too slow".
      if ! printf '%s' "$raw" | jq -e '.ok == true' >/dev/null 2>&1; then
        gone=$(( gone + 1 ))
        if [ "$gone" -ge 4 ]; then
          echo "ready: session $id is GONE — spawned claude likely exited at startup (check usage limits / login):"
          printf '%s' "$raw" | jq -c '.error // .' 2>/dev/null
          exit 2
        fi
        continue
      fi
      gone=0
      txt="$(printf '%s' "$raw" | jq -r '.result.text // ""')"
      if [ "$trusted" = 0 ] && printf '%s' "$txt" | grep -qi 'trust this folder'; then
        printf '\r' | "$AGT" session type --stdin --target "$id" >/dev/null 2>&1
        trusted=1; continue
      fi
      # Surface a usage-limit / login banner instead of timing out on it.
      if printf '%s' "$txt" | grep -qiE 'usage limit reached|Run /login|Please log in'; then
        echo "ready: $id is up but claude is BLOCKED (usage limit / login):"
        printf '%s\n' "$txt" | grep -iE 'usage limit|log ?in' | head -3
        exit 3
      fi
      # Ready markers, oldest UI to newest: shortcuts hint, "for agents" footer, token counter,
      # or the mode-cycle footer line ("⏵⏵ auto mode on (shift+tab to cycle)" — v2.1.224+ boot
      # screens show ONLY this one, which is why the old marker set caused false timeouts).
      if printf '%s' "$txt" | grep -qE '\? for shortcuts|for agents|[0-9]+ tokens|shift\+tab to cycle|mode on '; then
        echo "ready -> $id"; exit 0
      fi
    done
    echo "ready: timed out after ${secs}s waiting for $id"; exit 1 ;;
  select|view)
    # Bring a session to the foreground (steals focus). Rarely needed — read/status populate the buffer
    # without it — but wrapping it keeps even this allow-listed instead of a raw prompting agtermctl call.
    "$AGT" session select --target "$id" >/dev/null && echo "selected -> $id" ;;
  close)
    # Close a session (e.g. a worker you spawned) — allow-listed so shutdown/cleanup never prompts.
    "$AGT" session close --target "$id" >/dev/null && echo "closed -> $id" ;;
  read)
    out="$("$AGT" session text --target "$id" --json)"
    if ! printf '%s' "$out" | jq -e '.ok == true' >/dev/null 2>&1; then
      echo "read FAILED — session $id gone or unreadable:"
      printf '%s' "$out" | jq -c '.error // .' 2>/dev/null || printf '%s\n' "$out"
      exit 1
    fi
    printf '%s' "$out" | jq -r '.result.text' | tail -n "${1:-20}" ;;
  submit)
    printf '\r' | "$AGT" session type --stdin --target "$id" >/dev/null && echo "submitted Enter -> $id"
    "$(dirname "$0")/agterm-clear-status.sh" "$id" 2>/dev/null || true ;;
  key)
    case "${1:-}" in
      enter)  s=$'\r' ;;   down) s=$'\x1b[B' ;;   up) s=$'\x1b[A' ;;
      right)  s=$'\x1b[C' ;; left) s=$'\x1b[D' ;;   # right accepts a ghost prompt-suggestion
      ctrl-c) s=$'\x03' ;; esc)  s=$'\x1b' ;;
      *) echo "unknown key: '${1:-}' (enter|down|up|left|right|ctrl-c|esc)"; exit 1 ;;
    esac
    printf '%s' "$s" | "$AGT" session type --stdin --target "$id" >/dev/null && echo "sent '$1' -> $id"
    # enter/esc ANSWER a prompt (navigation keys don't) — clear a stale "blocked" glyph only then.
    case "$1" in enter|esc) "$(dirname "$0")/agterm-clear-status.sh" "$id" 2>/dev/null || true ;; esac ;;
  type|say)
    text="$*"; [ -n "$text" ] || { echo "missing text"; exit 1; }
    # clear any queued text (200 backspaces) then type ours
    { printf '\x7f%.0s' $(seq 1 200); printf '%s' "$text"; } \
      | "$AGT" session type --stdin --target "$id" >/dev/null
    if [ "$cmd" = say ]; then
      sleep 1
      printf '\r' | "$AGT" session type --stdin --target "$id" >/dev/null
      "$(dirname "$0")/agterm-clear-status.sh" "$id" 2>/dev/null || true
      echo "said + submitted -> $id: $text"
    else
      echo "typed (NOT submitted; verify then 'agent-ctl.sh submit') -> $id: $text"
    fi ;;
  status)
    # Classify a session: line 1 = idle|busy|waiting, then a short screen tail for context.
    # We ALWAYS compute BOTH signals and decide on both — neither is reliable alone:
    #
    #   (1) NATIVE — agterm's per-session .status from `tree --json` (idle|active|blocked|completed),
    #       published by a LOCAL agent's Claude Code lifecycle hooks (Stop->completed,
    #       UserPromptSubmit->active, Notification->blocked). Authoritative but can be STALE (a focused
    #       session auto-resets on visit) or ABSENT (an SSH agent runs Claude on a remote host that
    #       can't reach this socket — $AGTERM_SESSION_ID is unset there — so it never sets one).
    #   (2) SCREEN — a footer-anchored read of the LIVE buffer. The bottom footer is definitive and
    #       mutually exclusive: idle shows "? for shortcuts", busy "esc to interrupt", a live prompt
    #       "Esc to cancel" (whose menu can sit above a rendered task list, so scan a wider window for
    #       it). Anchoring on the footer — NOT the whole screen — is what stops the orchestrator's own
    #       transcript (which quotes worker prompt strings) from reading as a live prompt. "unknown"
    #       when the footer shows none of them (e.g. a plain shell, or a self-scrape of a blank frame).
    #       NB: "← for agents" is in BOTH idle and busy footers, so only "? for shortcuts" means idle.
    #
    # DECISION: the LIVE screen wins when it is definitive (it reflects the current frame); native
    # fills the gap when the screen is inconclusive. So an SSH agent (native none) rides on the screen;
    # a focused local whose native went stale rides on its live footer; and a self-scrape that catches
    # a blank frame falls back to native. This never re-blocks a real /o inject (a live idle footer
    # beats a stale native busy) and never injects into a menu (a menu footer is never "? for shortcuts").
    native="$("$AGT" tree --json | jq -r --arg id "$id" '
        .result.tree.workspaces[].sessions[] | select(.id==$id) | (.status // "")' | head -1)"
    case "$native" in
      idle|completed) nat=idle ;;
      active)         nat=busy ;;
      blocked)        nat=waiting ;;
      *)              nat=none ;;
    esac
    txt="$("$AGT" session text --target "$id" --json 2>/dev/null | jq -r '.result.text')"
    nb="$(printf '%s\n' "$txt" | grep -vE '^[[:space:]]*$')"
    foot="$(printf '%s\n' "$nb" | tail -6)"
    region="$(printf '%s\n' "$nb" | tail -20)"
    busy_re='to run in background|[Ee]sc to interrupt|ctrl\+b|· ↓ [0-9]'
    idle_re='\? for shortcuts|accept edits on|bypass permissions on|plan mode on'
    wait_re='Esc to cancel|Do you want to (proceed|create|make)|(❯|›)[[:space:]]*[0-9]+[.)][[:space:]]*(Yes|Allow|Proceed|No)'
    if   printf '%s' "$foot"   | grep -qE "$busy_re"; then scr=busy
    elif printf '%s' "$foot"   | grep -qE "$idle_re"; then scr=idle
    elif printf '%s' "$region" | grep -qE "$wait_re"; then scr=waiting
    else scr=unknown; fi
    if   [ "$scr" != unknown ]; then st="$scr"
    elif [ "$nat" != none ];    then st="$nat"
    else st=idle; fi
    echo "$st"
    echo "--- native=${native:-<none>} screen=$scr -> $st ---"
    printf '%s\n' "$nb" | tail -12 ;;
  *) echo "unknown subcommand: '$cmd'"; exit 1 ;;
esac
