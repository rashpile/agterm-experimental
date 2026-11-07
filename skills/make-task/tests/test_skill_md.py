"""SKILL.md and references/profiles.md must name only `mt` commands, flags and values the real `mt` accepts."""

from __future__ import annotations

import argparse
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import MT, subparsers

SKILL = Path(__file__).resolve().parent.parent / "SKILL.md"
TEXT = SKILL.read_text(encoding="utf-8")
RESOLUTION_LINE = (
    "Paths below are relative to this skill's directory (the one containing this SKILL.md) — "
    "resolve them against it, not the current working directory."
)
# read by the orchestrator only during setup (a new named profile, --update) and by maintainers
PROFILES = SKILL.parent / "references" / "profiles.md"
PROFILES_TEXT = PROFILES.read_text(encoding="utf-8")
PROFILES_RESOLUTION_LINE = (
    "Paths below are relative to this skill's directory, the one containing `SKILL.md` (the parent of this "
    "`references/` directory). Resolve them against it, not the current working directory or this file's "
    "directory."
)
STOP_TOKENS = {"&&", "||", "|", ";", ")", "→", ">", "2>"}


def code_spans(text: str) -> list[str]:
    """Inline code spans plus every line of fenced blocks."""
    spans: list[str] = []
    fenced = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            spans.append(line)
        else:
            spans.extend(re.findall(r"`([^`\n]+)`", line))
    return spans


def mt_calls(text: str) -> list[list[str]]:
    """Token lists after each `mt` word in code, cut at shell separators or the next `mt`."""
    calls: list[list[str]] = []
    for span in code_spans(text):
        for m in re.finditer(r"(?<![\w/.-])mt\s+(.*)", span):
            tokens: list[str] = []
            for tok in m.group(1).split():
                if tok in STOP_TOKENS or tok == "mt":
                    break
                tokens.append(tok.lstrip("[").rstrip(")`;]"))
            if tokens:
                calls.append(tokens)
    return calls


def placeholder(tok: str) -> bool:
    return tok.startswith(("<", '"<', "$", '"$'))


def flat(text: str) -> str:
    """The text with every run of whitespace (line breaks and indents included) as one space."""
    return " ".join(text.split())


CALLS = mt_calls(TEXT)
PROFILES_CALLS = mt_calls(PROFILES_TEXT)
# (file, tokens) for the tests that hold for both files
ALL_CALLS = [("SKILL.md", c) for c in CALLS] + [("profiles.md", c) for c in PROFILES_CALLS]
PARSER = MT.build_parser()


def resolve(tokens: list[str]) -> tuple[argparse.ArgumentParser, list[str], list[str]] | None:
    """(deepest parser, command words, remaining tokens), or None for a placeholder command."""
    rest = list(tokens)
    if rest[:1] == ["--run"]:
        rest = rest[2:]
    parser, words = PARSER, []
    while rest and not rest[0].startswith("-"):
        subs = subparsers(parser)
        if not subs:
            break
        if placeholder(rest[0]):
            return None
        assert rest[0] in subs, f"unknown mt command {' '.join([*words, rest[0]])!r}"
        parser, words = subs[rest[0]], [*words, rest[0]]
        rest = rest[1:]
    return parser, words, rest


def test_frontmatter_and_resolution_line() -> None:
    assert TEXT.startswith("---\nname: make-task\n")
    head = TEXT.split("---", 2)[1]
    assert "description:" in head and "make-task" in head.split("description:", 1)[1]
    for trigger in ("make a task", "run the full dev workflow", "brainstorm, plan, execute and review"):
        assert trigger in head
    allowed = re.search(r"^allowed-tools: (.+)$", head, re.MULTILINE)
    assert allowed is not None
    tools = {t.strip() for t in allowed.group(1).split(",")}
    # no Skill: every companion is a profile copy the orchestrator reads, never a registered skill
    assert tools == {
        "Agent",
        "AskUserQuestion",
        "EnterWorktree",
        "TaskCreate",
        "TaskUpdate",
        "Bash",
        "Read",
        "Write",
        "Edit",
        "Glob",
        "Grep",
    }
    assert RESOLUTION_LINE in TEXT
    # the bundled-file rule: no ${CLAUDE_SKILL_DIR} and no hardcoded install path for mt itself
    assert "${CLAUDE_SKILL_DIR}/scripts" not in TEXT
    assert "~/.claude/skills/make-task" not in TEXT


def test_skill_md_names_mt_calls() -> None:
    assert len(CALLS) > 40
    named = {" ".join(r[1]) for c in CALLS if (r := resolve(c)) is not None}
    for cmd in (
        "init",
        "status",
        "set",
        "phase",
        "activity",
        "assume",
        "decide",
        "event",
        "checkpoint begin",
        "checkpoint end",
        "fail",
        "dirty",
        "done",
        "plan",
        "refresh",
        "artifact",
        "review start",
        "review wait",
        "review result",
        "review verify-final",
        "profile resolve",
        "profile check",
        "profile install-default",
    ):
        assert cmd in named, f"SKILL.md never shows `mt {cmd}`"


@pytest.mark.parametrize(("doc", "tokens"), ALL_CALLS, ids=[f"{d}: {' '.join(c)[:60]}" for d, c in ALL_CALLS])
def test_every_flag_exists(doc: str, tokens: list[str]) -> None:
    resolved = resolve(tokens)
    if resolved is None:
        return
    parser, words, rest = resolved
    known = set(parser._option_string_actions)
    for tok in rest:
        if tok.startswith("--"):
            flag = tok.split("=", 1)[0]
            assert flag in known, f"`mt {' '.join(words)}` has no flag {flag} ({doc}: mt {' '.join(tokens)})"


def test_values_are_accepted() -> None:
    for tokens in CALLS:
        resolved = resolve(tokens)
        if resolved is None:
            continue
        _, words, rest = resolved
        args = [t for t in rest if not t.startswith("-")]
        if words == ["set"]:
            pairs = [m.group(1) for t in args if (m := re.match(r"([A-Za-z_]+)=", t))]
            assert pairs, f"mt set without KEY=VALUE: {tokens}"
            for key in pairs:
                assert key in MT.SET_KEYS, f"mt set: unknown key {key}"
        elif words == ["phase"] and args and not placeholder(args[0]):
            assert args[0] in MT.PHASE_IDS
        elif words == ["artifact"] and args and not placeholder(args[0]):
            assert args[0] in MT.ARTIFACT_IDS
    statuses = re.findall(r'mt done --status "([^"<]+)"|mt done --status ([a-z]+)', TEXT)
    assert statuses
    for quoted, bare in statuses:
        assert (quoted or bare) in MT.FINAL_STATUSES
    for status in MT.FINAL_STATUSES:
        assert f"`{status}`" in TEXT, f"SKILL.md does not explain final status {status!r}"


def test_global_run_flag_goes_first() -> None:
    # `--run DIR` after a command other than `review start` would be a parse error
    for _, tokens in ALL_CALLS:
        resolved = resolve(tokens)
        if resolved is None:
            continue
        _, words, rest = resolved
        if "--run" in rest:
            assert words == ["review", "start"], f"--run after `mt {' '.join(words)}`"


