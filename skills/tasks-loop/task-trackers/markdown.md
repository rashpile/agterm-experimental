# Tracker — markdown files

> ⚠️ **Experimental — never run end to end.** It exists to keep the tracker seam honest, not
> because it works. Use `gitea` for real work.
>
> **Known gap:** the standard workflow's `done`+`pending` ready-to-ship freeze has no
> representation here — a file cannot be in two folders at once, so a task reaching that state
> either loses its `done` or never freezes. Fixing it means making freeze a frontmatter field
> rather than the `pending/` folder. Until then this tracker cannot complete a review cycle.

Tasks are markdown files in a folder tree; **a phase is the folder** the file sits in. No
server, no CLI — everything is `git` and the filesystem.

**Status instruction:** `markdown-status.md`, beside this file.

## Install

Ask the user for the tasks root (default `./tasks`) and write `tasks-dir: <path>` into the
config frontmatter. Create one folder per phase the workflow defines, and commit them.

## Layout

```
tasks/
  pending/  discuss/  implement/  review/  done/
    add-auth.md
```

A task file:

```markdown
---
owner:            # set while in flight, cleared on completion
session:          # the driver session name, while in flight
branch:           # set by the implement worker
---
# Add auth

<body>

## <author> — <ISO date>
<comment>
```

The filename without `.md` is both the task **id** and its **slug**.

## Read

| Need | Command |
|---|---|
| List open tasks | `ls tasks/*/*.md` — the parent folder is the phase |
| One task in full | `cat tasks/<phase>/<id>.md` — body **and** every `## author — date` section |
| Its work | `git log --oneline <branch>` and `git diff <default>...<branch>` |

The `##` sections are the thread. Read them: they are where the user answered the task's open
questions.

## Phase

- Set: `git mv tasks/<old>/<id>.md tasks/<new>/<id>.md` and commit.
- In-flight marker: the `owner:` and `session:` frontmatter fields — set on dispatch, cleared
  on completion. A file in a phase folder with no `owner:` is free.
- Freeze: the file lives in `tasks/pending/`.
- There is no equivalent of a label description here: a phase this workflow does not define
  is an unknown folder — report it and skip.

## Claim — `owner:` is ownership

- Eligible: no `owner:`, or `owner:` is you.
- Claim on dispatch: set `owner:` and `session:`, commit.
- Someone else's `owner:` → off-limits: no dispatch, no edits. Report "owned by <user>, skipped".

## Write

- Comment: append a `## <author> — <ISO date>` section to the task file and commit.
- There is no PR: the implement worker delivers a **branch**, records it in `branch:`, and
  the review happens on `git diff <default>...<branch>`. Wherever the workflow says "post on
  the PR thread", post a `##` section on the task file instead; wherever it says "merge the
  PR", merge the branch with `--no-ff` after the gate passes.
- A task an agent files is created in `tasks/pending/` and its body ends with
  `_Filed by agent while working on <id>._`

## Verify a deliverable

Never on the worker's word:

| Claim | Check |
|---|---|
| Work done | `git log <branch>` shows the commits |
| Report posted | the `##` section exists in the task file |
| Tests green | run the project's test command yourself, or read the CI log it names |

Missing → the task is not complete: report it and don't move the file.
