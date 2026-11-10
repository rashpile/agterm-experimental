# make-task
Run one development task end to end: brainstorm, plan, plan review, task-by-task execution, revmux review loop, finalize. Inside agterm it shows a live dashboard, and its buttons open the viewer: one page with the tabs Design, Plan, Progress, Review and Stats, updated while the run goes on. The Review tab follows a running revmux round live (stages, agents, findings). Closing the viewer never affects the run. Every question is an `AskUserQuestion` in the terminal, in agterm or not, also in a remote session.

**Usage:** `/make-task [--profile=<name>] [--update] [task text]`. Without `--profile` the profile is `default`.

**Profiles:** a profile is a directory `~/.config/make-task/profiles/<name>/` (`MT_CONFIG_HOME` or `XDG_CONFIG_HOME` move it; its path may not hold a space or a shell metacharacter, since the copies and the skill use it in shell commands unquoted). It holds:
- copies of the companion skills (brainstorm, planning and revmux, with make-task's edits in them); a run reads these copies, not the installed plugins
- which companion runs each phase; `brainstorm` and `review` may be `skip`
- optional `## <phase>` sections in `profile.md` with your own instructions for that phase
- the autonomy: `autonomous` (asks nothing; the worktree and leftovers choices come from `defaults`), `default` (during brainstorm it asks only what needs your knowledge or taste) or `interactive` (asks more, validates the design section by section, and adds the `design`, `plan` and `review-round` gates)

The first run installs `default` and asks one question, the autonomy. `--update` checks the copies against their sources, shows the diffs, merges your own edits (listed under `## Changes` in the profile's `UPDATE.md`) and offers the workflow interview; `--update` without task text stops after that. `--profile=<name>` with a new name installs another profile from the template. Do not copy a profile directory: its copies hold absolute paths, and `mt profile check` refuses the copy.

**Requires:**
- `uv`: `scripts/mt` runs through it (`uv run -q --script` in its shebang)
- for the review: the `revmux` CLI on PATH, `jq`, and the model CLIs (`claude`, `codex`) its review profile names; the copy's `preflight.sh` checks them. Without them the review is skipped and the run ends `review skipped`, never `clean`
- no umputun or revmux plugin: the profile's copies take their place
- optional: agterm for the dashboard, the viewer and the sidebar status; `revdiff` for the Changes button (disabled without it); network access for a companion with a `git:` source (`--update` only); `gh` to file an issue proposal

**What it changes:**
- the first run creates `~/.config/make-task/profiles/default/`; `--update` and the interview edit `profile.md` and `UPDATE.md` there
- run state under `${MT_STATE_HOME:-${XDG_STATE_HOME:-~/.local/state}/make-task}/<repo>-<slug>-<date>` (dashboard, viewer pages, rendered artifacts and the data scripts the viewer reads)
- a feature branch, plus a worktree under `.claude/worktrees/` when you choose one
- in place on a dirty tree: your dirty paths are stashed before execution and restored at the end (also when the run stops); only make-task's own stash entry is touched
- `.revmux/tasks/` is appended to `.git/info/exclude` when it is not ignored
- outside agterm two concurrent runs share the `current-default` pointer, so every `mt` call passes `--run <state_dir>`
- an issue proposal (a workflow change a profile cannot express) runs `gh issue create -R rashpile/agterm-experimental`, only after you confirm the draft

**Upgrading from a version before profiles:**
- user-level prompts and rules in `~/.claude/plugins/data/planning-umputun-cc-thingz/` (`prompts/`, `planning-rules.md`) and `~/.claude/plugins/data/brainstorm-umputun-cc-thingz/` (`brainstorm-rules.md`) are no longer read. `/make-task --update` offers to carry prompt edits into the profile's copy and rules files into `<profile>/data/planning/` and `<profile>/data/brainstorm/`
- project-level `.claude/exec-plan/prompts/`, `.claude/planning-rules.md` and `.claude/brainstorm-rules.md` still apply
- a run started before profiles cannot continue with this version: `mt status` warns; finish it with the old make-task or start a new run

**Third-party code:** `assets/profiles/default/` bundles MIT-licensed copies of Umputun's brainstorm and planning plugins and of the revmux skill. Their licenses are in `assets/profiles/default/LICENSES/`.

**Triggers:** "make-task", "/make-task", "make a task", "run the full dev workflow", "brainstorm, plan, execute and review", "take this from idea to reviewed branch"


Notes for editing the skill (tests, the default template, the profile schema) are in [`CLAUDE.md`](CLAUDE.md).