def test_set_keys_and_stop_reasons_documented() -> None:
    assert MT.SET_CHOICES["stop_reason"] == ("clean", "not-converging", "round-cap", "degraded", "skipped")
    named = set(re.findall(r"stop_reason=([a-z-]+)", TEXT)) | set(
        re.findall(r"`(not-converging|round-cap)`", TEXT)
    )  # the stop row names those two as `mt set stop_reason=<not-converging or round-cap>`
    assert named == set(MT.SET_CHOICES["stop_reason"])
    for mode in MT.SET_CHOICES["mode"]:
        assert f"mt set mode={mode}" in TEXT
    assert "timeout: 600000" in TEXT
    assert "Exit codes: `0` ok, `1` failure with the message on stderr." in TEXT


def section(text: str, head: str, nxt: str | None = None) -> str:
    """text from the heading line `head` (e.g. "## Plan") to the heading line `nxt` (or the end)."""
    start = text.index(f"\n{head}\n")
    return text[start : text.index(f"\n{nxt}\n") if nxt else len(text)]


def test_phases_run_in_overview_order() -> None:
    """Overview: brainstorm → plan → plan review → task loop → review loop → leftovers → finalize, stats and
    completion, each a section of SKILL.md in that order; brainstorm, plan and review read the profile's
    companion entry, exec runs this file's playbook on the exec companion's scripts."""
    heads = re.findall(r"^#{2,3} (.+)$", TEXT, re.MULTILINE)
    order = [
        "Brainstorm",
        "Plan",
        "Exec",
        "Review",
        "Leftovers question",
        "Finalize",
        "Stats (best-effort)",
        "Completion",
    ]
    assert [h for h in heads if h in order] == order
    for head, nxt, entry in (
        ("## Brainstorm", "## Plan", "<brainstorm_entry>"),
        ("## Plan", "## Exec", "<plan_entry>"),
        ("## Review", "## The end of the run", "<review_entry>"),
    ):
        body = section(TEXT, head, nxt)
        assert f"Read `{entry}` and follow it (Running a companion)" in body, f"{head} does not read {entry}"
        assert re.search(r"Inputs: ", body), f"{head} does not state its inputs"
        phase = head[3:].lower()
        assert f"`## {phase}` section" in body, f"{head} does not apply the profile's `## {phase}` section"
    assert "Read `<plan_reviewer>`" in section(TEXT, "## Plan", "## Exec")
    assert "Inputs: " in section(TEXT, "## Exec", "## Review")
    assert "The exec companion's own skill text (planning's `skills/exec/SKILL.md`) is never followed" in flat(TEXT)


def test_running_a_companion_resolves_relative_paths_against_the_entry() -> None:
    """A Read announces no base directory: the copies' relative paths need the entry file's directory."""
    running = section(TEXT, "### Running a companion", "## cwd discipline")
    assert "resolves against the directory of the phase's entry file" in running
    assert "Never through the Skill tool" in running
    assert "Then apply the profile's `## <phase>` section" in running


def test_no_pre_profile_names_left() -> None:
    """The planning/revmux plugin roots and the Skill-tool compositions are gone: every phase runs the profile."""
    for name in ("<planning_root>", "<planning_data>", "<scripts>", "<rx>", "<revmux_root>"):
        assert name not in TEXT, f"SKILL.md still names {name}"
    for key in ("planning_root", "planning_data", "revmux_root"):
        assert key not in TEXT, f"SKILL.md still names the init key {key}"
    for skill in ("brainstorm:brainstorm", "planning:make", "revmux:revmux", "planning:plan-review", "planning:exec"):
        assert skill not in TEXT, f"SKILL.md still composes {skill}"
    assert not re.search(r"Skill tool with|Invoke the Skill tool|invokes the Skill tool", TEXT)


def test_profile_keys_are_the_ones_mt_stores() -> None:
    """Every profile key SKILL.md names as `<key>` is one `mt init` stores, and the names table lists them all."""
    named = set(re.findall(r"<((?:profile|brainstorm|plan|exec|review)_[a-z_]+)>", TEXT))
    other_state_keys = {"review_base", "plan_path"}  # run-state keys of their own (mt set, mt plan)
    assert named - other_state_keys <= set(MT.PROFILE_STATE_KEYS), named - other_state_keys - set(MT.PROFILE_STATE_KEYS)
    table = TEXT[TEXT.index("| key | value |") : TEXT.index("**`<branch>` is always")]
    for key in MT.PROFILE_STATE_KEYS:
        assert f"`<{key}>`" in table, f"the names table does not list <{key}>"


def test_start_step_0_sets_up_and_checks_the_profile_before_init() -> None:
    """First use installs the default profile and asks only the autonomy; a check failure stops before any run;
    a named profile and --update go through references/profiles.md."""
    start = section(TEXT, "## Start", "## Question rule")
    step0 = start[start.index("0. **Profile.**") : start.index("\n1. ")]
    order = [
        step0.index("`mt profile resolve --profile <name>`"),
        step0.index("`mt profile install-default`"),
        step0.index("`references/profiles.md`"),
        step0.index("`mt profile check --profile <name>`"),
        start.index("`mt init --title"),
    ]
    assert order == sorted(order)
    assert "`/make-task [--profile=<name>] [--update] [task text]`" in step0
    assert "plain `AskUserQuestion`s with no `mt` call before or after them" in flat(step0)
    for level in MT.AUTONOMY_PRESETS:
        assert f"`{level}`" in step0, f"the first-use question does not offer {level}"
    assert "`autonomy:` line" in step0 and "with Edit" in flat(step0)
    assert "`--update` without task text → stop" in step0
    assert "No `mt init`, no run." in step0
    assert "`updates` non-empty" in step0 and "/make-task --update" in step0
    assert "(cd <main_checkout> && bash <review_scripts>/preflight.sh)" in start
    assert "`review_entry` is null" in start


def test_question_rule_names_the_presets_and_gates() -> None:
    """The preset table matches mt's AUTONOMY_PRESETS; autonomous answers both questions from `defaults`; the
    gate table covers mt's GATES; interactive validates the design section by section."""
    rule = section(TEXT, "## Question rule", "## Subagent prompts")
    rows = {
        m.group(1): m.group(2)
        for m in re.finditer(r"^\| `(autonomous|default|interactive)` \|(?:[^|]*\|){2}([^|]*)\|", rule, re.MULTILINE)
    }
    assert set(rows) == set(MT.AUTONOMY_PRESETS)
    for level, gates in MT.AUTONOMY_PRESETS.items():
        assert set(re.findall(r"`([a-z-]+)`", rows[level])) == set(gates), f"{level} preset gates"
    assert "validated section by section" in rule
    gate_table = rule[rule.index("| gate | when | the question names | a revision |") :]
    gate_rows = re.findall(r"^\| `([a-z-]+)` \| ([^|]+) \|", gate_table, re.MULTILINE)
    assert [g for g, _ in gate_rows] == list(MT.GATES)
    for gate, when in gate_rows:
        assert not re.fullmatch(r"`[a-z]+`", when.strip()), f"the {gate} row still has the phase column"
    for key in ("`defaults.mode`", "`defaults.leftovers`"):
        assert key in rule
    assert "never overrides the linked-worktree rule" in flat(rule)
    brainstorm = section(TEXT, "## Brainstorm", "## Plan")
    assert "mt set mode=<defaults.mode>" in brainstorm and "`autonomous`" in brainstorm
    leftovers = section(TEXT, "### Leftovers question", "### Finalize")
    assert "`defaults.leftovers`" in leftovers and "`autonomous`" in leftovers


