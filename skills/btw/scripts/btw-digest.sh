#!/bin/zsh
# btw-digest.sh <transcript.jsonl> <from-line>
#
# Reads a Claude transcript from <from-line> (1-based, inclusive) to EOF and
# renders the human-readable turns as markdown.
#
#   stdout  the markdown body — no timestamps, no paths, no ids, so it is a
#           pure function of its inputs and can be golden-tested
#   stderr  one report line:
#           messages=N markers=N mode=verbatim|squeezed bytes=N collapsed=N
#
# Reads lines defensively (`fromjson? // empty`): the base transcript may be
# mid-write, so a torn final line must be skipped, not fatal.
set -u

file=${1:?usage: btw-digest.sh <transcript.jsonl> <from-line>}
from=${2:-1}

ACHARS=${BTW_ASSISTANT_CHARS:-1200}

read -r -d '' NORMALIZE <<'JQ'
def textof:
  if (.message.content | type) == "string" then .message.content
  else [ .message.content[]? | select(.type == "text") | .text ] | join("\n\n")
  end;

def clean:
    gsub("<system-reminder>.*?</system-reminder>"; ""; "m")
  | gsub("<local-command-stdout>.*?</local-command-stdout>"; ""; "m")
  | sub("^[[:space:]]+"; "")
  | sub("[[:space:]]+$"; "");

fromjson? // empty
| if (.type == "system" and .subtype == "compact_boundary") then
    {kind: "marker", text: "base compacted here"}
  elif (.isCompactSummary == true) then
    empty
  elif (.isSidechain == true or .isMeta == true) then
    empty
  elif (.type == "user" or .type == "assistant") then
    (textof | clean) as $t
    | if $t == "" then empty else {kind: "msg", role: .type, text: $t} end
  else
    empty
  end
JQ

norm=$(tail -n "+$from" -- "$file" 2>/dev/null | jq -cR "$NORMALIZE" 2>/dev/null)

if [[ -z $norm ]]; then
  print -ru2 -- "messages=0 markers=0 mode=verbatim bytes=0 collapsed=0"
  exit 0
fi

read -r -d '' RENDER <<'JQ'
def trunc($n):
  if (length > $n) then .[0:$n] + "…[+" + ((length - $n) | tostring) + " chars]" else . end;

def full:
  if .kind == "marker" then "--- \(.text) ---"
  elif .role == "user" then "## user\n\(.text)"
  else "## claude\n\(.text | trunc($ac))"
  end;

map(full) | join("\n\n")
JQ

body=$(print -r -- "$norm" | jq -sr --argjson ac "$ACHARS" "$RENDER")

msgs=$(print -r -- "$norm"   | jq -s '[.[] | select(.kind == "msg")]    | length')
marks=$(print -r -- "$norm"  | jq -s '[.[] | select(.kind == "marker")] | length')
bytes=$(print -r -- "$body" | wc -c | tr -d ' ')

print -r -- "$body"
print -ru2 -- "messages=$msgs markers=$marks mode=verbatim bytes=$bytes collapsed=0"
