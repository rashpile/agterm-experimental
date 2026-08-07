#!/bin/bash
# Read Claude Code's local /usage screen without sending a model prompt.
set -euo pipefail

readonly SCRIPT_NAME="${0##*/}"
readonly POLL_INTERVAL="${CLAUDE_USAGE_POLL_INTERVAL:-0.5}"
readonly START_TIMEOUT="${CLAUDE_USAGE_START_TIMEOUT:-30}"
readonly USAGE_TIMEOUT="${CLAUDE_USAGE_RESULT_TIMEOUT:-15}"

session_id=""
last_screen=""

die() {
  printf '%s: %s\n' "$SCRIPT_NAME" "$*" >&2
  exit 1
}

usage() {
  printf 'Usage: %s [--current]\n' "$SCRIPT_NAME"
}

current_only=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --current) current_only=1 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown option: $1" ;;
  esac
  shift
done
readonly CURRENT_ONLY="$current_only"

need_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

close_session() {
  local closing_id="$session_id"
  [ -n "$closing_id" ] || return 0

  if "$AGTERMCTL_BIN" session close --target "$closing_id" >/dev/null 2>&1; then
    session_id=""
    return 0
  fi

  printf '%s: warning: could not close temporary agterm session %s\n' \
    "$SCRIPT_NAME" "$closing_id" >&2
  return 1
}

on_exit() {
  close_session || true
}

trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

need_command agtermctl
need_command claude
need_command jq

readonly AGTERMCTL_BIN="${AGTERMCTL_BIN:-$(command -v agtermctl)}"
readonly CLAUDE_BIN="${CLAUDE_BIN:-$(command -v claude)}"

case "$START_TIMEOUT:$USAGE_TIMEOUT" in
  *[!0-9:]*|:*|*:) die "timeouts must be whole seconds" ;;
esac

read_screen() {
  "$AGTERMCTL_BIN" session text \
    --target "$session_id" --lines 240 --json 2>/dev/null \
    | jq -er '.result.text // ""' 2>/dev/null
}

wait_for_ready() {
  local deadline=$((SECONDS + START_TIMEOUT))

  while [ "$SECONDS" -lt "$deadline" ]; do
    last_screen="$(read_screen || true)"

    if printf '%s\n' "$last_screen" | grep -Eqi 'trust (this folder|the files in this folder)'; then
      die "Claude is waiting for folder trust; open Claude once in $PWD and approve it"
    fi

    if printf '%s\n' "$last_screen" | grep -q '❯' \
      && printf '%s\n' "$last_screen" | grep -q 'Model:'; then
      return 0
    fi

    sleep "$POLL_INTERVAL"
  done

  die "Claude did not reach its input prompt within ${START_TIMEOUT}s"
}

wait_for_usage() {
  local deadline=$((SECONDS + USAGE_TIMEOUT))
  local reset_count=0

  while [ "$SECONDS" -lt "$deadline" ]; do
    last_screen="$(read_screen || true)"

    if [ "$CURRENT_ONLY" -eq 1 ]; then
      reset_count="$(printf '%s\n' "$last_screen" \
        | grep -c '^[[:space:]]*Resets ' || true)"
      if printf '%s\n' "$last_screen" | grep -q 'Current session' \
        && printf '%s\n' "$last_screen" | grep -q 'Current week (all models)' \
        && [ "$reset_count" -ge 2 ]; then
        return 0
      fi
    else
      if printf '%s\n' "$last_screen" | grep -q 'Total cost:' \
        && printf '%s\n' "$last_screen" | grep -q 'Current session' \
        && { printf '%s\n' "$last_screen" | grep -q 'Usage credits' \
          || printf '%s\n' "$last_screen" | grep -q 'Esc to cancel'; }; then
        return 0
      fi
    fi

    sleep "$POLL_INTERVAL"
  done

  die "Claude did not render /usage within ${USAGE_TIMEOUT}s"
}

