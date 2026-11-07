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

The pages' scripts run in node with a stub DOM: `test_viewer.py` for `assets/viewer.html` (`run_viewer`, its
steps listed in its docstring: inject a `state.js` or `artifacts/<id>.js` text, click, scroll, fail a script
load, set the clock, fire the timers, snapshot) and `test_agterm.py` for `assets/dashboard.html` (`run_page`,
clicks and a `reject` message that makes every `agterm.request` fail). Both skip when `node` is missing.
Build state and payloads with `MT.state_js`, `MT.script_json` and `MT.render_markdown`, so a test sees what
`mt` writes. A fixture taken from a real run (`tests/fixtures/progress.txt`, `events-round.jsonl`) is trimmed
and has its home paths scrubbed.

## The dashboard, the viewer and their data

- Pages: `assets/dashboard.html` (`mt init` copies it into the run dir), `assets/viewer.html` (`mt init`
  writes it as `viewer-<tab>.html`, one per tab, differing only in `<body data-tab>`; the dashboard's file
  buttons open them) and `assets/doc.html` (the static page `render_artifact` fills for each artifact).
- A javascript page cannot `fetch()` a `file://` URL, so `mt` writes its data as scripts the pages re-inject
  every 1.5 s: `state.js` (`window.mtState(<json>)`) and `artifacts/<id>.js` (`window.mtArtifact("<id>", {...})`).
  `artifact_payload` is the one builder per artifact id and `write_artifact_data` the one writer (new
  `updated` only when the rest changed). `render_changed` and `review start`/`wait`/`result` all go through
  them, so a later call never replaces a payload with another shape. The payload shapes are in `mt`'s
  docstring.
- The review data is rebuilt each time from the state's round entries, each finished round's report and
  each round's `events.jsonl` folded by `fold_events`. Finding bodies and fixes never go into `state.json`.
- The static checks in `test_viewer.py` and `test_agterm.py` hold for every page: no declared `--agterm-*`
  variable (agterm injects them; the pages only read them), no `fetch(`, no external `<script src>`.
- Every string from the state or a payload is escaped; only renderer output (the `html` fields, markdown-it
  with raw HTML off) goes in as HTML.
- Expanded items, scroll positions, the Progress filter and the picked review round live in the page's
  script, keyed by id, never read back from the DOM, so a re-render keeps them. Handle clicks, not
  `toggle` (it does not bubble).
- A run started before a change keeps its copied dashboard and may lack newer files (`viewer-<tab>.html`):
  `mt` must keep working for it.

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
