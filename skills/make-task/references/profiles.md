# make-task profiles: setup, interview and updates

Read this file when SKILL.md Start step 0 sends you here: `--profile=<name>` names a profile that does not
exist yet, or `--update` is given. It also covers the template make-task ships, for its maintainers.
SKILL.md stays the rulebook (how to call `mt`, the phases, the Question rule, autonomy, gates, errors).
This file only adds the setup steps.

Paths below are relative to this skill's directory, the one containing `SKILL.md` (the parent of this
`references/` directory). Resolve them against it, not the current working directory or this file's
directory.

`mt` is the same command as in SKILL.md: `uv run -q --script <skill dir>/scripts/mt`. Every call in this
file is an `mt profile` command. It runs before `mt init`, never takes `--run`, prints one JSON line and
exits 1 with a message on failure; a failure is SKILL.md's Errors row "another `mt profile` command
fails". `<name>` is the profile's name and `<profile_dir>` is the `dir` that `mt profile resolve` printed.
install-default, import, fetch, accept and raise-schema hold the profile's lock while they change it, so two
sessions never interleave them; one that waits too long for another session's fails with `another make-task
command is changing profile <name>` and changes nothing: run it once more (that Errors row says so too).

No run exists here, so there is no dashboard. Every question is an `AskUserQuestion`, one at a
time, with the recommended option first. A question that `## Preferences` already answers is not asked.

## What a profile holds

```
<profile_dir>/
├── profile.md        frontmatter: schema, template, autonomy, gates, defaults, phases; body: ## <phase> sections
├── UPDATE.md         frontmatter: rewritten_for, each companion's source and version; body: ## Preferences, ## Changes, ## Wanted
├── LICENSES/<id>     the companions' licenses
├── companions/<id>/  the copies a run reads and runs
├── upstream/<id>/    the source as last imported or accepted, rewritten like the copy: the merge base
└── data/<id>/        the companion's data dir (what ${CLAUDE_PLUGIN_DATA} meant), empty at first
```

The install wrote absolute paths into the copies, for this directory: `rewritten_for` in `UPDATE.md`
records which one. A profile copied or moved elsewhere would still run the files of the old directory, so
`mt profile check` (and `mt init`) fail on it. Make a second profile with
`mt profile install-default --profile <name>`, never by copying a profile's directory.