reveal_clipped_credits() {
  local first_screen="$last_screen"
  local scrolled_screen=""
  local deadline=$((SECONDS + USAGE_TIMEOUT))

  [ "$CURRENT_ONLY" -eq 0 ] || return 0

  if ! printf '%s\n' "$first_screen" | grep -q 'Usage credits' \
    || printf '%s\n' "$first_screen" | grep -q ' spent'; then
    return 0
  fi

  # Page Down only drives the local /usage view; it does not submit a prompt.
  printf '\033[6~' \
    | "$AGTERMCTL_BIN" session type --stdin --target "$session_id" >/dev/null 2>&1 \
    || return 0

  while [ "$SECONDS" -lt "$deadline" ]; do
    scrolled_screen="$(read_screen || true)"
    if printf '%s\n' "$scrolled_screen" | grep -q 'Usage credits' \
      && { printf '%s\n' "$scrolled_screen" | grep -q ' spent' \
        || printf '%s\n' "$scrolled_screen" | grep -q 'Esc to cancel'; }; then
      last_screen="${first_screen}
${scrolled_screen}"
      return 0
    fi
    sleep "$POLL_INTERVAL"
  done
}

parse_report() {
  awk -v current_only="$CURRENT_ONLY" '
    function trim(value) {
      sub(/^[[:space:]]+/, "", value)
      sub(/[[:space:]]+$/, "", value)
      return value
    }

    function begin_quota(value) {
      quota_label = trim(value)
      quota_usage = ""
    }

    {
      line = trim($0)

      if (line == "Current session" || line ~ /^Current week( \(|$)/) {
        begin_quota(line)
        next
      }

      if (quota_label != "" && quota_usage == "" \
          && match(line, /[0-9][0-9]*% used/)) {
        quota_usage = substr(line, RSTART, RLENGTH)
        next
      }

      if (quota_label != "" && quota_usage != "" && line ~ /^Resets /) {
        wanted = !current_only || quota_label == "Current session" \
          || quota_label == "Current week (all models)"
        if (wanted && !printed[quota_label]) {
          printf "  %s: %s; %s\n", quota_label, quota_usage, line
          printed[quota_label] = 1
          found = 1
        }
        quota_label = ""
        quota_usage = ""
        next
      }

      if (!current_only && line == "Usage credits") {
        credits = 1
        credit_usage = ""
        next
      }

      if (credits && credit_usage == "" && match(line, /[0-9][0-9]*% used/)) {
        credit_usage = substr(line, RSTART, RLENGTH)
        next
      }

      if (credits && credit_usage != "" && line ~ / spent/) {
        printf "  Usage credits: %s — %s\n", credit_usage, line
        credits = 0
        found = 1
      }
    }

    END {
      if (!found) exit 1
    }
  '
}

current_time_line() {
  local report_timezone=""
  local clock_time=""

  report_timezone="$(printf '%s\n' "$last_screen" \
    | sed -n 's/.*(\([^()]*\))[[:space:]]*$/\1/p' \
    | sed -n '1p')"

  if [ -n "$report_timezone" ]; then
    clock_time="$(TZ="$report_timezone" date '+%I:%M%p')"
  else
    report_timezone="$(date '+%Z')"
    clock_time="$(date '+%I:%M%p')"
  fi

  clock_time="$(printf '%s' "$clock_time" | tr '[:upper:]' '[:lower:]')"
  clock_time="${clock_time#0}"
  printf 'Current time: %s (%s)\n' "$clock_time" "$report_timezone"
}

processes_workspace_id="$("$AGTERMCTL_BIN" tree --json 2>/dev/null \
  | jq -er '[.result.tree.workspaces[] | select(.name == "processes")][0].id // empty' \
    2>/dev/null || true)"

session_args=(session new
  --cwd "$PWD"
  --name claude-usage
  --command "$CLAUDE_BIN"
  --no-select
  --json)
if [ -n "$processes_workspace_id" ]; then
  session_args+=(--workspace "$processes_workspace_id")
fi

create_json="$("$AGTERMCTL_BIN" "${session_args[@]}" 2>/dev/null)" \
  || die "could not create a background agterm session"

session_id="$(printf '%s\n' "$create_json" | jq -er '.result.id' 2>/dev/null)" \
  || die "agterm did not return the new session id"

wait_for_ready

printf '/usage\n' \
  | "$AGTERMCTL_BIN" session type --stdin --target "$session_id" >/dev/null 2>&1 \
  || die "could not type /usage into the temporary session"

wait_for_usage
reveal_clipped_credits

report="$(printf '%s\n' "$last_screen" | parse_report)" \
  || die "could not parse Claude's /usage screen"
now="$(current_time_line)"

close_session || die "temporary agterm session remains open"
trap - EXIT INT TERM

if [ "$CURRENT_ONLY" -eq 1 ]; then
  printf '%s\n%s\n' "$now" "$report"
else
  printf '%s\nClaude usage (no model request sent)\n%s\n' "$now" "$report"
fi
