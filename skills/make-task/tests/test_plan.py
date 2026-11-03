"""Plan parsing, `mt plan` and `mt refresh`."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from conftest import FIXTURES, MT, git, init_run

PLAN_REL = "docs/plans/20261029-session-store.md"


def add_plan(repo: Path, text: str | None = None, rel: str = PLAN_REL) -> Path:
    plan = repo / rel
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text(text if text is not None else (FIXTURES / "plan.md").read_text())
    return plan


def check_all(plan: Path, header: str) -> None:
    """Tick every box in the section that starts with `header`."""
    out, inside, fenced = [], False, False
    for line in plan.read_text().splitlines():
        if line.startswith("```"):
            fenced = not fenced
        elif line.startswith("#") and not fenced:
            inside = line.startswith(header) or (inside and line.startswith("####"))
        out.append(line.replace("- [ ]", "- [x]") if inside else line)
    plan.write_text("\n".join(out) + "\n")


def by_n(state: dict) -> dict[int, dict]:
    return {t["n"]: t for t in state["tasks"]}


# ---------------------------------------------------------------- parser


def test_parse_fixture():
    tasks = MT.parse_plan((FIXTURES / "plan.md").read_text())
    assert tasks == [
        {"n": 1, "title": "Storage layer", "done": 3, "total": 4},
        {"n": 2, "title": "Login and logout handlers", "done": 1, "total": 3},
        {"n": 3, "title": "Polish", "done": 0, "total": 1},
        {"n": 4, "title": "Done before the run", "done": 1, "total": 1},
    ]


@pytest.mark.parametrize("end", ["# Top", "## Next", "### Notes"])
def test_section_ends_at_h1_h2_h3(end):
    text = f"### Task 1: A\n- [ ] one\n{end}\n- [ ] outside\n"
    assert MT.parse_plan(text) == [{"n": 1, "title": "A", "done": 0, "total": 1}]


def test_h4_does_not_end_section_and_header_needs_colon():
    text = "### Task 1: A\n#### Sub\n- [x] in sub\n### Task two: no\n- [ ] not a task\n### Task 2 missing colon\n"
    assert MT.parse_plan(text) == [{"n": 1, "title": "A", "done": 1, "total": 1}]


def test_tilde_fence_and_unclosed_fence():
    text = "### Task 1: A\n~~~\n## not a header\n- [ ] no\n~~~\n- [ ] yes\n```\n- [ ] never closed\n"
    assert MT.parse_plan(text) == [{"n": 1, "title": "A", "done": 0, "total": 1}]


def test_longer_fence_holds_a_shorter_one():
    """A ```` fence that shows a ``` example closes only at ```` or longer: the example's header and boxes
    stay text, and the real task after the fence is still found."""
    text = (
        "### Task 1: Docs\n- [x] write\n````markdown\n```\n### Task 9: Example\n- [ ] example box\n```\n"
        "````\n- [ ] after the fence\n### Task 2: Real\n- [ ] real\n"
    )
    assert MT.parse_plan(text) == [
        {"n": 1, "title": "Docs", "done": 1, "total": 2},
        {"n": 2, "title": "Real", "done": 0, "total": 1},
    ]


@pytest.mark.parametrize(
    ("opening", "closing"),
    [
        ("~~~~", "~~~"),  # a shorter run
        ("```", "~~~"),  # the other character
        ("~~~", "~~~ text"),  # text after the run
        ("```", "```python"),  # an info string
    ],
)
def test_fence_closes_only_on_a_bare_run_of_the_same_char_at_least_as_long(opening, closing):
    text = f"### Task 1: A\n{opening}\n{closing}\n- [ ] inside\n### Task 2: Hidden\n"
    assert MT.parse_plan(text) == [{"n": 1, "title": "A", "done": 0, "total": 0}]


@pytest.mark.parametrize(("opening", "closing"), [("~~~", "~~~~~"), ("````", "`````  "), ("  ```", "```")])
def test_fence_closes_on_a_longer_or_padded_run(opening, closing):
    text = f"### Task 1: A\n{opening}\n- [ ] inside\n{closing}\n- [ ] outside\n"
    assert MT.parse_plan(text) == [{"n": 1, "title": "A", "done": 0, "total": 1}]


def test_inline_code_with_backticks_does_not_open_a_fence():
    text = "### Task 1: A\n```code``` inline\n- [ ] counted\n"
    assert MT.parse_plan(text) == [{"n": 1, "title": "A", "done": 0, "total": 1}]


def test_plan_with_no_tasks():
    assert MT.parse_plan("# Plan\n\n## Overview\n- [ ] not a task box\n") == []


# ---------------------------------------------------------------- mt plan


