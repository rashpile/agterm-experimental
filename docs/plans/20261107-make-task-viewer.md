# make-task viewer with a live Review

## Overview
- make-task shows its artifacts in its own viewer instead of pages rendered by the `/view` skill's `view.py`:
  one JavaScript page in the dashboard's theme with tabs Design · Plan · Progress · Review · Stats, updated
  while the run goes on.
- The Review tab shows the running revmux round live (stages, agents, findings), folded by `mt review wait`
  from the round's `events.jsonl`. No revmux change, no `revmux watch`.
- The dashboard keeps its layout: its file buttons open the viewer on their tab, Review shows `● live` while
  a round runs, and a compact status block sits under the running round. Changes stays `revdiff`.
- Closing the viewer never affects the run: it only reads files `mt` writes.
- make-task's floating question cards are removed: in practice they break the flow and they do not work with
  remote sessions. Every question is an `AskUserQuestion` in the terminal.
- Design: `~/.local/state/make-task/agterm-experimental-live-review-view-on-revmux-events-20261106-2347/design.md`.
  Visual reference: `mockup-viewer.html` in the same directory (its "Changes ↗ revdiff" tab is not part of
  the viewer). Use only the dashboard's tokens and primitives.

## Context (from discovery)
- Files involved: `skills/make-task/scripts/mt` (renderer hooks `renderer_path`, `renderer_argv`,
  `render_artifact`, `render_changed`, `register_artifact`; `parse_plan`, `sync_plan`, `cmd_plan`,
  `cmd_refresh`; `cmd_init`; `cmd_review_start`, `cmd_review_wait`, `read_events`, `milestone`,
  `cmd_review_result`; `cmd_ask` and the card helpers), `skills/make-task/assets/dashboard.html` (`openable`, the click handler,
  `roundRows`), `skills/make-task/assets/card.html` (style reference; removed in Task 2), `skills/make-task/tests/`
  (`conftest.py` with the `MT_VIEW` stub, `test_artifacts.py`, `test_state.py`, `test_review.py`,
  `test_plan.py`, `test_ask.py`, `fake_agtermctl.py`, `test_agterm.py` with a node harness, `test_skill_md.py`),
  `skills/make-task/SKILL.md`, `skills/make-task/README.md`, `skills/make-task/CLAUDE.md`,
  `skills/make-task/references/profiles.md`, `docs/make-task.md` (with three card screenshots under
  `docs/images/`), the root `README.md`, the default profile template under
  `skills/make-task/assets/profiles/default/` (`profile.md`, `UPDATE.md`, the brainstorm and revmux copies under
  `companions/`, never `upstream/`), `skills/make-task/tests/test_profile.py` (`TEMPLATE_EDITS`).
- Patterns: `mt` writes `state.js` as `window.mtState(<json>)` and the dashboard re-injects it every 1.5 s (a
  page cannot `fetch()` `file://`); `atomic_write` for every state file; `mt` already renders markdown with
  markdown-it-py (raw HTML off) for cards, which Task 2 removes; colors only from `--agterm-*` variables; `cmd_plan` and
  `cmd_refresh` run `sync_plan` and then `render_changed`.
- Formats checked:
  - revmux `events.jsonl`: one JSON line per event with `kind`, `at`, `agent`, `stage`, `text`, `findings`;
    finding keys `id, file, line, end_line, severity, confidence, title, body, fix, sources, lenses, verdict`;
    kinds in the design;
  - the progress file (written by `append-progress.sh`, which prefixes every line with
    `[YYYY-MM-DD HH:MM:SS] `): header block, `task N: title` entries, `- ` items with nested `  - ` items,
    `[decision] …` and `[deviation] …` (with or without the timestamp, from tasks and from `fixer:`),
    `review round N…: …` (`review round 3 (final): …`, `review round 3 fix (commit …)`), `finalize: …`,
    `completed`;
  - the page bridge's open arguments, captured from `agtermctl`'s socket request: `html`, `cwd`, `javascript`,
    `sizePercent`, `follow`, `chromeless`, `pane`.
