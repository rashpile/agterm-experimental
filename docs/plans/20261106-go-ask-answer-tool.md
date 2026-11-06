# go-ask: answer tool for Claude

## Overview

- Add an `answer` tool to go-ask (the "Local Agents MCP", `mcps/go-ask/`). A caller that sent a
  message with `ask` can later get the reply to that message from a Claude agent running in agterm.
- The flow: `ask` returns at once with a `request_id` and the caller carries on. When the user asks
  to check, the caller calls `answer` once. It returns at once: the reply, or a state saying why
  there is none yet. No polling, no subscription, no waiting inside the call.
- Claude's own hooks record each turn of each pane into a journal (`go-ask hook`); `answer`
  matches the request against that journal by the normalized message text, with no tag added to
  the message.
- Claude only. Pi is postponed: `answer` on a non-Claude target returns `unsupported`.
- Design: `~/.local/state/make-task/agterm-experimental-go-ask-answer-tool-for-claude-20261106-2014/design.md`.

## Context (from discovery)

- files/components involved: `mcps/go-ask/main.go` (server struct, `ask`, tool registration, flag
  parsing), `mcps/go-ask/agterm.go` (`Pane`, `rawSession`, `panesFromTree`, `resolveTarget`,
  `checkSendable`, `agtermctl.send`, `pastePayload`), `mcps/go-ask/http.go` (one MCP server per
  HTTP session via `newMCPServer`), tests in `mcps/go-ask/agterm_test.go` and
  `mcps/go-ask/http_test.go` (`fakeAgtermctl`, `fakeAgtermctlTree` with a call log, `connect`,
  `treeJSON`), docs in `mcps/go-ask/README.md`, `mcps/go-ask/docs/api.md`, `mcps/go-ask/docs/usage.md`.
- related patterns found: pure functions over the parsed tree, tested with JSON fixtures; the
  official `github.com/modelcontextprotocol/go-sdk` v1.6.0 with typed tool handlers
  (`mcp.AddTool` + In/Out structs, `jsonschema` tags); errors returned from a handler become
  `isError` results; docs in plain English, sentence-case headings, no em dashes.
- dependencies identified: none new. Standard library only (`crypto/sha256`, `crypto/rand`,
  `encoding/json`, `os`, `regexp`, `strings`).
