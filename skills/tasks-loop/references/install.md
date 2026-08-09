# Install — write `.tasks-loop/config.md`

Run this when `./.tasks-loop/config.md` does not exist. **Nothing is copied into the project.**
Bundled tracker and workflow docs are read where they live, so the project stores only its own
config — and picks up skill updates for free. Paths below are relative to this skill's directory.

## 1. Ask, in one `AskUserQuestion` call

| Question | Options |
|---|---|
| Task tracker? | `gitea` · `none` · `markdown` (experimental — say so) · custom |
| Workflow? | `standard` · custom |
| Agent driver? | `bash-claude-agterm` · `go-claude-agterm` · custom |

List the bundled options from what is actually in `task-trackers/` and `workflows/`, not from
what this doc remembers. **`none`** means the project has no backlog: tasks come from the user
in the session, and the loop asks for one instead of listing. Offer it — a project can use the
phases, the review depth and the parallel workers without owning a tracker at all. **Custom** means the project writes its own doc: ask for its path (it
need not exist yet) and tell the user what it has to answer — for a tracker: list, read body
**and** thread, claim, comment, set phase, verify a deliverable; for a workflow: the phases,
their transitions, and what each kind of worker is told.

## 2. Tracker follow-ups

Read the chosen tracker doc's `## Install` section and follow it — it asks its own questions
and adds its own keys — the markdown tracker needs a tasks root; the **gitea tracker checks
`tea` is logged in and offers to create its default labels in the repo**. It runs here and only
here; a run skips that section.

## 3. Driver

Find it, in this order — only ask when the first two miss:

1. **On `PATH`** (`command -v <driver-exe>`) → use the bare command.
2. **Already at `~/.config/agent-driver/<driver>/`** → use it. That is a git checkout;
   `git pull` there is how it updates later.
3. **Neither** → ask the user: a path they already have on this machine, or download it.

**Download** — one repo, one subdirectory per driver. Offer `~/.config/agent-driver` as the
location and let them override it:

```bash
git clone https://gitea.rashpile.net/pkoptilin/agent-driver.git ~/.config/agent-driver
```

The bash driver is the executable itself (`bash-claude-agterm/claude-agterm`); a driver that
needs building says so in its own README — follow it, then check the binary exists.

Set `agent-driver` (the name) and `agent-driver-cmd` (the executable, **with no verb
appended**): `~/.config/agent-driver/bash-claude-agterm/claude-agterm`. Write the `~` form, not
an expanded `/Users/<you>/…` — the tilde survives being committed and read on another machine.

Verify before continuing: `<agent-driver-cmd> skill` prints its contract and exits 0. A driver
that cannot describe itself is unusable — stop and say so rather than improvising another way
to spawn agents.

## 4. Phases — always ask, always write

**Multi-select:** which of the workflow's phases does this project run? List them from the
chosen workflow doc's phase table (the standard one: Brainstorm · Plan · Plan Review · Execute
· Code Review · Test · Push · Deploy), all pre-selected. Ask too whether they want a custom
phase inserted anywhere.

Write a `# Workflow` section into the config with one `## <n>. <Phase>` heading per selected
phase. A skipped phase gets **no heading** — that is how a run knows it is off. Leave a
heading's body empty when the project has no detail yet; the workflow's own description
applies until someone fills it in.

Three follow-ups worth prompting for while you are here:

- **`## 5. Code Review`** — how deep, if that phase is on:

  | Option | Review phases | |
  |---|---|---|
  | **full** | 1 internal + 2 external + 3 final | with an external codex review |
  | **standard** | 1 internal + 3 final | no external model |
  | **simple** | 3 final only | one pass and the gate |

  Record the chosen word as the body of the heading. `full` needs the `codex` CLI on this
  machine — check, and fall back to `standard` if it is missing rather than configuring a leg
  that cannot run.
- **`## 6. Test`** — the project's typecheck/lint/test commands.
- **`## 8. Deploy`** — how it ships. **No heading → the loop merges and closes without
  deploying**, and reports that. Never write an empty `## 8. Deploy`: it reads as configured.

## 5. Review agents

Copy this skill's `agents/*.txt` → `.tasks-loop/agents/`. These are the review-leg prompts; one
file per reviewer, filename = its name. They are copied rather than resolved because they are
meant to be tuned per project. Tell the user they can edit, add, or delete any of them.

## 6. Write the config

`.tasks-loop/config.md`, from `references/config.md`: the frontmatter gathered above, then the
`# Workflow` section from step 4, then `# Notes` if the project has any.

**Migrating from tasks-loop v1:** an existing `.claude/tasks-loop.md` already holds most of
this — its `implementation` section is a phase list in prose. Split it across the phase
headings (brainstorm/plan/plan-checkpoint → `## 1.`–`## 3.`, verification commands →
`## 6. Test`, `deploy` → `## 8. Deploy`), then delete the old file.

## 7. Finish

Commit `.tasks-loop/` — it is project configuration. Show the user the finished config, then
resume at Startup step 1.