Those paths go into shell commands unquoted, in the copies and in SKILL.md, so the profile's path must be
one plain shell word: no whitespace and no character the shell treats as special (quotes, `$`, `&`, `|`,
`;`, `<`, `>`, parentheses, brackets, braces, `*`, `?`, `!`, `#`, `~`, `\`, `^`). `install-default`,
`import` and `fetch` refuse any other target, and `check` reports it as an error, naming the characters.
Tell the user to set `MT_CONFIG_HOME` to a directory without them, then start again. The same rule holds
for the paths in the `phases` map below (`check` names the field: rename the file or dir and the path) and
for a source's skill dir when its files name that dir (`import` and `fetch` refuse such a source).

The `phases` map in `profile.md` names a companion for each phase and paths inside `companions/<id>/`:

| phase | fields | optional | may be `skip` |
|---|---|---|---|
| `brainstorm` | `companion`, `entry` | — | yes |
| `plan` | `companion`, `entry` | `reviewer` | no |
| `exec` | `companion`, `scripts`, `rules` | — | no |
| `review` | `companion`, `entry`, `scripts` | `requires` | yes |

`requires` (review only) lists commands that must be on PATH. A missing one is a `check` warning, and
SKILL.md Start step 3 counts it as a failed preflight, so the run asks the preflight question. Finalize is not
in the map: it always runs with the exec companion. `autonomy` is `autonomous`, `default` or
`interactive`; `gates`, when present, replaces the preset's gates; `defaults.mode` and
`defaults.leftovers` answer the worktree and leftovers questions under `autonomous` (SKILL.md Question rule).

The body of `UPDATE.md` has three sections:

- `## Preferences`: the user's standing answers about the workflow and its updates, one line each, for
  example "take brainstorm updates without showing the diff". Read them before every interview and update.
- `## Changes`: the user's own edits to the copies, one line per edit under `### <id>`, in the form
  ``- `<path>` <where>: <what>. <why>.`` The update flow merges by these lines. make-task's own edits to the
  upstream skills are listed in the template's `assets/profiles/default/UPDATE.md`. The user's copies
  already contain them, and they never appear in the user's `## Changes`.
- `## Wanted`: what the user asked for that a profile cannot express and that was not filed as an issue
  (Issue proposal).

Each companion's `source` in the `UPDATE.md` frontmatter:

| source | where the files come from | when an update shows |
|---|---|---|
| `make-task:default/<id>` | the template, `assets/profiles/default/companions/<id>` | a make-task release changed it; `check` sees it |
| `plugin:<name>@<marketplace>[:<subpath>]` | the installed plugin, from `installed_plugins.json` | Claude Code installed another version; `check` sees it |
| `git:<url>#<ref>[:<subpath>]` | a shallow clone of `<ref>` | `<ref>` moved; only `outdated --network` looks |
| `local` | files the user wrote in `companions/<id>/` | never |

## First use

SKILL.md Start step 0 does it without this file: `mt profile install-default`, one question for the
autonomy, and a line telling the user that `/make-task --update` changes the profile later. The `default`
profile then runs as shipped until the user runs the interview below.

## New profile

`--profile=<name>` was given and `mt profile resolve --profile <name>` printed `status: missing`.

1. Ask whether to create the profile from make-task's default template: `create` (recommended) or `stop`.
   A mistyped name would otherwise become a stray profile. `stop` ends make-task with no run.
2. `mt profile install-default --profile <name>`. It installs the template's copies, which carry
   make-task's edits, with their paths rewritten for `<profile_dir>`, and records each companion's source
   as `make-task:default/<id>`.
3. The interview, from its first question.
4. `--update` given too → the Update flow, which goes straight to its last step. Otherwise continue at
   SKILL.md Start step 0.4 (`mt profile check`).

## The interview

It runs for a new profile, and at the end of the update flow when the user wants it. Start with
`mt profile status --profile <name>` (autonomy, gates, phases, and each companion's source, version and
`modified`) and the `## Preferences` and `## Changes` of `<profile_dir>/UPDATE.md`, so every question can
say what is set now. Write each answer before the next question. Change `profile.md` with the Edit tool,
one line or one map entry at a time, never by rewriting the whole frontmatter.

1. **Autonomy.** `autonomous`, `default` or `interactive`, with SKILL.md's preset table in the question
   and the current value recommended. Write the answer into the `autonomy:` line.
   - `autonomous` answers the worktree and leftovers questions from `defaults`. Show `defaults.mode` and
     `defaults.leftovers` and ask whether to keep them.
   - Gates only when the user wants other gates than the preset's: write `gates: [<gate>, ...]` with
     `design`, `plan` and `review-round` to pick from. `gates: []` turns them off; no `gates:` line means
     the preset's.
2. **Phases to change.** One multi-select question: `brainstorm`, `plan`, `exec`, `review`. None picked →
   step 4.
3. **Each picked phase**, in that order: `keep`, `replace`, `skip` (brainstorm and review only) or
   `instructions`.
   - `keep`: nothing changes.
   - `instructions`: the free text becomes the profile's `## <phase>` section in the body of
     `profile.md` (`## brainstorm`, `## plan`, `## exec`, `## review`), added or replacing the one there.
     It wins over the copy's text and gives way to SKILL.md (SKILL.md Precedence). The `## exec` section
     goes into every task prompt.
   - `skip`: write `<phase>: skip` in the `phases` map. Say what it means first. Without brainstorm the
     task text is the design. Without review no review round runs and the run ends `review skipped`,
     never `clean`; make-task records that as an assumption and asks no question about it.
   - `replace`: Replacing a phase, below.
4. **Preferences.** Ask whether make-task should remember anything for later updates and interviews
   (free text; "nothing" is the recommended option). Write each answer as one line under `## Preferences`.
5. **Check.** `mt profile check --profile <name>`. `errors` → fix each (the message names the field or the
   file), ask the user only when a fix needs their choice, and check again until `ok` is true. Except
   `schema … is older than this make-task reads`: only the Update flow's Schema step raises it, never an
   edit; report it. `warnings` → tell the user, one line each.

A wish the profile cannot express goes to the Issue proposal, at any point of the interview.

## Replacing a phase

The new companion becomes a copy inside the profile, like the shipped ones.

1. **Source.** Ask where the skill comes from: an installed plugin (`plugin:<name>@<marketplace>`, with
   `:<subpath>` when the skill is one directory of the plugin), a git repository
   (`git:<url>#<ref>[:<subpath>]`), or files the user has (`local`). Pick a new companion id, usually the
   skill's name (letters, digits, `.`, `_` and `-`, starting with a letter or digit). Keep the old
   companion: `planning` serves both plan and exec, an unused companion costs nothing, and switching back
   is one `profile.md` edit.
2. **Import.** `mt profile import <id> --source <S> --profile <name>`. For `local`, copy the user's files
   into `<profile_dir>/companions/<id>/` first, then `mt profile import <id> --source local --profile <name>`
   (a `local` companion is never updated). When the source has a license file, copy it to
   `<profile_dir>/LICENSES/<id>`.
3. **Point the phase at it.** Edit the phase's entry in the `phases` map: `companion: <id>` and the fields
   from the table above, as paths inside `companions/<id>/`. Exec and review must meet their contracts
   (below).
4. **Adapt the copy.** A third-party skill was written to run on its own. Read its entry and the files it
   points to, and change the copy (`companions/<id>/`, never `upstream/<id>/`) wherever it would break a
   run:
   - a question to the user → the question goes to the orchestrator's question handling (make-task
     decides it, records an assumption or asks it as one of its own questions), never straight to
     `AskUserQuestion`;
   - a menu or a "what next" offer at the end → not shown; the result goes back to the orchestrator;
   - a commit, branch, stash, push or worktree step → not done: SKILL.md owns every git state change;
   - a command of make-task's own `mt` → removed: the copies never name one;
   - what it reads and writes → the phase's inputs and outputs (next section).

   Change only what conflicts, line by line, so a later upstream change still merges. The template's
   `assets/profiles/default/UPDATE.md` `## Changes` shows how make-task adapted brainstorm, planning and
   revmux. Then show the diff, `git diff --no-index <profile_dir>/upstream/<id> <profile_dir>/companions/<id>`,
   and ask `approve` or `revise` (free text says what to change). Each approved edit gets one line under
   `### <id>` in `## Changes`.