@pytest.mark.parametrize(("doc", "text"), [("SKILL.md", TEXT), ("profiles.md", PROFILES_TEXT)])
def test_no_card_is_left(doc: str, text: str) -> None:
    """Questions are `AskUserQuestion`s in the terminal: no floating card, no `mt ask`, no `MT_NO_CARDS`."""
    assert not re.search(r"\bcards?\b", text, re.IGNORECASE), f"{doc} still names a card"
    assert "mt ask" not in text, f"{doc} still names `mt ask`"
    assert "MT_NO_CARDS" not in text, f"{doc} still names MT_NO_CARDS"
    assert "ask.json" not in text and "delegate`" not in text


def test_questions_section_is_the_one_way_to_ask() -> None:
    """Every question is an `AskUserQuestion`: the waiting mark before it (but not before an error question,
    which `mt fail` already shows), `mt decide` after it, and a one-option error question is not asked."""
    questions = flat(section(TEXT, "### Questions", "### Gates"))
    assert "is one `AskUserQuestion`, one at a time" in questions
    for kind in ("a brainstorm question", "the preflight question", "the worktree question", "a gate"):
        assert kind in questions, kind
    assert "an error question, the leftovers question" in questions
    wait = '`mt activity "Waiting for your answer · <topic>" --wait`'
    decide = '`mt decide "<question>" "<answer>"`'
    order = [
        questions.index(wait),
        questions.index("except before an error question (`mt fail` already shows the error)"),
        questions.index(decide),
        questions.index("then a normal `mt activity`"),
    ]
    assert order == sorted(order)
    assert 'its label ends in "(Recommended)"' in questions and "There is no delegate option" in questions
    assert "An option's pro and con become its description" in questions
    assert "A gate's question is its exact title" in questions
    assert 'an "Other" answer that maps to no option asks the same question again' in questions
    one = questions[questions.index("**One option.**") :]
    assert "An error question with one option (`stop`, `keep`) is not asked" in one
    assert "the run takes that option and says why" in one
    # long material is named by its artifact, registered before the question
    for call in (
        "`mt artifact design <state_dir>/design.md` before the `design` gate",
        "`mt artifact design <state_dir>/design-draft.md`",
        "`mt artifact plan <absolute plan path>` before the `plan` gate",
        "for the `review-round` gate and the leftovers question",
    ):
        assert call in questions, call
    brainstorm = flat(section(TEXT, "## Brainstorm", "## Plan"))
    step2 = brainstorm[brainstorm.index("2. Read `<brainstorm_entry>`") : brainstorm.index("3. The preflight")]
    assert step2.index("`<state_dir>/design-draft.md`") < step2.index("asks its question again")
    errors = flat(section(TEXT, "## Errors", "## Outside agterm"))
    assert "| condition | mt call | question options | next |" in errors
    assert "A row with one option (`stop`, `keep`) asks nothing" in errors


def test_gates_sit_where_the_gate_table_says() -> None:
    """design after `mt artifact design` (the question names the artifact, so it is registered first), plan
    after the reviewer's findings and `mt artifact plan`, before Exec, review-round before the fixer's
    checkpoint."""
    brainstorm = section(TEXT, "## Brainstorm", "## Plan")
    step5 = brainstorm[brainstorm.index("5. Write `<state_dir>/design.md`") :]
    assert step5.index("`mt artifact design <state_dir>/design.md`") < step5.index("the `design` gate")
    plan = section(TEXT, "## Plan", "## Exec")
    assert plan.index("Plan review") < plan.index("The `plan` gate")
    step4 = plan[plan.index("4. The `plan` gate") :]
    assert step4.index("`mt artifact plan <absolute plan path>`") < step4.index("then the gate (Gates)")
    recovery = flat(section(TEXT, "## Recovery", "## Start"))
    assert "(`mt artifact design`, then the `design` gate when `<gates>` has it and it has not passed)" in recovery
    each = section(TEXT, "### Each round `<n>`", "### Reconciling a round")
    step8 = each[each.index("8. Stop per loop.md") :]
    assert step8.index("the `review-round` gate") < step8.index("`mt checkpoint begin review.fix.<n>`")


def test_a_gate_revision_registers_its_artifact_again() -> None:
    """A design or plan gate sends the user to the artifact's view, so a revision registers the changed file
    again before the gate is asked again; otherwise the view still shows the version before the change."""
    rows = {m.group(1): m.group(0) for m in re.finditer(r"(?m)^\| `(design|plan)` \|.*$", TEXT)}
    revision = {gate: row.rsplit("|", 2)[-2] for gate, row in rows.items()}
    design, plan = revision["design"], revision["plan"]
    assert design.index("`mt artifact design <state_dir>/design.md` again") < design.index("ask the gate again")
    assert plan.index("`mt artifact plan <absolute plan path>` again") < plan.index("ask the gate again")


def test_brainstorm_and_review_skip() -> None:
    """A profile may skip brainstorm (the questions are still asked, a minimal design.md) or review (no round, no
    question)."""
    brainstorm = section(TEXT, "## Brainstorm", "## Plan")
    skipped = flat(brainstorm[brainstorm.index("**Brainstorm skipped**") :])
    assert "`mt phase brainstorm --skipped" in skipped
    assert "(the preflight question when the preflight failed, and the worktree question, are still asked)" in skipped
    assert "minimal `design.md`" in skipped
    review = flat(section(TEXT, "## Review", "## The end of the run"))
    assert "no round runs and no leftovers question is asked" in review
    assert "the run ends `review skipped`" in review