- Dependency added: `mdit-py-plugins` (task lists, footnotes, heading ids).
- Before Exec, outside this plan: the uncommitted `revmux watch` edits on `main` are committed to a side
  branch, so the task starts from a clean HEAD.

## Development Approach
- **testing approach**: Regular (code first, then tests in the same task)
- complete each task fully before moving to the next
- make small, focused changes
- **CRITICAL: every task MUST include new/updated tests** for code changes in that task
  - tests are not optional - they are a required part of the checklist
  - write unit tests for new functions/methods
  - write unit tests for modified functions/methods
  - add new test cases for new code paths
  - update existing test cases if behavior changes
  - tests cover both success and error scenarios
- **CRITICAL: all tests must pass before starting next task** - no exceptions
- **CRITICAL: update this plan file when scope changes during implementation**
- **CRITICAL: every task leaves `mt` runnable.** The run that executes this plan uses the same
  `skills/make-task` it edits (in place, symlinked skill).
- run tests after each change, from the repository root:
  `uv run --with pytest --with markdown-it-py --with mdit-py-plugins --with pyyaml pytest skills/make-task/tests -q`
- maintain backward compatibility: a run started before this change keeps working with its copied dashboard

## Testing Strategy
- **unit tests**: pytest for every new function and changed command
- **page tests**: the node harness with a stub DOM, as in `test_agterm.py` (skipped when `node` is missing).
  Expanded and open state lives in JS (a Set keyed by finding id or task n), never read back from the DOM, so
  the stub can test that a re-render keeps it; handle clicks, not `toggle` (it does not bubble)
- **static checks** for `viewer.html` and `doc.html`, like `test_dashboard_declares_no_agterm_variables_and_loads_state_js`:
  no declared `--agterm-*` variable, no `fetch(`, no external `<script src>`
- no e2e suite in this project; the manual check in agterm is listed under Post-Completion

## Progress Tracking
- mark completed items with `[x]` immediately when done
- add newly discovered tasks with ➕ prefix
- document issues/blockers with ⚠️ prefix
- update plan if implementation deviates from original scope
- keep plan in sync with actual work done

## Solution Overview
- `mt` produces data, pages render it. `mt` renders markdown to HTML fragments, folds the plan, the progress
  log and revmux events into data, and writes one script per artifact, `artifacts/<id>.js`
  (`window.mtArtifact("<id>", {...})`), plus a static `artifacts/<id>.html` in the same theme.
- **One payload builder per artifact id**, `artifact_payload(run, state, art)`, is the only thing that writes
  `artifacts/<id>.js`. `render_artifact` (via `render_changed`) and the `review start`/`wait`/`result`
  commands all call it, so a later call never replaces a payload with another shape.
- `assets/viewer.html` loads `state.js` and the current tab's script every 1.5 s and re-renders a tab only
  when its `updated` changed, keeping scroll and expanded items.
- The dashboard opens the viewer through per-tab entry files `viewer-<tab>.html` with `javascript: true`,
  `cwd: <run>`, `sizePercent: 85`, `follow: true`.
- Questions: `AskUserQuestion` with `mt activity "Waiting for your answer · <topic>" --wait` before and
  `mt decide` after; `mt ask` and the card page are gone.

## Technical Details
- `render_markdown(text) -> (html, toc)`: `MarkdownIt("commonmark", {"html": False})` with `table` and
  `strikethrough` enabled, `tasklists_plugin`, `footnote_plugin`, `anchors_plugin` (levels 2-3, ids prefixed
  `doc-` through `slug_func`); `toc` = `[{level, id, text}]`. `render_inline(text)` (markdown-it `renderInline`,
  raw HTML off) for one-line strings such as task titles and checklist items.
