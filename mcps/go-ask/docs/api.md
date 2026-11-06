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
  │◀──────────────── ask, sessions + schemas
  │── tools/call {name, arguments} ──────▶│
  │◀──────────────── result
```

Reply to `initialize`:

```json
{"protocolVersion": "2025-06-18",
 "serverInfo": {"name": "go-ask", "version": "0.1.0"},
 "capabilities": {"logging": {}, "tools": {"listChanged": true}}}
```

Claude Code shows the tools to the model as `mcp__go-ask__sessions` and `mcp__go-ask__ask`.

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

Output:

| Field | Always | Meaning |
|-------|--------|---------|
| `sent` | yes | `true`. |
| `target` | yes | The resolved address. |
| `name` | yes | Session name. |
| `agent` | no | Agent CLI found in the target pane. |

Example:

```json
{"jsonrpc": "2.0", "id": 4, "method": "tools/call",
 "params": {"name": "ask", "arguments": {"text": "run the tests", "session_id": "agent"}}}
```

```json
{"jsonrpc": "2.0", "id": 4, "result": {
  "structuredContent": {"sent": true, "target": "7E1C11A9-3D4B-4609-AFAB-0DFD423D961C",
                        "name": "agent", "agent": "claude"},
  "content": [{"type": "text", "text": "{\"sent\":true,...}"}]}}
```

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

## Environment

| Variable | Use |
|----------|-----|
| `AGTERM_SESSION_ID`, `AGTERM_PANE` | stdio only. Name the caller's pane. agterm sets them in every shell, and Claude Code passes them on to the server it starts. Without them no pane is `self`. In HTTP mode go-ask ignores them and reads the headers. |
| `AGTERMCTL` | Path to `agtermctl`. Without it go-ask tries `PATH`, then `/opt/homebrew/bin/agtermctl`, then `/Applications/agterm.app/Contents/MacOS/agtermctl`. |

Each `agtermctl` call times out after 15 seconds.
