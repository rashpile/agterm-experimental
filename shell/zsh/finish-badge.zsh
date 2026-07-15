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
#   AGTERM_BADGE_IGNORE_RE   (see below)  commands this hook never touches (agents + interactive TUIs)
#   AGTERM_BADGE_IGNORE_FILE (~/.config/agterm-projects/.badgeignore)
#                                         extra commands to never badge — one bare command name per
#                                         line, e.g. `bookokrat`. Blanks and #comments ignored.
#   AGTERM_BADGE_ALLOW_FILE  (~/.config/agterm-projects/.badgeallow)
#                                         ALLOWLIST. If this file has ANY entry, badge ONLY the commands
#                                         named in it and skip everything else — the ignore lists then
#                                         don't apply. One bare command name per line. Empty/absent =
#                                         normal (ignore-list) behaviour.
#         ^ both files are read live per command, so edits apply immediately — no re-source needed.
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
: "${AGTERM_BADGE_IGNORE_RE:=^(claude|nclaude|vim|nvim|less|more|man|htop|top|ssh|tmux|watch|fzf|yazi)([[:space:]]|$)}"
: "${AGTERM_BADGE_IGNORE_FILE:=$HOME/.config/agterm-projects/.badgeignore}"
: "${AGTERM_BADGE_ALLOW_FILE:=$HOME/.config/agterm-projects/.badgeallow}"

# _fb_list <file> — print the file's usable entries (one per line; blanks and #comments dropped).
# Read live per command, so editing a list file takes effect immediately. Forkless ($(<) / read builtins).
_fb_list() {
  [[ -r $1 ]] || return
  local line
  while IFS= read -r line || [[ -n $line ]]; do
    line=${line%%#*}                # strip trailing comment
    line=${line//[[:space:]]/}      # entries are bare command names
    [[ -n $line ]] && print -r -- $line
  done < $1
}

# _fb_skip <cmdline> — true for commands whose glyph we do NOT own.
#
# MUST be the single source of truth for BOTH hooks. Badging in preexec but only skipping in precmd is
# what put a stuck glyph on every session after an agterm restart: agterm re-runs each session's restored
# foreground command, that fires preexec, and a long-running one (claude/ssh/yazi) never returns to a
# prompt — so precmd never ran and nothing ever cleared it. If we would not colour a command on finish,
# we must not badge it on start.
#
# Order:
#   1. $AGTERM_BADGE_ALLOW_FILE has any entry → ALLOWLIST MODE: badge ONLY those commands, skip all else.
#      Authoritative — the regex/ignore lists below do not apply (listing `claude` there means you get a
#      badge on claude, overwriting Claude Code's own status; that's your call).
#   2. otherwise skip: agents ($AGTERM_AGENT_RE) · interactive ($AGTERM_BADGE_IGNORE_RE) ·
#      anything named in $AGTERM_BADGE_IGNORE_FILE.
# Lists match the command's FIRST WORD (`bookokrat --foo` → `bookokrat`), same as the regexes.
_fb_skip() {
  # The command's first word, NORMALISED. Three traps here, all found the hard way:
  #  1. assign to an array first — `${${(z)1}[1]}` silently subscripts the *string* (giving "b" for
  #     "bookokrat") when the split yields a single word, and only acts as an array with 2+ words.
  #  2. (Q) strips one level of shell quoting. agterm's restart re-runs each session's restored
  #     foreground command SHELL-QUOTED — preexec receives `'claude' '--resume' '…'` — so the raw word is
  #     `'claude'` (leading quote) and every match fails. That badged every restored session.
  #  3. :t takes the basename, so a full-path invocation (/opt/homebrew/bin/claude) matches too.
  local -a words allow ignore
  words=(${(z)1})
  local w=${${(Q)words[1]}:t}

  allow=(${(f)"$(_fb_list $AGTERM_BADGE_ALLOW_FILE)"})
  if (( $#allow )); then
    (( ${allow[(Ie)$w]} )) && return 1        # on the allowlist → ours, badge it
    return 0                                  # allowlist mode: everything else is skipped
  fi

  # match the normalised word, NOT the raw line — the line may be shell-quoted (see above). The regexes
  # end with `([[:space:]]|$)`, so they match a bare word fine.
  [[ $w =~ $AGTERM_AGENT_RE ]]        && return 0
  [[ $w =~ $AGTERM_BADGE_IGNORE_RE ]] && return 0
  ignore=(${(f)"$(_fb_list $AGTERM_BADGE_IGNORE_FILE)"})
  (( ${ignore[(Ie)$w]} )) && return 0
  return 1
}

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
  # Not our command → touch nothing (no active here, and the unset makes precmd a no-op too, so we can
  # never clobber a glyph another integration owns). Also covers agterm's restart, which re-runs each
  # session's restored foreground command.
  if _fb_skip "$1"; then unset __fb_start; return; fi
  __fb_start=$EPOCHSECONDS
  __fb_cmd=$1
  _fb_tinted "${AGTERM_BADGE_RUN_COLOR-#a9c1d4}" active --blink   # running: light blue-grey
}

# precmd: the command finished → replace the running glyph with the terminal state.
_fb_precmd() {
  local st=$?                                     # exit code — capture FIRST
  [ -z "${AGTERM_BADGE_DISABLE:-}" ] || return    # live off-switch
  # unset unless preexec claimed this command — a bare Enter, or one _fb_skip rejected. Either way the
  # glyph isn't ours, so we leave it entirely alone (setting `idle` here would wipe Claude's own status).
  [ -n "${__fb_start:-}" ] || return
  local dur=$(( EPOCHSECONDS - __fb_start )); unset __fb_start

  if (( dur < AGTERM_BADGE_MIN_SECS )); then
    _fb_status idle                               # too short to colour: just clear our running glyph
  elif (( st == 0 )); then
    _fb_tinted "${AGTERM_BADGE_OK_COLOR-#8fce9b}" completed --auto-reset          # success: light green
  else
    _fb_tinted "${AGTERM_BADGE_FAIL_COLOR-#e6b800}" blocked --blink --auto-reset  # failure: amber/yellow
  fi
}

add-zsh-hook preexec _fb_preexec
add-zsh-hook precmd  _fb_precmd
