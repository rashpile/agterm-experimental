# Working on tasks-loop

Rules for an agent **developing** this skill. Running it is `../SKILL.md`. If this becomes its
own repo, this file moves to the root unchanged.

## Layout — and who pays for each file

| Path | Read by | When |
|---|---|---|
| `SKILL.md` | orchestrator | **every run** |
| `workflows/<name>.md` | orchestrator | **every run** |
| `task-trackers/<name>.md` | orchestrator | **every run** |
| `workflows/exec.md` | orchestrator | phase 3, when the plan has task checkboxes |
| `workflows/review.md` | review worker | phase 4 |
| `task-trackers/<name>-status.md` | orchestrator | `--status` |
| `task-trackers/gitea-install.md` | installer | install / `--update` |
| `references/install.md` | orchestrator | config missing |
| `references/update.md` | orchestrator | `--update` |
| `references/config.md` | installer | install |
| `CHANGELOG.md` | orchestrator | `--update` |
| `agents/*.txt` | review worker | copied into the project at install |

The first three are loaded on every run of every project. Length there is a real cost; length
in the rest is not. **Cut by load frequency, not by size.**

## Design invariants

Break one of these and the skill stops being this skill.

- **Three-way seam.** Tracker = where tasks live and what state they are in. Driver = how to
  talk to a worker. Workflow = how one task is worked. This skill owns the **loop only**:
  selection, slots, dispatch, supervision, completion.
- **Tracker vocabulary is not workflow vocabulary.** A label or folder is never a phase name;
  a phase is never set on a task.
- **Resolve, don't copy.** Bundled docs are read where they live, so a fix reaches every
  project. Only `agents/*.txt` is copied in — it is meant to be tuned per project. The trade is
  that nothing is pinned; a project that needs a frozen copy points `task-tracker` at its own.
- **Precedence: config body > docs > skill.** Two exceptions: Safety, and who runs a phase.
- **A phase is a unit of dispatch that produces an artifact or a state change.** Anything
  failing that test is not a phase. "Plan Review" and "Test" were both removed by it — the
  first happens inside phase 2, the second is a predicate over the others.
- **Separation of duties.** The reviewer never fixes; the author never judges its own verdict;
  the fixer is whoever has the knowledge.
- **Never invent a tracker command, a driver verb, or a phase.** If no source covers the
  situation, ask.

## Writing rules

- **Docs carry rules; commits carry rationale.** Write the instruction and stop. Measurements,
  the failure modes a rule prevents, why one approach beat another — that goes in the commit
  message. A one-clause "because" is the most a doc should spend.
- **Match the form to the failure.** They are not interchangeable:

  | Failure | Form |
  |---|---|
  | knows the rule, skips it under pressure | prohibition + rationalization table |
  | complies, output is the wrong shape | positive recipe: what the output **is**, in order |
  | omits a required element | a slot in the template they already fill in |
  | behavior should depend on a condition | conditional on an **observable** predicate |

- **No nuance clauses.** "Don't X unless it matters" reopens the negotiation every turn. A real
  exception gets its own conditional, keyed to something observable.
- **Exemption clauses don't scope.** If part of the output must be exempt from a rule,
  restructure so the rule cannot reach it.

## Verifying a change

There is no test suite. A skill is verified by running it against a real backlog —
`/Users/pkoptilin/workspace/repos/agent-driver` has a config and open issues — and reading what
it did afterwards.

**A running orchestrator never picks up an edit.** It read `SKILL.md` at startup, so there is no
hotfix: a change lands on the next `/tasks-loop`.

## Debugging a live run

```bash
docs/inspect-run.py                  # newest session for the cwd
docs/inspect-run.py <session-id>     # that one, found across all projects
```

Transcripts are append-only and readable **while the run is going** —
`~/.claude/projects/<abs-path, non-alphanumerics as dashes>/<session-id>.jsonl`. A snapshot of a
running session looks exactly like a stalled one, so check mtime before reading anything into a
gap.

**Never ask the orchestrator how the run went.** It answers with what it believes. Its narration
is a claim; its tool calls are the evidence, and the two have disagreed.

Read in this order:

1. **User turns** — every one is something the loop did not handle. A turn spent decoding what
   the orchestrator said is a reporting bug, not a question.
2. **The labels on backgrounded waits.** `Resume waiting…`, `Wait for gone only`, `Poll until
   genuinely idle` — an escalating series is the orchestrator narrating a workaround it invented,
   and it names the defect without reading a line of driver code.
3. **Where the context went**, by intent rather than by tool name.

Then classify each finding as exactly one of three. The fixes are not interchangeable, and
misclassifying means patching the skill around a driver bug forever:

| The orchestrator… | Fix in |
|---|---|
| did the wrong thing, and the docs were right | the **skill** |
| did what the docs said, and the docs were wrong | the **doc** |
| was lied to by its channel | a **driver issue** |

**Two traps in the JSON**, both of which produced wrong conclusions before `inspect-run.py`
existed. Thinking text is stripped to a bare signature, so it is derived from `output_tokens`
minus visible content and never read. And `tool_use` inputs are not uniformly
`{command, file_path}` — keying on those renders every `AskUserQuestion` blank.

**Measure before claiming.** Every number in this file came from one of those transcripts.

## Commits and the changelog

- Subject: `tasks-loop: <lowercase, imperative>`. The body carries the reasoning the doc does
  not.
- **A `CHANGELOG.md` entry exists only when an existing project must act.** New wording, better
  reasoning, a clearer example: none of it belongs there. `--update` applies every entry above
  a project's recorded `skill-rev`, so the file is a work list, not a history.
