"""Plan parsing, `mt plan`, `mt refresh` and the plan's data script."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from conftest import FIXTURES, MT, git, init_run
from test_artifacts import artifact_data, bump_mtime

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


def counts(text: str) -> list[dict]:
    """parse_plan without the items, after checking that the counts are the items'."""
    out = []
    for task in MT.parse_plan(text):
        items = task.pop("items")
        assert (task["done"], task["total"]) == (sum(i["checked"] for i in items), len(items)), task
        out.append(task)
    return out


def items_of(text: str) -> dict[int, list[tuple[str, bool]]]:
    return {t["n"]: [(i["text"], i["checked"]) for i in t["items"]] for t in MT.parse_plan(text)}


FIXTURE_ITEMS = {
    1: [
        ("create the schema", True),
        ("write the store", True),
        ("an indented sub-item counts", True),
        ("a box under a #### subsection still belongs to task 1", False),
    ],
    2: [("login handler", True), ("logout handler", False), ("write tests", False)],
    3: [("tidy the README", False)],
    4: [("already done", True)],
}


# ---------------------------------------------------------------- parser


def test_parse_fixture():
    text = (FIXTURES / "plan.md").read_text()
    assert counts(text) == [
        {"n": 1, "title": "Storage layer", "done": 3, "total": 4},
        {"n": 2, "title": "Login and logout handlers", "done": 1, "total": 3},
        {"n": 3, "title": "Polish", "done": 0, "total": 1},
        {"n": 4, "title": "Done before the run", "done": 1, "total": 1},
    ]
    assert items_of(text) == FIXTURE_ITEMS
    assert all(set(i) == {"text", "checked"} for t in MT.parse_plan(text) for i in t["items"])


def test_items_of_nested_lists_in_file_order():
    text = (
        "### Task 1: A\n"
        "- [ ] top\n"
        "  - [x] nested\n"
        "    - [X] deeper `code`\n"
        "  - a plain sub-bullet\n"
        "  continuation of the paragraph\n"
        "- [ ]   padded text  \n"
        "- [x]\n"
        "* [ ] a star bullet\n"
        "1. [ ] an ordered item\n"
    )
    assert items_of(text) == {
        1: [("top", False), ("nested", True), ("deeper `code`", True), ("padded text", False), ("", True)]
    }


def test_items_skip_boxes_inside_fences():
    text = (
        "### Task 1: A\n"
        "- [ ] before\n"
        "```markdown\n- [x] backtick fence\n```\n"
        "~~~\n- [ ] tilde fence\n~~~\n"
        "- [x] item with an example\n"
        "  ````\n"
        "  ```\n"
        "  - [ ] a longer fence holds a shorter one\n"
        "  ```\n"
        "  ````\n"
        "- [ ] after\n"
        "```\n- [ ] in a fence never closed\n"
    )
    assert items_of(text) == {1: [("before", False), ("item with an example", True), ("after", False)]}


def test_items_keep_h4_subsections_and_stop_at_the_section_end():
    text = (
        "### Task 1: A\n- [x] one\n#### Notes\n- [ ] in the subsection\n"
        "### Task 2: B\n#### Only a subsection\n- [ ] two\n"
        "## Post-Completion\n- [ ] belongs to no task\n"
    )
    assert items_of(text) == {1: [("one", True), ("in the subsection", False)], 2: [("two", False)]}


def test_task_with_no_boxes_has_no_items():
    text = "### Task 1: Verify\nRead the code.\n\n- a plain bullet\n### Task 2: B\n- [ ] two\n"
    assert MT.parse_plan(text)[0] == {"n": 1, "title": "Verify", "done": 0, "total": 0, "items": []}


def test_plan_items_by_task_with_inline_html():
    text = (
        "### Task 1: A\n- [x] add `mt plan` and **bold**\n- [ ] <script>alert(1)</script> & co\n"
        "### Task 2: Nothing to tick\n"
        "### Task 1: A again\n- [ ] the first section with this number wins\n"
    )
    assert MT.plan_items(text) == {
        "1": [
            {
                "text": "add `mt plan` and **bold**",
                "checked": True,
                "html": "add <code>mt plan</code> and <strong>bold</strong>",
            },
            {
                "text": "<script>alert(1)</script> & co",
                "checked": False,
                "html": "&lt;script&gt;alert(1)&lt;/script&gt; &amp; co",
            },
        ],
        "2": [],
    }
    assert MT.plan_items("# no tasks\n- [ ] x\n") == {}


@pytest.mark.parametrize("end", ["# Top", "## Next", "### Notes"])
def test_section_ends_at_h1_h2_h3(end):
    text = f"### Task 1: A\n- [ ] one\n{end}\n- [ ] outside\n"
    assert counts(text) == [{"n": 1, "title": "A", "done": 0, "total": 1}]


def test_h4_does_not_end_section_and_header_needs_colon():
    text = "### Task 1: A\n#### Sub\n- [x] in sub\n### Task two: no\n- [ ] not a task\n### Task 2 missing colon\n"
    assert counts(text) == [{"n": 1, "title": "A", "done": 1, "total": 1}]


