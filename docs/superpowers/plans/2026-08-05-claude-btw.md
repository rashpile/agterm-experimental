# claude-btw Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A hotkey that forks the Claude conversation in the current agterm tab into a new tab, which can later pull in what the base tab did since the fork and push a one-line answer back.

**Architecture:** A Claude `SessionStart`/`SessionEnd` hook maintains a registry mapping agterm tab+pane to the Claude session running there. An agterm custom command reads that registry, records a watermark into the base transcript, and spawns a tab running `claude --resume <id> --fork-session`. Two scripts in the fork read the base transcript past the watermark (`btw-sync`) and type a capped one-liner into the base prompt after a refusal-verdict check (`btw-back`).

**Tech Stack:** zsh, `jq`, `agtermctl`. No compiled code, no daemon, no package manager, no test framework.

**Spec:** `docs/superpowers/specs/2026-08-03-claude-btw-design.md`. Read it before Task 1.

## Global Constraints

- **zsh, not bash.** Every script starts `#!/bin/zsh` and `set -u`. The repo's existing commands (`commands/quickloc/quickloc.sh`) are zsh and use zsh-only constructs (`(N)` glob qualifiers, `${0:A:h}`). Match that.
- **No new dependencies.** `jq` and `agtermctl` only. No `bats`, no `shunit2`, no Python.
- **Every script reads its roots from the environment**, with these exact names and defaults:
  - `AGTERM_TABS_DIR` → `$HOME/.claude/agterm/tabs`
  - `BTW_ROOT` → `$HOME/.claude/btw`
  - `AGTERMCTL` → `/opt/homebrew/bin/agtermctl`
  - `CLAUDE_PROJECTS` → `$HOME/.claude/projects`
- **Never derive the Claude project-directory slug from a cwd.** Find a transcript by globbing `$CLAUDE_PROJECTS/*/<session-id>.jsonl`. Session ids are UUIDs and globally unique.
- **`agtermctl session type` turns every newline into Return.** Any text handed to it must be a single line. This is a safety constraint, not a style rule.
- **`agtermctl session new --command` runs argv-style with no shell** and without `/opt/homebrew/bin` on `PATH`. Always wrap: `--command "zsh -lc '…'"`.
- **`--json` is required** to read values back from `agtermctl`; without it, mutations print `ok`. New session id is at `.result.id`. Tree sessions are at `.result.tree.workspaces[].sessions[]`. Pane text is at `.result.text`.
- **`btw-digest.sh` output must contain no timestamps, no paths, and no session ids.** It is a pure function of its inputs so golden-file tests stay stable. Headers with timestamps are added by `btw-sync.sh`, which is not golden-tested.
- **Terminology clarification against the spec:** the spec's size guard says "last 20 turns" and "N older turns collapsed". A transcript contains messages, not turns, and turns are not delimited in it. The implementation counts **messages** and the knob is `BTW_TAIL_MSGS`. Report text says "messages".
- **Commit after every task** with the message given in the task's final step.

---

## File Structure

| File | Responsibility |
|---|---|
| `commands/claude-btw/tests/run.sh` | The whole automated suite. Run by hand. Never touches agterm or Claude. |
| `commands/claude-btw/tests/fixtures/*.jsonl` | Synthetic transcripts, one per filtering hazard. |
| `commands/claude-btw/tests/golden/*.md` | Expected digest output. |
| `commands/claude-btw/tests/fake-agtermctl.sh` | Canned `tree`/`session text` responses for verdict tests. |
| `skills/btw/scripts/btw-digest.sh` | Transcript slice → markdown. Pure function. The only fiddly logic in the project. |
| `skills/btw/scripts/lib.sh` | Shared: env roots, dependency check, transcript lookup, registry read, fork-meta read, refusal verdicts. |
| `skills/btw/scripts/btw-sync.sh` | Calls the digest, writes files with a header, prints a report, advances the watermark. |
| `skills/btw/scripts/btw-back.sh` | Verdict-check, compose capped one-liner, type into base, raise glyph. |
| `skills/btw/SKILL.md` | When and how Claude in a fork uses the two operations. |
| `skills/btw/commands/btw-sync.md`, `btw-back.md` | Slash-command wrappers, copied to `~/.claude/commands/` at install. |
| `hooks/agterm-tabs/register-tab.sh` | Maintain the registry; pin fork restore. |
| `hooks/agterm-tabs/README.md` | Registry file format, for other consumers. |
| `hooks/agterm-tabs/settings-snippet.json` | The two hook entries to paste into `~/.claude/settings.json`. |
| `commands/claude-btw/claude-btw.sh` | Hotkey target: prune, resolve, watermark, spawn, record. |
| `commands/claude-btw/keymap.conf` | Chord binding. |
| `README.md` | A `### claude-btw` section and a `hooks/` row in Contents. |

Task order follows the dependency chain: digest (pure, no dependencies) → shared lib → hook that writes what the lib reads → hotkey that creates what the lib reads → the two consumers → docs.

---

### Task 1: Test harness and the digest filter

The digest is where correctness bugs hide and the only thing worth automated tests. Build the harness and the filtering pass together — the harness is scaffolding the first test needs.

**Files:**
- Create: `skills/btw/scripts/btw-digest.sh`
- Create: `commands/claude-btw/tests/run.sh`
- Create: `commands/claude-btw/tests/fixtures/basic.jsonl`, `noise.jsonl`, `meta-sidechain.jsonl`, `system-reminder.jsonl`, `from-line.jsonl`
- Create: `commands/claude-btw/tests/golden/basic.md`, `noise.md`, `meta-sidechain.md`, `system-reminder.md`, `from-line.md`

**Interfaces:**
- Consumes: nothing.
- Produces: `btw-digest.sh <transcript.jsonl> <from-line>` — writes markdown body to **stdout**, and one report line to **stderr** in the form `messages=N markers=N mode=verbatim bytes=N collapsed=N`. Exit 0 even when the slice is empty (stdout empty, `messages=0`). Consumed by Task 2 and Task 6.
- Produces: `tests/run.sh` with helpers `ok`, `bad`, `assert_eq <name> <expected> <actual>`, `assert_golden <name> <actual>`, and `digest_case <fixture-name> <from-line> [ENV=val …]`. Consumed by Tasks 2 and 3.

- [ ] **Step 1: Write the fixtures**

`commands/claude-btw/tests/fixtures/basic.jsonl` — covers string content, block-array content, thinking, tool_use, and a user entry that is only `tool_result`:

```jsonl
{"type":"user","message":{"role":"user","content":"Refactor the auth middleware"}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"thinking","thinking":"secret reasoning"},{"type":"text","text":"I'll read the middleware first."}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","id":"t1","name":"Read","input":{"file_path":"/x"}}]}}
{"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"t1","content":"file contents here"}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"Done."}]}}
```

`fixtures/noise.jsonl` — every non-message type must vanish:

```jsonl
{"type":"attachment","payload":{"x":1}}
{"type":"last-prompt","text":"hi"}
{"type":"mode","mode":"default"}
{"type":"permission-mode","mode":"acceptEdits"}
{"type":"ai-title","title":"Some title"}
{"type":"file-history-snapshot","files":[]}
{"type":"file-history-delta","files":[]}
{"type":"queue-operation","op":"add"}
{"type":"agent-name","name":"x"}
{"type":"bridge-session","id":"x"}
{"type":"custom-title","title":"x"}
{"type":"pr-link","url":"http://x"}
{"type":"frame-link","url":"http://x"}
{"type":"user","message":{"role":"user","content":"the only real message"}}
```

`fixtures/meta-sidechain.jsonl` — skill injections and subagent turns must vanish:

