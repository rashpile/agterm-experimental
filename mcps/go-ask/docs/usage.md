# Using go-ask

## Install

Build the binary first:

```sh
cd mcps/go-ask
go build -o ~/.local/bin/go-ask .
```

Then register it with Claude Code in one of two modes. Use one, not both: two registrations give
Claude two copies of each tool.

### stdio

Claude Code starts its own go-ask process for every session.

```sh
claude mcp add -s user go-ask -- ~/.local/bin/go-ask
```

A running session keeps the old binary after a rebuild until `/mcp` reconnects it.

### HTTP

One go-ask process serves every session. Start it:

```sh
go-ask -http                      # http://127.0.0.1:7275/mcp
go-ask -http -addr 127.0.0.1:9000 # another port
```

Register it with headers that tell go-ask which pane the caller sits in:

```sh
claude mcp add --transport http -s user go-ask http://127.0.0.1:7275/mcp \
  --header 'X-Agterm-Session-Id: ${AGTERM_SESSION_ID:-}' \
  --header 'X-Agterm-Pane: ${AGTERM_PANE:-}'
```

Keep the single quotes, so Claude Code expands the variables in each session and not your shell
once. With `:-` a Claude session outside agterm sends empty headers and still connects, with no pane
as `self`. `claude mcp get` prints the headers without `:-`, the config keeps it.

Restart the server after a rebuild. The restart ends every MCP session, and a Claude session may
need `/mcp` to reconnect.

### HTTP from other machines

go-ask only listens on loopback unless it has a token. Make one:

```sh
mkdir -p ~/.config/go-ask
openssl rand -hex 32 > ~/.config/go-ask/token
chmod 600 ~/.config/go-ask/token
```

Listen on the VPN address, or on `0.0.0.0` for every interface:

```sh
go-ask -http -addr 10.0.0.2:7275 -token-file ~/.config/go-ask/token
```

macOS may ask once whether go-ask can accept incoming connections.

On the other machine, register the server with the token. Copy the token over a channel you trust,
such as `scp`:

```sh
claude mcp add --transport http -s user go-ask http://10.0.0.2:7275/mcp \
  --header "Authorization: Bearer $(cat ~/.config/go-ask/token)"
```

Double quotes here, so your shell puts the token into the config once. The pane headers are left out
on purpose: the other machine's agterm session ids never match a session on this Mac.

Claude Code on this Mac can use the same server. It needs the token too, and keeps the pane headers:

```sh
claude mcp add --transport http -s user go-ask http://10.0.0.2:7275/mcp \
  --header "Authorization: Bearer $(cat ~/.config/go-ask/token)" \
  --header 'X-Agterm-Session-Id: ${AGTERM_SESSION_ID:-}' \
  --header 'X-Agterm-Pane: ${AGTERM_PANE:-}'
```

With a wrong token `claude mcp get go-ask` says `Server rejected the configured Authorization header
(HTTP 401)`.

An SSH tunnel needs no token and no open port. On the other machine:

```sh
ssh -N -L 7275:127.0.0.1:7275 pkoptilin@10.0.0.2
```

It then registers `http://127.0.0.1:7275/mcp` as if the server ran there.

### launchd

To keep the HTTP server running, give it to launchd. Save this as
`~/Library/LaunchAgents/com.rashpile.go-ask.plist`. For other machines, add `-addr 10.0.0.2:7275`
and `-token-file /Users/pkoptilin/.config/go-ask/token` to `ProgramArguments`, one `<string>` per
word:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.rashpile.go-ask</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/pkoptilin/.local/bin/go-ask</string>
    <string>-http</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardErrorPath</key><string>/Users/pkoptilin/Library/Logs/go-ask.log</string>