- `artifact_payload` by id (every payload has `updated`):
  - `design`, `stats`: `{title, html, toc, source}`;
  - `plan`: `{title, html, toc, source, items: {"<n>": [{text, html, checked}]}}`; the viewer joins `items`
    with `state.tasks` (n, title, status, done, total), which it already loads;
  - `progress`: `{source, header, entries: [{at, task, title, lines: [{at, kind, text, children}]}], raw}`,
    `kind` one of `item`, `decision`, `deviation`, `review`, `text`; a line that matches nothing is kept as
    `text`, never dropped;
    ➕ as built in Task 6: `header` is `{plan, branch, started}` (lowercased keys); `at` is the stamp string
    `YYYY-MM-DD HH:MM:SS` or null; `task` is N for a task entry and null for `fixer: …`/`finalize: …` entries
    (title = the line) and for lines before the first entry (title `""`); a review line opens an entry
    titled `Review` (task null) unless one is open, so the fixer's lines after it are not filed under the
    last task; `children` are the nested `  - ` items, one level; a decision's text keeps its `task N: ` or
    `fixer: ` prefix;
  - `review`: `{rounds: [{round, run, state, verdict, counts, findings, dropped, started, finished, live}]}`,
    rebuilt each time from three sources: the state's round entries; each finished round's report JSON at
    `entry["report"]` (full findings, body capped at 4000 and fix at 2000 characters); `fold_events` over each
    round's `events.jsonl` (`dropped` for every round, `live` for the running one). Bodies and fixes never
    enter `state.json`.
    ➕ as built in Task 8: each round also carries `milestone`, `questions` (the report's open questions,
    capped the same), `not_gating`, `deferred`, `degraded`, `confirming`, `duration`, `events` (the
    `events.jsonl` path) and `log` (`<run>/revmux/round-<n>.log`); `counts` is `{critical, major, minor}`
    from the state; `state` is `done` (a result), `running` (the live round) or `failed` (no result and not
    live: its wait failed, a later round replaced it, or the run ended); `findings` is `[]` for a round
    without a result (the running one's are in `live.findings`); report findings keep the report's order and
    get `fold_finding`'s keys (`agent` blank); a report that cannot be read falls back to the state's
    summaries (blank body and fix). `mt review result` sets the round's `report` to the file it read, so
    the tab shows the findings the result was recorded from.
- `write_artifact_data(run, aid, payload)`: compares the new payload with the written one without `updated`,
  and writes (with a new `updated`) only when the rest changed. ASCII-escaped JSON, `atomic_write`.
  `render_artifact` returns early when its `.html` exists and, for an id that has a payload builder, its `.js` too.
- `fold_events(lines) -> snapshot`, pure: complete lines only; a non-JSON or non-object line skipped; unknown
  kinds ignored; missing fields blank; agents keyed by name and placed under the stage active at
  `agent_started`; states `running`, `done`, `retrying`, `rate-limited`, `degraded`; each finder's latest
  findings replace its list and synthesis's replace them all, with the finder total counted for the
  "13 → 10" line; `dropped` apart; verify `N of M kept` summed; caps: 200 findings, body 4000, fix 2000,
  activity 300 characters, five recent lines per agent; `format_ok` false when complete lines exist but none
  of a known kind.
  ➕ as built in Task 7: `lines` are the file's lines with their `\n` (one without is the line still being
  written); times are epoch seconds (`at` parsed, None when missing). Snapshot keys: `format_ok`, `events`,
  `skipped`, `unknown`, `started`, `updated`, `stage`, `stages` `[{name, started, finished}]`, `agents`
  `[{name, stage, lenses, model, state, started, finished, findings, result, retries, activity, progress,
  recent}]` (`recent` `[{at, kind, text}]`, retry/degrade/rate-limit notes prefixed as revmux prints them),
  `findings` (sorted by severity, then capped; each with the emitting `agent`), `total`, `found`, `merged`,
  `counts`, `dropped`, `dropped_total`, `verify` `{kept, total, agents}`, `activity` `{agent, text, at}`
  (the latest line of any agent, progress included); a done or degraded agent stays so on a later line.