- measured facts the code relies on (Claude Code 2.1.319, ten messages sent through go-ask):
  - a hook's environment carries the `AGTERM_*` variables of the pane the agent runs in; this
    session's shell has `AGTERM_PANE_ID`, and `agtermctl tree --json` lists the same id as
    `surfaces[].paneID`;
  - `UserPromptSubmit` input has `session_id` (Claude's own) and `prompt`; `Stop` input has
    `session_id`, `last_assistant_message` (the turn's final text) and `stop_hook_active`;
  - each message gave exactly one `UserPromptSubmit` and one `Stop`;
  - Claude changes the text it was sent only like this: a long paste (somewhere between 298 and
    1197 characters) arrives as `\n\n<pasted_content id="f033">\n<text>\n</pasted_content id="f033">\n`,
    every tab becomes 4 spaces, every `\r` is dropped; decomposed characters and trailing spaces
    are kept.
- facts not measured, handled by the state rules and checked in Task 6: `Stop` does not run when
  the user interrupts a turn (Claude Code hooks docs); a message sent mid-turn into a turn with
  tool calls is folded into that turn (`queued_command`, seen in this session's transcript); every
  `AGTERM_*` variable is inherited by child processes, so a `claude -p` started inside a pane fires
  the same hooks into the same journal.

## Development Approach

- **testing approach**: Regular (code first, then tests, in the same task)
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
- run tests after each change: `cd mcps/go-ask && gofmt -l . && go vet ./... && go test ./...`
  (`gofmt -l` must print nothing), then `git status --porcelain -- mcps/go-ask` must list only the
  files the task changed: no test may write outside `t.TempDir()`
- maintain backward compatibility: existing `ask` and `sessions` behaviour and output fields stay;
  `ask` only gains a `request_id` field

## Testing Strategy

- **unit tests**: required for every task (see Development Approach above). Table tests with JSON
  fixtures, the existing `fakeAgtermctl`/`fakeAgtermctlTree` for anything that reads the tree, a
  `t.TempDir()` as the state dir for anything that touches files, existing tests included.
- **e2e tests**: the project has no UI e2e suite. Task 6 runs one live end-to-end check against a
  real Claude session in agterm; it is not committed as a test.

## Progress Tracking

- mark completed items with `[x]` immediately when done
- add newly discovered tasks with ➕ prefix
- document issues/blockers with ⚠️ prefix
- update plan if implementation deviates from original scope
- keep plan in sync with actual work done

## Solution Overview

```
ask(text) ──> requests/<id>.json (hash, pane_id, sent_at) ──> paste + Enter into the pane
                                                       │
Claude in pane ── UserPromptSubmit ──> go-ask hook ──> turns/<pane_id>.jsonl (session, prompt hash)
               └─ Stop ───────────────> go-ask hook ──> turns/<pane_id>.jsonl (session, reply)
                                                       │
answer(id) ──> request + journal + agterm tree ──> state [+ reply]
```

- The hook is a subcommand of the same binary so the hook and the server share one
  normalization function.
- The journal is keyed by the stable pane id (`AGTERM_PANE_ID` / tree `paneID`), not by
  `AGTERM_PANE`: that one is a spawn role that can be `scratch`, and goes stale after a pane swap.
- Every record carries Claude's `session_id`, and turns are grouped per Claude session, so a
  nested `claude -p` writing into the same file cannot split the pane's own turn.
- The journal stores a hash of the normalized prompt, never the prompt; the stop record stores the
  reply.
- Requests live on disk: the check can come long after the send, and the HTTP daemon restarts.
- "Last matching turn wins until delivered": while the caller has not received a final state, a
  later turn with the same normalized text rebinds the request. The first `done` or
  `interrupted` answer stores the result in the request and closes it; later calls return the
  stored result.
- `ask` keeps its sending rules (busy agents are still allowed: the user's decision). A message
  Claude folds into a running turn has no turn of its own; `answer` reports `lost` for it.

## Technical Details

### State dir and file modes

`stateDir()` = `$GO_ASK_STATE_DIR` when set, else `$HOME/.local/state/go-ask`. Two subdirs:
`turns/` and `requests/`, created on first write with mode 0700. Every file go-ask writes,
temp files for renames included, is created with mode 0600: the journal holds reply text.
The state dir is passed explicitly: `main` passes `stateDir()` to the stdio server, to
`serveHTTP` and to the hook; `httpHandler` and `server` take it as a parameter/field. Nothing
below `main` calls `stateDir()`.

### Normalization (`normalizePrompt`)

In this order:
1. drop every `\r`;
2. unwrap pasted blocks. Go's `regexp` has no backreferences, so: find an opening tag with
   `regexp.MustCompile(`<pasted_content id="([^"]*)">\n`)`, build the literal closing tag
   `"\n</pasted_content id=\"" + id + "\">"`, find it with `strings.Index` after the opening tag,
   replace the whole block by its body, and loop until no opening tag with a matching closing tag
   is left (an opening tag without its closing tag stays as text);
3. replace every tab by 4 spaces;
4. trim trailing spaces and tabs on each line;
5. trim leading and trailing whitespace of the whole text.

No Unicode normalization. `promptHash(s)` = lowercase hex sha256 of `normalizePrompt(s)`.

### Journal

File `turns/<pane_id>.jsonl`, `<pane_id>` = `AGTERM_PANE_ID` with every character outside
`[A-Za-z0-9-]` replaced by `-`. One JSON object per line:

```json
{"event":"prompt","t":1793987000123,"session":"f1637954-e014-4317-99a6-a9c9c6a07f69","hash":"<64 hex>"}
{"event":"stop","t":1793987004567,"session":"f1637954-e014-4317-99a6-a9c9c6a07f69","reply":"ALPHA"}
```

`t` is Unix milliseconds; `session` is the hook input's `session_id`. `reply` is present on every
stop record, possibly `""`. Append with `O_APPEND|O_CREATE|O_WRONLY` (mode 0600), one `Write` per
line. Before appending, if the file is larger than 1 MiB, rewrite it (temp file + rename) keeping
only the newest whole lines that fit in 512 KiB. A trim and a concurrent append from another
process can lose that one append; accepted and documented (a rare race on a 1 MiB boundary).
Readers skip lines that do not parse.

### Hook (`go-ask hook`)

- Testable entry point: `hookMain(stdin io.Reader, stderr io.Writer, getenv func(string) string, dir string, now func() time.Time) int`,
  returning the exit code (always 0). It has no stdout parameter: nothing is ever written to stdout,
  because `UserPromptSubmit` stdout becomes prompt context. `main` calls it for `go-ask hook`
  before flag parsing and exits with its result.
- Reads one JSON object from stdin: `hook_event_name`, `session_id`, `prompt`, `last_assistant_message`.
- `AGTERM_PANE_ID` empty → do nothing.
- `UserPromptSubmit` → prompt record with `promptHash(prompt)`; `Stop` → stop record with
  `last_assistant_message`; any other event → nothing.
- Problems go to `stderr` as one `go-ask hook: …` line.

### Pane id in the tree

`rawSession.Surfaces[]` gains `PaneID string \`json:"paneID"\``; `Pane` gains an unexported
`paneID`, set from the surface of the same kind (`left` → the left surface, `right` → the right
one). `findPaneByID(panes, id)` returns the pane or not found.

### Request (`requests/<id>.json`)

```json
{"id":"3f9a0c1b2d4e","target":"<pane target>","pane_id":"<paneID>","name":"<session name>",
 "agent":"claude","hash":"<64 hex>","sent_at":1793987000000,
 "state":"","reply":"","finished_at":0}
```

`id` = 12 lowercase hex from `crypto/rand`; `loadRequest` accepts only `^[0-9a-f]{12}$` (no path
tricks). Saved with temp file + rename. `hash` = `promptHash` of the text with ESC bytes removed
(that is what `pastePayload` actually types). `sent_at` = Unix ms taken right before the send.
`state` stays `""` until a final state is stored: a non-empty `state` means delivered.

### State of a request (`requestState`, pure)

Inputs: the request, the pane's journal records, and the target pane looked up by `pane_id` in the
tree (found, not found, or tree unavailable). Only records with `t >= sent_at` count ("the window").
Turns are grouped per `session`: a prompt record opens a turn of its session; the turn holds that
session's records up to its next prompt record; its reply is its **last** stop record.

| condition (first match wins) | state |
|---|---|
| request `state` non-empty | stored `state` + `reply` |
| request `agent` is not `claude` | `unsupported` |
| take the **last** prompt record in the window whose hash equals the request's; its turn: | |
| — has a stop with text | `done` + reply |
| — has a stop with `""` | `interrupted` |
| — no stop, and its session has a later prompt record | `interrupted` (Stop does not run on a user interrupt) |
| — no stop, target found with status `blocked` | `blocked` |
| — no stop | `running` |
| no matching prompt; some stop record in the window has no prompt record of its session before it in the window (a turn that started before the send finished after it) | `lost` (the message was most likely folded into that running turn) |
| otherwise | `pending` |

`finished_at` = the `t` of the stop (or of the later prompt for an interrupt). `detail` is one
sentence per state: `pending` says that if it persists the go-ask hooks may not be installed for
this agent, or Claude changed the text; `lost` says the message was most likely folded into the
turn that was running when it was sent; `blocked` says to answer the prompt in agterm;
`unsupported` says answer supports Claude only for now. When the target is not found in the tree
(the tree lists only the frontmost window) or the tree is unavailable, the `blocked` check is
skipped and `detail` says which; that alone never makes a request `lost`.

### Tools

- `ask` output gains `request_id`. The server (one per stdio process, one per HTTP MCP session)
  remembers the last `request_id` it returned. Its description says it returns a `request_id`
  that `answer` takes.
- `answer` input: `request_id` (optional). Missing → the last `ask` of this MCP session; none →
  error "no request_id and no ask in this session; pass the request_id ask returned".
  Unknown id → error naming it. Journal unreadable (exists but cannot be read) → error.
- `answer` output: `request_id`, `state`, `reply` (omitempty), `target`, `name`, `sent_at`,
  `finished_at` (omitempty), `detail` (omitempty). A `done` or `interrupted` result is stored
  into the request (`state`, `reply`, `finished_at`).
- version `0.2.0`.

## What Goes Where

- **Implementation Steps** (`[ ]` checkboxes): code, tests and docs in `mcps/go-ask/`.
- **Post-Completion** (no checkboxes): installing the hooks into `~/.claude/settings.json`,
  rebuilding and restarting the user's go-ask daemon.

## Implementation Steps

### Task 1: State dir, normalization and turn journal

**Files:**
- Create: `mcps/go-ask/turns.go`
- Create: `mcps/go-ask/turns_test.go`

- [ ] add `stateDir()`, `normalizePrompt`, `promptHash` to `mcps/go-ask/turns.go` as specified in Technical Details (unwrapping without backreferences)
- [ ] add the journal: `turnRecord` (`event`, `t`, `session`, `hash`, reply as `*string` so `""` is kept), `journalPath(dir, paneID)`, `appendTurn(path, rec)` with the 1 MiB → 512 KiB trim and mode 0600 files / 0700 dirs, `readTurns(path)` that skips bad lines and returns no error for a missing file
- [ ] write tests for `normalizePrompt`/`promptHash`: the measured long-paste wrapper (exact shape from Context), two wrapped blocks in one prompt, an opening tag without a closing tag left as text, tabs, `\r`, trailing spaces, a decomposed `é` kept, a short multi-line text unchanged, two different texts hashing differently
- [ ] write tests for the journal: append/read round trip, `""` reply kept, malformed line skipped, missing file, pane id sanitizing, file mode 0600, trim past 1 MiB keeps the newest lines within 512 KiB, records of two sessions interleaved in one file read back in order
- [ ] run tests - must pass before task 2

### Task 2: `go-ask hook` subcommand

**Files:**
- Create: `mcps/go-ask/hook.go`
- Create: `mcps/go-ask/hook_test.go`
- Modify: `mcps/go-ask/main.go`

- [ ] add `hookMain(stdin, stderr, getenv, dir, now) int` in `mcps/go-ask/hook.go` per Technical Details (prompt and stop records with `session`, other events ignored, no `AGTERM_PANE_ID` → no-op, always returns 0)
- [ ] dispatch `go-ask hook` in `main()` before flag parsing: `os.Exit(hookMain(os.Stdin, os.Stderr, os.Getenv, stateDir(), time.Now))`
- [ ] write tests: UserPromptSubmit and Stop records land in `turns/<pane_id>.jsonl` with the right fields (session, hash of the normalized prompt, reply); unknown event ignored; no `AGTERM_PANE_ID` → no file
- [ ] write tests for error cases: malformed stdin → one stderr line, no file, result 0; an unwritable state dir → one stderr line, result 0
- [ ] run tests - must pass before task 3

### Task 3: Request store, pane id and `request_id` from `ask`

**Files:**
- Create: `mcps/go-ask/requests.go`
- Create: `mcps/go-ask/requests_test.go`
- Modify: `mcps/go-ask/agterm.go`
- Modify: `mcps/go-ask/agterm_test.go`
- Modify: `mcps/go-ask/main.go`
- Modify: `mcps/go-ask/http.go`
- Modify: `mcps/go-ask/http_test.go`

- [ ] add the pane id to the tree parsing (`rawSession.Surfaces[].PaneID`, `Pane.paneID` from the surface of the same kind) and `findPaneByID` in `mcps/go-ask/agterm.go`
- [ ] add `request`, `newRequestID()`, `saveRequest(dir, r)` (temp + rename, 0600), `loadRequest(dir, id)` (id must match `^[0-9a-f]{12}$`) in `mcps/go-ask/requests.go`
- [ ] give `server` a state dir field and a mutex-guarded last `request_id`; pass the dir explicitly: `httpHandler(ctl, token, dir)` and `serveHTTP(addr, tokenFile, dir, ctl)`, `main` passes `stateDir()`; every existing test that builds a `server` or calls `httpHandler` passes `t.TempDir()`
- [ ] in `ask`, after the target checks and before `send`: take `sent_at`, build the request (hash of the text with ESC removed, target, pane id, name, agent), save it, then send; a failed save returns the error and sends nothing; return `request_id` in `askOut` and remember it as the session's last
- [ ] write tests for the pane id: `panesFromTree` sets it for left and right panes (add `paneID` to the `treeJSON` fixture), `findPaneByID` found and not found
- [ ] write tests for the store: save/load round trip, file mode 0600, a bad id (`../x`, uppercase, wrong length) refused, unknown id error
- [ ] write tests for `ask`: the request file exists with the right hash, pane id and fields and `request_id` is returned (fake agtermctl, temp state dir); an unwritable state dir sends nothing (the fake's call log has no `session type`); existing `ask` tests still pass and `git status` stays clean after `go test`
- [ ] run tests - must pass before task 4

### Task 4: Request state from the journal

**Files:**
- Create: `mcps/go-ask/answer.go`
- Create: `mcps/go-ask/answer_test.go`

- [ ] add the pure `requestState` in `mcps/go-ask/answer.go` implementing the state table in Technical Details (turns grouped per session), returning state, reply, finished_at and detail
- [ ] write tests for every row of the table: stored state, unsupported, done, interrupted by `""`, interrupted by a later prompt of the same session, blocked, running, lost (an orphan stop in the window and no prompt), pending
- [ ] write tests for the matching rules: records before `sent_at` ignored; two matching turns → the last one wins; a stop-hook continuation (two stops in one turn) → the last stop's reply; a nested session's prompt and stop interleaved inside the matching turn do not split it and its stop is not taken as the reply; a later prompt of another session does not mark the turn interrupted
- [ ] write tests for the tree cases: target not found and tree unavailable skip the `blocked` check, never give `lost`, and say so in `detail`
- [ ] run tests - must pass before task 5

### Task 5: `answer` tool

**Files:**
- Modify: `mcps/go-ask/main.go`
- Modify: `mcps/go-ask/http_test.go`
- Modify: `mcps/go-ask/answer_test.go`

- [ ] register `answer` in `newMCPServer` with `answerIn`/`answerOut` per Technical Details and a description that says it returns at once and never waits; update the `ask` description to mention `request_id` and `answer`, and the package comment's tool list (with `hook`)
- [ ] implement the handler: resolve the id (given, else the session's last, else the error), load the request, read the pane's journal, look up the target by pane id in the tree (tree failure → "tree unavailable", not an error), compute the state; on `done`/`interrupted` store the result into the request
- [ ] bump `version` to `0.2.0`
- [ ] write tests: `ask` then `answer` without an id through a fake agtermctl and a hand-written journal (done, then the same call again returns the stored reply even after the journal gains a newer matching turn); `answer` with an unknown id; `answer` without id and without a prior ask; a failing tree still answers from the journal; an unreadable journal (a directory in its place) is an error
- [ ] write an HTTP test: two MCP sessions each `ask`, then `answer` without an id answers each session's own request
- [ ] run tests - must pass before task 6

### Task 6: Verify acceptance criteria

- [ ] verify all requirements from Overview are implemented
- [ ] verify edge cases are handled (every row of the state table has a test)
- [ ] run full test suite: `cd mcps/go-ask && gofmt -l . && go vet ./... && go test ./...`
- [ ] live end-to-end check against a real Claude in agterm, nothing committed and nothing in the user's settings changed. Setup: build the binary to a temp path; start a background agterm session (`agtermctl session new --no-select --after "$AGTERM_SESSION_ID" --cwd <temp dir> --command "<claude> --model haiku --setting-sources project,local --strict-mcp-config --settings <temp settings.json>"`, `<claude>` = `/Users/pkoptilin/.local/bin/claude`) whose settings hold only `UserPromptSubmit` and `Stop` hooks running `GO_ASK_STATE_DIR=<temp state> <temp binary> hook`, plus a `UserPromptSubmit` hook that once writes `env | grep ^AGTERM_` to a temp file; answer a first-run trust prompt with `\r` if one shows. Then, from a stdio client of the temp binary with the same `GO_ASK_STATE_DIR`: (a) `ask` a short message, `answer` right away (expect `pending` or `running`), wait for the turn, `answer` again (expect `done` with the reply); (b) the same with a long multi-line message containing a tab (expect `done`); (c) `ask` a message that makes a long reply, send ESC into the session (`agtermctl session type --stdin --target <id>` with `\x1b`) before it ends, then `ask` another message, and record the journal records and the first request's state (expect `interrupted`); (d) `ask` while a turn is running and record the state after both turns end. Close the session with `agtermctl session close`. Write the observed states, the journal records of (c) and (d), and whether `AGTERM_PANE_ID` was in the hook env into the progress file `/tmp/progress-20261106-go-ask-answer-tool.txt`
- [ ] verify test coverage: every new function has tests (`go test -cover ./...` reported in the progress file)

### Task 7: [Final] Update documentation

- [ ] update `mcps/go-ask/docs/api.md`: the `answer` tool (input, output, states table, matching and normalization rules, "last matching turn wins until delivered", returns at once), `request_id` in `ask`'s output, the hook and the journal/request files, new errors, `GO_ASK_STATE_DIR` in the Environment table, version `0.2.0` in the `initialize` example, `answer` in the message-flow diagram
- [ ] update `mcps/go-ask/docs/usage.md`: installing the hooks (the `UserPromptSubmit`/`Stop` JSON for `~/.claude/settings.json` with the absolute binary path, added beside existing hooks), asking for an answer from Claude in plain words, what each state means for the user, Pi not supported yet, `mcp__go-ask__answer` in the `permissions.allow` example, "Things to know" no longer saying no reply comes back, the Tests paragraph
- [ ] update `mcps/go-ask/README.md`: the tools table gains `answer`; one line on the hook
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion

*Items requiring manual intervention or external systems - no checkboxes, informational only*

**Manual setup**:
- add the two hooks to `~/.claude/settings.json` (docs/usage.md has the snippet); running Claude
  sessions pick them up after a restart
- rebuild and restart the go-ask daemon: `local/go-ask-daemon.sh build`

**Manual verification**:
- from the remote client, `ask` an agent, carry on, then ask for the answer once