def test_plan_records_paths_tasks_and_artifacts(mt, git_repo, run_state, tmp_path):
    run = init_run(mt, git_repo)
    plan = add_plan(git_repo)
    progress = tmp_path / "progress-session-store.txt"
    r = mt("plan", PLAN_REL, "--progress", str(progress), cwd=git_repo)
    assert r.code == 0, r.err
    s = run_state(run)
    assert s["plan_rel"] == PLAN_REL
    assert s["plan_path"] == str(plan)
    assert s["progress_path"] == str(progress)

    tasks = by_n(s)
    assert [t["n"] for t in s["tasks"]] == [1, 2, 3, 4]
    assert (tasks[1]["status"], tasks[1]["done"], tasks[1]["total"]) == ("run", 3, 4)
    assert tasks[1]["started"] and tasks[1]["finished"] is None
    assert tasks[2]["status"] == tasks[3]["status"] == "pending"
    assert tasks[2]["started"] is None
    assert tasks[4]["status"] == "done"
    assert tasks[4]["started"] is None and tasks[4]["finished"] is None, "done before the run: no stamps"

    arts = {a["id"]: a for a in s["artifacts"]}
    assert arts["plan"]["source"] == str(plan)
    assert arts["progress"]["source"] == str(progress)
    texts = [e["text"] for e in s["events"]]
    assert "Task 1 started · Storage layer" in texts
    assert f"Plan {PLAN_REL}" in texts


def test_plan_path_relative_to_cwd(mt, git_repo, run_state):
    run = init_run(mt, git_repo)
    add_plan(git_repo)
    assert mt("plan", "plans/20261029-session-store.md", cwd=git_repo / "docs").code == 0
    s = run_state(run)
    assert s["plan_rel"] == PLAN_REL
    assert s["toplevel"] == str(git_repo)
    assert s["progress_path"] is None


def test_plan_from_inside_a_worktree_records_the_worktree_copy(mt, git_repo, tmp_path, run_state):
    run = init_run(mt, git_repo)
    add_plan(git_repo)
    git(git_repo, "add", PLAN_REL)
    git(git_repo, "commit", "-q", "-m", "plan")
    wt = (tmp_path / "wt").resolve()
    git(git_repo, "worktree", "add", "-q", "-b", "session-store", str(wt))
    assert mt("set", f"worktree_path={wt}").code == 0

    assert mt("plan", PLAN_REL, cwd=wt).code == 0
    s = run_state(run)
    assert s["plan_path"] == str(wt / PLAN_REL)
    assert s["toplevel"] == str(wt)
    assert s["branch"] == "session-store"
    assert s["plan_rel"] == PLAN_REL


def test_plan_missing_file(mt, git_repo, run_state):
    run = init_run(mt, git_repo)
    before = run_state(run)
    r = mt("plan", "docs/plans/nope.md", cwd=git_repo)
    assert r.code == 1
    assert "no such file" in r.err
    assert run_state(run) == before


def test_plan_outside_the_checkout(mt, git_repo, tmp_path):
    init_run(mt, git_repo)
    outside = tmp_path / "elsewhere.md"
    outside.write_text("### Task 1: A\n- [ ] x\n")
    r = mt("plan", str(outside), cwd=git_repo)
    assert r.code == 1
    assert "outside the checkout" in r.err


def test_plan_without_tasks(mt, git_repo, run_state):
    run = init_run(mt, git_repo)
    add_plan(git_repo, "# Plan\n\n## Overview\n- [ ] not a task\n")
    assert mt("plan", PLAN_REL, cwd=git_repo).code == 0
    assert run_state(run)["tasks"] == []
    assert mt("refresh", cwd=git_repo).code == 0
    assert run_state(run)["tasks"] == []


# ---------------------------------------------------------------- mt refresh


def test_refresh_stamps_run_then_done(mt, git_repo, run_state):
    run = init_run(mt, git_repo)
    plan = add_plan(git_repo)
    mt("plan", PLAN_REL, cwd=git_repo)
    t1_started = by_n(run_state(run))[1]["started"]

    assert mt("refresh", cwd=git_repo).code == 0
    s = run_state(run)
    assert by_n(s)[1]["started"] == t1_started, "a refresh keeps the start stamp"
    assert sum(e["text"].startswith("Task 1 started") for e in s["events"]) == 1

    check_all(plan, "### Task 1:")
    assert mt("refresh", cwd=git_repo).code == 0
    s = run_state(run)
    t1, t2 = by_n(s)[1], by_n(s)[2]
    assert t1["status"] == "done" and t1["started"] == t1_started
    assert t1["finished"] >= t1_started and (t1["done"], t1["total"]) == (4, 4)
    assert t2["status"] == "run" and t2["started"] and t2["finished"] is None
    texts = [e["text"] for e in s["events"]]
    assert "Task 1 done · Storage layer" in texts
    assert "Task 2 started · Login and logout handlers" in texts

    t1_finished = t1["finished"]
    check_all(plan, "### Task 2:")
    check_all(plan, "### Iteration 3:")
    assert mt("refresh", cwd=git_repo).code == 0
    s = run_state(run)
    assert all(t["status"] == "done" for t in s["tasks"])
    assert by_n(s)[1]["finished"] == t1_finished
    assert by_n(s)[3]["started"] is None and by_n(s)[3]["finished"], "never seen current: finished only"
    assert by_n(s)[4]["finished"] is None, "done before the run stays unstamped"