- The caller reads `events.jsonl` whole when it is at most 8 MB; larger, the snapshot is `{"truncated": true}`
  and the Review tab shows the milestone only.
- Live round: only the highest-numbered round, when it has started, has no result, its `milestone` is not
  `failed`, and the run is not finished.
- Round state summary (state entry `live`): stage, agents done/total, counts by severity, latest activity,
  updated; saved only when it changed.
  ➕ as built in Task 8: `{format_ok, stage, stages (names seen, in order), agents_done (done or degraded),
  agents, counts, activity ({agent, text, at} or null), updated (latest event time)}`, `{"truncated": true}`
  past 8 MB; `null` from `mt review start` until the first `wait` that folds the events; a failed round keeps
  its last summary (the dashboard decides live-ness by the rule above).

## What Goes Where
- **Implementation Steps** (`[ ]` checkboxes): code, tests and documentation in this repository
- **Post-Completion** (no checkboxes): the manual check in agterm

## Implementation Steps

### Task 1: Questions through AskUserQuestion in the skill text

**Files:**
- Modify: `skills/make-task/SKILL.md`
- Modify: `skills/make-task/references/profiles.md`
- Modify: `skills/make-task/README.md`
- Modify: `docs/make-task.md`
- Delete: `docs/images/make-task-card-question.png`, `docs/images/make-task-card-leftovers.png`, `docs/images/make-task-card-worktree.png`
- Modify: `README.md`
- Modify: `skills/make-task/assets/profiles/default/profile.md`
- Modify: `skills/make-task/assets/profiles/default/UPDATE.md`
- Modify: `skills/make-task/assets/profiles/default/companions/brainstorm/skills/brainstorm/SKILL.md`
- Modify: `skills/make-task/assets/profiles/default/companions/revmux/SKILL.md`
- Modify: `skills/make-task/assets/profiles/default/companions/revmux/references/loop.md`
- Modify: `skills/make-task/tests/test_skill_md.py`
- Modify: `skills/make-task/tests/test_profile.py`

- [x] `SKILL.md`: the Cards section becomes a Questions section. Every question (brainstorm class 3, the worktree question, gates, error questions, the leftovers question) is an `AskUserQuestion` with the options it had as a card:
  - the question text carries a short context (what is at stake, the recommendation; the worktree question lists the dirty paths); long material is named by its artifact, registered before the question so the viewer shows it: `mt artifact design` before the design gate (and before each interactive design-section question, the design written so far), `mt artifact plan <plan>` before the plan gate, the Review tab for the review-round gate and the leftovers question;
  - pro and con become the option's description; the recommended option comes first, its label ending in "(Recommended)"; there is no delegate option;
  - before it `mt activity "Waiting for your answer · <topic>" --wait`, except for an error question (`mt fail` already shows the error); after it `mt decide "<question>" "<answer>"`, then a normal `mt activity`;
  - a gate's question is its exact title and `mt decide` records `Approve`, `Revise` or the text, so "a gate has passed" stays as written; an "Other" answer that maps to no option asks the same question again;
  - a one-option error question is not asked: the run takes that option and says why
