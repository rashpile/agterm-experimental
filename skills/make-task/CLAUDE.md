# make-task — notes for editing this skill

Commands below run from the repository root (`agterm-experimental`).

## Bundled paths

`SKILL.md` and `references/*.md` name bundled files by paths relative to the skill directory and say so in a
resolution line ("Paths below are relative to this skill's directory …"). Keep that line near the first
bundled path. Do not use `${CLAUDE_SKILL_DIR}` or a hardcoded `~/.claude/skills/make-task/…` path in them:
the skill is used through a `~/.claude/skills/make-task` symlink, where `${CLAUDE_SKILL_DIR}` expands to
nothing.

## Tests

Run: `uv run --with pytest --with markdown-it-py --with mdit-py-plugins --with pyyaml pytest skills/make-task/tests -q`

Tests often run inside agterm, so they must never reach the live terminal. An autouse fixture in
`tests/conftest.py` removes every `AGTERM_*` variable, puts a fake `agtermctl` (`tests/fake_agtermctl.py`)
first on `PATH` and redirects state to `tmp_path`. The fake logs its argv and returns canned replies per
subcommand; it can also sleep or fail. A test that needs agterm sets the variables itself.

The same fixture isolates profiles: `MT_CONFIG_HOME` points under `tmp_path` (`XDG_CONFIG_HOME` is dropped),
`MT.PROFILE_TEMPLATES` too, and a stub `revmux` is on `PATH`. `mt init` requires `--profile-dir`: use conftest's
`init()`/`init_args()`, which pass `isolate.profile_dir` (a stub profile outside `MT_CONFIG_HOME`), and the
`profile_factory` fixture for a profile under `MT_CONFIG_HOME`. A test that needs the shipped template sets
`MT.PROFILE_TEMPLATES = MT.ASSETS / "profiles"` and only reads it; copy it under `tmp_path` before changing it.

## Profiles and the default template

A user's profile lives in `~/.config/make-task/profiles/<name>/` (`MT_CONFIG_HOME` or `XDG_CONFIG_HOME` move
it). The shipped template is `skills/make-task/assets/profiles/default`. Its `profile.md` says
`template: true`, so its copies keep the raw `${CLAUDE_PLUGIN_ROOT}`-style tokens: `mt profile install-default`
rewrites them per user profile and `mt init` refuses the template. Maintain it with the same `mt profile`
commands plus `--dir <template>` (`import`, `outdated`, `fetch`, `accept`, `check`), not by copying plugin files in.
make-task's own edits to the copies are listed in the template's `UPDATE.md` under `## Changes`, one line per
edit; the tests check that it names exactly the files where `companions/<id>` differs from `upstream/<id>`.
Setup, the `--update` flow and the template steps are in `references/profiles.md`.

Rules for the template that the tests enforce or that keep it working:
- Example: `uv run -q --script skills/make-task/scripts/mt profile check --dir skills/make-task/assets/profiles/default`; the same form for `outdated`, `fetch <id>`, `accept <id> --expect <tree>` and `import`.
- Never edit `upstream/`: it is the merge base. Edit `companions/<id>/` only.
- Each make-task edit to a copy gets a `## Changes` line and a `TEMPLATE_EDITS` row in `tests/test_profile.py`.
- A copy never names an `mt` command (`test_template_companions_name_no_mt_command`). Text that names an `mt` command, a git state change or a run-state key stays in `SKILL.md`, and so do rules that protect the user's data (the revmux project-profile and archive-housekeeping rules): a user's replaced or merged copy may lack them.
- revmux non-negotiables 1, 3, 4, 5 and 6 in the copy stay byte-identical to upstream's (`test_template_revmux_copy_keeps_the_safety_non_negotiables`).
- The template holds plugin-shaped dirs (`skills/`, `commands/`, `agents/`, `.claude-plugin/plugin.json`). Claude Code does not register them only because they sit under `skills/make-task/assets/`, below the one level it scans in a skills dir. Never add a `.claude/` dir there or move the template higher (`test_shipped_template_holds_no_claude_dir`).
- The raw `${CLAUDE_PLUGIN_ROOT}`/`${CLAUDE_SKILL_DIR}` tokens in the template are a deliberate exception to "Bundled paths" above: `install-default` rewrites them. Do not "fix" them.
- An unaccepted `fetch` leaves `<template>/.staging/`, which is not gitignored. Delete it before committing.
- When `SKILL.md` starts to rely on text that only the template's copies carry, bump `PROFILE_SCHEMA` in `scripts/mt` and the template's `schema`, instead of letting runs silently miss the rule. `check` (so every run) then stops a profile with an older `schema` and names `/make-task --update`: its update flow takes the template's companion updates, then runs `mt profile raise-schema`, which raises the profile's `schema` only once every `make-task:default/<id>` companion records the template's current version. The user keeps `## Changes`, `## Preferences` and the `## <phase>` sections; never tell them to reinstall.