def test_review_skipped_by_the_profile_asks_no_preflight_question() -> None:
    """`phases.review: skip` is the profile's choice, not a failure: an assumption and a skipped review phase,
    never the preflight question, at every autonomy level (so an `autonomous` run never stops on it). The
    question stays for a real preflight failure: preflight.sh fails, or a `requires` command is missing."""
    start = section(TEXT, "## Start", "## Question rule")
    step3 = start[start.index("3. **Review check.**") : start.index("\n4. ")]
    cut = step3.index("- Otherwise the revmux preflight")
    skip, preflight = flat(step3[:cut]), flat(step3[cut:])
    assert "`review_entry` is null (the profile's `phases.review` is `skip`)" in skip
    assert '`mt assume "no revmux review" --why "profile <profile_name> skips review"`' in skip
    assert "no preflight and no preflight question, at every autonomy level" in skip
    assert "preflight.sh" not in skip and "question during brainstorm" not in skip and "Errors row" not in skip
    assert "`(cd <main_checkout> && bash <review_scripts>/preflight.sh)`" in preflight
    assert (
        "init's `warnings` name a review `requires` command missing on PATH (that counts as a failed preflight: preflight.sh does not read `requires`)"
        in preflight
    )
    assert '`mt assume "no revmux review" --why "<what preflight or the warning said>"`' in preflight
    assert "the preflight question during brainstorm" in preflight

    brainstorm = section(TEXT, "## Brainstorm", "## Plan")
    step3 = flat(brainstorm[brainstorm.index("\n3. The preflight question") : brainstorm.index("\n4. ")])
    assert "only when Start step 3's preflight failed" in step3
    assert "A review the profile skips has no question." in step3
    assert "found no review to run" not in TEXT

    review = section(TEXT, "## Review", "## The end of the run")
    head = review[: review.index("revmux runs as one loop")]
    by_profile = flat(head[head.index("- `review_entry` is null") : head.index("- the preflight failed")])
    by_question = flat(head[head.index("- the preflight failed") :])
    assert '`mt phase review --skipped --note "skipped by profile"`' in by_profile
    assert "No question, at every autonomy level." in by_profile
    assert "preflight question" not in by_profile
    assert "`noreview` on the preflight question" in by_question
    assert '`mt phase review --skipped --note "revmux unavailable"`' in by_question

    errors = section(TEXT, "## Errors", "## Outside agterm")
    row = next(line for line in errors.splitlines() if line.startswith("| revmux preflight fails at Start"))
    assert "`noreview`" in row and "`requires` command missing on PATH" in row
    assert "review_entry" not in row and "phases.review" not in row and "skip" not in row.split("|")[1]
    assert "A review the profile skips (`review_entry` null) is not an error and asks no question" in flat(errors)


def test_plan_reviewer_runs_as_a_read_only_agent_without_bypass() -> None:
    """The built-in `Plan` type has no Edit/Write tools, like the `tools:` line of the plan-review copy."""
    plan = section(TEXT, "## Plan", "## Exec")
    step3 = plan[plan.index("3. Plan review") : plan.index("4. The `plan` gate")]
    assert "drop its frontmatter" in step3
    assert '`subagent_type: "Plan"`' in step3 and "`model: <plan_reviewer_model>`" in step3
    assert "general-purpose" not in step3
    assert "without `bypassPermissions`" in step3
    assert "change no files" in step3
    assert "`<plan_reviewer>` null →" in step3
    # a Claude Code without the built-in `Plan` type: no review, recorded, and the run goes on
    fallback = flat(step3)
    assert "The Agent tool refuses the `Plan` type (this Claude Code has none) → no plan review" in fallback
    assert '`mt assume "no plan review" --why "the Agent tool has no Plan agent type"`, then step 4' in fallback
    prompts = flat(section(TEXT, "## Subagent prompts", "### Resolving and substituting"))
    assert "except the plan reviewer (Plan step 3: `Plan`, without `bypassPermissions`)" in prompts


def test_harness_addendum_fills_exec_scripts() -> None:
    """The append line names the profile's exec scripts; the orchestrator fills them in with the other values."""
    harness = TEXT[TEXT.index("### Harness addendum") : TEXT.index("### Fixer addendum")]
    assert "| bash <exec_scripts>/append-progress.sh PROGRESS_FILE_PATH" in harness
    fill = harness[: harness.index("```text")]
    for value in ("`<review_base>`", "`<exec_scripts>`", "`<pre-existing paths>`"):
        assert value in fill, f"the addendum's fill-in list does not name {value}"
    assert "${CLAUDE_PLUGIN_ROOT}/skills/exec/scripts" not in TEXT


def test_errors_cover_the_profile() -> None:
    errors = section(TEXT, "## Errors", "## Outside agterm")
    assert "| profile check fails:" in errors
    assert "| another `mt profile` command fails at Start step 0 or in `references/profiles.md` |" in errors
    assert "`requires` command missing on PATH" in errors
    assert "show at every autonomy level" in errors


def test_profile_paths_go_into_commands_unquoted_so_mt_refuses_others() -> None:
    """The profile's paths are used unquoted (in SKILL.md and in the copies' rewritten text): SKILL.md, the Errors
    row and profiles.md say mt refuses a profile path that is not one plain shell word, and how to move it."""
    names = flat(TEXT[TEXT.index("Names in angle brackets") : TEXT.index("| key | value |")])
    assert "The commands below use these profile paths unquoted" in names
    for cmd in ("`mt profile install-default`", "`mt profile check`", "`mt init`", "(`MT_CONFIG_HOME`)"):
        assert cmd in names, cmd
    assert "bash <exec_scripts>/create-branch.sh" in TEXT and '"<exec_scripts>' not in TEXT
    errors = flat(section(TEXT, "## Errors", "## Outside agterm"))
    assert "a profile path that is not one plain shell word: set `MT_CONFIG_HOME`" in errors
    holds = flat(section(PROFILES_TEXT, "## What a profile holds", "## First use"))
    assert "the profile's path must be one plain shell word" in holds
    assert "`install-default`, `import` and `fetch` refuse" in holds and "`MT_CONFIG_HOME`" in holds


# stage-and-commit.sh is not used: it ends in a bare `git commit`, which commits the whole index
EXEC_SCRIPTS = {"create-branch", "init-progress", "append-progress", "move-plan", "resolve-file"}
EXEC_PROMPTS = {"task", "fixer", "finalizer", "stats"}


def test_exec_resources_are_the_planned_ones() -> None:
    """Only the exec companion's listed scripts and prompts are used, through resolve-file.sh for the prompts,
    and each is part of the exec companion contract `mt profile check` enforces."""
    scripts = set(re.findall(r"<exec_scripts>/([a-z-]+)\.sh", TEXT))
    assert scripts == EXEC_SCRIPTS
    assert {f"{s}.sh" for s in scripts} <= set(MT.EXEC_CONTRACT_SCRIPTS)
    assert not re.search(r"skills/exec/scripts/[a-z-]+\.sh", TEXT), "a script outside <exec_scripts>"
    prompts = set(re.findall(r"prompts/([a-z]+)\.md", TEXT))
    assert prompts == EXEC_PROMPTS
    assert {f"{p}.md" for p in prompts} <= set(MT.EXEC_CONTRACT_PROMPTS)
    assert "(cd <main_checkout> && bash <exec_scripts>/resolve-file.sh prompts/<name>.md <exec_data>)" in TEXT
    assert "(cd <main_checkout> && bash <exec_rules> planning-rules.md <exec_data>)" in TEXT
    table = TEXT[TEXT.index("| token | value |") : TEXT.index("4. Write the result")]
    assert "| `${CLAUDE_PLUGIN_ROOT}` | `exec_root`" in table
    assert "| `RESOLVE_SCRIPT` | `<exec_scripts>/resolve-file.sh` |" in table
    assert "| `PLUGIN_DATA_DIR` | `exec_data` |" in table


def full_calls(text: str) -> list[tuple[str, list[str]]]:
    """Each `mt …` invocation in code, shell-split with dummy values: (source text, argv for mt)."""
    calls = []
    for span in code_spans(text):
        for m in re.finditer(r"(?<![\w/.-])mt\s+(.*)", span):
            src = m.group(1)
            cmd = re.sub(r"\$\([^)]*\)", "1", src)  # $(git …) substitutions
            cmd = re.sub(r"<[^<>]*>", "1", cmd)  # <n>, <state_dir>, <reason> …
            cmd = cmd.replace("…", "1")
            try:
                tokens = shlex.split(cmd)
            except ValueError:
                tokens = cmd.split()
            argv: list[str] = []
            for tok in tokens:
                if tok in STOP_TOKENS or tok == "mt" or tok.startswith(("2>", ">")):
                    break
                tok = tok.strip("[]`")
                if tok in ("...", ""):
                    continue
                argv.append(tok)
            calls.append((src, argv))
    return calls


