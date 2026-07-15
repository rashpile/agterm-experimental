# finish-badge.zsh — drive the agterm session's sidebar status glyph from the shell:
#   • while a command RUNS   → dim blinking `active` (the same running glyph agterm shows for agents)
#   • when it FINISHES        → green `completed` (exit 0) or blinking yellow `blocked` (exit != 0),
#                               but only if it ran >= AGTERM_BADGE_MIN_SECS, and --auto-reset so the
#                               colour is only ever SEEN on a tab you're not watching (clears on visit)
#   • short / agent / interactive commands → just clear back to `idle` on finish (running glyph only)
#
# Companion to agterm's shipped agent-status integration; self-contained. Source from your interactive
# zshrc. Clean no-op outside agterm, outside zsh, or when disabled.
#
# Both the running-set (preexec) and the finish-set (precmd) run SYNCHRONOUSLY — the shipped agent
# integration does the same. This is deliberate: two async status writes from consecutive commands can
# arrive out of order and leave a stuck glyph, so we order them by running in the foreground. Each call
# is a fast local-socket round-trip.
#
# Knobs (export any time — AGTERM_BADGE_DISABLE is a LIVE toggle; the rest are read per command):
#   AGTERM_BADGE_DISABLE     (unset)      any non-empty value turns the whole hook off, immediately,
#                                         in the current shell too — `export AGTERM_BADGE_DISABLE=1` to
#                                         stop it now; `unset AGTERM_BADGE_DISABLE` to turn it back on.
#   AGTERM_BADGE_MIN_SECS    (10)         min command seconds before a FINISH shows green/yellow
#   AGTERM_BADGE_RUN_COLOR   (#a9c1d4)    RUNNING glyph tint (light blue-grey)
#   AGTERM_BADGE_OK_COLOR    (#8fce9b)    SUCCESS glyph tint (light green)
#   AGTERM_BADGE_FAIL_COLOR  (#e6b800)    FAILURE glyph tint (amber)
#         ^ all three default to LIGHT shades so a command is distinct from an AGENT (which uses
#           agterm's default active/completed/blocked colours); set any of them empty to match agents.
#   AGTERM_BADGE_IGNORE_RE   (see below)  commands that only get the running glyph (no finish colour)
#   AGTERM_BADGE_CTL         (agtermctl)  path to agtermctl
#   AGTERM_AGENT_RE          (shipped default) agent commands: running glyph only, no finish colour

# ── guards ───────────────────────────────────────────────────────────────────
# Structural guards are constant for a shell, so they gate at source time. AGTERM_BADGE_DISABLE is NOT
# checked here — it's a runtime toggle re-checked inside each hook (below), so it can flip on/off live.
[ -n "${AGTERM_SESSION_ID:-}" ]    || return 0   # agterm sessions only
[ -n "${ZSH_VERSION:-}" ]          || return 0   # zsh only
[ -z "${__fb_loaded:-}" ]          || return 0   # wire the hooks once per shell
__fb_loaded=1

zmodload zsh/datetime 2>/dev/null                # provides $EPOCHSECONDS
autoload -Uz add-zsh-hook

: "${AGTERM_BADGE_MIN_SECS:=10}"
: "${AGTERM_BADGE_CTL:=/opt/homebrew/bin/agtermctl}"
: "${AGTERM_AGENT_RE:=^(codex|gemini|cursor-agent|aider|opencode|crush|goose)([[:space:]]|$)}"
: "${AGTERM_BADGE_IGNORE_RE:=^(vim|nvim|less|more|man|htop|top|ssh|tmux|watch|fzf)([[:space:]]|$)}"

_fb_status() {  # _fb_status <state> [extra flags...] — always tags this shell's pane + session
  "$AGTERM_BADGE_CTL" session status "$@" \
    --pane "${AGTERM_PANE:-left}" --target "$AGTERM_SESSION_ID" >/dev/null 2>&1
}

# _fb_tinted <colour-or-empty> <state> [flags...] — _fb_status plus `--color <c>` when <c> is non-empty.
# Our badges default to LIGHT tints so a command is visually distinct from an AGENT (agents use agterm's
# default active/completed/blocked colours); set the matching *_COLOR var empty to match agents exactly.
_fb_tinted() {
  local c=$1; shift
  if [ -n "$c" ]; then _fb_status "$@" --color "$c"; else _fb_status "$@"; fi
}

# ── hooks ────────────────────────────────────────────────────────────────────
# preexec: a command is about to run → show the dim running glyph immediately (blinking; agterm dims it).
_fb_preexec() {
  [ -z "${AGTERM_BADGE_DISABLE:-}" ] || return   # live off-switch
  __fb_start=$EPOCHSECONDS
  __fb_cmd=$1
  _fb_tinted "${AGTERM_BADGE_RUN_COLOR-#a9c1d4}" active --blink   # running: light blue-grey
}

# precmd: the command finished → replace the running glyph with the terminal state.
_fb_precmd() {
  local st=$?                                     # exit code — capture FIRST
  [ -z "${AGTERM_BADGE_DISABLE:-}" ] || return    # live off-switch
  [ -n "${__fb_start:-}" ] || return              # no command ran this prompt (bare Enter): leave it
  local dur=$(( EPOCHSECONDS - __fb_start )); unset __fb_start

  if (( dur < AGTERM_BADGE_MIN_SECS )) \
     || [[ ${__fb_cmd:-} =~ $AGTERM_AGENT_RE ]] \
     || [[ ${__fb_cmd:-} =~ $AGTERM_BADGE_IGNORE_RE ]]; then
    _fb_status idle                               # too short / agent / interactive: clear running glyph
  elif (( st == 0 )); then
    _fb_tinted "${AGTERM_BADGE_OK_COLOR-#8fce9b}" completed --auto-reset          # success: light green
  else
    _fb_tinted "${AGTERM_BADGE_FAIL_COLOR-#e6b800}" blocked --blink --auto-reset  # failure: amber/yellow
  fi
}

add-zsh-hook preexec _fb_preexec
add-zsh-hook precmd  _fb_precmd