def test_tilde_fence_and_unclosed_fence():
    text = "### Task 1: A\n~~~\n## not a header\n- [ ] no\n~~~\n- [ ] yes\n```\n- [ ] never closed\n"
    assert counts(text) == [{"n": 1, "title": "A", "done": 0, "total": 1}]


def test_longer_fence_holds_a_shorter_one():
    """A ```` fence that shows a ``` example closes only at ```` or longer: the example's header and boxes
    stay text, and the real task after the fence is still found."""
    text = (
        "### Task 1: Docs\n- [x] write\n````markdown\n```\n### Task 9: Example\n- [ ] example box\n```\n"
        "````\n- [ ] after the fence\n### Task 2: Real\n- [ ] real\n"
    )
    assert counts(text) == [
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
    assert counts(text) == [{"n": 1, "title": "A", "done": 0, "total": 0}]


@pytest.mark.parametrize(("opening", "closing"), [("~~~", "~~~~~"), ("````", "`````  "), ("  ```", "```")])
def test_fence_closes_on_a_longer_or_padded_run(opening, closing):
    text = f"### Task 1: A\n{opening}\n- [ ] inside\n{closing}\n- [ ] outside\n"
    assert counts(text) == [{"n": 1, "title": "A", "done": 0, "total": 1}]


def test_inline_code_with_backticks_does_not_open_a_fence():
    text = "### Task 1: A\n```code``` inline\n- [ ] counted\n"
    assert counts(text) == [{"n": 1, "title": "A", "done": 0, "total": 1}]


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


# ---------------------------------------------------------------- the plan's data script


def payload_items(run: Path) -> dict[int, list[tuple[str, bool]]]:
    data = artifact_data(run, "plan")
    return {int(n): [(i["text"], i["checked"]) for i in items] for n, items in data["items"].items()}


def test_plan_writes_the_plan_payload(mt, git_repo, run_state):
    run = init_run(mt, git_repo)
    plan = add_plan(git_repo)
    assert mt("plan", PLAN_REL, cwd=git_repo).code == 0
    data = artifact_data(run, "plan")
    assert sorted(data) == ["html", "items", "source", "title", "toc", "updated"]
    assert data["title"] == "Session store" and data["source"] == str(plan)
    assert {"level": 3, "id": "doc-task-1-storage-layer", "text": "Task 1: Storage layer"} in data["toc"]
    assert payload_items(run) == FIXTURE_ITEMS
    assert data["items"]["1"][0] == {"text": "create the schema", "checked": True, "html": "create the schema"}
    assert all("items" not in t for t in run_state(run)["tasks"]), "the items stay out of state.json"


def test_plan_payload_takes_its_items_from_the_text_of_its_document(tmp_path):
    gone = str(tmp_path / "gone.md")  # read again, it would raise
    doc = {"title": "T", "html": "<p>x</p>", "toc": [], "source": gone}
    payload = MT.artifact_payload(tmp_path, {}, {"id": "plan", "source": gone}, doc, "### Task 1: A\n- [x] one\n")
    assert payload == {**doc, "items": {"1": [{"text": "one", "checked": True, "html": "one"}]}}
    assert "items" not in doc, "the caller's document stays as it was"


def test_artifact_plan_writes_the_plan_payload(mt, git_repo):
    run = init_run(mt, git_repo)
    plan = add_plan(git_repo)
    assert mt("artifact", "plan", str(plan), cwd=git_repo).code == 0
    assert payload_items(run) == FIXTURE_ITEMS


def test_a_ticked_box_writes_the_plan_payload_again_with_items(mt, git_repo, run_state):
    run = init_run(mt, git_repo)
    plan = add_plan(git_repo)
    assert mt("plan", PLAN_REL, cwd=git_repo).code == 0
    (run / "artifacts" / "plan.js").unlink()
    assert mt("refresh", cwd=git_repo).code == 0
    first = artifact_data(run, "plan")
    assert payload_items(run) == FIXTURE_ITEMS, "mt refresh writes a missing data script"

    check_all(plan, "### Task 1:")
    bump_mtime(plan)
    assert mt("refresh", cwd=git_repo).code == 0
    data = artifact_data(run, "plan")
    assert sorted(data) == sorted(first) and data["updated"] > first["updated"]
    assert payload_items(run)[1] == [(text, True) for text, _ in FIXTURE_ITEMS[1]]
    assert data["items"]["2"] == first["items"]["2"]

    # render_changed, which every command that renders goes through, keeps the plan's shape
    check_all(plan, "### Task 2:")
    bump_mtime(plan, 20)
    state = run_state(run)
    MT.render_changed(run, state)
    data = artifact_data(run, "plan")
    assert sorted(data) == sorted(first), "never the document-only shape"
    assert payload_items(run)[2] == [(text, True) for text, _ in FIXTURE_ITEMS[2]]
    assert next(a for a in state["artifacts"] if a["id"] == "plan")["mtime"] == plan.stat().st_mtime