```jsonl
{"type":"user","isMeta":true,"message":{"role":"user","content":[{"type":"text","text":"Base directory for this skill: /x"}]}}
{"type":"user","isSidechain":true,"message":{"role":"user","content":"subagent task brief"}}
{"type":"assistant","isSidechain":true,"message":{"role":"assistant","content":[{"type":"text","text":"subagent answer"}]}}
{"type":"user","message":{"role":"user","content":"real question"}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"real answer"}]}}
```

`fixtures/system-reminder.jsonl` — wrappers stripped, surrounding text kept:

```jsonl
{"type":"user","message":{"role":"user","content":"before<system-reminder>\nnoise\nmore noise\n</system-reminder>after"}}
{"type":"user","message":{"role":"user","content":"<local-command-stdout>dropped</local-command-stdout>kept"}}
```

`fixtures/from-line.jsonl` — six messages; the test reads from line 4:

```jsonl
{"type":"user","message":{"role":"user","content":"one"}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"two"}]}}
{"type":"user","message":{"role":"user","content":"three"}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"four"}]}}
{"type":"user","message":{"role":"user","content":"five"}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"six"}]}}
```

- [ ] **Step 2: Write the golden files**

`tests/golden/basic.md`:

```markdown
## user
Refactor the auth middleware

## claude
I'll read the middleware first.

## claude
Done.
```

`tests/golden/noise.md`:

```markdown
## user
the only real message
```

`tests/golden/meta-sidechain.md`:

```markdown
## user
real question

## claude
real answer
```

`tests/golden/system-reminder.md`:

```markdown
## user
beforeafter

## user
kept
```

`tests/golden/from-line.md` — note the label for an assistant message is `claude`, not `assistant`:

```markdown
## claude
four

## user
five

## claude
six
```

- [ ] **Step 3: Write the test harness**

`commands/claude-btw/tests/run.sh`, `chmod +x`:

```zsh
#!/bin/zsh
# Automated suite for claude-btw. Run by hand:
#   commands/claude-btw/tests/run.sh
# Never talks to agterm or Claude. Exits non-zero on any failure.
set -u

HERE=${0:A:h}
REPO=${HERE:h:h:h}
DIGEST=$REPO/skills/btw/scripts/btw-digest.sh

typeset -g PASS=0 FAIL=0

ok()  { print -r -- "  ok    $1"; (( PASS++ )) }
bad() { print -r -- "  FAIL  $1"; shift; print -r -- "$@" | sed 's/^/        /'; (( FAIL++ )) }

assert_eq() {  # name expected actual
  if [[ "$2" == "$3" ]]; then ok "$1"
  else bad "$1" "expected: $2" "actual:   $3"; fi
}

assert_golden() {  # name actual
  local golden=$HERE/golden/$1.md
  if [[ ! -f $golden ]]; then bad "$1" "no golden file: $golden"; return; fi
  local d
  d=$(diff -u "$golden" =(print -r -- "$2") 2>&1)
  if [[ -z $d ]]; then ok "$1"; else bad "$1" "$d"; fi
}

digest_case() {  # fixture from-line [ENV=val ...]
  local name=$1 from=$2; shift 2
  assert_golden "$name" "$(env "$@" "$DIGEST" "$HERE/fixtures/$name.jsonl" "$from" 2>/dev/null)"
}

print -r -- "digest:"
digest_case basic 1
digest_case noise 1
digest_case meta-sidechain 1
digest_case system-reminder 1
digest_case from-line 4

print -r -- ""
print -r -- "$PASS passed, $FAIL failed"
(( FAIL == 0 ))
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `commands/claude-btw/tests/run.sh`
Expected: every digest case FAILs — `btw-digest.sh` does not exist yet, so `env` reports "No such file or directory" and the actual output is empty. Exit code 1.

- [ ] **Step 5: Write the digest script**

`skills/btw/scripts/btw-digest.sh`, `chmod +x`:

```zsh
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

# jq's dotall flag is "m", NOT "s". jq inverts the PCRE convention: "s" is
# single-line mode (^ and $ anchor the whole string) and "m" is what makes `.`
# match a newline. With "s" these two gsubs silently never match.
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
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `commands/claude-btw/tests/run.sh`
Expected: `5 passed, 0 failed`, exit 0.

If `system-reminder` fails on whitespace, the cause is `clean` trimming only the outer edges — `before<system-reminder>…</system-reminder>after` becomes `beforeafter` with no space, which is what the golden file says. Do not "fix" this by inserting a space; match the golden.

- [ ] **Step 7: Commit**

```bash
git add skills/btw/scripts/btw-digest.sh commands/claude-btw/tests
git commit -m "btw: transcript digest filter and test harness"
```

---

### Task 2: Digest markers, truncation, and the squeeze

**Files:**
- Modify: `skills/btw/scripts/btw-digest.sh`
- Modify: `commands/claude-btw/tests/run.sh`
- Create: `commands/claude-btw/tests/fixtures/compact.jsonl`, `truncate.jsonl`, `squeeze.jsonl`
- Create: `commands/claude-btw/tests/golden/compact.md`, `truncate.md`, `squeeze.md`

**Interfaces:**
- Consumes: `btw-digest.sh` and the harness from Task 1.
- Produces: the same `btw-digest.sh` contract, now honouring `BTW_SQUEEZE_BYTES` (default `24576`), `BTW_TAIL_MSGS` (default `20`), `BTW_ASSISTANT_CHARS` (default `1200`), `BTW_COLLAPSE_CHARS` (default `160`), and reporting `mode=squeezed` with a non-zero `collapsed=` count.

- [ ] **Step 1: Write the fixtures**

`tests/fixtures/compact.jsonl` — the boundary becomes one marker and the summary entry vanishes:

```jsonl
{"type":"user","message":{"role":"user","content":"before compaction"}}
{"type":"system","subtype":"compact_boundary","content":"…"}
{"type":"user","isCompactSummary":true,"message":{"role":"user","content":"a very long compact summary that must not appear"}}
{"type":"user","message":{"role":"user","content":"after compaction"}}
```

`tests/fixtures/truncate.jsonl` — user text verbatim, assistant text truncated. The test runs with `BTW_ASSISTANT_CHARS=20`:

```jsonl
{"type":"user","message":{"role":"user","content":"this user message is definitely longer than twenty characters and must survive whole"}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"0123456789abcdefghijKLMNO"}]}}
```

`tests/fixtures/squeeze.jsonl` — six messages. The test runs with `BTW_SQUEEZE_BYTES=1 BTW_TAIL_MSGS=2 BTW_COLLAPSE_CHARS=20`, which forces the squeeze without needing a 24 KB fixture:

```jsonl
{"type":"user","message":{"role":"user","content":"first question. with a second sentence that gets cut"}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"first answer\nsecond line dropped"}]}}
{"type":"user","message":{"role":"user","content":"second question"}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"second answer"}]}}
{"type":"user","message":{"role":"user","content":"third question"}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"third answer"}]}}
```

- [ ] **Step 2: Write the golden files**

`tests/golden/compact.md`:

```markdown
## user
before compaction

--- base compacted here ---

## user
after compaction
```

`tests/golden/truncate.md`:

```markdown
## user
this user message is definitely longer than twenty characters and must survive whole

## claude
0123456789abcdefghij…[+5 chars]
```

`tests/golden/squeeze.md`:

```markdown
## earlier (collapsed)

- **user** first question.
- **claude** first answer
- **user** second question
- **claude** second answer

## user
third question

## claude
third answer
```

- [ ] **Step 3: Add the cases to the harness**

In `tests/run.sh`, after `digest_case from-line 4`:

```zsh
digest_case compact 1
digest_case truncate 1 BTW_ASSISTANT_CHARS=20
digest_case squeeze 1 BTW_SQUEEZE_BYTES=1 BTW_TAIL_MSGS=2 BTW_COLLAPSE_CHARS=20
```

