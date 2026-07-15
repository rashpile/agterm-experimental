#!/bin/bash
# register.sh — record an agterm session as the orchestrator that Telegram (/o /os /oa) reaches.
# "Newer wins": plain overwrite.
#
#   register.sh                       # bind THIS session — $AGTERM_SESSION_ID (skill activation)
#   register.sh <session-id>          # bind <session-id> unconditionally (ostart.sh)
#   register.sh --toggle <session-id> # bind it, or UNBIND if it is already the registered one
#
# --toggle is opt-in, and only the cmd+shift+r chord passes it. Binding must stay the default: ostart.sh
# binds a session it just created, and the skill binds on request — for them a toggle would silently
# UNBIND on a repeat call, which is never what they mean.
#
# UNBINDING IS GLOBAL, not per-session: with no registered session, `/o` fails loudly (inject.sh exits 3
# "orchestrator not registered") and the approval buttons go quiet (tg-agterm.sh exits 0, posting
# nothing). It is the mute switch for the whole Telegram bridge.
#
# The argument exists for the keymap: a custom command runs under /bin/sh from the app, NOT inside the
# session's shell, so it has no $AGTERM_SESSION_ID — only the runner's $AGT_SESSION_ID. Same reason its
# stdout goes nowhere, which is why the status badge below IS the feedback: cyan = bound, grey = unbound.
#
# The badge is deliberately the ONLY mark: it is transient (Claude's lifecycle hooks own the glyph, and
# a keystroke in the pane clears it), so it acks the press but does not persist. A durable mark would
# have to be a background tint/watermark, which was tried and rejected — it darkens the whole session.
# To ask which session currently owns Telegram, read orchestrator_session.
#
# Exit: 0 registered/unregistered · 1 no session id (nothing written).
set -uo pipefail
export PATH="/opt/homebrew/bin:$PATH"
AGT=/opt/homebrew/bin/agtermctl

CYAN='#00d7d7'   # bound
GREY='#8a8a8a'   # unbound

cfgdir="$HOME/.config/orchestrating-agterm-agents"
regfile="$cfgdir/orchestrator_session"

toggle=0
if [ "${1:-}" = --toggle ]; then toggle=1; shift; fi
sid="${1:-${AGTERM_SESSION_ID:-}}"

if [ -z "$sid" ]; then
  echo "register.sh: no session id — pass one, or run inside an agterm session (\$AGTERM_SESSION_ID)." >&2
  exit 1
fi

lower() { printf '%s' "${1:-}" | tr 'A-Z' 'a-z'; }

prev="$([ -r "$regfile" ] && tr -d '[:space:]' < "$regfile")"

# Toggle off: this session is already the target, so pressing again means "stop reaching me". Compared
# case-insensitively — the id can arrive from $AGT_SESSION_ID, $AGTERM_SESSION_ID or `tree`, and
# tg-agterm.sh already folds case when matching the same file, so don't assume they agree.
if [ "$toggle" = 1 ] && [ -n "$prev" ] && [ "$(lower "$prev")" = "$(lower "$sid")" ]; then
  rm -f "$regfile"
  "$AGT" session status active --color "$GREY" --auto-reset --target "$sid" >/dev/null 2>&1
  echo "unregistered orchestrator session: $sid"
  exit 0
fi

mkdir -p "$cfgdir"
printf '%s\n' "$sid" > "$regfile"
chmod 600 "$regfile"

# Cyan one-shot badge: "the chord fired, this session now owns Telegram". --auto-reset clears it back
# to idle on the next visit, so it never lingers as a stale signal.
"$AGT" session status active --color "$CYAN" --auto-reset --target "$sid" >/dev/null 2>&1

echo "registered orchestrator session: $sid"
