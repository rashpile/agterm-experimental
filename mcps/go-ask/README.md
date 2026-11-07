# go-ask

An MCP server in Go that types a message into another agterm session and presses Enter. `ask`
doesn't wait for a reply. From a Claude agent, `answer` gets the reply later, and `recap` sums up
what the Claude in a pane has been doing. It runs over stdio, or over HTTP with `-http` at
`http://127.0.0.1:7275/mcp`. Other machines can connect with `-addr` and a bearer token from
`-token-file`.

| Tool | Arguments | What it does |
|------|-----------|--------------|
| `sessions` | `agents?: bool` | Lists every session's primary pane and its open right split. `target` is the address for `ask`, `self` marks the caller's own pane, `focused` the pane with focus. `agents=true` keeps only panes running an agent CLI. |
| `ask` | `text`, `session_id?` | Clears the target's input, pastes `text` as one message and submits it. Returns a `request_id`. `session_id` is an id, a unique id prefix or an exact session name, with `:right` for the split pane. Without it, or with `focused`, the message goes to the pane that has focus in agterm. |
| `answer` | `request_id?` | Returns at once with the reply to an `ask` sent to a Claude agent, or with the state that says why there is none yet: `running`, `blocked`, `pending`, `interrupted`, `lost` or `unsupported`. Without `request_id` it answers the last `ask` of this MCP session. |
| `recap` | `session_id?`, `turns?`, `model?` | Returns a markdown recap in Russian of the Claude session in a pane over the user's last `turns` prompts, 6 by default: what was done, the user's decisions, the current state, what is left. A local Ollama model writes it from the session's transcript in under a minute. `session_id` works as for `ask`. It types nothing, so it accepts the caller's own pane. Claude panes only. |

`ask` refuses the caller's own pane, any pane at a bare shell, where the pasted lines would run
as commands, and any agent whose status is `blocked`, where the paste and Enter could answer its
permission prompt.

`answer` needs `go-ask hook` installed as Claude's `UserPromptSubmit` and `Stop` hook. The hook
writes each turn of the pane into a journal in `~/.local/state/go-ask`, and `answer` finds the turn
by the message text.

`recap` needs no hooks, only Ollama running on the same Mac with `gemma4:e4b` pulled, or the model
named in `model` or `RECAP_MODEL`.

## Docs

- [docs/usage.md](docs/usage.md): install in stdio or HTTP mode, access from other machines, launchd, the hooks for `answer`, Ollama for `recap` and the Codex timeout, use from Claude, call by hand, tests.
- [docs/api.md](docs/api.md): transports, the MCP messages, tool schemas, target resolution, the bytes `ask` sends, the `answer` states and matching rules, how `recap` finds a pane's Claude and builds its digest, the hook and the state files, errors.