def test_refresh_picks_up_a_task_added_later(mt, git_repo, run_state):
    run = init_run(mt, git_repo)
    plan = add_plan(git_repo, "### Task 1: A\n- [ ] one\n")
    mt("plan", PLAN_REL, cwd=git_repo)
    check_all(plan, "### Task 1:")
    mt("refresh", cwd=git_repo)
    t1_finished = by_n(run_state(run))[1]["finished"]
    assert t1_finished

    plan.write_text(plan.read_text() + "\n### Task 2: ➕ B\n- [ ] two\n")
    assert mt("refresh", cwd=git_repo).code == 0
    s = run_state(run)
    assert [(t["n"], t["status"]) for t in s["tasks"]] == [(1, "done"), (2, "run")]
    assert by_n(s)[2]["title"] == "➕ B"
    assert by_n(s)[1]["finished"] == t1_finished


def test_refresh_reopens_a_done_task_when_a_box_is_added(mt, git_repo, run_state):
    run = init_run(mt, git_repo)
    plan = add_plan(git_repo, "### Task 1: A\n- [x] one\n### Task 2: B\n- [ ] two\n")
    mt("plan", PLAN_REL, cwd=git_repo)
    plan.write_text("### Task 1: A\n- [x] one\n- [ ] ➕ one more\n### Task 2: B\n- [ ] two\n")
    assert mt("refresh", cwd=git_repo).code == 0
    s = run_state(run)
    t1, t2 = by_n(s)[1], by_n(s)[2]
    assert t1["status"] == "run" and t1["finished"] is None and t1["started"]
    assert t2["status"] == "pending"


def test_refresh_follows_plan_moved_to_completed(mt, git_repo, run_state):
    run = init_run(mt, git_repo)
    plan = add_plan(git_repo)
    mt("plan", PLAN_REL, cwd=git_repo)
    moved = plan.parent / "completed" / plan.name
    moved.parent.mkdir()
    shutil.move(plan, moved)

    assert mt("refresh", cwd=git_repo).code == 0
    s = run_state(run)
    assert s["plan_path"] == str(moved)
    assert s["plan_rel"] == PLAN_REL
    assert next(a for a in s["artifacts"] if a["id"] == "plan")["source"] == str(moved)
    assert len(s["tasks"]) == 4


def test_refresh_missing_plan_fails_and_keeps_state(mt, git_repo, run_state):
    run = init_run(mt, git_repo)
    plan = add_plan(git_repo)
    mt("plan", PLAN_REL, cwd=git_repo)
    before = run_state(run)
    plan.unlink()
    r = mt("refresh", cwd=git_repo)
    assert r.code == 1
    assert "not found" in r.err
    assert run_state(run) == before


def test_refresh_from_main_checkout_in_worktree_mode_fails(mt, git_repo, tmp_path, run_state):
    run = init_run(mt, git_repo)
    wt = (tmp_path / "wt").resolve()
    git(git_repo, "worktree", "add", "-q", "-b", "session-store", str(wt))
    mt("set", f"worktree_path={wt}")
    before = run_state(run)

    r = mt("refresh", cwd=git_repo)
    assert r.code == 1
    assert str(wt) in r.err
    assert run_state(run) == before

    assert mt("refresh", cwd=wt / "src").code == 0
    s = run_state(run)
    assert s["toplevel"] == str(wt) and s["branch"] == "session-store"


def test_refresh_without_plan_updates_branch(mt, git_repo, run_state):
    run = init_run(mt, git_repo)
    git(git_repo, "checkout", "-q", "-b", "session-store")
    assert mt("refresh", cwd=git_repo).code == 0
    s = run_state(run)
    assert s["branch"] == "session-store"
    assert s["tasks"] == [] and s["plan_path"] is None


def test_refresh_outside_git(mt, git_repo, tmp_path):
    init_run(mt, git_repo)
    outside = tmp_path / "plain"
    outside.mkdir()
    r = mt("refresh", cwd=outside)
    assert r.code == 1
    assert "not inside a git work tree" in r.err


@pytest.mark.parametrize("args", [("plan", "x.md"), ("refresh",)])
def test_without_active_run(mt, git_repo, args):
    r = mt(*args, cwd=git_repo)
    assert r.code == 1
    assert "no active run" in r.err