def bare_mention(argv: list[str]) -> bool:
    """A prose mention such as `mt done` or `mt review wait` (command words only), not an invocation."""
    resolved = resolve(argv)
    return resolved is None or not resolved[2] and not any(a.startswith("-") for a in argv)


def invocations(text: str) -> list[tuple[str, list[str]]]:
    return [c for c in full_calls(text) if c[1] and "--help" not in c[1] and not bare_mention(c[1])]


FULL = invocations(TEXT)
PROFILES_FULL = invocations(PROFILES_TEXT)
ALL_FULL = [("SKILL.md", *c) for c in FULL] + [("profiles.md", *c) for c in PROFILES_FULL]


@pytest.mark.parametrize(("doc", "src", "argv"), ALL_FULL, ids=[f"{d}: {c[:60]}" for d, c, _ in ALL_FULL])
def test_every_full_call_parses(doc: str, src: str, argv: list[str]) -> None:
    """Required flags and positional arity, not just flag names: a call missing --round-dir or --why fails."""
    try:
        PARSER.parse_args(argv)
    except SystemExit as e:
        pytest.fail(f"{doc} `mt {src}` does not parse as {argv} (exit {e.code})")


def test_full_call_check_catches_a_missing_required_flag() -> None:
    with pytest.raises(SystemExit):
        PARSER.parse_args(full_calls("`mt review start --round <n> --run <run>`")[0][1])
    assert len(FULL) > 30


def launch_line() -> str:
    m = re.search(r"\(cd <main_checkout> && (revmux --task .*?) > <state_dir>/revmux/round-<n>\.json", TEXT)
    assert m, "SKILL.md lost the revmux launch line"
    return m.group(1)


def test_launch_puts_task_and_run_first_as_review_wait_matches() -> None:
    tokens = shlex.split(launch_line())
    assert tokens[:5] == ["revmux", "--task", "<revmux_task>", "--run", "<run>"]
    argv = [t.replace("<revmux_task>", "pr+1.x").replace("<run>", "01-initial") for t in tokens[1:]]
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", *argv])
    try:
        deadline = time.monotonic() + 5
        while not MT.process_alive("pr+1.x", "01-initial") and time.monotonic() < deadline:
            time.sleep(0.02)
        assert MT.process_alive("pr+1.x", "01-initial"), "mt review wait would not see the documented launch"
        assert not MT.process_alive("pr+1.x", "01-init")
    finally:
        proc.kill()
        proc.wait()


def test_reconcile_uses_mt_review_wait_not_pgrep() -> None:
    """A pgrep run in the session's Bash call matches that call's own shell, and an unanchored pattern
    matches a longer run name; `mt review wait --deadline 0` does neither."""
    assert "mt review wait --round <n> --deadline 0" in TEXT
    assert not re.search(r"`pgrep [^`]*`", TEXT)


def exec_step1() -> str:
    exec_section = TEXT[TEXT.index("## Exec") : TEXT.index("## Review")]
    return exec_section[exec_section.index("1. **Branch and plan commit**") : exec_section.index("2. **Progress**")]


def test_review_base_is_taken_after_the_branch_exists_and_before_the_plan_commit() -> None:
    """EnterWorktree (worktree.baseRef) moves HEAD, so review_base must be read after the branch steps of
    both modes, right before the plan commit."""
    step1 = exec_step1()
    take = step1.index("mt set review_base=$(git -C <toplevel> rev-parse HEAD)")
    assert step1.count("mt set review_base=") == 1
    assert step1.index("the `EnterWorktree`") < take
    assert step1.index("`git -C <toplevel> checkout -b <name>`") < take
    assert take < step1.index('commit -m "docs: add <topic> plan"')
    assert "forks from it" not in TEXT, "EnterWorktree does not fork the user's branch under worktree.baseRef=fresh"


def test_in_place_branch_uses_the_free_name_and_runs_before_anything_is_stashed() -> None:
    """create-branch.sh checks out an existing branch of the plan's name (stale, merged, or held by a
    worktree, so it fails); in place the one free-name recipe of both modes picks the name, and the branch is
    created before the .revmux save and the stash push, so a branch failure never strands the stash."""
    step1 = exec_step1()
    assert "bash <exec_scripts>/create-branch.sh --print-name <plan>" in step1
    assert "bash <exec_scripts>/create-branch.sh <plan>" not in TEXT, "create-branch.sh must not create the branch"
    assert step1.count("while git -C <main_checkout> show-ref") == 1, "one free-name recipe for both modes"
    inplace = step1[step1.index("- in place (or dirty linked worktree)") :]
    order = [
        inplace.index("`git -C <toplevel> checkout -b <name>`"),
        inplace.index("mt_diff --cached -- .revmux"),
        inplace.index("`mt dirty --exclude <plan>`"),
        inplace.index('stash push -u -m "make-task <run_id>"'),
    ]
    assert order == sorted(order)
    assert "| in-place branch: `git -C <toplevel> checkout -b <name>` fails |" in TEXT


def test_harness_commits_with_a_pathspec_not_stage_and_commit() -> None:
    """stage-and-commit.sh runs `git add -- <paths>` and a bare `git commit`, which sweeps whatever else is
    staged into the task commit; the harness rule commits only the named paths."""
    harness = TEXT[TEXT.index("MAKE-TASK HARNESS RULES") : TEXT.index("### Fixer addendum")]
    assert (
        'git --literal-pathspecs add -- <path> ... && git --literal-pathspecs commit -m "<message>" -- <path> ...'
        in harness
    ), "literal pathspecs: a path is never read as a glob"
    assert "Do not use stage-and-commit.sh" in harness
    assert "scripts/stage-and-commit.sh" not in TEXT


def test_revmux_edits_are_set_aside_before_the_finalizer_and_put_back_on_every_path() -> None:
    """`git rebase` refuses a tracked file with unstaged changes, and in place an edited `.revmux/` profile is the
    only dirt left at Finalize: the aside recipe runs before the finalizer, Completion puts the working copies
    back before the index restage, and every stop and the Recovery text cover the saved copies."""
    finalize = TEXT[TEXT.index("### Finalize") : TEXT.index("### Stats")]
    aside = "mt revmux aside --state-dir <state_dir> --toplevel <toplevel>"
    assert finalize.index(aside) < finalize.index("mt checkpoint begin finalize`;")
    completion = TEXT[TEXT.index("### Completion") : TEXT.index("## Errors")]
    put_back = "mt revmux put-back --state-dir <state_dir> --toplevel <toplevel>"
    assert completion.index(put_back) < completion.index("revmux_index=ok")
    stops = TEXT[TEXT.index("**Every stop gives the stash back**") : TEXT.index("## Outside agterm")]
    assert "both Completion step 3 `.revmux/` steps in order (`mt revmux put-back`" in flat(stops)
    # the working-copy backup is never done in shell again: no readlink/ln/cmp recipe for revmux-tree
    assert "mt_same" not in TEXT and "mt_copy" not in TEXT and "readlink" not in TEXT
    recovery = TEXT[TEXT.index("## Recovery") : TEXT.index("## Start")]
    assert "revmux-tree/list" in recovery
    assert "| setting `.revmux/` aside fails" in TEXT