And add a report-line case, which needs stderr rather than stdout. `bytes=` is dropped before comparison — it is a `wc -c` of the rendered body, so asserting it would just restate the golden files while making every wording tweak a two-file edit:

```zsh
report_case() {  # name fixture from-line expected-report-without-bytes [ENV=val ...]
  local name=$1 fixture=$2 from=$3 expected=$4; shift 4
  local actual
  actual=$(env "$@" "$DIGEST" "$HERE/fixtures/$fixture.jsonl" "$from" 2>&1 >/dev/null)
  actual=${actual//bytes=[0-9]## /}      # drop the byte count
  assert_eq "$name" "$expected" "$actual"
}

print -r -- ""
print -r -- "report:"
report_case report-verbatim basic 1 \
  "messages=3 markers=0 mode=verbatim collapsed=0"
report_case report-squeezed squeeze 1 \
  "messages=6 markers=0 mode=squeezed collapsed=4" \
  BTW_SQUEEZE_BYTES=1 BTW_TAIL_MSGS=2 BTW_COLLAPSE_CHARS=20
report_case report-empty noise 15 \
  "messages=0 markers=0 mode=verbatim collapsed=0"
```

`${actual//bytes=[0-9]## /}` needs zsh extended globbing; add `setopt extended_glob` near the top of `run.sh`.

- [ ] **Step 4: Run the tests to verify they fail**

Run: `commands/claude-btw/tests/run.sh`
Expected: `compact` passes already (the marker logic is in the Task 1 normalizer), `truncate` passes already, `squeeze` FAILs (no squeeze implemented — output is the verbatim rendering), `report-squeezed` FAILs (`mode=verbatim`, `collapsed=0`).

- [ ] **Step 5: Implement the squeeze**

In `btw-digest.sh`, add the knobs next to `ACHARS`:

```zsh
SQUEEZE=${BTW_SQUEEZE_BYTES:-24576}
TAIL=${BTW_TAIL_MSGS:-20}
CCHARS=${BTW_COLLAPSE_CHARS:-160}
```

Replace the single `RENDER` program with one that takes a `$mode`:

```zsh
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
```

The `total > TAIL` guard matters: without it, a slice shorter than the tail window would report `mode=squeezed collapsed=0` while emitting identical output.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `commands/claude-btw/tests/run.sh`
Expected: `11 passed, 0 failed`, exit 0.

- [ ] **Step 7: Commit**

```bash
git add skills/btw/scripts/btw-digest.sh commands/claude-btw/tests
git commit -m "btw: digest truncation, compaction markers, and size squeeze"
```

---

### Task 3: Shared library and refusal verdicts

The verdicts are what stop `btw-back` typing into the wrong session. They are pure functions of state on disk plus `agtermctl` output, so a fake `agtermctl` makes them testable.

**Files:**
- Create: `skills/btw/scripts/lib.sh`
- Create: `commands/claude-btw/tests/fake-agtermctl.sh`
- Modify: `commands/claude-btw/tests/run.sh`

**Interfaces:**
- Consumes: the harness from Task 1.
- Produces, all sourced via `source "${0:A:h}/lib.sh"`:
  - Globals `AGTERM_TABS_DIR`, `BTW_ROOT`, `AGTERMCTL`, `CLAUDE_PROJECTS`.
  - `btw_die <msg…>` — print `btw: <msg>` to stderr, exit 1.
  - `btw_need <cmd…>` — die unless every command exists.
  - `btw_transcript <claude-session-id>` — print the transcript path, or nothing.
  - `btw_registry <agterm-sid> <pane>` — print the registry JSON, or nothing.
  - `btw_meta` — print the current tab's fork meta JSON, or die.
  - `btw_verdict <agterm-sid> <pane>` — print exactly one of `ok`, `moved`, `ambiguous`, `gone`.
  - `btw_pane_for <agterm-sid>` — print the single registered pane for a session; only meaningful after `moved`.
  - `btw_input_empty <agterm-sid> <pane>` — exit 0 if the base prompt box is empty, 1 otherwise (including when it cannot tell).

- [ ] **Step 1: Write the fake agtermctl**

`commands/claude-btw/tests/fake-agtermctl.sh`, `chmod +x`. It answers only the two read commands the verdicts need, from environment variables the test sets:

```zsh
#!/bin/zsh
# Canned agtermctl for verdict tests. Answers `tree --json` from $FAKE_TREE_IDS
# (space-separated session ids) and `session text` from $FAKE_PANE_TEXT.
set -u

case "${1:-}:${2:-}" in
  tree:*)
    ids=(${=FAKE_TREE_IDS:-})
    if (( $#ids )); then
      jq -n --argjson s "$(printf '%s\n' "${ids[@]}" | jq -R . | jq -s .)" \
            '{result: {tree: {workspaces: [{sessions: ($s | map({id: .}))}]}}}'
    else
      jq -n '{result: {tree: {workspaces: [{sessions: []}]}}}'
    fi
    ;;
  session:text)
    jq -n --arg t "${FAKE_PANE_TEXT:-}" '{result: {text: $t}}'
    ;;
  *)
    print -r -- '{"result":"ok"}'
    ;;
esac
```

- [ ] **Step 2: Write the failing verdict tests**

Append to `tests/run.sh`:

```zsh
# --- verdicts -------------------------------------------------------------
LIB=$REPO/skills/btw/scripts/lib.sh

verdict_case() {  # name tree-ids registry-keys pane expected
  local name=$1 ids=$2 keys=$3 pane=$4 expected=$5
  local root; root=$(mktemp -d)
  mkdir -p "$root/tabs"
  local k
  for k in ${=keys}; do
    jq -n --arg c "cl-$k" '{claude_session_id: $c}' > "$root/tabs/$k"
  done
  local actual
  actual=$(
    AGTERM_TABS_DIR=$root/tabs BTW_ROOT=$root/btw \
    AGTERMCTL=$HERE/fake-agtermctl.sh FAKE_TREE_IDS=$ids \
    zsh -c "source '$LIB'; btw_verdict 'sid1' '$pane'"
  )
  rm -rf "$root"
  assert_eq "$name" "$expected" "$actual"
}

print -r -- ""
print -r -- "verdicts:"
verdict_case verdict-ok        "sid1 sid2" "sid1.left"            left ok
verdict_case verdict-moved     "sid1 sid2" "sid1.right"           left moved
verdict_case verdict-ambiguous "sid1 sid2" "sid1.right sid1.scratch" left ambiguous
verdict_case verdict-gone-tab  "sid2"      "sid1.left"            left gone
verdict_case verdict-gone-noclaude "sid1 sid2" ""                 left gone

input_case() {  # name pane-text expected-exit
  local name=$1 text=$2 expected=$3
  local actual
  AGTERMCTL=$HERE/fake-agtermctl.sh FAKE_PANE_TEXT=$text \
    zsh -c "source '$LIB'; btw_input_empty 'sid1' 'left'"
  actual=$?
  assert_eq "$name" "$expected" "$actual"
}

print -r -- ""
print -r -- "input box:"
input_case input-empty     $'│ > │\n' 0
input_case input-typed     $'│ > half typed │\n' 1
input_case input-noprompt  $'nothing here\n' 1
```

`verdict-gone-noclaude` covers a case the spec's table leaves implicit: the tab is alive but no Claude is registered in any of its panes. `gone` is the right answer — there is nothing to type into.

`input-noprompt` asserts the fail-closed rule: when the check cannot find a prompt, it reports "not empty" rather than guessing.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `commands/claude-btw/tests/run.sh`
Expected: all eight verdict/input cases FAIL — `lib.sh` does not exist, so `source` errors and the actual value is empty (or `1` from the failed source for the input cases). Digest cases still pass.

- [ ] **Step 4: Write the library**

`skills/btw/scripts/lib.sh` (sourced, not executed — no shebang needed, but keep one for editors):