The interview's last step checks the profile.

## Phase inputs and outputs

What SKILL.md hands each companion and what it expects back. A replacement must work with these.

| phase | gets | gives back |
|---|---|---|
| `brainstorm` | the task text and the repository at `<toplevel>` | the design, which SKILL.md writes to `<state_dir>/design.md` |
| `plan` | the path of `design.md`, the assumptions and decisions so far, the directory `<toplevel>/docs/plans/` | one plan file there, with `### Task N:` (or `### Iteration N:`) sections of `- [ ]` checkboxes: the exec loop runs one section per subagent and trusts only the checkboxes |
| plan `reviewer` | the plan's path and the design's | findings only; it runs as Claude Code's read-only `Plan` agent type with the reviewer file's frontmatter `model` and changes no files |
| `exec` | the plan, through the exec contract | the task commits and the progress file |
| `review` | the range `<review_base>..HEAD` in `<toplevel>`, the main checkout, the revmux tasks dir | review rounds; SKILL.md launches the `revmux` CLI itself and reads its JSON report, so a review companion is a revmux skill (another version or a fork) |

### Exec contract

There is no exec entry file. SKILL.md's Exec, Finalize and Stats sections are the playbook, and they call
the exec companion's scripts and prompts directly. `mt profile check` fails a profile whose exec companion
lacks one of them.