- [x] `SKILL.md`: rename "Leftovers card", "error card", "preflight card", "stop card" and the other card wording to questions in every section (description, How to run `mt`, Precedence, Recovery, Start, Question rule, Gates (drop the "card `phase`" column), Subagent prompts, Brainstorm, Exec, Review, The end of the run, Completion, Errors with its "card options" header, Outside agterm); drop `cards`, `MT_NO_CARDS`, `mt ask` and its exit codes
- [x] `references/profiles.md`, `skills/make-task/README.md`, the template's `profile.md`, `docs/make-task.md` (its "Question cards" section, the card wording, the agterm requirement row, the three card images, which are deleted) and the root `README.md` (the make-task row): drop the cards
- [x] the template copies (only `companions/`, never `upstream/`): "make-task's question cards" and the "card" wording become make-task's questions; the matching `UPDATE.md` `## Changes` lines and `TEMPLATE_EDITS` rows (and the docstrings near them) updated in place; no `PROFILE_SCHEMA` bump
- [x] run `uv run -q --script skills/make-task/scripts/mt profile check --dir skills/make-task/assets/profiles/default`
- [x] update the `test_skill_md.py` tests that read card text: `test_skill_md_names_mt_calls` (no `ask`), `test_set_keys_and_stop_reasons_documented` (the card exit codes), `test_question_rule_names_the_presets_and_gates` (the gate table without the phase column), `test_autonomous_worktree_names_a_branch_it_may_not_hold`, `test_start_step_0…`, `test_brainstorm_and_review_skip`, `test_review_skipped_by_the_profile_shows_no_preflight_card`, `test_plan_reviewer_edit…`, the section order ("Leftovers question"); negative checks move to the new wording
- [x] write tests: `SKILL.md` has no word-boundary "card", no `mt ask`, no `MT_NO_CARDS`; the Questions section names `AskUserQuestion`, `mt activity … --wait`, the error-question exception, `mt decide` and the one-option rule; the design gate comes after `mt artifact design`
- [x] run tests - must pass before task 2

### Task 2: Remove mt ask and the card page