```zsh
#!/bin/zsh
# Shared helpers for claude-btw. Sourced by btw-sync.sh, btw-back.sh and
# claude-btw.sh. Every root is overridable so the test suite can redirect it.

AGTERM_TABS_DIR=${AGTERM_TABS_DIR:-$HOME/.claude/agterm/tabs}
BTW_ROOT=${BTW_ROOT:-$HOME/.claude/btw}
AGTERMCTL=${AGTERMCTL:-/opt/homebrew/bin/agtermctl}
CLAUDE_PROJECTS=${CLAUDE_PROJECTS:-$HOME/.claude/projects}

btw_die() { print -ru2 -- "btw: $*"; exit 1 }

btw_need() {
  local c
  for c in "$@"; do
    command -v "$c" >/dev/null 2>&1 || btw_die "missing dependency: $c"
  done
}

# Session ids are UUIDs and globally unique, so never derive the project slug.
btw_transcript() {
  local hits=( $CLAUDE_PROJECTS/*/$1.jsonl(N) )
  (( $#hits )) && print -r -- "$hits[1]"
}

btw_registry() {
  local f=$AGTERM_TABS_DIR/$1.$2
  [[ -f $f ]] && cat -- "$f"
}

btw_meta() {
  local f=$BTW_ROOT/forks/${AGTERM_SESSION_ID:-none}/meta.json
  [[ -f $f ]] || btw_die "this tab is not a btw fork"
  cat -- "$f"
}

btw_pane_for() {
  local hits=( $AGTERM_TABS_DIR/$1.*(N) )
  (( $#hits == 1 )) && print -r -- "${${hits[1]:t}##*.}"
}

btw_verdict() {  # <agterm-sid> <pane> -> ok|moved|ambiguous|gone
  local sid=$1 pane=$2 tree
  tree=$("$AGTERMCTL" tree --json 2>/dev/null) || { print -r -- gone; return }
  print -r -- "$tree" | jq -e --arg s "$sid" '
    [ .result.tree.workspaces[].sessions[]
      | select((.id | ascii_downcase) == ($s | ascii_downcase)) ] | length > 0
  ' >/dev/null 2>&1 || { print -r -- gone; return }

  if [[ -f $AGTERM_TABS_DIR/$sid.$pane ]]; then print -r -- ok; return; fi

  local hits=( $AGTERM_TABS_DIR/$sid.*(N) )
  case $#hits in
    1) print -r -- moved ;;
    0) print -r -- gone ;;
    *) print -r -- ambiguous ;;
  esac
}

# Fail closed: anything we cannot read as an empty prompt counts as occupied.
btw_input_empty() {  # <agterm-sid> <pane>
  local txt line
  txt=$("$AGTERMCTL" session text --lines 6 --target "$1" --pane "$2" --json 2>/dev/null \
        | jq -r '.result.text // ""' 2>/dev/null) || return 1
  line=$(print -r -- "$txt" | grep -- '>' | tail -1)
  [[ -n $line ]] || return 1
  line=${line//[│┃|]/}
  line=${line#*>}
  line=${line//[[:space:]]/}
  [[ -z $line ]]
}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `commands/claude-btw/tests/run.sh`
Expected: `19 passed, 0 failed`, exit 0.

- [ ] **Step 6: Commit**

```bash
git add skills/btw/scripts/lib.sh commands/claude-btw/tests
git commit -m "btw: shared library and refusal verdicts"
```

---

### Task 4: The tab registry hook

**Files:**
- Create: `hooks/agterm-tabs/register-tab.sh`
- Create: `hooks/agterm-tabs/settings-snippet.json`
- Create: `hooks/agterm-tabs/README.md`

**Interfaces:**
- Consumes: `BTW_ROOT` layout from Task 5 (only to test for `forks/<sid>/meta.json`); nothing else.
- Produces: registry files at `$AGTERM_TABS_DIR/<agterm-sid>.<pane>` containing `{claude_session_id, agterm_session_id, pane, pane_id, cwd, updated_at}`. Read by `btw_registry` (Task 3) and `claude-btw.sh` (Task 5).

- [ ] **Step 1: Write the hook**

`hooks/agterm-tabs/register-tab.sh`, `chmod +x`:

```zsh
#!/bin/zsh
# Claude SessionStart / SessionEnd hook: maintain the agterm tab registry.
#
#   register-tab.sh start   < hook-json-on-stdin
#   register-tab.sh end     < hook-json-on-stdin
#
# Writes $AGTERM_TABS_DIR/<agterm-session-id>.<pane>, a generic map of which
# Claude session runs in which agterm tab and pane. Not btw-specific; btw is
# its first consumer.
#
# Outside agterm this exits immediately and costs nothing.
set -u

[[ -n ${AGTERM_SESSION_ID:-} ]] || exit 0
command -v jq >/dev/null 2>&1 || exit 0

tabs_dir=${AGTERM_TABS_DIR:-$HOME/.claude/agterm/tabs}
pane=${AGTERM_PANE:-left}
file=$tabs_dir/$AGTERM_SESSION_ID.$pane

payload=$(cat)
sid=$(print -r -- "$payload" | jq -r '.session_id // empty' 2>/dev/null)

if [[ ${1:-start} == end ]]; then
  # Only delete our own registration. On /clear the ending and starting hooks
  # can interleave; deleting unconditionally would drop the fresh entry.
  cur=$(jq -r '.claude_session_id // empty' -- "$file" 2>/dev/null)
  [[ -n $sid && $sid == $cur ]] && rm -f -- "$file"
  exit 0
fi

# Never write a partial record.
[[ -n $sid ]] || exit 0

cwd=$(print -r -- "$payload" | jq -r '.cwd // empty' 2>/dev/null)
[[ -n $cwd ]] || cwd=$PWD

mkdir -p -- "$tabs_dir" || exit 0
jq -n \
  --arg cs "$sid" --arg as "$AGTERM_SESSION_ID" --arg p "$pane" \
  --arg pid "${AGTERM_PANE_ID:-}" --arg cwd "$cwd" \
  --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  '{claude_session_id: $cs, agterm_session_id: $as, pane: $p,
    pane_id: $pid, cwd: $cwd, updated_at: $ts}' > "$file"

# A fork tab must restore to ITSELF. Without this pin, agterm's auto-capture
# restores the literal `claude --resume <base> --fork-session` line and
# re-forks the base on every launch. Rewritten every start because a restored
# fork gets a new session id each time. Inert unless the user has agterm's
# "Restore running commands on restart" setting on.
btw_root=${BTW_ROOT:-$HOME/.claude/btw}
if [[ -f $btw_root/forks/$AGTERM_SESSION_ID/meta.json ]]; then
  agtermctl=${AGTERMCTL:-/opt/homebrew/bin/agtermctl}
  if [[ -x $agtermctl ]]; then
    if [[ -n ${AGTERM_PANE_ID:-} ]]; then
      "$agtermctl" session restore "claude --resume $sid" \
        --target "$AGTERM_SESSION_ID" --pane-id "$AGTERM_PANE_ID" >/dev/null 2>&1
    else
      "$agtermctl" session restore "claude --resume $sid" \
        --target "$AGTERM_SESSION_ID" >/dev/null 2>&1
    fi
  fi
fi
exit 0
```

- [ ] **Step 2: Verify it writes a record**

Run:

```bash
AGTERM_SESSION_ID=testsid AGTERM_PANE=left AGTERM_PANE_ID=tok1 \
AGTERM_TABS_DIR=/tmp/btw-test/tabs \
  hooks/agterm-tabs/register-tab.sh start <<<'{"session_id":"abc-123","cwd":"/tmp/proj"}'