</dict>
</plist>
```

```sh
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.rashpile.go-ask.plist
launchctl kickstart -k gui/$(id -u)/com.rashpile.go-ask   # restart after a rebuild
launchctl bootout gui/$(id -u)/com.rashpile.go-ask        # stop and unload
```

agtermctl finds the agterm socket without any env, so launchd needs none.

### Hooks for answer

`answer` reads the replies from a journal that the target Claude writes through two hooks. Install
them on the Mac where the agents run. The machine that only calls go-ask needs no hooks.

Add `go-ask hook` to `UserPromptSubmit` and `Stop` in `~/.claude/settings.json`, with the binary's
absolute path:

```json
{"hooks": {
  "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "/Users/pkoptilin/.local/bin/go-ask hook"}]}],
  "Stop": [{"hooks": [{"type": "command", "command": "/Users/pkoptilin/.local/bin/go-ask hook"}]}]
}}
```

Most settings already have hooks for these events. Add each `{"hooks": [...]}` object to the
event's existing array, beside the entries there, and keep the rest of the file as it is.

Restart running Claude sessions to load the hooks. A session that started before you added them
writes no journal, and `answer` stays `pending` for messages sent to it.

The hook prints nothing, never fails a turn and does nothing outside agterm. It writes to
`~/.local/state/go-ask/turns/`. If the server runs with `GO_ASK_STATE_DIR`, put the same variable in
front of the hook command:

```json
{"type": "command", "command": "GO_ASK_STATE_DIR=/path/to/state /Users/pkoptilin/.local/bin/go-ask hook"}
```

### Check and remove

`claude mcp get go-ask` should say `✔ Connected`. Remove it with `claude mcp remove go-ask -s user`.

## From Claude

Start a new Claude session in agterm, or reconnect in `/mcp`. Then ask in plain words, and Claude
picks the tool:

> show the agents in agterm

Claude calls `sessions` with `agents: true` and lists each pane's name, workspace, agent and target.

> send to sh:kobo: "run the tests and tell me what failed"

> tell the agent in the right pane of sh:pako-channels to check the last commit

A session can be named by its exact name, its id or a unique id prefix. Add `:right` for the split
pane. Leave the target out and the message goes to the pane that has focus in agterm:

> send "check the build" to the agent I'm looking at

That helps a Claude on another machine or in a background session. A Claude you are looking at
holds the focus itself, so go-ask refuses to send it its own message.

`ask` returns as soon as the message is sent, and Claude carries on with your conversation. When you
want the reply, ask for it:

> what did sh:kobo answer?

> is the agent in sh:kobo done with the tests?

Claude calls `answer` once, with the `request_id` its `ask` got back, or with none for the last
`ask` of its MCP session. `answer` returns at once and never waits for the agent, so each check is
one call. Ask again later if the reply isn't there yet. After go-ask restarts, Claude has to pass
the `request_id`. It still has it from the `ask` result.

The state tells you where the message is:

| State | What it means |
|-------|---------------|
| `done` | The agent finished the turn. Claude shows you its reply. |
| `running` | The agent is still working on the message. Check again later. |
| `blocked` | The agent waits for someone to answer a permission prompt or a question. Answer it in agterm. |
| `pending` | The agent hasn't started on the message. If it stays that way, the target Claude may lack the hooks or may have started before you added them. |
| `interrupted` | The turn ended without a reply, for example after Esc. Send the message again if you still need it. |
| `lost` | The message went into a turn the agent was already running, so it has no reply of its own. Read the answer in the session. |
| `unsupported` | The target is not Claude. |

The reply can still change until the agent starts its next turn, for example when a Stop hook makes
Claude carry on. `final` in the result says it no longer can, and go-ask then keeps that result.
Send the same text twice, and `answer` for the first message follows the newer one and never says
`final`.

`answer` supports Claude only for now. For Pi, codex and the other agents it returns
`unsupported`, and you read the reply in their session.

Claude asks permission for each tool the first time. To stop the prompts, add the tools to
`permissions.allow` in `~/.claude/settings.json`:

```json
{"permissions": {"allow": ["mcp__go-ask__sessions", "mcp__go-ask__ask", "mcp__go-ask__answer"]}}
```

## Things to know

- `ask` doesn't wait for the reply. The other agent gets the text as a user message and answers in
  its own session. From a Claude agent with the hooks installed, `answer` fetches that reply later.
- A message sent while the agent is in the middle of a turn waits in Claude's queue. go-ask then
  can't be sure which turn answers which message, so from then on no result from that Claude
  session is `final`. The reply is go-ask's best guess, and the `detail` says so. Read the session
  when it matters, and send to an idle agent to avoid it. A new Claude session starts clean.
  [api.md](api.md#messages-sent-while-a-turn-runs) has the details.
- The journal in `~/.local/state/go-ask/turns/` holds the final text of every turn of every hooked
  Claude session in agterm, up to about 1 MiB per pane. Only your user can read it. go-ask never
  deletes the request files in `~/.local/state/go-ask/requests/`.
- go-ask clears the target's input box before typing. A draft left there unsent is lost.
- The text is submitted, so the other agent starts working on it right away.
- go-ask won't send to your own pane, to a pane at a bare shell, or to an agent whose status is
  `blocked`, waiting for someone to answer a permission prompt or a question. Answer it in agterm,
  then ask again.

## By hand

Pipe JSON-RPC into the stdio binary. This lists the agent panes:

```sh
{ echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"me","version":"0"}}}'
  echo '{"jsonrpc":"2.0","method":"notifications/initialized"}'
  echo '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"sessions","arguments":{"agents":true}}}'
  sleep 1; } | ~/.local/bin/go-ask
```

The `sleep` keeps stdin open until the reply is written. go-ask stops when stdin closes.

Against the HTTP server, `initialize` returns the session id in a header:

```sh
curl -si http://127.0.0.1:7275/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H "X-Agterm-Session-Id: $AGTERM_SESSION_ID" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"me","version":"0"}}}'
```

Later requests send it back as `Mcp-Session-Id`.

The MCP inspector gives the same calls a form in the browser. Pass it the binary for stdio, or
pick Streamable HTTP and enter the URL:

```sh
npx @modelcontextprotocol/inspector ~/.local/bin/go-ask
```

Message formats, schemas and errors are in [api.md](api.md).

## Tests

```sh
go test ./...
```

The tests cover tree parsing, agent detection, target resolution, the refusals, the paste bytes,
the per-session pane over HTTP, the 403s for browser and rebinding requests, the bearer token, the
token file checks and the refusal to listen off loopback without a token. For `answer` they cover
the text normalization with the shapes Claude was seen to send, the journal with its trim and file
modes, `go-ask hook` with good and bad input, the request store, every row of the state rules,
messages queued while a turn runs, Stop hook continuations, when a result becomes final, `ask`
saving its request, and `answer` over stdio and over two HTTP sessions. They run against a
fake agtermctl and a temp state dir, and touch neither agterm nor `~/.local/state`.