def test_prompt_layers_keep_the_harness_last() -> None:
    """Precedence is this file > the profile's `## <phase>` section > the copy; in the task prompt the profile's
    `## exec` section comes before the harness addendum, so the harness's rules win; the fixer and the finalizer
    get no profile section."""
    precedence = flat(section(TEXT, "## Precedence", "### Running a companion"))
    layers = [
        precedence.index(f"{n}. **{name}") for n, name in enumerate(("This file", "The profile's", "The companion"), 1)
    ]
    assert layers == sorted(layers)
    resolving = flat(section(TEXT, "### Resolving and substituting", "### Harness addendum (task, fixer, finalizer)"))
    step2 = resolving[resolving.index("2. Append the addenda:") : resolving.index("3. Substitute")]
    task = step2[step2.index("task →") : step2.index("; fixer →")]
    assert task.index("the profile's `## exec` section") < task.index("then harness")
    assert "## exec" not in step2[step2.index("; fixer →") :], "only the task prompt gets the profile's section"
    assert "The harness comes after the profile section, so its rules win." in step2
    exec_ = flat(section(TEXT, "## Exec", "## Review"))
    assert "(task → the profile's `## exec` section, then the harness addendum)" in exec_


def test_gates_have_fixed_titles_and_a_passed_rule() -> None:
    """Recovery must tell a gate's approval from a revision: fixed titles and labels, and one rule for "passed"."""
    gates = flat(section(TEXT, "### Gates", "## Subagent prompts"))
    for title in ("`Gate: design`", "`Gate: plan`", "`Gate: review-round <n>`"):
        assert title in gates
    assert 'Its options are "Approve (Recommended)" and "Revise"; typed text is the change the user wants.' in gates
    assert 'Each answer is recorded with `mt decide "<the gate\'s title>" "<Approve, Revise or the text>"`' in gates
    assert "the last entry in `decisions` with its title is `Approve`" in gates
    assert "`assumptions` hold `<its title> → Approve`" in gates
    recovery = flat(section(TEXT, "## Recovery", "## Start"))
    assert "its `review-round` gate again, unless it passed: Gates" in recovery
    assert "the `design` gate when `<gates>` has it and it has not passed" in recovery
    assert "the `plan` gate (unless it passed)" in recovery
    assert "holds no answer" not in recovery and "holds the answer" not in recovery


def test_review_round_gate_records_what_it_takes_away() -> None:
    """Step 6 records every gating finding before the gate: one the gate's text takes from the fixer must be
    re-recorded for the round, and a ruled-out one written into later rounds' input (loop.md: an exclusion not
    written in the round's input gates again), else it gates every later round and the run never ends clean."""
    gates = flat(section(TEXT, "### Gates", "## Subagent prompts"))
    row = gates[gates.index("| `review-round` |") :]
    assert "a gating finding it takes away is recorded for the round" in row
    assert "excluded from every later round (Each round step 8)" in row
    each = section(TEXT, "### Each round `<n>`", "### Reconciling a round")
    step1 = flat(each[each.index("1. Prepare the round") : each.index("\n2. ")])
    assert '"Exclusions the user has stated" lists every finding an earlier `review-round` gate ruled out' in step1
    step8 = flat(each[each.index("8. Stop per loop.md") :])
    order = [
        step8.index("the `review-round` gate"),
        step8.index("run step 6's call for round `<n>` again"),
        step8.index('`--not-gating <id>="gate: <text>"`'),
        step8.index('`--deferred <id>="gate: <text>"`'),
        step8.index(
            '`mt review result <state_dir>/revmux/round-<n>.json --round <n> --not-gating <id>="gate: <text>" ...`'
        ),
        step8.index('lists each ruled-out one under "Exclusions the user has stated"'),
        step8.index("records it with the same flag and reason"),
        step8.index("`mt set stop_reason=clean` and no fixer"),
        step8.index("`mt checkpoint begin review.fix.<n>`"),
    ]
    assert order == sorted(order)
    assert "the same lists plus" in step8
    assert "`<state_dir>/review.md` marks them `not gating: gate: …` and `deferred: gate: …`" in step8


def test_placeholder_check_covers_the_harness_fill_ins() -> None:
    """An unfilled `<exec_scripts>` turns every `[decision]` line into a failed redirection; the step-4 grep
    catches it with the other placeholders."""
    resolving = section(TEXT, "### Resolving and substituting", "### Harness addendum (task, fixer, finalizer)")
    grep = re.search(r"`grep -nE '([^']+)' <state_dir>/prompts/<name>\.md`", resolving)
    assert grep is not None
    tokens = grep.group(1).split("|")
    for fill in ("<exec_scripts>", "<review_base>", "<pre-existing paths>"):
        assert fill in tokens, fill
        assert fill in TEXT[TEXT.index("### Harness addendum") : TEXT.index("```text\nMAKE-TASK HARNESS")]


def test_plan_reviewer_edit_to_the_plan_is_reverted_and_nothing_else_stops_the_run() -> None:
    """The reviewer's Bash can still write: its edit to the plan is reverted. During Plan `<toplevel>` is the user's
    checkout, so a HEAD or `git status` compare would blame the user's own edits and commits on the reviewer."""
    plan = flat(section(TEXT, "## Plan", "## Exec"))
    step3 = plan[plan.index("3. Plan review") : plan.index("4. The `plan` gate")]
    order = [
        step3.index("`cp <plan> <state_dir>/plan.before-review.md`"),
        step3.index("(`cmp`)"),
        step3.index("copy the saved one back"),
        step3.index('`mt assume "reverted the plan reviewer\'s edit to the plan"'),
    ]
    assert order == sorted(order)
    assert "Compare nothing else (no HEAD or `git status` check)" in step3
    for gone in ("rev-parse HEAD", "status --porcelain", "mt fail", "Errors"):
        assert gone not in step3, gone
    errors = section(TEXT, "## Errors", "## Outside agterm")
    assert "plan reviewer changed" not in errors


def test_revmux_rules_on_the_users_data_stay_in_the_base() -> None:
    """Never offering a project profile and never running archive housekeeping are base rules, not only edits in
    the default copy: a replaced or merged copy cannot drop them."""
    overrides = flat(section(TEXT, "### revmux step overrides", "### Before round 1"))
    assert "never create or offer the project profile (`.revmux/profile.md`)" in overrides
    assert "never write a round-local `input/profile.md`" in overrides
    assert "**Archive housekeeping** never runs: no `revmux stats` check and no cleanup question" in overrides
    assert "whatever the review companion's copy says" in overrides
    completion = flat(section(TEXT, "### Completion", "## Errors"))
    assert "skipped archive housekeeping" in completion


