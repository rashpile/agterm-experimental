# pako-telegram commands — orchestrator transport

[pako-telegram](https://github.com/rashpile/pako-telegram) command definitions that expose the
[`orchestrating-agterm-agents`](../orchestrating-agterm-agents/) skill over Telegram. pako-telegram is a
small bot that runs allow-listed shell commands on your Mac; these YAML files add the orchestrator
control commands so you can drive and watch the orchestrator from your phone.

## Commands

| Command | What it does |
|---------|--------------|
| `/o <msg>` | Inject a message into the orchestrator's session (types + submits). With `default_command: o`, plain (non-slash) messages route here too — so you can just type. |
| `/os` | Read-only snapshot of the orchestrator's screen — watch progress without sending anything. |
| `/oa <keys>` | Send exact keystrokes to answer a prompt/menu — **no** auto-Enter; the bare word `ENTER` sends an Enter keypress (`/oa 1`, `/oa ENTER`). |

## Install

Copy into your pako-telegram commands directory, then reload the bot (`/reload`, or restart it):

```sh
cp -r commands/orchestrator ~/.config/pako-telegram/commands/
```

To make plain (non-slash) messages route to the orchestrator with no `/o` prefix, add to
`~/.config/pako-telegram/config.yaml`:

```yaml
default_command: o
```

## Dependencies

The commands shell out to the skill's scripts under
`$HOME/.claude/skills/orchestrating-agterm-agents/scripts/` (`inject.sh`, `oa.sh`) and expect the
orchestrator to have registered its session (`register.sh`). Requires `agtermctl` and `jq` on the Mac
running the bot.

> `default_command` routing is a pako-telegram feature added in
> [rashpile/pako-telegram#3](https://github.com/rashpile/pako-telegram/pull/3).
