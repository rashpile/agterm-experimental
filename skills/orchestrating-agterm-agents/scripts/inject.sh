#!/bin/bash
# inject.sh — inject a Telegram-originated message into the registered orchestrator session.
# Called by the pako-telegram /o command. Prefixes "[tg] " so the orchestrator knows the
# message arrived remotely and must reply via notify.sh (the user is not at the keyboard).
#
#   inject.sh <text...>
#
# Two rewrites of the text before it is typed:
#   * "-/xxx" -> "/xxx": pako-telegram treats any message that starts with "/" as one of ITS commands,
#     so a slash command meant for the session (pi's /continue, Claude's /clear) can never arrive
#     as "/o /continue". Send "/o -/continue" instead; the leading "-" is stripped here.
#   * no "[tg] " prefix for a tavern session (registered name starts with "tavern-"): the prefix is
#     an instruction to a Claude orchestrator to answer via notify.sh, and a tavern character would
#     read it as part of the user's line.
#
# And one re-route before either: "<name>: text" delivers to the live session "tavern-<name>" instead
# of the registered one, so several taverns can be held in one Telegram chat without switching.
# "<name>!: text" does the same AND makes that session the default target; "<name>!:" on its own is
# just the switch. See the block below.
#
# Exit: 0 injected · 2 empty message · 3 not registered · 4 registered session gone · 8 ambiguous name.
set -uo pipefail
export PATH="/opt/homebrew/bin:$PATH"

here="$(cd "$(dirname "$0")" && pwd)"
cfgdir="$HOME/.config/orchestrating-agterm-agents"
regfile="$cfgdir/orchestrator_session"
AGT=/opt/homebrew/bin/agtermctl
JQ="$(command -v jq || true)"   # resolves /usr/bin/jq or /opt/homebrew/bin/jq — don't hardcode
[ -n "$JQ" ] || { echo "inject.sh: jq not found on PATH." >&2; exit 5; }

text="$*"
if [ -z "$text" ]; then
  echo "inject.sh: empty message — nothing to send." >&2
  exit 2
fi

# Telegram's phone clients rewrite a typed "--" into an em dash (U+2014) before the message leaves the
# app, so "/o -/scene --auto" arrives as "—auto". Turn every em dash back into "--": a real em dash
# typed from a phone is rare, a flag is not. A single "-" (as in "-/cmd") is left alone by Telegram.
# Done before the name check below so "raluka: —auto" is repaired too.
text="${text//—/--}"

# ---- address a tavern by character name: "raluka: what's new" / "raluka!: what's new" ----
# One Telegram chat, several taverns. The register holds ONE session, so without this every reply to a
# second character needs a /tavern-select round trip. Two forms, and the difference is the point:
#
#   "raluka: text"   one-off delivery — the register is NOT touched, the next bare message still goes
#                    wherever it went before. Answering a second character costs nothing and loses nothing.
#   "raluka!: text"  the same delivery, and raluka BECOMES the default target, so the conversation can
#                    continue without the prefix. "raluka!:" with no text is the switch on its own.
#
# Two forms rather than one because both are wanted: a quick aside to another character must not silently
# hijack the thread, and a real change of who you are talking to must not need a separate command.
#
# It engages only when a session really matches, so an ordinary line that happens to open with a word
# and a colon ("note: buy milk") falls through untouched to the registered orchestrator. Matching is
# case-insensitive; an exact "tavern-<name>" beats the date-suffixed "tavern-<name>-09-21-22-42" that
# /tavern generates. More than one candidate and no single exact hit is reported, never guessed —
# picking a wrong tavern would put the message in front of the wrong character.

# Move the default target, through the ONE bind path everything else uses (register.sh: plain
# "newer wins" overwrite), so /tavern-select and the cmd+shift+r chord agree with it. register.sh's
# own stdout is dropped — the line printed here is what reaches Telegram.
bind_target() {
  if "$here/register.sh" "$1" >/dev/null 2>&1; then
    echo "★ $2 is now the default target — plain messages go here"
  else
    echo "⚠️ could not move the default target to $2 — it stays where it was"
  fi
}

routed=""
sticky=""
who=""
# The colon is checked on the untouched text: without it there is no address at all, and stripping a
# trailing "!" first would turn a plain "hello!" into a bid for the session "tavern-hello".
case "$text" in
  *:*)
    who="${text%%:*}"
    case "$who" in
      *!) sticky=1; who="${who%!}" ;;
    esac
    ;;
