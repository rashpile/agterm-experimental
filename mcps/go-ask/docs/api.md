# go-ask MCP API

go-ask speaks MCP as JSON-RPC 2.0, over stdio or over Streamable HTTP. Both carry the same tools.

## Transports

| | stdio | HTTP |
|---|---|---|
| Start | `go-ask` | `go-ask -http [-addr 127.0.0.1:7275] [-token-file path]` |
| Who starts it | The client, once per client session | You or launchd, once for all clients |
| Messages | One JSON message per line on stdin and stdout | `POST /mcp`, replies as JSON or SSE |
| Caller's own pane | `AGTERM_SESSION_ID` and `AGTERM_PANE` from the env it inherits | `X-Agterm-Session-Id` and `X-Agterm-Pane` headers |

Logs go to stderr in both modes. In HTTP mode go-ask logs every new MCP session with the pane it
treats as the caller's.

### HTTP

The endpoint is `http://127.0.0.1:7275/mcp` unless `-addr` says otherwise. It follows the MCP
Streamable HTTP transport. The reply to `initialize` carries an `Mcp-Session-Id` header, and every
later request sends it back. A session left idle for 24 hours is closed, and the client has to
initialize again.

The pane headers are read once, from the request that opens the MCP session:

| Header | Value |
|--------|-------|
| `X-Agterm-Session-Id` | The caller's agterm session id. Without it no pane is `self`. |
| `X-Agterm-Pane` | `right` when the caller sits in the split pane, else `left` or absent. |

`ask` types into terminals, so go-ask refuses requests a web page could make:

- a request that reached a localhost address with a non-localhost `Host` header gets 403. This
  stops DNS rebinding. The MCP SDK does this check.
- a browser request from another origin gets 403, by `Sec-Fetch-Site` or `Origin`. Clients outside a
  browser send neither header and pass.

### Token

With `-token-file`, every request to `/mcp` must carry the token from that file:

```
Authorization: Bearer <token>
```

A missing or wrong token gets `401` with `WWW-Authenticate: Bearer realm="go-ask"`, and go-ask logs
`401 from <address>`. go-ask compares tokens in constant time.

go-ask refuses to start when:

- `-addr` is anything but a loopback address, `127.0.0.1`, `::1` or `localhost`, and there is no
  `-token-file`. `0.0.0.0`, an empty host and hostnames count as reachable from other machines;
- the token file can be read by group or others. It wants mode `600`, as ssh does for keys;
- the token is shorter than 16 characters after trimming whitespace.

On loopback the token is optional. Without it, anyone with a shell on this Mac can type into your
sessions through the port.

The token travels in plain HTTP. Use it on a network you already trust, such as a VPN, or put the
port behind an SSH tunnel.

## Message flow

```
client                                   go-ask
  │── initialize ────────────────────────▶│
  │◀──────────────── serverInfo, capabilities
  │── notifications/initialized ─────────▶│  notification, no reply
  │── tools/list ────────────────────────▶│
  │◀──────────────── sessions, ask, answer + schemas
  │── tools/call ask {text} ─────────────▶│  saves a request, types the text
  │◀──────────────── sent, request_id     │  returns once Enter is sent
  │                                       │
  │   the caller carries on               │  the target Claude's hooks journal its turn
  │                                       │
  │── tools/call answer {request_id} ────▶│  reads the request and the journal
  │◀──────────────── state, reply         │  returns at once, never waits
```

Reply to `initialize`:

```json
{"protocolVersion": "2025-06-18",
 "serverInfo": {"name": "go-ask", "version": "0.2.0"},
 "capabilities": {"logging": {}, "tools": {"listChanged": true}}}
```

Claude Code shows the tools to the model as `mcp__go-ask__sessions`, `mcp__go-ask__ask` and
`mcp__go-ask__answer`.

## sessions

Lists the panes `ask` can send to. Every session gives one entry for its primary pane and a second
one for its right split when the split is open. Scratch panes are not listed.

