# go-ask recap tool

## Overview
- Add a go-ask MCP tool `recap(session_id?, turns?, model?)` that returns a markdown recap of the
  last N user turns of the Claude running in an agterm pane, written by a local Ollama model.
- Same four sections, prompt and digest rules as `commands/recap/recap.py` (what was done, the
  user's decisions, current state incl. unpushed/unmerged, what is left; Russian, ≤ 250 words).
- Lets an agent (or the user through an agent) catch up on another session without opening it.
- Integrates as a fourth tool next to `sessions`, `ask`, `answer`; reuses `ask`'s target resolution.
- Source of the task: `docs/handoff/go-ask-recap-tool.md` (gitignored). Design:
  `~/.local/state/make-task/agterm-experimental-go-ask-recap-mcp-tool-20261107-1101/design.md`.

## Context (from discovery)
- files/components involved:
  - `mcps/go-ask/main.go` — tool registration (`newMCPServer`), `server` struct, `main`
  - `mcps/go-ask/http.go` — `httpHandler` builds one `server` per HTTP client session
  - `mcps/go-ask/agterm.go` — `Pane` (has unexported `paneID`), `resolveTarget`, `agtermctl.panes`
  - `commands/recap/recap.py` — the digest (`build_digest`), `SYSTEM`/`PROMPT`, `num_ctx_for`,
    the Ollama call; the reference implementation to port
  - `mcps/go-ask/docs/api.md`, `mcps/go-ask/docs/usage.md`, `mcps/go-ask/README.md`
- related patterns found:
  - tests use a fake `agtermctl` shell script (`fakeAgtermctlTree` in `http_test.go`, test trees
    `treeJSON` in `agterm_test.go`, `answerTree(...)` in `answer_test.go`) and connect a real MCP
    client over `httptest` (`connect` + `callTool` in the existing tests)
  - `readTurns` (`turns.go`) reads a whole file with `os.ReadFile` + `bytes.Split`, never
    `bufio.Scanner` (transcript lines reach 1 MB; transcripts reach 170 MB on this machine)
  - errors are plain `error`s returned from handlers with full, actionable sentences
  - standard library only besides the MCP go-sdk; Go 1.27
- dependencies identified:
  - Claude Code writes `~/.claude/sessions/<pid>.json` (`pid`, `sessionId`, `updatedAt`, `kind`)
    per live process; transcripts are `~/.claude/projects/*/<sessionId>.jsonl`
  - a pane's shell (and the claude in it) carries `AGTERM_PANE_ID=<pane id>` in its env; `/bin/ps
    -E -o pid=,command= -p <pids>` shows it for the user's own processes
  - Ollama at `127.0.0.1:11434` (`OLLAMA_HOST` overrides), model `gemma4:e4b` installed
  - the daemon runs under launchd with a bare `PATH`: name `/bin/ps` absolutely

## Development Approach
- **testing approach**: Regular (code first, then tests, inside each task)
- complete each task fully before moving to the next
- make small, focused changes
- **CRITICAL: every task MUST include new/updated tests** for code changes in that task
  - tests are not optional - they are a required part of the checklist
  - write unit tests for new functions/methods
  - write unit tests for modified functions/methods
  - add new test cases for new code paths
  - update existing test cases if behavior changes
  - tests cover both success and error scenarios
- **CRITICAL: all tests must pass before starting next task** - no exceptions
- **CRITICAL: update this plan file when scope changes during implementation**
- run tests after each change: `cd mcps/go-ask && go test ./... && go vet ./...` (and `gofmt -l .`
  prints nothing)
- maintain backward compatibility: `sessions`, `ask`, `answer` unchanged

## Testing Strategy
- **unit tests**: required for every task (see Development Approach above)
- no UI, no e2e suite. Nothing in `go test ./...` calls a real model, a real `ps` on real Claude
  processes, real agterm or reads `~/.claude`: every dependency is injected (fake agtermctl, temp
  dirs, fake ps func, `httptest` Ollama)
- one live test behind the `live` build tag (Task 5), run by hand, not by `go test ./...`

## Progress Tracking
- mark completed items with `[x]` immediately when done
- add newly discovered tasks with ➕ prefix
- document issues/blockers with ⚠️ prefix
- update plan if implementation deviates from original scope
- keep plan in sync with actual work done

## Solution Overview
- Everything native in Go inside `mcps/go-ask`, one synchronous tool call (~40–50 s).
- **Pane → transcript natively**: resolve the pane like `ask`; read `~/.claude/sessions/*.json`;
  one `ps -E` call over those pids; keep pids whose env has `AGTERM_PANE_ID=<pane id>` as a whole
  token and does **not** have `CLAUDECODE=1` (that marks a claude started from inside another
  claude: revmux reviewers, `claude -p`, Agent SDK tools, which inherit the pane id); newest
  `updatedAt` wins; its `sessionId` → `~/.claude/projects/*/<sessionId>.jsonl`.
  Pane-precise (a `:right` split resolves to its own claude), needs no go-ask hooks, no
  out-of-repo script.
- **Digest + Ollama ported to Go**; `recap.py` stays as is. A parity test keeps the two digests
  equal on a shared fixture.
- **Synchronous**: the configured client is Claude Code over HTTP (long tool timeout). Codex's 60 s
  default `tool_timeout_sec` is documented with the setting that raises it. No async split.
- Non-streaming Ollama request (`stream:false`) bound to the MCP request context.
- Recap of the caller's own pane is allowed: `recap` types nothing.

## Technical Details
- New files: `recap_digest.go` (digest), `recap_ollama.go` (Ollama client), `recap_locate.go`
  (pane → transcript), `recap.go` (tool handler, in/out types); tests beside each; fixture
  `testdata/recap-transcript.jsonl`.
- `recapIn{SessionID string "session_id,omitempty"; Turns int "turns,omitempty"; Model string
  "model,omitempty"}` with jsonschema descriptions in the style of `askIn`.
- `recapOut{Recap, Target, Name, Model string; Turns int; Transcript string; DurationMs int64
  "duration_ms"; PromptTokens, OutputTokens int "prompt_tokens"/"output_tokens" (omitempty)}`.
- turns: absent/0 → 6; then clamped to 1…50 (negative → 1, like recap.py's `max(turns, 1)`).
- model: `model` arg, else `$RECAP_MODEL`, else `gemma4:e4b`. The launchd plist passes no env,
  so for the daemon the `model` argument is the way to pick another model.
- Ollama host: `$OLLAMA_HOST`, else `http://127.0.0.1:11434`; prefix `http://` when it has no
  scheme; trailing `/` trimmed.
- Request: `POST {host}/api/chat`, body `{"model", "stream": false, "keep_alive": "5m",
  "options": {"num_ctx": numCtxFor(digest), "temperature": 0.2}, "messages": [{system: SYSTEM},
  {user: PROMPT + digest}]}`; the call is bounded by the request ctx plus a 10-minute ceiling.
  Response: `message.content` (trimmed), `prompt_eval_count`, `eval_count`; a JSON `error` field,
  a non-2xx status or an empty content is an error.
- Transcript reading: `os.ReadFile` + `bytes.Split` (as `readTurns`), each line decoded into a
  narrow `transcriptRow{Type, IsMeta, Timestamp, Message{Content json.RawMessage}, Attachment{Type,
  Prompt, Origin{Kind}}}` (not `map[string]any`: a 170 MB transcript as maps is a large spike in a
  long-lived daemon). Lines are decoded from the end backwards until the `turns`-th real user
  prompt is found, then only that tail is returned, so a long transcript is never decoded whole.
  Lines that do not parse are skipped (recap.py would crash; go-ask skips, as `readTurns` does).
- Digest port rules (from `recap.py`, exact): caps `TEXT_CAP=600`, `FINAL_CAP=4000`,
  `USER_CAP=1500`, `MAX_CHARS=60000`, answer tail 400, queued-command subagent line 300, tool
  description 120, command fallback 80; every cap and slice counts **runes** (Python slices code
  points); `cap()` appends `" …"`; truncation prefix `"(начало журнала обрезано)\n"`;
  `numCtxFor`: `need = runes/3 + 2500`, first of 8192/16384/32768/65536 that fits, else 65536.
  `SYSTEM` and `PROMPT` copied byte for byte from `recap.py`.
- `server` gains a `deps recapDeps` field (`ollama` client, `locator`; not named `recap`, which is
  the handler method's name: Go refuses a field and a method with the same name); `main` builds it
  once from the env and passes it to the stdio server and to `httpHandler`/`serveHTTP` (new
  parameter; update the existing call sites in tests).

## What Goes Where
- **Implementation Steps** (`[ ]` checkboxes): code, tests, docs inside this repo
- **Post-Completion** (no checkboxes): rebuilding the user's launchd daemon, client config

## Implementation Steps

### Task 1: Port the recap digest to Go

**Files:**
- Create: `mcps/go-ask/recap_digest.go`
- Create: `mcps/go-ask/recap_digest_test.go`
- Create: `mcps/go-ask/testdata/recap-transcript.jsonl`

- [x] create `recap_digest.go` with `transcriptRow` (Technical Details) and
      `buildDigest(rows []transcriptRow, turns int) string`, a line-for-line port of `build_digest`
      in `commands/recap/recap.py` incl. `is_real_user`, `clean_user`, `text_of`, the `asks` set
      (AskUserQuestion and Bash `\bmt\b.*\bask\b`), queued `attachment` rows (human vs subagent),
      the final-text rule and the newest-60k truncation; rune-based caps and slices
      ⚠️ `attachment.prompt` is a list (text + image blocks) when a queued message carries an
      image (10 such rows in the user's recent transcripts); recap.py slices the list and prints
      its Python repr, base64 included. Go keeps `Prompt` raw and takes its text blocks
- [x] add `readTranscript(path string, turns int) ([]transcriptRow, error)`: `os.ReadFile` +
      `bytes.Split`, decode backwards to the `turns`-th real user prompt, return that tail; bad
      lines skipped; a missing or unreadable file is an error
- [x] add `numCtxFor(digest)` and the `recapSystem`/`recapPrompt` constants copied from recap.py;
      a code comment at each place where Go cannot match Python exactly and the choice made: `\b`
      in the `mt … ask` regex (RE2 ASCII vs Python Unicode), `strings.TrimSpace` vs `str.strip()`
      whitespace set, `str()` of a non-string tool input (Go takes strings only), `None` names
      (the whitespace set is matched exactly: `unicode.IsSpace` plus U+001C–U+001F equals
      Python's `str.isspace` over every code point)
- [x] create the synthetic fixture `testdata/recap-transcript.jsonl` (valid JSON lines only, no
      real user data): ≥ 8 real user turns, a meta row, a `<local-command…>` echo, a
      tool-result-only user row, a `<command-message>`/"Base directory for this skill" prompt, an
      AskUserQuestion with its tool_result, a Bash `mt … ask` call with its result, a queued human
      message and a queued subagent report, tool calls with description/command/file_path/query,
      assistant texts that exceed 600 and 4000 runes, Cyrillic text, timestamps
- [x] write digest tests: turn cut (turns=1, 2, more than available), USER ANSWER lines, caps hit
      at rune boundaries with Cyrillic text, final-text rule (4000 vs 600), cleaned user text,
      skipped meta/echo/tool-result-only rows, truncation prefix with a synthetic >60k digest,
      `numCtxFor` table, no rows → ""
- [x] write `readTranscript` tests: a line over 64 KB is read, a malformed line is skipped, the tail
      starts at the right row (and at 0 when there are fewer turns), missing file → error
- [x] write a parity test: for turns 2, 6 and 20 (above the fixture's turn count), the Go digest of
      the fixture equals the stdout of `python3 ../../commands/recap/recap.py
      testdata/recap-transcript.jsonl --digest --turns N` minus its trailing newline, run with
      `PYTHONUTF8=1`; `t.Skip` when `python3` is not on PATH
      ➕ also parity on a generated transcript past the 60k cut, and `recapSystem`/`recapPrompt`
      compared with recap.py's `SYSTEM`/`PROMPT` (imported by `python3 -c`)
- [x] run tests - must pass before task 2

### Task 2: Ollama chat client

**Files:**
- Create: `mcps/go-ask/recap_ollama.go`
- Create: `mcps/go-ask/recap_ollama_test.go`

- [x] create `ollamaClient{host string; http *http.Client}` with `newOllamaClient(getenv)` (host
      rules above) and `chat(ctx, model, digest) (text string, promptTokens, outputTokens int, err)`
      sending the request body above (`stream:false`), 10-minute ceiling on top of ctx
      ➕ the ceiling is a third field `ceiling time.Duration` (a ctx deadline with a cause, 0 = none),
      not `http.Client.Timeout`, so a ceiling hit gets its own message (`ollama did not answer
      within 10m0s; …`) and is not mistaken for a network timeout; a nil `http` uses the default
      client
- [x] errors: connection failure → `ollama is not reachable at <host> (<err>); start it with:
      ollama serve`; non-2xx → `ollama answered <code>: <body, first 300 chars>`, with `; pull it
      with: ollama pull <model>` when the status is 404 or the body says the model is not found; a
      JSON `error` field → `ollama: <error>`; empty `message.content` → `ollama returned an empty
      answer`; ctx cancellation returned as the ctx error
- [x] write tests with `httptest.Server`: request path, method and decoded body (model, stream
      false, keep_alive, num_ctx, temperature, system and user messages = SYSTEM and PROMPT+digest),
      success with token counts and trimmed content
- [x] write tests for errors: 404 model not found (pull hint), 500 body, `error` field in a 200,
      empty content, unreachable host (a closed listener's address), cancelled ctx, host
      normalization (`127.0.0.1:11434` → `http://…`, trailing slash)
- [x] run tests - must pass before task 3

### Task 3: Pane → Claude transcript locator

**Files:**
- Create: `mcps/go-ask/recap_locate.go`
- Create: `mcps/go-ask/recap_locate_test.go`

- [x] create `claudeLocator{sessionsDir, projectsDir string; ps func(ctx, pids []int) (map[int]string, error)}`
      with `newClaudeLocator()` (dirs under `os.UserHomeDir()/.claude`) and the real runner
      `runPS`: `/bin/ps -E -o pid=,command= -p <comma-separated pids>`, output parsed by
      `parsePS(out []byte) map[int]string` (pids are left-padded with spaces). ps rules measured on
      this Mac: a dead pid among live ones → exit 0; only dead pids → exit 1 with empty stdout and
      stderr, which means "no live pids", not an error; an error only when stderr has text or the
      exit code is other than 0/1; never called with an empty pid list
- [x] `transcriptFor(ctx, paneID) (sessionID, path string, err)`: read `<sessionsDir>/<digits>.json`
      (skip other names, unparsable files and records with an empty `sessionId`); no records →
      `errNoClaude` without calling ps; one ps call over all pids; keep pids whose env has the whole
      token `AGTERM_PANE_ID=<paneID>` and not the whole token `CLAUDECODE=1` (whole token: preceded
      by start or space, followed by space or end); newest `updatedAt` wins; then
      `filepath.Glob(projectsDir/*/<sessionId>.jsonl)`, first hit sorted
- [x] errors the handler maps to messages: no matching live claude (`errNoClaude`), record found but
      no transcript on disk (session id named), empty pane id
- [x] write tests with temp dirs and a fake ps: match on the right pane only (left/right panes of one
      session with different pane ids), whole-token match (`…ID=ABC` vs `…ID=ABCD`,
      `X_AGTERM_PANE_ID`), a parent record plus a newer child record whose env has `CLAUDECODE=1`
      resolves to the parent, dead pid absent from ps output, two live records → newest
      `updatedAt`, empty `sessionId` skipped, non-numeric and broken files ignored, no records (ps
      not called), transcript missing, ps failing
- [x] write `parsePS` table tests on canned output (padded pids, a line without env, blank lines)
      and one test of the real `runPS`: start `sleep 30` with `cmd.Env = []string{"AGTERM_PANE_ID=TEST"}`,
      call `runPS` with its pid plus a dead pid (a finished child's), expect only the live pid with
      the token; and `runPS` with only the dead pid → empty map, no error
      ⚠️ ps -E shows no environment for Apple platform binaries (`/bin/sleep`, `/bin/zsh`), so the
      live process is the test binary itself re-run as a helper (`TestPSHelperProcess`,
      `GO_ASK_PS_HELPER=1`); claude is not a platform binary and shows its env
- [x] run tests - must pass before task 4

### Task 4: recap tool handler and registration

**Files:**
- Create: `mcps/go-ask/recap.go`
- Create: `mcps/go-ask/recap_test.go`
- Modify: `mcps/go-ask/main.go`
- Modify: `mcps/go-ask/http.go`
- Modify: `mcps/go-ask/http_test.go` (call sites of `httpHandler`), other tests that build `server`

- [x] add `recapIn`/`recapOut`, `recapDeps{ollama ollamaClient; locator claudeLocator}`, the
      `deps recapDeps` field on `server`, and `(*server).recap`: turns rule, `ctl.panes` →
      `resolveTarget` (default `focused`) → refuse `p.Agent` known and not `claude` (`recap supports
      Claude only; <target> runs <agent>`) → locator (`no live claude in <target> (<name>)`, `no
      transcript on disk for Claude session <id>`) → `readTranscript` → `buildDigest` (empty → `the
      transcript of <target> has no turns yet`) → `ollama.chat` → `recapOut` with model, turns,
      transcript path, duration and token counts
      ➕ `recapDeps` also holds the default `model` (`RECAP_MODEL`, else `gemma4:e4b`), read once by
      `newRecapDeps(getenv)` in `main`; a server built without one falls back to `gemma4:e4b`
- [x] register `recap` in `newMCPServer` with a description (what it returns, ~40–50 s with the
      default model, Claude panes only, default target = focused pane, needs a local Ollama); build
      `recapDeps` once in `main` from the env and pass it to the stdio server and through
      `serveHTTP` → `httpHandler`; bump `version` to `0.3.0`; add `recap` to the package comment
- [x] add a test tree constant (`recapTreeJSON`, like `answerTree` in `answer_test.go`): one session
      with claude in both panes and distinct pane ids, the focused pane among them, one pane running
      codex; `treeJSON`'s focused pane runs codex, so it cannot serve the success tests
- [x] write tests over `httptest` + `connect` + `callTool` (the existing pattern): `tools/list`
      includes `recap` with `session_id`, `turns`, `model` in its input schema; a recap for an
      explicit session id and for the focused pane (temp sessions/projects dirs holding the
      fixture, fake ps, `httptest` Ollama) checking target, name, model, turns, transcript, tokens
      and the digest the fake Ollama received; a `:right` target picks the right pane's claude; a
      recap of the caller's own pane (`X-Agterm-Session-Id` header) succeeds
- [x] write tests for errors and limits: pane running codex, no live claude in the pane, transcript
      with no turns, Ollama not reachable, unknown session; turns 0 → 6, negative → 1, above 50 → 50
- [x] run tests - must pass before task 5

### Task 5: Live check against a real session

**Files:**
- Create: `mcps/go-ask/recap_live_test.go`

- [ ] create `recap_live_test.go` behind `//go:build live`: build the binary into `t.TempDir()`,
      start it with `mcp.CommandTransport`, call `recap` with `session_id` from `$GO_ASK_LIVE_SESSION`
      (skip when unset) and the default model, print the recap, turns, tokens and duration
- [ ] run it against a live Claude pane (e.g. `GO_ASK_LIVE_SESSION=$AGTERM_SESSION_ID go test
      -tags live -run TestRecapLive -v -timeout 10m`) with the real Ollama; check the recap has the
      four sections, is in Russian and summarizes that pane's own session (not a child claude's);
      also call it once without `session_id` when the focused pane runs claude
- [ ] record the numbers (model, turns, prompt→output tokens, seconds, sections present) as a
      `[decision]` line in the progress file named in the task prompt and in this task's commit
      message
- [ ] confirm `go test ./...` (no tag) does not build or run the live test
- [ ] run tests - must pass before task 6

### Task 6: Verify acceptance criteria
- [ ] verify all requirements from Overview are implemented (four sections, Russian, ≤ 250 words
      prompt, default model and `RECAP_MODEL`, target resolution as `ask`, Claude only)
- [ ] verify edge cases are handled (no live claude, Ollama down, unknown model, empty transcript,
      split pane, own pane)
- [ ] run full test suite: `cd mcps/go-ask && go test ./... && go vet ./... && gofmt -l .`
- [ ] verify test coverage of the new files (`go test -cover ./...`; new code paths covered)

### Task 7: [Final] Update documentation
- [ ] `mcps/go-ask/docs/api.md`: a `recap` section (input schema, output fields, how the pane's
      claude and transcript are found incl. the `CLAUDECODE=1` exclusion, the digest rules in
      brief, typical duration from the live check); its rows in the central Errors table;
      `RECAP_MODEL` and `OLLAMA_HOST` under Environment, with the note that the launchd plist passes
      no env, so the daemon's model is chosen with the `model` argument; the `initialize` example's
      version `0.3.0`; the message-flow `tools/list` line and the `mcp__go-ask__…` tool-name list
      gain `recap`
- [ ] `mcps/go-ask/docs/usage.md`: recap from Claude (example call), the Ollama requirement, the
      Codex note (`tool_timeout_sec`, default 60 s, raise it for `recap`), the Tests section
      (what the new tests cover, the `live` tag)
- [ ] `mcps/go-ask/README.md`: `recap` row in the tools table, one sentence on what it needs, and
      the Docs bullets mention recap
- [ ] update CLAUDE.md if new patterns discovered (none expected; there is no CLAUDE.md here)
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion
*Items requiring manual intervention or external systems - no checkboxes, informational only*

**Manual verification**:
- rebuild and restart the launchd daemon so Claude Code sees the new tool:
  `local/go-ask-daemon.sh build`, then `/mcp` reconnect in open Claude sessions
- try `recap` from a Claude session on another pane and on a `:right` split
- confirm Claude Code does not cut off a ~50 s `recap` call over HTTP (the live check used the Go
  SDK client, not Claude Code); if it does, raise `MCP_TOOL_TIMEOUT`

**External system updates**:
- Codex, if go-ask is ever added there: set `tool_timeout_sec` ≥ 180 for go-ask