esac
if [ -n "$who" ] && printf '%s' "$who" | grep -qE '^[A-Za-z0-9_-]{1,32}$'; then
  want="$(printf '%s' "$who" | tr 'A-Z' 'a-z')"
  # The full session name works as the prefix too ("tavern-raluka: hi"), which is what the ambiguity
  # message below tells the user to fall back to — so don't turn it into "tavern-tavern-raluka".
  case "$want" in
    tavern-*) ;;
    *) want="tavern-$want" ;;
  esac
  # rank\tid\tname, rank 0 = exact name, 1 = "tavern-<name>-<something>"; sort -n puts exact first.
  hits="$("$AGT" tree --json 2>/dev/null | "$JQ" -r --arg n "$want" '
    .result.tree.workspaces[].sessions[]
    | (.name | ascii_downcase) as $ln
    | select($ln == $n or ($ln | startswith($n + "-")))
    | "\(if $ln == $n then 0 else 1 end)\t\(.id)\t\(.name)"' | sort -n)"
  if [ -n "$hits" ]; then
    n_all="$(printf '%s\n' "$hits" | awk 'END{print NR}')"
    n_exact="$(printf '%s\n' "$hits" | awk -F'\t' '$1=="0"{c++} END{print c+0}')"
    if [ "$n_exact" != 1 ] && [ "$n_all" -gt 1 ]; then
      printf '"%s" matches %s sessions — no single exact "%s", so nothing was sent:\n' "$who" "$n_all" "$want"
      printf '%s\n' "$hits" | cut -f3 | sed 's/^/  /'
      printf 'Use the full name as the prefix, or /tavern-select.\n'
      exit 8
    fi
    first="$(printf '%s\n' "$hits" | head -1)"
    target="$(printf '%s' "$first" | cut -f2)"
    name="$(printf '%s' "$first" | cut -f3)"
    text="${text#*:}"
    text="${text#"${text%%[![:space:]]*}"}"   # drop the whitespace right after the colon, keep the rest
    if [ -z "$text" ]; then
      # "<name>!:" alone: a switch and nothing else. Without the "!" an empty line is just a mistake.
      if [ -n "$sticky" ]; then
        bind_target "$target" "$name"
        exit 0
      fi
      echo "inject.sh: \"$who:\" carries no message — nothing to send." >&2
      exit 2
    fi
    routed=1
  fi
fi

if [ -z "$routed" ]; then
  target="$([ -r "$regfile" ] && tr -d '[:space:]' < "$regfile")"
  if [ -z "$target" ]; then
    echo "inject.sh: orchestrator not registered — start the orchestrating-agterm-agents skill so it registers its session (expected file: $regfile)." >&2
    exit 3
  fi

  # Fail loudly if the registered session has since closed, instead of injecting into the void.
  # The same lookup yields the session NAME (jq -e: no match -> non-zero), which picks the prefix below.
  if ! name="$("$AGT" tree --json | "$JQ" -er --arg id "$target" \
        '.result.tree.workspaces[].sessions[] | select(.id==$id) | .name // ""' 2>/dev/null)"; then
    echo "inject.sh: registered orchestrator session $target no longer exists — re-run register.sh in the orchestrator." >&2
    exit 4
  fi
fi

# "-/cmd" -> "/cmd" (see header). Only a leading "-/" is touched; "-" alone or "--foo" pass through.
case "$text" in
  -/*) text="${text#-}" ;;
esac

# Tavern sessions get the text verbatim and are NOT status-gated: the busy/waiting classifier below
# reads Claude Code footers and agterm hook statuses, neither of which a pi chat produces reliably
# (a pi session was seen stuck at native=active while idle at its prompt, so /o kept refusing with
# "busy"). A tavern has no permission menus to mis-answer, so just type and submit.
case "$name" in
  tavern-*)
    # Only on a re-route: with the registered session the user already knows who they are talking to.
    if [ -n "$routed" ]; then
      echo "→ $name"
      # Bound before the send, not after: the send `exec`s away, and a failed type is far rarer than
      # the confusion of a message that arrived while the target silently did not move.
      if [ -n "$sticky" ]; then bind_target "$target" "$name"; fi
    fi
    exec "$here/agent-ctl.sh" say "$target" "$text" ;;
esac
msg="[tg] $text"

# Keep the orchestrator's own unread-notification badge from climbing: it fires its own permission
# notifications while unfocused, and driving it purely over Telegram never "visits" the session to
# clear them. Ack on each inbound /o (session.seen, agterm 0.8.3+ — idempotent, focus-free,
# best-effort; skipped silently on older agterm).
"$AGT" session seen --target "$target" >/dev/null 2>&1 || true

# Status-aware injection: never blindly push into a busy session or a live prompt. Pushing text +
# Enter into an AskUserQuestion / permission menu makes the message become the menu's answer (that
# exact bug corrupted a real reply). So read the orchestrator's state first and give the user honest
# Telegram feedback instead of silently mis-firing.
status_out="$("$here/agent-ctl.sh" status "$target")"
state="$(printf '%s\n' "$status_out" | head -1)"
context="$(printf '%s\n' "$status_out" | sed -n '3,14p')"

case "$state" in
  idle)
    exec "$here/agent-ctl.sh" say "$target" "$msg" ;;
  waiting)
    printf '⚠️ Orchestrator is waiting on a prompt — your message was NOT sent.\nIt is asking:\n%s\n\nReply with your choice to answer it, or resend once it moves on.\n' "$context"
    exit 6 ;;
  busy)
    printf '⏳ Orchestrator is busy right now — message NOT sent. Please resend in a moment.\n'
    exit 7 ;;
  *)
    # unknown state — best-effort inject rather than drop the message
    exec "$here/agent-ctl.sh" say "$target" "$msg" ;;
esac