Input:

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `agents` | bool | no | Keep only panes whose foreground process is an agent CLI. |

Output is `{"sessions": [pane, ...]}`. Each pane:

| Field | Always | Meaning |
|-------|--------|---------|
| `target` | yes | The address to pass to `ask`. `<session-id>` for the primary pane, `<session-id>:right` for the split. |
| `workspace` | yes | Workspace name. |
| `name` | yes | Session name as the sidebar shows it. |
| `pane` | yes | `left` or `right`. |
| `cwd` | no | Working directory of the pane. |
| `foreground` | no | Command line of the foreground process. Absent means a bare shell. |
| `agent` | no | Agent CLI found in `foreground`: claude, codex, pi, gemini, opencode, aider, amp, goose, crush, qwen or cursor-agent. |
| `status` | no | agterm's agent status: `idle`, `active`, `blocked` or `completed`. A session has one status, and it goes on the pane agterm reports it for. |
| `self` | no | `true` on the caller's own pane, see [Transports](#transports). |
| `focused` | no | `true` on the pane with keyboard focus: the selected session of the active workspace in agterm's frontmost window, and its left or right pane, whichever has focus. No pane has it when focus is on a scratch pane. |

go-ask finds the agent in the foreground argv. It checks `argv[0]`, the command string of a shell
started with `-c` such as `zsh -ilc "VAR=x claude"`, and the script of a runtime such as
`node /opt/homebrew/bin/codex`. An agent it doesn't know gets no `agent` field, and `ask` still
sends to it.

Example:

```json
{"jsonrpc": "2.0", "id": 3, "method": "tools/call",
 "params": {"name": "sessions", "arguments": {"agents": true}}}
```

```json
{"jsonrpc": "2.0", "id": 3, "result": {
  "structuredContent": {"sessions": [
    {"target": "7E1C11A9-3D4B-4609-AFAB-0DFD423D961C", "workspace": "ops", "name": "agent",
     "pane": "left", "cwd": "/Users/pkoptilin/workspace/ops",
     "foreground": "claude --resume 636abdbb-8439-493f-a620-cf7952b447f6", "agent": "claude"},
    {"target": "885D35D1-0109-4B67-A106-4064E0A1F435", "workspace": "agterm-experimental",
     "name": "sh:agterm-experimental", "pane": "left",
     "cwd": "/Users/pkoptilin/workspace/repos/agterm-experimental",
     "foreground": "claude", "agent": "claude", "status": "active", "self": true}]},
  "content": [{"type": "text", "text": "{\"sessions\":[...]}"}]}}
```

`structuredContent` follows the tool's `outputSchema`. `content` holds the same JSON as a string,
for clients that don't read structured output.

## ask

Types `text` into a pane's input and submits it. Returns once Enter is sent.

Input:

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `text` | string | yes | The message. Newlines are kept. |
| `session_id` | string | no | Which pane to send to, see below. Omitted means `focused`. |

go-ask resolves `session_id` in this order:

1. `focused`, case ignored, picks the pane `sessions` marks `focused`. It is also the default.
2. A `:right` suffix picks the split pane. `:left` or no suffix picks the primary pane.
3. An exact session id, case ignored.
4. A unique prefix of a session id.
5. An exact session name.

A prefix or a name that matches more than one session is an error, and the error lists the ids.

go-ask reads the focus at the moment of the call. A Claude that runs `ask` without `session_id` while
the user is looking at it gets its own pane back, and `ask` refuses it. The default is for callers
the user is not looking at: a Claude on another machine, or one working in a background session.

`ask` refuses four targets, and types nothing into them:

- the caller's own pane;
- a pane at a bare shell, because each pasted line would run as a command there;
- a pane whose `status` is `blocked`. The agent there waits for a person to answer a permission
  prompt or a question, and the pasted text and the Enter could pick an answer in that menu;