cat /tmp/btw-test/tabs/testsid.left
```

Expected: JSON with `"claude_session_id": "abc-123"`, `"pane": "left"`, `"cwd": "/tmp/proj"`.

- [ ] **Step 3: Verify the guards**

Run:

```bash
# no agterm -> no file, exit 0.
# `env -u` is REQUIRED, not optional tidiness: you will be running this from a
# shell inside agterm, which exports AGTERM_SESSION_ID and AGTERM_PANE. Without
# unsetting them the hook sees a live agterm session, writes a real record, and
# the test silently passes while verifying nothing.
env -u AGTERM_SESSION_ID -u AGTERM_PANE \
  AGTERM_TABS_DIR=/tmp/btw-test/tabs hooks/agterm-tabs/register-tab.sh start <<<'{"session_id":"x"}'; echo "exit=$?"
ls /tmp/btw-test/tabs

# empty session_id -> record unchanged
AGTERM_SESSION_ID=testsid AGTERM_PANE=left AGTERM_TABS_DIR=/tmp/btw-test/tabs \
  hooks/agterm-tabs/register-tab.sh start <<<'{}'
jq -r .claude_session_id /tmp/btw-test/tabs/testsid.left

# end with a DIFFERENT id -> file survives
AGTERM_SESSION_ID=testsid AGTERM_PANE=left AGTERM_TABS_DIR=/tmp/btw-test/tabs \
  hooks/agterm-tabs/register-tab.sh end <<<'{"session_id":"other"}'
ls /tmp/btw-test/tabs/testsid.left

# end with the MATCHING id -> file removed
AGTERM_SESSION_ID=testsid AGTERM_PANE=left AGTERM_TABS_DIR=/tmp/btw-test/tabs \
  hooks/agterm-tabs/register-tab.sh end <<<'{"session_id":"abc-123"}'
ls /tmp/btw-test/tabs/testsid.left
```

Expected: `exit=0` and no directory for the first; `abc-123` for the second; the file listed for the third; `No such file or directory` for the fourth. Then `rm -rf /tmp/btw-test`.

- [ ] **Step 4: Write the settings snippet**

`hooks/agterm-tabs/settings-snippet.json` — the entries to merge into `~/.claude/settings.json`. Replace `<HOME>` with the real path; Claude Code does not expand `~` or `$HOME` in hook commands.

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "<HOME>/.claude/hooks/agterm-tabs/register-tab.sh start",
            "timeout": 5
          }
        ]
      }
    ],
    "SessionEnd": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "<HOME>/.claude/hooks/agterm-tabs/register-tab.sh end",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

- [ ] **Step 5: Write the README**

`hooks/agterm-tabs/README.md` documenting: what the registry is for, the exact file path and JSON shape, that `pane` is `left`/`right`/`scratch` (matching both the shell's `$AGTERM_PANE` and a custom command's `$AGT_PANE`), that `pane_id` is recorded but is *not* the key because it is not visible to custom commands, the install steps (copy to `~/.claude/hooks/agterm-tabs/`, merge the snippet, substitute `<HOME>`), and that entries are removed on SessionEnd but consumers should still tolerate staleness.

- [ ] **Step 6: Commit**

```bash
git add hooks/agterm-tabs
git commit -m "hooks: agterm tab registry (SessionStart/SessionEnd)"
```

---

### Task 5: The fork hotkey

**Files:**
- Create: `commands/claude-btw/claude-btw.sh`
- Create: `commands/claude-btw/keymap.conf`

**Interfaces:**
- Consumes: `lib.sh` (Task 3) for `btw_transcript` and the roots; registry files (Task 4).
- Produces: `$BTW_ROOT/forks/<new-agterm-sid>/meta.json` with keys `base_claude_id`, `base_agterm_sid`, `base_pane`, `base_cwd`, `transcript`, `watermark`, `forked_at`, `sync_count`, `synced_at`. Read by `btw-sync.sh` (Task 6) and `btw-back.sh` (Task 7).

- [ ] **Step 1: Write the script**

`commands/claude-btw/claude-btw.sh`, `chmod +x`:

```zsh
#!/bin/zsh
# Fork the Claude session running in this pane into a new tab beside it.
# Bound to a chord in keymap.conf; see commands/claude-btw/design-notes.md and
# docs/superpowers/specs/2026-08-03-claude-btw-design.md.
#
#   claude-btw.sh [--dry-run] <AGT_SESSION_ID> <AGT_PANE> <AGT_SESSION_PWD> <AGT_SESSION_NAME>
set -u

source "${0:A:h}/../../skills/btw/scripts/lib.sh"

dry=0
if [[ ${1:-} == --dry-run ]]; then dry=1; shift; fi

sid=${1:?missing session id}
pane=${2:-left}
base_pwd=${3:-$HOME}
base_name=${4:-session}

# Under --dry-run this prints instead of posting: a desktop notification is a
# real side effect, and "print what you would do" has to mean all of it.
notify() {
  if (( dry )); then
    print -r -- "notify: $1"
  else
    [[ -x $AGTERMCTL ]] && "$AGTERMCTL" notify "$1" --title "claude-btw" >/dev/null 2>&1
  fi
}

btw_need jq
[[ -x $AGTERMCTL ]] || { notify "agtermctl not found"; btw_die "agtermctl not found: $AGTERMCTL" }

