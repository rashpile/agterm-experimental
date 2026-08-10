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

There is no test suite. A skill is verified by running it.

- **Run it against a real backlog.** `/Users/pkoptilin/workspace/repos/agent-driver` has a
  config and open issues.
- **Then read the transcript**, don't ask the orchestrator how it went:
  `~/.claude/projects/<cwd-slug>/<session-id>.jsonl`. Attribute context growth by tool and by
  intent before proposing a fix.
- **Thinking text is stripped from the transcript** — only a signature survives. Measure it as
  `output_tokens` minus visible text and tool inputs, never by reading.
- **Measure before claiming.** Every rule here that cites a number came from one of those
  transcripts.

## Commits and the changelog

- Subject: `tasks-loop: <lowercase, imperative>`. The body carries the reasoning the doc does
  not.
- **A `CHANGELOG.md` entry exists only when an existing project must act.** New wording, better
  reasoning, a clearer example: none of it belongs there. `--update` applies every entry above
  a project's recorded `skill-rev`, so the file is a work list, not a history.