def test_autonomous_worktree_names_a_branch_it_may_not_hold() -> None:
    brainstorm = flat(section(TEXT, "## Brainstorm", "## Plan"))
    step4 = brainstorm[brainstorm.index("4. **Last question") : brainstorm.index("5. Write")]
    auto = step4[: step4.index("Otherwise the question:")]
    assert "`branch` set and not `default_branch`" in auto and "`worktree.baseRef`" in auto


def test_first_use_names_the_old_plugin_data_dirs() -> None:
    start = section(TEXT, "## Start", "## Question rule")
    step0 = flat(start[start.index("0. **Profile.**") : start.index("\n1. ")])
    first_use = step0[step0.index("2. `status` is `missing`") : step0.index("3. `status` is `missing`")]
    for path in (
        "`~/.claude/plugins/data/planning-umputun-cc-thingz/`",
        "`planning-rules.md`",
        "`~/.claude/plugins/data/brainstorm-umputun-cc-thingz/`",
        "`brainstorm-rules.md`",
    ):
        assert path in first_use, path
    assert "make-task no longer reads them" in first_use


# ---------------------------------------------------------------- references/profiles.md


def test_profiles_md_is_where_skill_md_points_and_resolves_its_paths() -> None:
    """Start step 0 sends a new named profile and --update there; its bundled paths follow the repo rule."""
    step0 = section(TEXT, "## Start", "## Question rule")
    assert "follow `references/profiles.md`" in step0
    assert PROFILES.is_file()
    assert PROFILES_RESOLUTION_LINE in flat(PROFILES_TEXT)
    for heading in ("## New profile", "## The interview", "## Update flow"):
        assert f"\n{heading}\n" in PROFILES_TEXT, f"profiles.md has no {heading} (SKILL.md Start step 0 names it)"
    # the bundled-file rule: no ${CLAUDE_SKILL_DIR} and no hardcoded install path
    assert "${CLAUDE_SKILL_DIR}" not in PROFILES_TEXT
    assert "~/.claude/skills/make-task" not in PROFILES_TEXT
    assert "`uv run -q --script <skill dir>/scripts/mt`" in PROFILES_TEXT


def test_profiles_md_calls_only_mt_profile_commands() -> None:
    """Setup runs before `mt init`: every call is an `mt profile` command, never with --run, and the flows name
    each command they need."""
    assert len(PROFILES_FULL) >= 15
    named = set()
    for src, argv in PROFILES_FULL:
        resolved = resolve(argv)
        assert resolved is not None, src
        _, words, _ = resolved
        assert words[:1] == ["profile"], f"profiles.md calls `mt {src}`, which is not an `mt profile` command"
        assert "--run" not in argv, f"profiles.md `mt {src}` takes --run"
        named.add(" ".join(words[1:]))
    assert named == {
        "resolve",
        "install-default",
        "status",
        "import",
        "check",
        "outdated",
        "fetch",
        "accept",
        "raise-schema",
    }
    assert set(subparsers(subparsers(PARSER)["profile"])) == named, "an `mt profile` command profiles.md never uses"


def test_profiles_md_phase_fields_match_mt() -> None:
    """The phases table lists the fields `mt profile check` requires and allows, and which phases may be skip."""
    rows = re.findall(r"^\| `([a-z]+)` \| ([^|]+) \| ([^|]+) \| (yes|no) \|$", PROFILES_TEXT, re.MULTILINE)
    assert [r[0] for r in rows] == list(MT.PROFILE_PHASES)
    for pid, required, optional, skip in rows:
        assert tuple(re.findall(r"`([a-z]+)`", required)) == MT.PHASE_FIELDS[pid][0], f"{pid} required fields"
        assert tuple(re.findall(r"`([a-z]+)`", optional)) == MT.PHASE_FIELDS[pid][1], f"{pid} optional fields"
        assert (skip == "yes") == (pid in MT.SKIPPABLE_PHASES), f"{pid} skip"
    for level in MT.AUTONOMY_PRESETS:
        assert f"`{level}`" in section(PROFILES_TEXT, "## The interview", "## Replacing a phase")
    for gate in MT.GATES:
        assert f"`{gate}`" in section(PROFILES_TEXT, "## The interview", "## Replacing a phase")


def test_profiles_md_source_kinds_match_mt() -> None:
    sources = PROFILES_TEXT[PROFILES_TEXT.index("| source | where the files come from |") :]
    for form in ("plugin:<name>@<marketplace>[:<subpath>]", "git:<url>#<ref>[:<subpath>]", "local"):
        assert form in MT.SOURCE_FORMS and f"| `{form}` |" in sources, form
    assert "| `make-task:default/<id>` |" in sources and "make-task:<profile>/<id>" in MT.SOURCE_FORMS


def test_profiles_md_contracts_match_mt() -> None:
    """The exec and review contracts list exactly what `mt profile check` enforces."""
    exec_contract = section(PROFILES_TEXT, "### Exec contract", "### Review contract")
    for name in (*MT.EXEC_CONTRACT_SCRIPTS, *MT.EXEC_CONTRACT_PROMPTS):
        assert f"`{name}`" in exec_contract, f"the exec contract does not list {name}"
    assert "`<exec_scripts>/../references/prompts/`" in exec_contract
    assert "`bash <exec_rules> planning-rules.md <exec_data>`" in exec_contract
    for name in ("<scripts>", "<rules>", "<data dir>"):
        assert name not in PROFILES_TEXT, f"profiles.md names {name}; SKILL.md calls it <exec_...>"
    review_contract = section(PROFILES_TEXT, "### Review contract", "### Where the exec prompts come from")
    for name in MT.REVIEW_CONTRACT_SCRIPTS:
        assert f"`{name}`" in review_contract, f"the review contract does not list {name}"
    assert "`requires: [revmux]`" in review_contract


def test_profiles_md_new_profile_and_interview() -> None:
    """A new profile starts from the template; the interview asks one thing at a time in the planned order and
    ends with `check`; adapting a third-party skill shows a diff and records `## Changes` lines."""
    new = section(PROFILES_TEXT, "## New profile", "## The interview")
    assert new.index("`stop`") < new.index("`mt profile install-default --profile <name>`") < new.index("interview")
    interview = section(PROFILES_TEXT, "## The interview", "## Replacing a phase")
    assert "one at a time" in flat(PROFILES_TEXT[: PROFILES_TEXT.index("## What a profile holds")])
    order = [
        interview.index("`mt profile status --profile <name>`"),
        interview.index("**Autonomy.**"),
        interview.index("**Phases to change.**"),
        interview.index("**Each picked phase**"),
        interview.index("**Preferences.**"),
        interview.index("**Check.** `mt profile check --profile <name>`"),
    ]
    assert order == sorted(order)
    for choice in ("keep", "replace", "skip", "instructions"):
        assert f"- `{choice}`:" in interview, f"the interview does not offer {choice}"
    assert "`## <phase>` section" in interview and "`autonomy:` line" in interview
    replacing = flat(section(PROFILES_TEXT, "## Replacing a phase", "## Phase inputs and outputs"))
    steps = [
        replacing.index("`mt profile import <id> --source <S> --profile <name>`"),
        replacing.index("**Adapt the copy.**"),
        replacing.index("`git diff --no-index <profile_dir>/upstream/<id> <profile_dir>/companions/<id>`"),
        replacing.index("one line under `### <id>` in `## Changes`"),
    ]
    assert steps == sorted(steps)
    assert "never straight to `AskUserQuestion`" in replacing and "never `upstream/<id>/`" in replacing


