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
SQUEEZE=${BTW_SQUEEZE_BYTES:-24576}
TAIL=${BTW_TAIL_MSGS:-20}
CCHARS=${BTW_COLLAPSE_CHARS:-160}

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

def brief($n):
  ((split("\n") | map(select(length > 0)) | .[0]) // "") as $l
  | (($l | index(". ")) as $i | if $i == null then $l else $l[0:$i+1] end)
  | if (length > $n) then .[0:$n] + "…" else . end;

# NOT `def label:` — `label` is a jq reserved keyword (`label $out | …`) and
# defining it is a hard syntax error.
def rolename: if . == "user" then "user" else "claude" end;

def full:
  if .kind == "marker" then "--- \(.text) ---"
  elif .role == "user" then "## user\n\(.text)"
  else "## claude\n\(.text | trunc($ac))"
  end;

def collapsed:
  if .kind == "marker" then "- --- \(.text) ---"
  else "- **\(.role | rolename)** \(.text | brief($cc))"
  end;

if $mode == "verbatim" then
  map(full) | join("\n\n")
else
  (length - $tail) as $cut
  | if $cut <= 0 then (map(full) | join("\n\n"))
    else
      "## earlier (collapsed)\n\n"
      + (.[0:$cut] | map(collapsed) | join("\n"))
      + "\n\n"
      + (.[$cut:] | map(full) | join("\n\n"))
    end
end
JQ

render() {  # $1 = verbatim|squeezed
  print -r -- "$norm" | jq -sr \
    --arg mode "$1" --argjson ac "$ACHARS" --argjson tail "$TAIL" --argjson cc "$CCHARS" \
    "$RENDER"
}

total=$(print -r -- "$norm" | jq -s 'length')
mode=verbatim
collapsed=0
body=$(render verbatim)
bytes=$(print -r -- "$body" | wc -c | tr -d ' ')

if (( bytes > SQUEEZE )) && (( total > TAIL )); then
  mode=squeezed
  collapsed=$(( total - TAIL ))
  body=$(render squeezed)
  bytes=$(print -r -- "$body" | wc -c | tr -d ' ')
fi

msgs=$(print -r -- "$norm"  | jq -s '[.[] | select(.kind == "msg")]    | length')
marks=$(print -r -- "$norm" | jq -s '[.[] | select(.kind == "marker")] | length')

print -r -- "$body"
print -ru2 -- "messages=$msgs markers=$marks mode=$mode bytes=$bytes collapsed=$collapsed"