**Files:**
- Modify: `skills/make-task/scripts/mt`
- Delete: `skills/make-task/assets/card.html`
- Delete: `skills/make-task/tests/test_ask.py`
- Modify: `skills/make-task/tests/test_scenarios.py`
- Modify: `skills/make-task/tests/test_state.py`
- ➕ Modify: `skills/make-task/tests/test_review.py` (keeps test_ask's `REVIEW_DEADLINE < BASH_LIMIT` check)

- [x] remove `cmd_ask`, its parser entry, `card_page`, `render_context`, `ASK_DEADLINE`/`ASK_CALL_TIMEOUT` when unused, the `cards` init/state field, `MT_NO_CARDS`, the `cards/` directory handling and the docstring lines about cards; keep `script_json`, which Task 4 reuses
- [x] delete `assets/card.html` and `tests/test_ask.py`; move `script_json`'s test to the file that tests the helpers that stay
- [x] update `test_scenarios.py` to answer with `mt decide`
- [x] write tests in `test_state.py`: `mt ask` is an unknown command; `mt init` reports no `cards`; `mt activity --wait` marks the wait and a following `mt activity` clears it; `mt decide` records the answer
- [x] run tests - must pass before task 3

### Task 3: Built-in markdown renderer replaces view.py

**Files:**
- Modify: `skills/make-task/scripts/mt`
- Create: `skills/make-task/assets/doc.html`
- Modify: `skills/make-task/tests/conftest.py`
- Modify: `skills/make-task/tests/test_artifacts.py`
- Modify: `skills/make-task/tests/test_state.py`
- Modify: `skills/make-task/tests/test_review.py`
- Modify: `skills/make-task/SKILL.md`
- Modify: `skills/make-task/README.md`
- Modify: `docs/make-task.md`
- Modify: `skills/make-task/CLAUDE.md`

- [x] add `mdit-py-plugins` to `mt`'s script dependencies, then run `uv run -q --script skills/make-task/scripts/mt status` once to prove the dependency resolves
- [x] add `render_markdown` and `render_inline` as in Technical Details
- [x] create `assets/doc.html`: the static page template with the dashboard's tokens and the mockup's doc styles (headings, lists, tables, code blocks, blockquotes, task lists, footnotes)
- [x] make `render_artifact` render with `render_markdown` into `artifacts/<id>.html` from `doc.html` (text files as a code block, as `text_as_markdown` does); keep the mtime caching
- [x] remove `renderer_path`, `renderer_argv`, `MT_VIEW`, the `renderer` init/state field and the "no renderer" path, everywhere: `mt` (docstring included), `SKILL.md` (Start step 2 fields, the Errors row "revdiff / view renderer missing"), the requirement lines in `skills/make-task/README.md` and `docs/make-task.md`; add `--with mdit-py-plugins` to the test command in `CLAUDE.md`
- [x] update tests that used the renderer: `conftest.py` (drop the view stub and `MT_VIEW`), `test_artifacts.py` (the `view_log` stub, `test_no_renderer`, `test_renderer_argv*`, `test_renderer_failure_*`, `test_renderer_writes_nothing`, `test_renderer_timeout_*`; `RENDER_TIMEOUT` goes), `test_state.py` (the `renderer` field, `test_no_renderer`, `test_isolation_holds`), `test_review.py` (`test_result_without_renderer_registers_artifact`)
- [x] write tests for `render_markdown` and `render_inline`: tables, task lists, footnotes, `doc-` heading ids and `toc`, raw HTML shown as text, empty input
- [x] write tests for `render_artifact`: page written, unchanged mtime skips, text source, missing source warns
- [x] run tests - must pass before task 4

### Task 4: Artifact data scripts and the document payload

**Files:**
- Modify: `skills/make-task/scripts/mt`
- Modify: `skills/make-task/tests/test_artifacts.py`
- ➕ Modify: `skills/make-task/tests/test_state.py` (`script_json` now escapes non-ASCII too)

- [x] add `write_artifact_data` and `artifact_payload` (Technical Details); in this task `artifact_payload` builds the document payload for `design` and `stats` and returns nothing for the other ids
- [x] `render_artifact` calls `artifact_payload` and `write_artifact_data`, and returns early when the `.html` exists and, for an id that has a payload builder, the `.js` too
- [x] write tests: document payload shape, title from the first `#` heading else the artifact label, ASCII escaping of `<`, `>`, `&` and non-ASCII
- [x] write tests for `updated`: unchanged content keeps the file and its `updated`, a changed source bumps it
- [x] run tests - must pass before task 5

### Task 5: Plan payload with checkbox items

**Files:**
- Modify: `skills/make-task/scripts/mt`
- Modify: `skills/make-task/tests/test_plan.py`

- [x] make `parse_plan` keep each task's checkbox items `{text, checked}` in file order, with the same fence and section rules it uses for counting
- [x] `artifact_payload` builds the `plan` payload: the document plus `items` keyed by task n, each item with `render_inline` html
- [x] write tests for `parse_plan` items: nested lists, boxes inside fences ignored, `####` subsections kept, a task with no boxes
- [x] write tests: `mt plan` and `mt refresh` write the plan payload, and after a ticked box `render_changed` writes it again with `items` (never the document-only shape)
- [x] run tests - must pass before task 6

### Task 6: Progress payload

**Files:**
- Modify: `skills/make-task/scripts/mt`
- Create: `skills/make-task/tests/fixtures/progress.txt`
- Modify: `skills/make-task/tests/test_artifacts.py`

- [x] add `parse_progress(text)` for the grammar in Context: an optional `[timestamp] ` prefix on any line; `task N: title`, `fixer: …` and `finalize: …` start entries; nested `  - ` items stay with their parent; unmatched lines are kept as `text`
- [x] `artifact_payload` builds the `progress` payload (entries plus `raw`)
- [x] add a fixture trimmed from a real progress file, home paths scrubbed
- [x] write tests: header, task entries, items and nested items, decisions and deviations with and without the timestamp, fixer and finalize lines, review round lines with suffixes, `completed`, unknown lines, empty file
- [x] run tests - must pass before task 7

### Task 7: Fold revmux events into a round snapshot

**Files:**
- Modify: `skills/make-task/scripts/mt`
- Create: `skills/make-task/tests/fixtures/events-round.jsonl`
- Modify: `skills/make-task/tests/test_review.py`

- [x] add `fold_events(lines) -> snapshot` as in Technical Details (pure, no I/O)
- [x] add the fixture: a real round's `events.jsonl` (`.revmux/tasks/go-ask-answer-tool/02-after-fix/events.jsonl`), trimmed, home paths scrubbed
- [x] write tests on the fixture: stages, agents per stage, states, finder findings replaced by synthesis's with the finder total, verify kept total
- [x] write tests for edge cases, synthetic: partial last line, non-JSON line, non-object line, unknown kind, missing fields, retry, degrade, rate limit, dropped, no synthesis stage, caps, empty input, `format_ok` false
- [x] run tests - must pass before task 8

### Task 8: Review payload and live updates

**Files:**
- Modify: `skills/make-task/scripts/mt`
- Modify: `skills/make-task/tests/test_review.py`
- Modify: `skills/make-task/SKILL.md`

- [x] `artifact_payload` builds the `review` payload from its three sources (Technical Details), with the 8 MB read cap in the caller
- [x] `mt review start` registers the `review` artifact (source `review.md`; `render_changed` skips it until it exists) and writes the payload with the round `running`
- [x] `mt review wait` rewrites the payload on each poll where `events.jsonl`'s size or mtime changed, and saves the round's state summary `live` only when it changed; a `failed` outcome rewrites it with the round `failed`
- [x] `mt review result` (also a re-record for a gate) rewrites it with the round `done`; `render_artifact` for `review.md` writes the static page only and goes through the same builder for the `.js`
- [x] update `SKILL.md` Each round step 6: the `review` artifact is registered at `mt review start`
- [x] write tests: start, several waits with a growing events file, unchanged file writes nothing, result, a re-record rewrites the round, failed, a second round keeps the first's findings, the payload keeps `rounds` after `render_changed`, no body or fix in `state.json`
- [x] run tests - must pass before task 9

### Task 9: Viewer shell with the Design and Stats tabs

**Files:**
- Create: `skills/make-task/assets/viewer.html`
- Modify: `skills/make-task/scripts/mt`
- Create: `skills/make-task/tests/test_viewer.py`
- ➕ Modify: `skills/make-task/tests/conftest.py` (`RUN_FILES`), `skills/make-task/tests/test_state.py` and
  `skills/make-task/tests/test_checkpoint.py` (the run dir listing after `mt init` holds the entry pages)

- [x] create `assets/viewer.html` per the design and the mockup: header, tabs Design · Plan · Progress · Review · Stats as file buttons with their age and badges, the current one framed; no Changes tab
- [x] load `state.js` and the current tab's `artifacts/<id>.js` every 1.5 s; re-render only on a changed `updated`; keep scroll and expanded items in JS state
- [x] render the Design and Stats tabs: the document with its table of contents; a tab whose payload is missing shows "not yet"
- [x] `mt init` writes `viewer-<tab>.html` for each tab from `assets/viewer.html` (only `<body data-tab>` differs); no plain `viewer.html` in the run dir
- [x] write node-harness tests: Design and Stats render from sample payloads, tab switch, missing payload, a re-render keeps the scroll and an expanded item
- [x] write pytest: the entry files exist after `mt init` and differ only in `data-tab`; the static checks on `viewer.html` and `doc.html`
- [x] run tests - must pass before task 10
- ➕ as built: tabs are `<button data-show="<tab>">` (`data-tab` is the body's); a tab with no artifact entry is
  dimmed but stays clickable and shows "<Label>: not yet"; expandable items are `<details>` whose
  `<summary data-fold="<key>">` click the page handles itself (the `open` Set, keyed `<tab>:<key>`); in-page
  `#` links scroll with `scrollIntoView` and never navigate. Until Tasks 10 and 11 give them their own view, Plan
  shows its document behind "Show the plan document", Progress its raw text (escaped) and Review the rounds strip

### Task 10: Viewer Plan and Progress tabs

**Files:**
- Modify: `skills/make-task/assets/viewer.html`
- Modify: `skills/make-task/tests/test_viewer.py`

- [ ] render the Plan tab: task rows from `state.tasks` joined with the payload's `items`, progress bars and durations, the running task's checklist open, the document behind "Show the plan document"
- [ ] render the Progress tab: the timeline grouped by entry, decisions and deviations marked, filters All · Decisions · Deviations · Review, the raw text when there are no entries
- [ ] escape every progress string; only renderer HTML (`html` fields) is inserted as HTML
- [ ] write node-harness tests: plan rows and statuses, the open checklist, the document toggle, timeline marks, each filter, the raw fallback, a progress line holding `<script>` shown as text
- [ ] run tests - must pass before task 11

### Task 11: Viewer Review tab

**Files:**
- Modify: `skills/make-task/assets/viewer.html`
- Modify: `skills/make-task/tests/test_viewer.py`

- [ ] render the Review tab per the mockup: rounds strip, status band, stage steps, agents grouped by stage, findings by severity with expandable body and fix, dropped list, paths of `events.jsonl` and the round log, "Closing this view does not stop the review"
- [ ] render a finished round's verdict and final findings, and the states "Starting revmux…", stale data after 30 s, failed round, `format_ok` false, truncated
- [ ] escape every finding and activity string
- [ ] write node-harness tests: live round, finished round, each state, and a finding title holding `<script>` shown as text
- [ ] run tests - must pass before task 12

### Task 12: Dashboard opens the viewer and shows the live round

**Files:**
- Modify: `skills/make-task/assets/dashboard.html`
- Modify: `skills/make-task/tests/test_agterm.py`

- [ ] file buttons open `viewer-<tab>.html` with `javascript: true`, `cwd: <run>`, `sizePercent: 85`, `follow: true`; Changes keeps `revdiff`
- [ ] a button is enabled when its artifact entry has `html`, and Review also while a round is live (Technical Details); Review shows `● live` then
- [ ] add the compact block under the live round from its state summary: stage steps, counts, agents done, latest activity, "Open live review"
- [ ] a refused open (agterm's `overlay already open`) shows agterm's error as the button's tooltip, as the click handler does today; the tooltip does not guess what holds the slot
- [ ] write node-harness tests: open arguments per tab, Review enabled and live during round 1 before any result, live off for a failed or older round, the compact block, a refused open
- [ ] run tests - must pass before task 13

### Task 13: Verify acceptance criteria
- [ ] verify all requirements from Overview are implemented
- [ ] verify edge cases are handled
- [ ] run full test suite: `uv run --with pytest --with markdown-it-py --with mdit-py-plugins --with pyyaml pytest skills/make-task/tests -q`
- [ ] verify nothing outside tests and `upstream/` copies still names `MT_VIEW`, `view.py`, `mt ask`, `card.html`, `MT_NO_CARDS` or a card: `grep -rnwi -E 'MT_VIEW|view\.py|mt ask|card\.html|MT_NO_CARDS|cards?' skills/make-task docs/make-task.md README.md --exclude-dir=tests --exclude-dir=upstream` prints nothing
- [ ] verify test coverage meets project standard

### Task 14: [Final] Update documentation
- [ ] update `skills/make-task/README.md` and `docs/make-task.md`: the viewer and its tabs, the live Review
- [ ] update `skills/make-task/SKILL.md`: `mt review wait` keeps the Review tab live
- [ ] update `skills/make-task/CLAUDE.md` with the viewer, the data scripts and the node-harness tests
- [ ] update `mt`'s module docstring (artifacts, viewer, review data)
- [ ] run tests - `test_skill_md.py` checks `SKILL.md`
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion
*Items requiring manual intervention or external systems - no checkboxes, informational only*

**Manual verification**:
- in agterm, on a real run: each dashboard button opens the viewer on its tab; tabs switch; the Plan tab ticks as
  tasks finish; the Review tab follows a live revmux round to its result; closing the viewer mid-round leaves the
  round running; every question arrives in the terminal, also in a remote session, and agterm's sidebar marks
  the session while a question waits

**After merging**:
- run `/make-task --update` once: the default template's brainstorm and revmux copies changed, so every installed
  default profile reports updates for them at Start until it takes them