def test_profiles_md_update_flow() -> None:
    """outdated --network → fetch → the diff → accept (unmodified) or a merge guided by `## Changes`, shown, then
    accept (modified); every accept names the tree the fetch printed, so it installs only what was shown."""
    update = section(PROFILES_TEXT, "## Update flow", "## Issue proposal")
    order = [
        update.index("`mt profile outdated --network --profile <name>`"),
        update.index("`mt profile fetch <id> --profile <name>`"),
        update.index("Show the diff"),
        update.index("`modified` false → `mt profile accept <id> --expect <tree> --profile <name>`"),
        update.index("`modified` true → merge into `companions/<id>` first"),
    ]
    assert order == sorted(order)
    modified = flat(update[update.index("`modified` true →") :])
    assert modified.index("keeping every edit `## Changes` lists") < modified.index("Show the result")
    assert modified.index("Show the result") < modified.index(
        "Then `mt profile accept <id> --expect <tree> --profile <name>`"
    )
    fetch = flat(update[update.index("`mt profile fetch <id>") : update.index("Show the diff")])
    assert "`tree`, the staged tree's hash" in fetch and "passes it as `--expect <tree>`" in fetch
    assert "On that refusal, start the entry again at this step" in fetch
    accepts = [argv for src, argv in PROFILES_FULL if argv[:2] == ["profile", "accept"]]
    assert len(accepts) == 4 and all("--expect" in argv for argv in accepts)
    assert "`git merge-file <companions file> <upstream file> <staging file>`" in modified
    assert "lists only the user's own" in flat(update)
    assert "`--update` without task text" not in update  # SKILL.md Start step 0 owns that stop


def test_profiles_md_issue_proposal_files_only_after_confirmation() -> None:
    proposal = section(PROFILES_TEXT, "## Issue proposal", "## Template maintenance")
    ask = proposal.index("Show the draft and ask: `file` or `note`")
    create = proposal.index('`gh issue create -R rashpile/agterm-experimental --title "<title>" --body-file <file>`')
    assert ask < create
    assert "Never file an issue without this answer" in proposal
    assert "one line under `## Wanted`" in flat(proposal)


def test_profiles_md_template_maintenance_and_prompt_source() -> None:
    """Maintainers run the same commands with --dir and re-apply make-task's `## Changes`; the exec prompts come
    from the profile copy now, no longer from the planning plugin's data dir."""
    maint = section(PROFILES_TEXT, "## Template maintenance")
    for cmd in ("outdated", "fetch <id>", "accept <id> --expect <tree>", "check"):
        assert f"`mt profile {cmd} --dir <template>`" in maint, f"template maintenance lacks mt profile {cmd}"
    assert "`assets/profiles/default`" in maint and "`template: true`" in maint
    assert "every edit listed under `### <id>` must be in the merged copy" in flat(maint)
    assert (MT.ASSETS / "profiles" / "default" / "profile.md").is_file()
    prompts = flat(section(PROFILES_TEXT, "### Where the exec prompts come from", "## Update flow"))
    assert "`~/.claude/plugins/data/planning-umputun-cc-thingz/`" in prompts
    assert "This is a deliberate change." in prompts and "make-task no longer reads them" in prompts
    assert "`.claude/exec-plan/prompts/<name>.md`" in prompts and "`.claude/planning-rules.md`" in prompts
    for name in (
        "`~/.claude/plugins/data/brainstorm-umputun-cc-thingz/`",
        "`planning-rules.md`",
        "`brainstorm-rules.md`",
    ):
        assert name in prompts, name
    assert "`<profile_dir>/data/planning/planning-rules.md`" in prompts
    assert "`<profile_dir>/data/brainstorm/brainstorm-rules.md`" in prompts
    assert "2026-04-15" not in PROFILES_TEXT, "no fact about one machine"


def test_profiles_md_returns_to_a_named_start_step() -> None:
    """Setup hands back to SKILL.md at the check (Start step 0.4), never to step 0 as a whole (0.3 would send
    --update round again)."""
    assert "Back to SKILL.md Start step 0" not in PROFILES_TEXT
    new = flat(section(PROFILES_TEXT, "## New profile", "## The interview"))
    assert "Otherwise continue at SKILL.md Start step 0.4 (`mt profile check`)" in new
    update = flat(section(PROFILES_TEXT, "## Update flow", "## Issue proposal"))
    assert "Continue at SKILL.md Start step 0.4 (`mt profile check`)" in update
    assert "go straight to step 7" in update and "7. Continue at SKILL.md Start step 0.4" in update


def test_profiles_md_update_flow_carries_the_template_profile_md() -> None:
    """Companion updates never touch profile.md or add a companion: the update flow compares the template's
    profile.md and companion ids with the profile's and asks before taking a difference."""
    update = flat(section(PROFILES_TEXT, "## Update flow", "## Issue proposal"))
    step = update[update.index("4. **The template's own changes.**") : update.index("5. **Schema.**")]
    assert "`assets/profiles/default/profile.md`" in step and "`assets/profiles/default/companions/`" in step
    assert "`take` or `keep`" in step
    assert "`mt profile import <id> --source make-task:default/<id> --profile <name>`" in step


def test_profiles_md_update_flow_raises_an_older_schema_after_the_companion_updates() -> None:
    """A PROFILE_SCHEMA bump fails `check` on every older profile; the update flow takes the companion updates
    and then raises the schema with `mt profile raise-schema`, never with an edit, and the check messages
    point there instead of a reinstall that install-default refuses."""
    update = flat(section(PROFILES_TEXT, "## Update flow", "## Issue proposal"))
    order = [
        update.index("`mt profile outdated --network --profile <name>`"),
        update.index("Then `mt profile accept <id> --expect <tree> --profile <name>`"),
        update.index("4. **The template's own changes.**"),
        update.index("5. **Schema.** `mt profile raise-schema --profile <name>`"),
        update.index("6. Ask whether to change the workflow too"),
    ]
    assert order == sorted(order)
    step = update[update.index("5. **Schema.**") : update.index("6. Ask whether")]
    assert "every `make-task:default/<id>` companion records the template's current version" in step
    assert "`take` (recommended: step 3 for those entries, then this step again) or `later`" in step
    assert "Never edit the `schema:` line yourself" in step
    assert "take updates between runs" in update[: update.index("1. Read")]
    interview_check = flat(section(PROFILES_TEXT, "## The interview", "## Replacing a phase"))
    assert "only the Update flow's Schema step raises it, never an edit" in interview_check
    start = flat(section(TEXT, "## Start", "## Question rule"))
    assert "raises an older `schema`" in start and "never the `schema:` line" in start
    assert "reinstall" not in PROFILES_TEXT and "reinstall" not in TEXT


def test_profiles_md_copied_profile_is_explained() -> None:
    holds = flat(section(PROFILES_TEXT, "## What a profile holds", "## First use"))
    assert "`rewritten_for`" in holds and "never by copying a profile's directory" in holds
