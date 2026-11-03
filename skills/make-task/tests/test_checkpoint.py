"""Checkpoint journal, `mt status` with begun checkpoints, `mt fail` and failure exits."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from conftest import init_run


def checkpoint(state: dict, key: str) -> dict:
    return next(c for c in state["checkpoints"] if c["key"] == key)


# ---------------------------------------------------------------- checkpoint lifecycle


def test_begin_end_lifecycle(mt, git_repo, run_state):
    run = init_run(mt, git_repo)
    assert mt("checkpoint", "begin", "exec.task.1").code == 0
    cp = checkpoint(run_state(run), "exec.task.1")
    assert cp["state"] == "begun" and cp["result"] is None
    assert cp["at"] > 0 and cp["ended"] is None

    assert mt("checkpoint", "end", "exec.task.1", "--result", "boxes checked").code == 0
    cp = checkpoint(run_state(run), "exec.task.1")
    assert cp["state"] == "ended" and cp["result"] == "boxes checked"
    assert cp["ended"] >= cp["at"]


def test_status_lists_begun_and_ended(mt, git_repo):
    init_run(mt, git_repo)
    mt("checkpoint", "begin", "exec.task.1")
    mt("checkpoint", "end", "exec.task.1", "--result", "done")
    mt("checkpoint", "begin", "exec.task.2")
    mt("checkpoint", "begin", "finalize")
    out = mt("status").json()
    assert [c["key"] for c in out["begun"]] == ["exec.task.2", "finalize"]
    assert out["ended"] == [{"key": "exec.task.1", "result": "done"}]
    assert out["error"] is None


def test_double_begin_refused(mt, git_repo, run_state):
    run = init_run(mt, git_repo)
    mt("checkpoint", "begin", "review.fix.1")
    before = run_state(run)
    r = mt("checkpoint", "begin", "review.fix.1")
    assert r.code == 1
    assert "already begun" in r.err and "--resume" in r.err
    assert run_state(run) == before


def test_resume_keeps_one_begun_entry(mt, git_repo, run_state):
    run = init_run(mt, git_repo)
    mt("checkpoint", "begin", "exec.task.3")
    first = checkpoint(run_state(run), "exec.task.3")["at"]
    r = mt("checkpoint", "begin", "exec.task.3", "--resume")
    assert r.code == 0, r.err
    s = run_state(run)
    assert len([c for c in s["checkpoints"] if c["key"] == "exec.task.3"]) == 1
    cp = checkpoint(s, "exec.task.3")
    assert cp["state"] == "begun" and cp["at"] >= first


def test_begin_after_end_starts_a_new_attempt(mt, git_repo, run_state):
    run = init_run(mt, git_repo)
    mt("checkpoint", "begin", "finalize")
    mt("checkpoint", "end", "finalize", "--result", "rebase failed")
    assert mt("checkpoint", "begin", "finalize").code == 0
    s = run_state(run)
    assert len(s["checkpoints"]) == 1
    cp = checkpoint(s, "finalize")
    assert cp["state"] == "begun" and cp["result"] is None and cp["ended"] is None


def test_end_twice_is_harmless(mt, git_repo, run_state):
    run = init_run(mt, git_repo)
    mt("checkpoint", "begin", "exec.task.1")
    mt("checkpoint", "end", "exec.task.1", "--result", "ok")
    ended = checkpoint(run_state(run), "exec.task.1")["ended"]
    assert mt("checkpoint", "end", "exec.task.1").code == 0
    cp = checkpoint(run_state(run), "exec.task.1")
    assert cp["state"] == "ended" and cp["result"] == "ok" and cp["ended"] == ended


# ---------------------------------------------------------------- fail and recovery


def test_fail_then_activity_clears_error_and_run_continues(mt, git_repo, run_state):
    run = init_run(mt, git_repo)
    mt("phase", "exec", "--activity", "Task 3")
    assert mt("fail", "task 3 failed after one retry").code == 0
    s = run_state(run)
    assert s["error"] == "task 3 failed after one retry"
    assert s["now_kind"] == "error"
    assert s["phases"][2]["status"] == "error"
    assert s["finished"] is None and s["final_status"] is None
    assert s["events"][-1]["text"] == "Error: task 3 failed after one retry"

    out = mt("status").json()
    assert out["error"] == "task 3 failed after one retry"
    assert out["phase"] == "exec" and out["phase_status"] == "error"

    assert mt("activity", "Retrying task 3").code == 0
    s = run_state(run)
    assert s["error"] is None and s["now_kind"] == ""
    assert s["phases"][2]["status"] == "run"
    assert s["activity"] == "Retrying task 3"

    assert mt("checkpoint", "begin", "exec.task.3", "--resume").code == 1  # never begun
    assert mt("checkpoint", "begin", "exec.task.3").code == 0
    assert mt("phase", "review").code == 0
    s = run_state(run)
    assert s["phases"][2]["status"] == "done" and s["phases"][3]["status"] == "run"


def test_fail_then_phase_resumes(mt, git_repo, run_state):
    run = init_run(mt, git_repo)
    mt("phase", "review")
    mt("fail", "revmux exit 2")
    assert mt("phase", "review", "--activity", "Re-running round 1").code == 0
    s = run_state(run)
    assert s["error"] is None and s["phases"][3]["status"] == "run"
    assert s["activity"] == "Re-running round 1"


def test_fail_before_any_phase(mt, git_repo, run_state):
    run = init_run(mt, git_repo)
    assert mt("fail", "revmux preflight failed").code == 0
    s = run_state(run)
    assert s["error"] == "revmux preflight failed"
    assert all(p["status"] == "pending" for p in s["phases"])


def test_fail_empty_message(mt, git_repo):
    init_run(mt, git_repo)
    r = mt("fail", "  ")
    assert r.code == 1 and "must not be empty" in r.err


# ---------------------------------------------------------------- errors


def test_end_of_unknown_key(mt, git_repo, run_state):
    run = init_run(mt, git_repo)
    before = run_state(run)
    r = mt("checkpoint", "end", "exec.task.9")
    assert r.code == 1 and "unknown key 'exec.task.9'" in r.err
    assert run_state(run) == before


@pytest.mark.parametrize("key", ["", "a b", "a/b", ".hidden"])
def test_invalid_key(mt, git_repo, key):
    init_run(mt, git_repo)
    r = mt("checkpoint", "begin", key)
    assert r.code == 1 and "invalid key" in r.err


def test_key_that_looks_like_a_flag_is_an_argparse_error(mt, git_repo):
    init_run(mt, git_repo)
    r = mt("checkpoint", "begin", "-x")
    assert r.code == 2 and "the following arguments are required: KEY" in r.err


@pytest.mark.parametrize(
    "args",
    [
        ("checkpoint", "begin", "exec.task.1"),
        ("checkpoint", "end", "exec.task.1"),
        ("fail", "boom"),
    ],
)
def test_without_active_run(mt, args):
    r = mt(*args)
    assert r.code == 1 and "no active run" in r.err


@pytest.fixture
def read_only(tmp_path):
    """Make a directory read-only for the test and restore it afterwards."""
    made: list[Path] = []

    def lock(path: Path) -> None:
        path.chmod(stat.S_IRUSR | stat.S_IXUSR)
        made.append(path)

    yield lock
    for path in made:
        path.chmod(stat.S_IRWXU)


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
@pytest.mark.parametrize(
    "args",
    [
        ("checkpoint", "begin", "exec.task.1"),
        ("fail", "boom"),
        ("set", "review_base=abc"),
        ("phase", "exec"),
        ("plan", "missing.md"),
    ],
)
def test_write_failure_read_only_run_dir(mt, git_repo, run_state, read_only, args):
    run = init_run(mt, git_repo)
    if args[0] == "plan":
        (git_repo / "missing.md").write_text("### Task 1: x\n- [ ] a\n")
    before = run_state(run)
    read_only(run)
    r = mt(*args)
    assert r.code == 1
    # `plan` also tries to render the plan artifact first, which warns before the save fails
    assert r.err.splitlines()[-1].startswith("mt: error: cannot write state in ")
    assert str(run) in r.err
    assert run_state(run) == before
    assert sorted(p.name for p in run.iterdir()) == ["dashboard.html", "state.js", "state.json"]


def test_corrupt_state_json(mt, git_repo):
    run = init_run(mt, git_repo)
    (run / "state.json").write_text("{not json")
    r = mt("checkpoint", "begin", "exec.task.1")
    assert r.code == 1 and "cannot read" in r.err and "state.json" in r.err