# 1. Prune registry entries whose agterm session no longer exists.
#    Skipped under --dry-run: deleting files is a side effect, and --dry-run
#    means print what you would do instead of doing it. Skipping costs no
#    fidelity — prune only removes entries for DEAD agterm sessions, while
#    resolution below looks up the current pane's sid, which is live by
#    definition, so the two never interact.
tree=$( (( dry )) || "$AGTERMCTL" tree --json 2>/dev/null )
if [[ -n $tree ]]; then
  live=( ${(f)"$(print -r -- "$tree" | jq -r '.result.tree.workspaces[].sessions[].id | ascii_downcase')"} )
  for f in $AGTERM_TABS_DIR/*(N); do
    base=${${f:t}%.*}
    (( ${live[(Ie)${base:l}]} )) || rm -f -- "$f"
  done
fi

# 2. Resolve the Claude running in this pane.
reg=$(btw_registry "$sid" "$pane")
if [[ -z $reg ]]; then
  notify "no Claude registered in this pane"
  exit 0
fi
base_id=$(print -r -- "$reg"  | jq -r '.claude_session_id // empty')
base_cwd=$(print -r -- "$reg" | jq -r '.cwd // empty')
[[ -n $base_cwd ]] || base_cwd=$base_pwd
[[ -n $base_id ]] || { notify "registry entry has no session id"; exit 1 }

# 3. Locate the transcript. A tab that opens onto a --resume error is worse
#    than no tab, so refuse before spawning.
transcript=$(btw_transcript "$base_id")
if [[ -z $transcript ]]; then
  notify "no transcript for $base_id"
  btw_die "no transcript found for $base_id"
fi

# 4. Watermark.
watermark=$(wc -l < "$transcript" | tr -d ' ')

# 5. Spawn. --command is argv-style with no shell and without Homebrew on
#    PATH, so wrap it in a login shell.
cmd="zsh -lc 'claude --resume $base_id --fork-session'"
if (( dry )); then
  print -r -- "$AGTERMCTL session new --json --cwd $base_cwd --after $sid --name 'btw: $base_name' --command \"$cmd\""
  print -r -- "watermark=$watermark transcript=$transcript base_id=$base_id"
  exit 0
fi

new=$("$AGTERMCTL" session new --json \
        --cwd "$base_cwd" --after "$sid" \
        --name "btw: $base_name" \
        --command "$cmd" 2>/dev/null | jq -r '.result.id // empty')
[[ -n $new ]] || { notify "could not create the fork tab"; btw_die "session new returned no id" }

# 6. Record fork metadata, keyed by the NEW tab's agterm session id.
dir=$BTW_ROOT/forks/$new
mkdir -p -- "$dir"
jq -n \
  --arg bc "$base_id" --arg ba "$sid" --arg bp "$pane" --arg bcwd "$base_cwd" \
  --arg tr "$transcript" --argjson wm "$watermark" \
  --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  '{base_claude_id: $bc, base_agterm_sid: $ba, base_pane: $bp, base_cwd: $bcwd,
    transcript: $tr, watermark: $wm, forked_at: $ts, sync_count: 0, synced_at: null}' \
  > "$dir/meta.json"
```

- [ ] **Step 2: Verify the dry run**

With a real Claude session registered in the current pane, run:

```bash
commands/claude-btw/claude-btw.sh --dry-run "$AGTERM_SESSION_ID" "${AGTERM_PANE:-left}" "$PWD" "test"
```

Expected: one `agtermctl session new --json …` line naming your cwd and a `zsh -lc 'claude --resume <uuid> --fork-session'` command, plus a `watermark=` line with a plausible line count. No tab opens.

If it prints "no Claude registered in this pane", the Task 4 hook is not installed yet — install it, start a fresh Claude session, and retry.

- [ ] **Step 3: Verify the refusals**

```bash
# unknown pane -> notify, exit 0, nothing spawned
commands/claude-btw/claude-btw.sh --dry-run "$AGTERM_SESSION_ID" scratch "$PWD" t; echo "exit=$?"
```

Expected: `exit=0` and no `session new` line.

- [ ] **Step 4: Write the keymap**

`commands/claude-btw/keymap.conf`:

```
# claude-btw — fork the Claude session in this pane into a new tab beside it.
# The script is expected at ~/.config/agterm-projects/claude-btw.sh — adjust the
# path below if you install it elsewhere.
#
# A keybinding rather than a slash command on purpose: it must work while the
# base Claude is mid-turn, when you cannot type into it. A single chord rather
# than a leader for the same reason — it should be a reflex.
command "Fork session (btw)"  cmd+shift+a  $HOME/.config/agterm-projects/claude-btw.sh "$AGT_SESSION_ID" "$AGT_PANE" "$AGT_SESSION_PWD" "$AGT_SESSION_NAME"
```

`cmd+shift+a` was chosen by the user after `cmd+shift+b` (the original proposal) turned out to be taken by their own "Toggle side panel" command at `~/.config/agterm/keymap.conf:229`. Verified free at the time of writing, along with `c g h i l p q s t u v x y z`.

- [ ] **Step 5: Commit**

```bash
git add commands/claude-btw/claude-btw.sh commands/claude-btw/keymap.conf
git commit -m "btw: fork hotkey"
```

---

### Task 6: btw-sync

**Files:**
- Create: `skills/btw/scripts/btw-sync.sh`

**Interfaces:**
- Consumes: `lib.sh` (Task 3), `btw-digest.sh` (Tasks 1–2), fork meta (Task 5), registry (Task 4).
- Produces: `$BTW_ROOT/forks/<sid>/sync-NNN.full.md` always, `sync-NNN.md` when squeezed; a report on stdout; an updated `watermark`, `sync_count`, `synced_at`, and — after a base reset — `transcript` and `base_claude_id` in `meta.json`. Read by `SKILL.md` (Task 8).

- [ ] **Step 1: Write the script**

`skills/btw/scripts/btw-sync.sh`, `chmod +x`:

```zsh
#!/bin/zsh
# Pull in what the base tab did since the fork (or since the last sync).
#
#   btw-sync.sh
#
# Writes a digest file and prints a short report. NEVER prints the payload:
# an oversize sync must not flood the fork's context by accident.
set -u

source "${0:A:h}/lib.sh"
DIGEST=${0:A:h}/btw-digest.sh

btw_need jq

meta=$(btw_meta)
dir=$BTW_ROOT/forks/$AGTERM_SESSION_ID

base_id=$(print -r -- "$meta"   | jq -r '.base_claude_id')
base_sid=$(print -r -- "$meta"  | jq -r '.base_agterm_sid')
base_pane=$(print -r -- "$meta" | jq -r '.base_pane')
transcript=$(print -r -- "$meta" | jq -r '.transcript')
watermark=$(print -r -- "$meta" | jq -r '.watermark')
count=$(print -r -- "$meta"     | jq -r '.sync_count')

# Which Claude is in the base pane NOW? The registry is the source of truth;
# meta is only what we remembered at fork time.
reg=$(btw_registry "$base_sid" "$base_pane")
now_id=$(print -r -- "$reg" | jq -r '.claude_session_id // empty' 2>/dev/null)

base_state=alive
body=""
if [[ -z $now_id ]]; then
  # Tab or Claude gone. The transcript is still on disk, so degrade.
  base_state=gone
  body=$($DIGEST "$transcript" "$watermark" 2>/tmp/btw-report.$$)
  report=$(< /tmp/btw-report.$$); rm -f /tmp/btw-report.$$
  new_transcript=$transcript
  new_id=$base_id
elif [[ $now_id == $base_id ]]; then
  body=$($DIGEST "$transcript" "$watermark" 2>/tmp/btw-report.$$)
  report=$(< /tmp/btw-report.$$); rm -f /tmp/btw-report.$$
  new_transcript=$transcript
  new_id=$base_id
else
  # The base was /clear'ed or restarted: chain the old tail onto the new head.
  base_state=reset
  new_transcript=$(btw_transcript "$now_id")
  [[ -n $new_transcript ]] || btw_die "base moved to $now_id but its transcript is missing"
  old=$($DIGEST "$transcript" "$watermark" 2>/dev/null)
  new=$($DIGEST "$new_transcript" 1 2>/tmp/btw-report.$$)
  report=$(< /tmp/btw-report.$$); rm -f /tmp/btw-report.$$
  body="$old

--- base session was reset here (/clear or restart) ---

$new"
  new_id=$now_id
fi

mode=${${(M)${=report}:#mode=*}#mode=}
collapsed=${${(M)${=report}:#collapsed=*}#collapsed=}
messages=${${(M)${=report}:#messages=*}#messages=}

if [[ -z ${body//[[:space:]]/} ]]; then
  synced=$(print -r -- "$meta" | jq -r '.synced_at // .forked_at')
  print -r -- "btw-sync: nothing new since $synced"
  exit 0
fi

n=$(printf '%03d' $(( count + 1 )))
mkdir -p -- "$dir"

header="# Base session activity — sync $n
base: $new_id (agterm tab $base_sid, pane $base_pane)
from line: $watermark
captured: $(date -u +%Y-%m-%dT%H:%M:%SZ)
"

# The full rendering is always written, so --summarize has something complete
# to read even when the squeezed version is what landed in context.
full=$dir/sync-$n.full.md
print -r -- "$header" > "$full"
BTW_SQUEEZE_BYTES=99999999 $DIGEST "$new_transcript" \
  "$([[ $base_state == reset ]] && print 1 || print $watermark)" 2>/dev/null >> "$full"

digest=$dir/sync-$n.md
if [[ $mode == squeezed ]]; then
  { print -r -- "$header"; print -r -- "$body" } > "$digest"
else
  cp -- "$full" "$digest"
fi

new_watermark=$(wc -l < "$new_transcript" | tr -d ' ')
print -r -- "$meta" | jq \
  --argjson wm "$new_watermark" --argjson c "$(( count + 1 ))" \
  --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --arg tr "$new_transcript" --arg id "$new_id" \
  '.watermark = $wm | .sync_count = $c | .synced_at = $ts
   | .transcript = $tr | .base_claude_id = $id' > "$dir/meta.json"

size=$(wc -c < "$digest" | tr -d ' ')
print -r -- "btw-sync: $messages new messages from base"
if [[ $mode == squeezed ]]; then
  print -r -- "mode: squeezed ($collapsed older messages collapsed; full text in $(basename $full))"
else
  print -r -- "mode: verbatim"
fi
print -r -- "digest: $digest ($size bytes)"
print -r -- "base: $base_state"
print -r -- "next: read the digest file"
```

- [ ] **Step 2: Verify against a real fork**

Fork a session with the hotkey, type something in the base tab and let Claude answer, then in the fork run:

```bash
skills/btw/scripts/btw-sync.sh
```

Expected: a five-line report, `mode: verbatim`, `base: alive`. Open the named digest file and confirm it contains the base's turns and **no** tool calls, thinking, or `<system-reminder>` blocks.

- [ ] **Step 3: Verify the empty case**

Run `btw-sync.sh` again immediately.
Expected: `btw-sync: nothing new since <timestamp>`, exit 0, no new file in `$BTW_ROOT/forks/<sid>/`.

- [ ] **Step 4: Verify the squeeze end to end**

```bash
BTW_SQUEEZE_BYTES=200 BTW_TAIL_MSGS=2 skills/btw/scripts/btw-sync.sh
```

after generating new base activity. Expected: `mode: squeezed (N older messages collapsed; full text in sync-NNN.full.md)`, and the `.md` file starts with `## earlier (collapsed)` after its header while the `.full.md` file does not.

- [ ] **Step 5: Commit**

```bash
git add skills/btw/scripts/btw-sync.sh
git commit -m "btw: sync base activity into the fork"
```

---

### Task 7: btw-back

**Files:**
- Create: `skills/btw/scripts/btw-back.sh`
- Modify: `commands/claude-btw/tests/run.sh`

**Interfaces:**
- Consumes: `lib.sh` (Task 3), fork meta (Task 5).
- Produces: `btw-back.sh [--dry-run] [--force] --line <text>` with the answer body on **stdin**. Writes `$BTW_ROOT/forks/<sid>/answer-NNN.md`, types one line into the base prompt, raises the base status glyph. Exits non-zero on refusal. Used by `SKILL.md` (Task 8).

- [ ] **Step 1: Write the failing composition tests**

The one-liner composer is a pure function and the place a newline could slip through to `session type`. `btw-back.sh` gets a `--compose` mode that runs the composer and exits, so the tests can exercise it without any state or agterm.

Append to `tests/run.sh`:

```zsh
# --- one-liner composition ------------------------------------------------
BACK=$REPO/skills/btw/scripts/btw-back.sh

compose_case() {  # name line path expected
  local name=$1 line=$2 path=$3 expected=$4
  assert_eq "$name" "$expected" "$("$BACK" --compose "$line" --answer-path "$path")"
}

print -r -- ""
print -r -- "one-liner:"
compose_case compose-plain \
  "Auth uses JWT" "/tmp/a.md" \
  "btw: Auth uses JWT. Full notes: /tmp/a.md"
compose_case compose-newlines \
  $'first\nsecond\n\nthird' "/tmp/a.md" \
  "btw: first · second · third. Full notes: /tmp/a.md"
compose_case compose-trailing-period \
  "Auth uses JWT." "/tmp/a.md" \
  "btw: Auth uses JWT. Full notes: /tmp/a.md"
```

Truncation is asserted by **property**, not by a literal string — the exact cut position is an implementation detail, but "never longer than the cap", "never contains a newline", and "always ends with the path" are the guarantees that matter:

```zsh
long=$(printf 'x%.0s' {1..500})
composed=$("$BACK" --compose "$long" --answer-path "/tmp/a.md")

assert_eq compose-capped-length 400 "${#composed}"
assert_eq compose-capped-lines   1   "$(print -r -- "$composed" | wc -l | tr -d ' ')"
if [[ $composed == *". Full notes: /tmp/a.md" ]]; then ok compose-capped-path
else bad compose-capped-path "does not end with the answer path: $composed"; fi
```

`wc -l` reports `1` for a single line because `print` adds the trailing newline; a composed value containing an embedded newline reports `2` or more.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `commands/claude-btw/tests/run.sh`
Expected: the six `compose-*` cases FAIL (no such script). The 19 earlier cases pass.

- [ ] **Step 3: Write the script**

`skills/btw/scripts/btw-back.sh`, `chmod +x`:

```zsh
#!/bin/zsh
# Send the fork's answer back to the base tab's prompt — unsubmitted.
#
#   btw-back.sh --line "<one sentence>" [--dry-run] [--force]  < answer-body.md
#   btw-back.sh --compose "<text>" --answer-path <path>        # composer only, for tests
#
# Two artifacts: the full answer goes to a file, and ONE line goes into the
# base prompt. `agtermctl session type` turns every newline into Return, so a
# multi-line brief would submit itself line by line — single line is a safety
# constraint, not a style rule.
set -u

source "${0:A:h}/lib.sh"

MAXLINE=${BTW_MAX_LINE:-400}

btw_compose_line() {  # <text> <answer-path>
  local text=$1 path=$2
  # Collapse every newline run; nothing multi-line may reach `session type`.
  text=${text//$'\n'/ · }
  while [[ $text == *' ·  · '* ]]; do text=${text//' ·  · '/' · '}; done
  text=${text##[[:space:]]#}
  text=${text%%[[:space:]]#}
  text=${text%.}

  local prefix="btw: " suffix=". Full notes: $path"
  local room=$(( MAXLINE - ${#prefix} - ${#suffix} ))
  if (( ${#text} > room )); then
    text="${text[1,$(( room - 1 ))]}…"
  fi
  print -r -- "${prefix}${text}${suffix}"
}

if [[ ${1:-} == --compose ]]; then
  btw_compose_line "$2" "$4"
  exit 0
fi

dry=0 force=0 line=""
while (( $# )); do
  case $1 in
    --dry-run) dry=1; shift ;;
    --force)   force=1; shift ;;
    --line)    line=$2; shift 2 ;;
    *) btw_die "unknown argument: $1" ;;
  esac
done
[[ -n $line ]] || btw_die "--line is required"

btw_need jq

meta=$(btw_meta)
dir=$BTW_ROOT/forks/$AGTERM_SESSION_ID
base_sid=$(print -r -- "$meta"  | jq -r '.base_agterm_sid')
base_pane=$(print -r -- "$meta" | jq -r '.base_pane')

# Write the answer file FIRST: every refusal below must leave the work intact.
body=$(cat)
[[ -n ${body//[[:space:]]/} ]] || btw_die "no answer body on stdin"
mkdir -p -- "$dir"
n=1
while [[ -f $dir/answer-$(printf '%03d' $n).md ]]; do (( n++ )); done
answer=$dir/answer-$(printf '%03d' $n).md
print -r -- "$body" > "$answer"

refuse() {
  print -ru2 -- "btw-back: refused ($1)"
  print -ru2 -- "answer kept at: $answer"
  exit 1
}

verdict=$(btw_verdict "$base_sid" "$base_pane")
case $verdict in
  ok) ;;
  moved)
    base_pane=$(btw_pane_for "$base_sid")
    [[ -n $base_pane ]] || refuse ambiguous
    print -r -- "btw-back: base pane moved to '$base_pane'"
    ;;
  *) refuse "$verdict" ;;
esac

# `session type` APPENDS to whatever is already in the box, so injecting into
# half-typed text would mangle it. Fail closed when we cannot tell.
if (( ! force )) && ! btw_input_empty "$base_sid" "$base_pane"; then
  refuse busy-input
fi

out=$(btw_compose_line "$line" "$answer")

if (( dry )); then
  print -r -- "verdict: $verdict"
  print -r -- "$AGTERMCTL session type <line> --target $base_sid --pane $base_pane"
  print -r -- "$out"
  exit 0
fi

"$AGTERMCTL" session type "$out" --target "$base_sid" --pane "$base_pane" >/dev/null \
  || refuse "type failed"

"$AGTERMCTL" session status blocked --blink --auto-reset \
  --target "$base_sid" --pane "$base_pane" >/dev/null 2>&1

print -r -- "btw-back: sent to base tab (glyph raised, not submitted)"
print -r -- "answer: $answer"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `commands/claude-btw/tests/run.sh`
Expected: `25 passed, 0 failed`, exit 0.

If `compose-capped-length` reports something other than `400`, the cause is `${#text}` counting characters while the cap is meant as characters too — that is correct and consistent. Do not switch to byte counting; `·` and `…` are multibyte and the cap exists to keep the prompt line readable, not to fit a buffer.

- [ ] **Step 5: Verify the refusals against real state**

From a fork tab, with the base tab's prompt holding some typed text:

```bash
echo "test answer body" | skills/btw/scripts/btw-back.sh --dry-run --line "test"
```

Expected: `btw-back: refused (busy-input)` and an `answer kept at:` path that exists. Clear the base prompt and retry.
Expected: `verdict: ok`, the `session type` line, and the composed one-liner ending in the answer path.

- [ ] **Step 6: Verify the real send**

```bash
echo "The auth middleware validates JWTs in a Gin handler." \
  | skills/btw/scripts/btw-back.sh --line "Auth validates JWTs in a Gin middleware"
```

Expected: one line appears in the base tab's prompt, **unsubmitted**; the base tab's sidebar glyph lights; the fork stays selected.

- [ ] **Step 7: Commit**

```bash
git add skills/btw/scripts/btw-back.sh commands/claude-btw/tests
git commit -m "btw: send an answer back to the base prompt"
```

---

### Task 8: Skill, slash commands, and documentation

**Files:**
- Create: `skills/btw/SKILL.md`
- Create: `skills/btw/commands/btw-sync.md`, `skills/btw/commands/btw-back.md`
- Modify: `README.md`
- Modify: `commands/claude-btw/design-notes.md`

**Interfaces:**
- Consumes: everything.
- Produces: nothing consumed downstream.

- [ ] **Step 1: Write SKILL.md**

`skills/btw/SKILL.md` with frontmatter:

```markdown
---
name: btw
description: Use when working in a btw fork tab — pull in what the base session did since the fork (btw-sync), or send a short answer back to the base tab's prompt (btw-back). Triggers - btw, btw sync, btw back, catch up on the base session, send this back, what did the main task do, fork sync.
---
```

Body covers, in this order:

1. **What a fork is** — one paragraph: created by `cmd+shift+b` in another tab, holds the base conversation's full history, identified by `$AGTERM_SESSION_ID`, metadata at `$BTW_ROOT/forks/<sid>/meta.json`.
2. **btw-sync** — run `skills/btw/scripts/btw-sync.sh`, read the report, then read the file it names. Do **not** cat the digest into the conversation blindly; the report gives its size first. If `mode: squeezed` and the user asked for a full summary, dispatch a subagent to read the `.full.md` file and return a brief rather than reading it directly — that is what `--summarize` means, and it is why the full rendering is always written.
3. **btw-back** — write the answer body to stdin via a heredoc and pass one sentence as `--line`. The script writes the answer file, checks the verdicts, types one line into the base prompt, and raises the glyph. It does not submit and does not switch tabs. On refusal the answer file is kept — tell the user its path.
4. **Refusal verdicts table** — `gone`, `ambiguous`, `busy-input`, and what the user should do about each (`busy-input` → clear the base prompt, or re-run with `--force`).
5. **What not to do** — do not auto-sync on the first message, do not close the fork, do not switch to the base tab.

Include a worked `btw-back` invocation:

````markdown
```bash
skills/btw/scripts/btw-back.sh --line "Auth validates JWTs in Gin middleware, not per-handler" <<'EOF'
## Where auth happens

`internal/api/middleware.go:41` installs a Gin middleware that validates the
JWT once per request...
EOF
```
````

- [ ] **Step 2: Write the slash commands**

`skills/btw/commands/btw-sync.md`:

```markdown
---
description: Pull in what the base session did since this fork was created
---

Run `skills/btw/scripts/btw-sync.sh` from the repo that holds this skill (or
`~/.claude/skills/btw/scripts/btw-sync.sh` if installed). Read its report, then
read the digest file it names.

If the report says `mode: squeezed` and the user passed `--summarize`, dispatch
a subagent to read the `.full.md` file and return a brief instead of reading the
full text into this conversation.

Then answer the user's side question with that context in hand.

$ARGUMENTS
```

`skills/btw/commands/btw-back.md`:

```markdown
---
description: Send this fork's answer back to the base tab's prompt
---

Compose the answer to the side question as markdown, and a single summarising
sentence. Then run:

```bash
skills/btw/scripts/btw-back.sh --line "<one sentence>" <<'EOF'
<the full answer as markdown>
EOF
```

The script writes the answer to a file, refuses if the base tab is gone,
ambiguous, or has half-typed text in its prompt, and otherwise types one line
into the base prompt without submitting it. Report what it printed. On refusal,
tell the user where the answer file was kept.

$ARGUMENTS
```

- [ ] **Step 3: Update the repo README**

Add a `hooks/` row to the Contents table:

```markdown
| [`hooks/agterm-tabs/`](hooks/agterm-tabs/) | A Claude Code **hook**: records which Claude session runs in which agterm tab and pane, as a small file registry. A generic facility — `claude-btw` is its first consumer. |
```

Add a `skills/btw/` row, and a `### claude-btw` section to the Commands section covering: what it does, the chord, the three moving parts (hook, hotkey, skill), install steps in order (hook first — the hotkey is useless without a registry), and the "Restore running commands on restart" caveat for fork-tab survival.

- [ ] **Step 4: Mark the design notes as implemented**

In `commands/claude-btw/design-notes.md`, change the superseded banner to also point at this plan.

- [ ] **Step 5: Run the full suite one last time**

Run: `commands/claude-btw/tests/run.sh`
Expected: `25 passed, 0 failed`, exit 0.

- [ ] **Step 6: Walk the manual smoke checklist**

From the spec. Record the result of each in the commit message if any deviate:

1. Fork from a live session — new tab has full history, empty prompt
2. Sync after base activity — digest contains the base's turns, no tool noise
3. Send an answer back — one line lands in the base prompt, unsubmitted, glyph lights
4. Restart agterm — the fork tab comes back as itself, the base is not re-forked (**only observable with "Restore running commands on restart" on**; with it off, both tabs come back as plain shells and that is the expected result)
5. Close the base tab, then sync and send back — degrades and refuses respectively
6. `/clear` the base, then sync — reset marker present, new turns included

- [ ] **Step 7: Commit**

```bash
git add skills/btw README.md commands/claude-btw/design-notes.md
git commit -m "btw: skill, slash commands, and documentation"
```

---

## Notes for the implementer

- **The transcript format is Anthropic's and can change without notice.** The golden-file tests are the early-warning system. If they start failing after a Claude Code upgrade, re-survey the entry shapes with:
  ```bash
  jq -r '[.type, (.isSidechain|tostring), (.isMeta|tostring), (.subtype // ""),
          ((.message.content // []) | if type=="string" then "STR" else (map(.type)|join(",")) end)] | @tsv' \
    ~/.claude/projects/*/*.jsonl | sort | uniq -c | sort -rn | head -25
  ```
- **`btw_input_empty` is a heuristic** over rendered terminal text and is the most fragile thing here. It fails closed by design. If it misfires often, the fix is a better prompt-line pattern, not removing the check.
- **Never let `btw-back` reach `session type` with a newline.** The composer collapses them; the `compose-newlines` test guards it. Do not add a code path that bypasses `btw_compose_line`.
- **The 24 KB threshold and the 20-message tail are guesses**, per the spec's Open Risks. They are one-line constants and environment-overridable; tune after living with them.
