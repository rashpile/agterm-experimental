# Install — write `.tasks-loop/config.md`

Run this when `./.tasks-loop/config.md` does not exist. **Nothing is copied into the project**
except the review agents: bundled tracker and workflow docs are read where they live, so the
project stores only its own config and picks up skill updates for free. Paths below are
relative to this skill's directory.

## 1. Discover — exactly this much

Look at these, and stop:

| Check | What it decides |
|---|---|
| `ls task-trackers/` and `ls workflows/` | the real option list, not what this doc remembers |
| the project's git remotes; whether the tracker CLI is logged in and has tasks | grounds the tracker recommendation with evidence |
| `command -v <driver-exe>`, then `~/.config/agent-driver/<driver>/` | where the driver already is (step 3) |
| `codex` on `PATH` | whether `full` code review can be offered at all |
| an existing `.claude/tasks-loop.md` | a v1 config to migrate |

**Do not read a tracker or workflow doc yet.** They are read once chosen; reading all of them
buys context for options the user is about to reject.

## 2. Ask — two rounds, batched

**Round one**, in one `AskUserQuestion`:

| Question | Options |
|---|---|
| Task tracker? | `gitea` · `none` · `markdown` (experimental — say so) · custom |
| Workflow? | `standard` · custom |
| Agent driver + where it lives? | see step 3 |

Ground every option in what step 1 found — "9 open issues, `implement`/`review` labels already
exist" beats "the Gitea tracker". **`none`** means the project has no backlog: tasks come from
the user in the session and the loop asks for one instead of listing. Offer it — a project can
use the phases, the review depth and the parallel workers without owning a tracker at all.
**custom** means the project writes its own doc: ask for its path (it need not exist yet) and
say what it must answer — for a tracker: list, read body **and** thread, claim, comment, set
state, verify a deliverable; for a workflow: the phases, their transitions, and what each kind
of worker is told.

**Now read the chosen tracker and workflow docs.** You need the workflow's phase table for:

**Round two**, one `AskUserQuestion`:

- **Phases** (multi-select, all pre-selected) — the phase table from the chosen workflow doc.
  Ask too whether they want a custom phase inserted anywhere.
- **Code review depth**, if that phase is on:

  | Option | Review legs | |
  |---|---|---|
  | **full** | 1 internal + 2 external + 3 final | needs the `codex` CLI |
  | **standard** | 1 internal + 3 final | no external model |
  | **simple** | 3 final only | one pass and the gate |

  `codex` missing from step 1 → don't offer `full`; say why. Configuring a leg that cannot run
  is worse than not offering it.
- **Checks** — the project's typecheck / lint / test commands, for the `# Checks` section. Not a
  phase: a predicate the author runs before committing, the review gate requires, and the ship
  worker re-verifies before merging. Read the project's README or Makefile first and propose
  what you found rather than asking cold.
- **Deploy** — how this project ships, if it does. **No answer means no `## 6. Deploy` heading**,
  and the loop merges and closes without deploying, and says so. Never write the heading empty.

## 3. Driver

From step 1, in this order — only ask when both miss:

1. **On `PATH`** → use the bare command.
2. **Already at `~/.config/agent-driver/<driver>/`** → use it. It is a git checkout; `git pull`
   there is how it updates later.
3. **Neither** → offer to clone, default location `~/.config/agent-driver`, overridable:

```bash
git clone https://gitea.rashpile.net/pkoptilin/agent-driver.git ~/.config/agent-driver
```

One subdirectory per driver. The bash driver is the executable itself; one that needs building
says so in its README — follow it, then confirm the binary exists.

**Never point at a copy the project itself can break.** If this repo *is* the driver's source,
the loop still runs the clone: a WIP break in the working tree would otherwise take the loop
down with it, and workers run in worktrees where that relative path means something else.

Set `agent-driver` (the name) and `agent-driver-cmd` (the executable, **no verb appended**):
`~/.config/agent-driver/bash-claude-agterm/claude-agterm`. Write the `~` form, not an expanded
`/Users/<you>/…` — the tilde survives being committed and read on another machine.

Verify against the copy you configured, not the one you found first:
`<agent-driver-cmd> skill` prints its contract and exits 0. A driver that cannot describe
itself is unusable — stop and say so rather than improvising another way to spawn agents.

## 4. Tracker follow-ups

Follow the chosen tracker doc's `## Install` section — it asks its own questions and adds its
own keys. The markdown tracker needs a tasks root; the **gitea tracker checks `tea` is logged in
and offers to create its default labels**. It runs here and only here; a run skips that section.

## 5. Review agents

Copy this skill's `agents/*.txt` → `.tasks-loop/agents/`. These are the review-leg prompts, one
file per reviewer, filename = its name. They are copied rather than resolved because they are
meant to be tuned per project. Tell the user they can edit, add or delete any of them.

## 6. Write the config

`.tasks-loop/config.md` from `references/config.md`: the frontmatter, then a `# Workflow`
section with one `## <n>. <Phase>` heading per selected phase, then `# Checks`, then `# Notes`.

- **A skipped phase gets no heading** — absence is the off switch.
- **Never write an empty `## 6. Deploy`**: it reads as configured when it isn't. Omit it and the
  loop merges and closes without deploying, and reports that.
- A heading with an empty body is fine — the workflow's own description applies until someone
  fills it in.
- Put in `# Notes` what a worker would otherwise have to rediscover: which files must change
  together, what is gitignored, anything that must never run unattended.

**Migrating from tasks-loop v1:** an existing `.claude/tasks-loop.md` already holds most of
this — its `implementation` section is a phase list in prose. Split it across the phase headings
(brainstorm/plan/plan-checkpoint → `## 1.` and `## 2.`, verification commands → `# Checks`,
`deploy` → `## 6. Deploy`), then delete the old file.

## 7. Finish

Commit `.tasks-loop/` — it is project configuration. Show the user the finished config, then
resume at Startup step 1.