- `scripts` holds `create-branch.sh` (`--print-name <plan>` prints the branch name for a plan),
  `init-progress.sh` (`<progress file> <plan> <branch>`), `append-progress.sh` (`<progress file> [message]`,
  stdin when there is no message), `move-plan.sh` (`<plan>`), `resolve-file.sh`
  (`<relative path> <exec_data>`, prints the file) and `detect-branch.sh` (prints the default branch on its
  last line).
- `<exec_scripts>/../references/prompts/` holds `task.md`, `fixer.md`, `finalizer.md` and `stats.md`. Each
  wraps one fenced prompt that uses the placeholders SKILL.md substitutes (`PLAN_FILE_PATH`,
  `PROGRESS_FILE_PATH`, `DEFAULT_BRANCH`, `USER_RULES`, `FINDINGS_LIST`).
- `rules` is a script that prints the custom rules: `bash <exec_rules> planning-rules.md <exec_data>`.

### Review contract

- `entry` is the revmux skill's `SKILL.md`. Review follows it for the round brief and the loop rules.
- `scripts` holds `preflight.sh` (exit 0 when revmux can run; an optional revmux profile argument checks
  that profile's agents) and `task-state.sh`.
- `requires: [revmux]` names the CLI. Without it on PATH the run counts the preflight as failed and asks
  the preflight question (the bundled `preflight.sh` fails on a missing `revmux` too).

### Where the exec prompts come from

`resolve-file.sh` prints the first of: the project's `.claude/exec-plan/prompts/<name>.md`,
`<profile_dir>/data/<exec companion>/prompts/<name>.md`, and the exec copy's own
`skills/exec/references/prompts/<name>.md`. The data dir starts empty, so a run uses the copy's prompts
unless the project overrides them. Custom rules resolve the same way, with the project's
`.claude/planning-rules.md` first.

This is a deliberate change. Before profiles, make-task read the user-level overrides in the plugins'
data dirs: `~/.claude/plugins/data/planning-umputun-cc-thingz/` (`prompts/<name>.md`, `planning-rules.md`)
and `~/.claude/plugins/data/brainstorm-umputun-cc-thingz/` (`brainstorm-rules.md`). In the copies,
`${CLAUDE_PLUGIN_DATA}` now means `<profile_dir>/data/<id>/`, which starts empty, so make-task no longer
reads them. Look in both dirs in the update flow (step 1), when the user changes exec or brainstorm in the
interview, and when the user asks about prompts or rules:

- a prompt that differs from the copy's prompt of the same name → tell the user and offer to carry the
  difference into the copy: an edit to `companions/<id>/skills/exec/references/prompts/<name>.md` with its
  `## Changes` line. Prompt changes belong in the copy, not in `data/`: the update flow merges the copy and
  nothing ever merges `data/`.
- a rules file → offer to copy it to `<profile_dir>/data/planning/planning-rules.md` or
  `<profile_dir>/data/brainstorm/brainstorm-rules.md`. Rules are the user's data, not an edit to the
  copy, so they go to `data/` as they are.

Project-level `.claude/exec-plan/prompts/`, `.claude/planning-rules.md` and `.claude/brainstorm-rules.md`
still apply as before.

## Update flow

`--update` was given. The profile exists: SKILL.md Start step 0 installed `default` on first use, or the
New profile steps ran. When the New profile steps ran just now, the copies are fresh and the interview is
done: go straight to step 7.

A run reads the copies in place, so an `accept` or `import --replace` changes what a make-task run of this
profile going on in another session reads from then on. Tell the user once, before step 3, to take updates
between runs.

1. Read `## Preferences` and `## Changes` in `<profile_dir>/UPDATE.md`, and look in the old plugin data
   dirs (Where the exec prompts come from).
2. `mt profile outdated --network --profile <name>` prints `updates`, `current`, `skipped` and `errors`.
   Each `errors` entry (a plugin no longer installed, a git source out of reach) → one line to the user;
   that companion keeps its copy. `skipped` lists the `local` companions. No `updates` → say so and go to
   step 4.
3. Each entry of `updates`, one at a time:
   1. `mt profile fetch <id> --profile <name>` copies the source as it is now to
      `<profile_dir>/.staging/<id>/tree/` and prints `staging` (that dir, `<staging>` below), `diff`
      (`upstream/<id>` → staging), `changed`, `modified` (the copy holds the user's edits) and `tree`, the
      staged tree's hash. Keep `tree`: every accept of this entry passes it as `--expect <tree>`, and accept
      refuses a staging that holds another tree (a fetch in another session replaced the one shown here).
      On that refusal, start the entry again at this step. `changed` false (only the version moved, no file
      did) → `mt profile accept <id> --expect <tree> --profile <name>` without asking (it keeps a modified
      copy as it is), then the next entry.
   2. Show the diff: the files it touches and what changes in each, with the diff itself when it is
      short. For a long one give the summary and the command that shows it all,
      `git diff --no-index <profile_dir>/upstream/<id> <staging>`. Ask `take` or `skip`.
      `skip` → the next entry; the staging stays and the next fetch replaces it.
   3. `modified` false → `mt profile accept <id> --expect <tree> --profile <name>`: the copy becomes the
      new version.
   4. `modified` true → merge into `companions/<id>` first, keeping every edit `## Changes` lists under
      `### <id>`. File by file: changed only upstream → take the staging file (its mode too); changed only
      in the copy → keep it; changed on both sides →
      `git merge-file <companions file> <upstream file> <staging file>` merges into the copy and marks the
      conflicts, which you resolve by what the `## Changes` line says the edit is for; a file the update
      deletes or renames that one of the user's edits touched → ask. An update that already does what a
      user edit did → drop that edit and its line, with the user's OK. Show the result,
      `git diff --no-index <staging> <profile_dir>/companions/<id>`, which must show only the user's edits,
      and ask `approve` or `revise`. Then
      `mt profile accept <id> --expect <tree> --profile <name>`: the copy stays as merged, and
      `upstream/<id>` becomes the new version, the base of the next merge.
4. **The template's own changes.** Only for a profile whose companions come from `make-task:default/<id>`:
   the companion updates above never carry the template's `profile.md` or a companion it added. Compare the
   `phases`, `defaults` and `gates` in the frontmatter of `assets/profiles/default/profile.md` with
   `<profile_dir>/profile.md`, and the template's `assets/profiles/default/companions/` ids with the
   profile's. A difference the user did not make (not an interview answer, not in `## Preferences`) →
   show it and ask `take` or `keep`. `take` → Edit that line or map entry of `<profile_dir>/profile.md`
   (the template's paths are the copies' paths), or, for a new companion,
   `mt profile import <id> --source make-task:default/<id> --profile <name>`.
5. **Schema.** `mt profile raise-schema --profile <name>` prints `previous`, `schema` and `raised` (false:
   the schema was current already). A newer make-task can rely on text that only its template's current
   copies carry, so `check` fails a profile whose `schema` is older and no task runs with it. This command
   raises `schema` once every `make-task:default/<id>` companion records the template's current version,
   which steps 3 and 4 bring. Its refusal names the companions still behind (an update skipped in step 3):
   tell the user that no task runs with this profile until they are taken, and ask `take` (recommended:
   step 3 for those entries, then this step again) or `later` (the check below reports the schema). Never
   edit the `schema:` line yourself: it says the copies carry what this make-task relies on.
6. Ask whether to change the workflow too: `no` (recommended) or `yes`. `yes` → The interview, which ends
   with the check. `no` → `mt profile check --profile <name>`, handled like the interview's step 5.
7. Continue at SKILL.md Start step 0.4 (`mt profile check`); with no task text make-task stops after this
   flow instead (Start step 0.3), with no run.

A `make-task:default/<id>` update brings make-task's edits and the upstream fixes together: the template
follows the plugins and holds make-task's edits, and the user's `## Changes` lists only the user's own.

## Issue proposal

A profile chooses the skill for each phase, adapts its copy, sets the autonomy and picks the gates. It
cannot add a phase or reorder the phases, add a question, a gate or a dashboard view, run a review that is not
revmux, or run an exec that does not meet the exec contract. Those need a change to `mt`, the dashboard or
SKILL.md. When the user asks for one:

1. Say that a profile cannot do it, and why.
2. Draft an issue for make-task's repository: a title, and a body with what the user wants, the workflow
   it serves, which part lacks it (`mt`, the dashboard or SKILL.md) and a possible shape. Leave out paths
   from the user's machine and anything from their projects.
3. Show the draft and ask: `file` or `note`.
4. `file` → write the body to a temporary file and run
   `gh issue create -R rashpile/agterm-experimental --title "<title>" --body-file <file>`, then give the user
   the URL it prints. Never file an issue without this answer.
5. `note`, or `gh` missing, not logged in or failing → one line under `## Wanted` in
   `<profile_dir>/UPDATE.md`: the date, what the user wants and why a profile cannot do it. Tell the user
   it was not filed.

## Template maintenance

For make-task's maintainers, working in this repository. The template is `assets/profiles/default`;
`<template>` below is its absolute path. Its `profile.md` says `template: true`, so its copies keep the raw
`${CLAUDE_PLUGIN_ROOT}` tokens, `install-default` rewrites them for each user profile, and `mt init`
refuses to run it. Its `UPDATE.md` records the installed plugins as sources and lists make-task's own
edits under `## Changes`. The commands are the update flow's, with `--dir <template>`:

1. `mt profile outdated --dir <template>`. The plugin sources show an update once Claude Code has the newer
   plugin version installed.
2. `mt profile fetch <id> --dir <template>`, and read the upstream diff. Keep the `tree` it prints.
3. The template's copies always hold make-task's edits, so merge as in update step 3.4, with the
   template's `## Changes` as the guide: every edit listed under `### <id>` must be in the merged copy. An
   edit whose upstream text changed → redo it against the new text and update its line; an edit upstream
   made unnecessary → drop it and its line.
4. `mt profile accept <id> --expect <tree> --dir <template>`.
5. `mt profile check --dir <template>` must print `ok` true with no errors. It scans the copies as an
   install would leave them, so a token a user's profile would fail on (a `${CLAUDE_PLUGIN_DATA:-…}`-style
   form, a `${user_config.…}`) fails here first. Then run the make-task tests
   in `tests/` (the command is in this skill's `CLAUDE.md`): they pin each of make-task's edits and
   check that `## Changes` names exactly the files where `companions/<id>` differs from `upstream/<id>`.
6. A fetch that was not accepted leaves `<template>/.staging/`. Delete it; never commit it.

Users get the new template through `/make-task --update`. Their sources are `make-task:default/<id>`,
versioned by the hash of the template's tree, so `mt profile check` lists the update at their next run.
When SKILL.md starts to rely on text that only the template's copies carry, raise `PROFILE_SCHEMA` in
`scripts/mt` and the template's `schema` together: `check` then stops each older profile and names
`/make-task --update`, whose Schema step raises the profile's `schema` once its copies are current.