- `:right` on a session without an open split.

The checks apply to every target, `focused` and the default included. `blocked` is agterm's status,
set by the agent's hook, and go-ask trusts it as it is. A status left over from an answered prompt
blocks `ask` until agterm clears it, for example when the user opens the session.

Every send is three writes through `agtermctl session type --stdin`:

1. 200 DEL bytes (`0x7f`) clear whatever is in the input box.
2. `text` wrapped in a bracketed paste, `ESC[200~ ... ESC[201~`. go-ask removes ESC bytes from `text`
   first, so the text can't close the paste early. Claude Code, codex and pi keep a newline inside a
   paste as a newline, so a multi-line message arrives as one message.
3. One second later, `\r` submits it. agconv's `agent-conversation` waits the same second.

go-ask doesn't read the target's screen after that and doesn't wait for a reply.

Before the first write, `ask` saves a request to `requests/<request_id>.json` in the state dir, see
[Requests](#requests). If go-ask can't save it, `ask` returns an error and types nothing. The
`request_id` in the output is what `answer` takes. The MCP session also remembers it as its last
`ask`, so `answer` without an id answers this message.

Output:

| Field | Always | Meaning |
|-------|--------|---------|
| `sent` | yes | `true`. |
| `target` | yes | The resolved address. |
| `name` | yes | Session name. |
| `agent` | no | Agent CLI found in the target pane. |
| `request_id` | yes | 12 lowercase hex characters. Pass it to `answer` to get the reply. |

Example:

```json
{"jsonrpc": "2.0", "id": 4, "method": "tools/call",
 "params": {"name": "ask", "arguments": {"text": "run the tests", "session_id": "agent"}}}
```

```json
{"jsonrpc": "2.0", "id": 4, "result": {
  "structuredContent": {"sent": true, "target": "7E1C11A9-3D4B-4609-AFAB-0DFD423D961C",
                        "name": "agent", "agent": "claude", "request_id": "3f9a0c1b2d4e"},
  "content": [{"type": "text", "text": "{\"sent\":true,...}"}]}}
```

## answer

Returns the reply to a message sent with `ask`, or the state that says why there is none yet. It
returns at once. It doesn't wait for the turn to end and doesn't poll. Call it when you want to
know, and call it again later for a newer state.

`answer` works for Claude agents only, and only when the target Claude runs the go-ask hooks, see
[Hook](#hook). For any other agent it returns `unsupported`.

Input:

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `request_id` | string | no | The `request_id` that `ask` returned. Omitted means the last `ask` of this MCP session. |

The MCP session keeps its last `request_id` in memory. A stdio process keeps it until it exits. An
HTTP MCP session keeps it until the session closes or the server restarts. After that, pass the id.

Output:

| Field | Always | Meaning |
|-------|--------|---------|
| `request_id` | yes | The request answered. |
| `state` | yes | One of the states below. |
| `reply` | no | The agent's final text for the turn, when `state` is `done`. |
| `target` | yes | The target `ask` resolved. |
| `name` | yes | Session name at the time of the `ask`. |
| `sent_at` | yes | When `ask` sent the message, Unix milliseconds. |
| `finished_at` | no | When the turn ended, Unix milliseconds. Set for `done` and `interrupted`. |
| `detail` | no | One sentence on why there is no reply yet, or on what go-ask could not check. |

### States

| State | Meaning |
|-------|---------|
| `done` | The turn ended with text. `reply` holds it. |
| `interrupted` | The turn ended without a reply. Either Claude stopped with an empty final message, or the same Claude session started its next turn before this one stopped. Claude runs no `Stop` hook when the user interrupts a turn, so the next prompt is the only sign that it ended. |
| `running` | The turn started and hasn't ended. |
| `blocked` | The turn started and hasn't ended, and the target's agterm status is `blocked`. The agent waits for a person to answer a permission prompt or a question. |
| `pending` | No turn has started for this message. If it stays `pending`, the target Claude may lack the go-ask hooks, or Claude changed the text in a way go-ask doesn't undo. |
| `lost` | No turn matches the message, and a turn that started before the send ended after it. Claude most likely folded the message into that turn, so it has no turn or reply of its own. Claude Code 2.1.319 journals a queued message at once, so this is rare, see [Messages sent while a turn runs](#messages-sent-while-a-turn-runs). |
| `unsupported` | The target is not Claude, or `ask` found no agent in it. |

`done` and `interrupted` are final. The first `answer` that returns one stores it in the request,
with `reply` and `finished_at`, and every later call returns the stored result without reading the
journal. `unsupported` never changes either, since it depends only on the agent `ask` recorded. The
other states describe the journal at the time of the call.

For `blocked`, go-ask looks the target up in agterm's tree by its pane id. The tree lists only the
frontmost window's sessions, so a target in another window, a closed session or a failing
`agtermctl` skips that check. A turn in progress then shows as `running`, and the `detail` of any
state but `done` and `interrupted` says which case it was. A target missing from the tree never
makes a request `lost`.

### How go-ask finds the turn

The hooks write each turn of the target pane to the pane's journal, see
[Turn journal](#turn-journal). `answer` reads that journal and:

1. keeps only the records at or after `sent_at`;
2. groups them into turns per Claude session. A prompt record opens a turn of its session. The turn
   holds that session's records up to the session's next prompt record, and its reply is its last
   stop record. A `Stop` hook that makes Claude carry on gives a turn two stop records, and the
   second one holds the final text;
3. takes the last prompt record whose hash equals the request's hash, and reports the state of its
   turn.

Grouping by session keeps a nested `claude -p` that runs inside the pane out of the turn. It writes
to the same journal under its own `session_id`, so it neither ends the turn nor gives its reply.

The last matching turn wins until a final state is delivered. While the request has no stored
state, a newer turn with the same text takes over the request. Send the same message twice and
`answer` follows the second one. Once `answer` has returned `done` or `interrupted`, the request
keeps that result.

No tag goes into the message. go-ask matches the text itself, after both sides go through the same
normalization:

1. drop every `\r`;
2. replace each `<pasted_content id="X">\n...\n</pasted_content id="X">` block by the text inside
   it. Claude Code wraps a long paste in such a block. An opening tag without its closing tag stays
   as text;
3. turn every tab into 4 spaces;
4. trim trailing spaces and tabs on each line;
5. trim leading and trailing whitespace of the whole text.

There is no Unicode normalization, because Claude keeps the bytes it gets. The hash is the
lowercase hex sha256 of the result. `ask` hashes the text with ESC bytes removed, since that is what
it types. These rules undo every change Claude Code 2.1.319 made to the text go-ask sent it.

### Messages sent while a turn runs

`ask` sends to a busy agent, and Claude queues the message. Claude Code 2.1.319 fires
`UserPromptSubmit` for a queued message at once, while the running turn still runs. The state rules
above read that wrong, in two ways that were measured:

- the running turn's request becomes `interrupted` as soon as the queued message's prompt record
  lands, and `answer` stores that as final. Its real reply is never returned;
- when the running turn makes no tool call, it ends with its own `Stop`, and the queued message then
  runs as a turn of its own with a second `Stop` and no prompt record. An `answer` for the queued
  message between those two stops returns `done` with the earlier turn's reply, and stores it.

When the queued message lands in a turn with tool calls, Claude folds it into that turn, and the
queued message's `done` reply is right. To get a reliable answer, send to an agent whose status is
`idle` or `completed`.

Example:

```json
{"jsonrpc": "2.0", "id": 6, "method": "tools/call",
 "params": {"name": "answer", "arguments": {"request_id": "3f9a0c1b2d4e"}}}
```

```json
{"jsonrpc": "2.0", "id": 6, "result": {
  "structuredContent": {"request_id": "3f9a0c1b2d4e", "state": "done",
                        "reply": "All 42 tests pass.", "target": "7E1C11A9-3D4B-4609-AFAB-0DFD423D961C",
                        "name": "agent", "sent_at": 1793987000000, "finished_at": 1793987041250},
  "content": [{"type": "text", "text": "{\"request_id\":\"3f9a0c1b2d4e\",...}"}]}}
```

A call before the turn started:

```json
{"request_id": "3f9a0c1b2d4e", "state": "pending", "target": "7E1C11A9-3D4B-4609-AFAB-0DFD423D961C",
 "name": "agent", "sent_at": 1793987000000,
 "detail": "No turn has started for this message yet; if this persists, the go-ask hooks may not be installed for this agent, or Claude changed the text."}
```

## Hook

`go-ask hook` is the Claude Code hook that feeds `answer`. Install it for `UserPromptSubmit` and
`Stop`, see [usage.md](usage.md#hooks-for-answer). Claude runs it with the hook input on stdin, in
the environment of the pane Claude runs in. The hook reads `hook_event_name`, `session_id`,
`prompt` and `last_assistant_message` from the input, and `AGTERM_PANE_ID` from the environment.
It appends one record to the pane's journal:

- `UserPromptSubmit` gives a prompt record with the hash of the normalized prompt;
- `Stop` gives a stop record with `last_assistant_message`, which may be `""`.

Other events write nothing. So does a Claude outside agterm, which has no `AGTERM_PANE_ID`.

The hook writes nothing to stdout, because Claude adds the stdout of a `UserPromptSubmit` hook to
the prompt's context. A problem, such as input that isn't JSON or a state dir it can't write, is one
`go-ask hook: ...` line on stderr. It always exits 0, so a journal problem never blocks a turn.

`go-ask hook` takes no flags. It reads the state dir from `GO_ASK_STATE_DIR` like the server, and the
two have to agree.

## State files

go-ask keeps its state in `$GO_ASK_STATE_DIR`, or `~/.local/state/go-ask` when the variable is
unset. It creates the `turns/` and `requests/` directories on first write with mode `700`. Every
file it writes has mode `600`, since the journal holds reply text. A rewrite goes through a temp
file and a rename, so no reader sees half a file.

### Turn journal

`turns/<pane_id>.jsonl` holds one pane's turns. `<pane_id>` is the pane's `AGTERM_PANE_ID`, the
same id agterm's tree lists as `surfaces[].paneID`, with every character outside `[A-Za-z0-9-]`
replaced by `-`. Unlike `AGTERM_PANE`, the id stays with the pane through a pane swap.

One JSON object per line:

```json
{"event":"prompt","t":1793987000123,"session":"f1637954-e014-4317-99a6-a9c9c6a07f69","hash":"<64 hex>"}
{"event":"stop","t":1793987004567,"session":"f1637954-e014-4317-99a6-a9c9c6a07f69","reply":"ALPHA"}
```

| Field | Meaning |
|-------|---------|
| `event` | `prompt` or `stop`. |
| `t` | When the hook ran, Unix milliseconds. |
| `session` | Claude's `session_id` from the hook input. |
| `hash` | Prompt records only. The normalized prompt's hash. The journal never holds the prompt. |
| `reply` | Stop records only, always present, possibly `""`. The turn's final text. |

When a journal is past 1 MiB, the next append first rewrites it to keep the newest whole lines that
fit in 512 KiB. A trim that races an append from another process can lose that one record. Readers
skip lines that don't parse.

### Requests

`requests/<request_id>.json` holds one `ask`:

```json
{"id":"3f9a0c1b2d4e","target":"7E1C11A9-3D4B-4609-AFAB-0DFD423D961C","pane_id":"8A7B9419-1E6E-4784-88CD-C92A985285F9",
 "name":"agent","agent":"claude","hash":"<64 hex>","sent_at":1793987000000,
 "state":"","reply":"","finished_at":0}
```

| Field | Meaning |
|-------|---------|
| `id` | The `request_id`, 12 random lowercase hex characters. go-ask refuses any other form before it opens a file. |
| `target`, `name`, `agent` | The target as `ask` resolved it. |
| `pane_id` | The target pane's agterm pane id, which names its journal. |
| `hash` | Hash of the normalized text, ESC bytes removed. |
| `sent_at` | Unix milliseconds, taken right before the send. |
| `state`, `reply`, `finished_at` | Empty until `answer` returns `done` or `interrupted`, then that result. A non-empty `state` means the caller got it. |

Requests live on disk, so `answer` works long after the `ask` and across server restarts. go-ask
never deletes them.

## Errors

A failed call is a normal `result` with `"isError": true` and the reason in `content[0].text`. It is
not a JSON-RPC error, so the model reads the reason and can retry, for example after calling
`sessions`.

```json
{"jsonrpc": "2.0", "id": 5, "result": {"isError": true, "content": [{"type": "text",
  "text": "885D35D1-0109-4B67-A106-4064E0A1F435 is your own pane; ask another session"}]}}
```

| Message | Cause |
|---------|-------|
| `text is empty` | `text` missing or empty. |
| `no listed pane has focus: ...` | `focused` or no `session_id`, and focus is on a scratch pane or agterm has no open window. |
| `no session matches "x"; call sessions to list them` | Nothing matched `session_id`. |
| `"x" matches N sessions (...); pass a full session id` | A prefix or name matched several sessions. |
| `session <id> has no right pane (is the split open?)` | `:right` on a session without a split. |
| `<target> is your own pane; ask another session` | Target is the caller's own pane. |
| `<target> (<name>) is at a bare shell: ...` | Target pane runs no program. |
| `<target> (<name>) is blocked: the agent is waiting for a person ... Nothing was sent. ...` | Target pane's status is `blocked`. Answer the prompt in agterm, then retry. |
| `a sandbox blocks the agterm socket ...` | The client's sandbox denied access to the agterm socket. |
| `agtermctl <command>: ...` | Any other agtermctl failure, with its message. |
| `save request: ... Nothing was sent` | `ask` could not write the request file to the state dir. It typed nothing. |
| `no request_id and no ask in this session; pass the request_id ask returned` | `answer` without `request_id` in an MCP session with no successful `ask`, for example after a server restart. |
| `invalid request_id "x": want the 12 lowercase hex characters ask returned` | `request_id` is not 12 lowercase hex characters. |
| `unknown request_id "x"` | The state dir has no request with that id. |
| `read request <id>: ...`, `parse request <id>: ...` | The request file can't be read, or isn't JSON. |
| `read the turn journal of <target>: ...` | The target pane's journal exists but can't be read. |

`answer` gives no error when agterm's tree fails. It answers from the journal and says in `detail`
that it could not check the target's status.

## Environment

| Variable | Use |
|----------|-----|
| `AGTERM_SESSION_ID`, `AGTERM_PANE` | stdio only. Name the caller's pane. agterm sets them in every shell, and Claude Code passes them on to the server it starts. Without them no pane is `self`. In HTTP mode go-ask ignores them and reads the headers. |
| `AGTERMCTL` | Path to `agtermctl`. Without it go-ask tries `PATH`, then `/opt/homebrew/bin/agtermctl`, then `/Applications/agterm.app/Contents/MacOS/agtermctl`. |
| `GO_ASK_STATE_DIR` | The state dir for the requests and the turn journals, see [State files](#state-files). Default `~/.local/state/go-ask`. The server and `go-ask hook` must see the same value. |
| `AGTERM_PANE_ID` | `go-ask hook` only. The pane id that names the journal. agterm sets it in every pane, and Claude passes it on to its hooks. Without it the hook writes nothing. |

Each `agtermctl` call times out after 15 seconds.
