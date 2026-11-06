"""`mt review start|wait|result|verify-final`: rounds, waiting on revmux, review.md, stale-review check."""

from __future__ import annotations

import json
import shutil
import stat
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import FIXTURES, MT, commit, git, init_run

FINDINGS = FIXTURES / "revmux-findings.json"
DEGRADED = FIXTURES / "revmux-degraded.json"
EVENTS = FIXTURES / "events.jsonl"
TASK = "session-store"


def start(mt, n: int, name: str, round_dir: Path) -> dict:
    r = mt("review", "start", "--round", str(n), "--run", name, "--round-dir", str(round_dir))
    assert r.code == 0, r.err
    return r.json()


def rnd(state: dict, n: int) -> dict:
    return next(r for r in state["review"] if r["round"] == n)


@pytest.fixture
def review_run(mt, git_repo, tmp_path) -> tuple[Path, Path]:
    """An initialized run with revmux_task set and round 1 started; returns (run dir, round dir)."""
    run = init_run(mt, git_repo)
    assert mt("set", f"revmux_task={TASK}").code == 0
    round_dir = tmp_path / "tasks" / TASK / "01-initial"
    round_dir.mkdir(parents=True)
    start(mt, 1, "01-initial", round_dir)
    return run, round_dir


# ---------------------------------------------------------------- start


def test_start_opens_round_and_prints_report_path(mt, git_repo, tmp_path, run_state):
    run = init_run(mt, git_repo)
    out = start(mt, 1, "01-initial", tmp_path / "rd")
    assert out == {
        "round": 1,
        "run": "01-initial",
        "round_dir": str(tmp_path / "rd"),
        "report": str(run / "revmux" / "round-1.json"),
    }
    assert (run / "revmux").is_dir()
    state = run_state(run)
    r = rnd(state, 1)
    assert r["run"] == "01-initial" and r["round_dir"] == str(tmp_path / "rd")
    assert r["critical"] == r["major"] == r["minor"] == r["open_questions"] == 0
    assert r["degraded"] == [] and r["verdict"] is None and r["milestone"] == "starting"
    assert r["started"] > 0 and r["finished"] is None
    assert state["activity"] == "Review round 1 · starting"
    assert state["events"][-1]["text"] == "Review round 1 started · 01-initial"


def test_start_records_the_head_the_round_reviews(mt, git_repo, tmp_path, run_state):
    run = init_run(mt, git_repo)
    first = git(git_repo, "rev-parse", "HEAD")
    start(mt, 1, "01-initial", tmp_path / "rd")
    assert rnd(run_state(run), 1)["head"] == first
    (git_repo / "README.md").write_text("a commit before the round is reconciled\n")
    git(git_repo, "commit", "-q", "-am", "later")
    start(mt, 1, "01-initial", tmp_path / "rd")
    assert rnd(run_state(run), 1)["head"] == first, "a repeated start keeps the head the launch reviewed"
    start(mt, 2, "02-after-fix", tmp_path / "rd2")
    assert rnd(run_state(run), 2)["head"] == git(git_repo, "rev-parse", "HEAD")
    status = mt("status").json()
    assert [r["head"] for r in status["rounds"]] == [first, rnd(run_state(run), 2)["head"]]


def test_start_outside_the_run_tree_is_refused(mt, git_repo, tmp_path, run_state):
    run = init_run(mt, git_repo)
    before = run_state(run)
    r = mt("--run", str(run), "review", "start", "--round", "1", "--run", "01", "--round-dir", "/x", cwd=tmp_path)
    assert r.code == 1 and "not inside a git work tree" in r.err
    assert run_state(run) == before


def test_start_relative_round_dir_resolved_against_cwd(mt, git_repo, run_state):
    run = init_run(mt, git_repo)
    start(mt, 1, "01-initial", Path("rel/dir"))
    assert rnd(run_state(run), 1)["round_dir"] == str(git_repo / "rel" / "dir")


def test_start_again_same_name_keeps_start_time(mt, git_repo, tmp_path, run_state):
    run = init_run(mt, git_repo)
    start(mt, 1, "01-initial", tmp_path / "rd")
    first = rnd(run_state(run), 1)["started"]
    n_events = len(run_state(run)["events"])
    time.sleep(0.01)
    start(mt, 1, "01-initial", tmp_path / "rd")
    state = run_state(run)
    assert rnd(state, 1)["started"] == first
    assert len(state["events"]) == n_events


def test_start_with_another_name_on_a_started_round_is_refused(mt, git_repo, tmp_path, run_state):
    """revmux cannot reuse a round name: a re-run is always the next round, never a replacement."""
    run = init_run(mt, git_repo)
    start(mt, 1, "01-initial", tmp_path / "rd1")
    before = run_state(run)
    r = mt("review", "start", "--round", "1", "--run", "01b-rerun", "--round-dir", str(tmp_path / "rd2"))
    assert r.code == 1 and "already started as 01-initial" in r.err and "new round 2" in r.err
    assert run_state(run) == before


def test_rounds_kept_in_order(mt, git_repo, tmp_path, run_state):
    run = init_run(mt, git_repo)
    start(mt, 2, "02-after-fix", tmp_path / "rd2")
    start(mt, 1, "01-initial", tmp_path / "rd1")
    assert [r["round"] for r in run_state(run)["review"]] == [1, 2]


def test_start_refused_after_result(mt, review_run, run_state):
    run, _ = review_run
    assert mt("review", "result", str(FINDINGS), "--round", "1").code == 0
    before = run_state(run)
    r = mt("review", "start", "--round", "1", "--run", "01b", "--round-dir", "/x")
    assert r.code == 1 and "already has a result" in r.err
    assert run_state(run) == before


@pytest.mark.parametrize(
    "argv, msg",
    [
        (["--round", "0", "--run", "x", "--round-dir", "/x"], "--round must be 1 or more"),
        (["--round", "1", "--run", " ", "--round-dir", "/x"], "must not be empty"),
    ],
)
def test_start_bad_args(mt, git_repo, argv, msg):
    init_run(mt, git_repo)
    r = mt("review", "start", *argv)
    assert r.code == 1 and msg in r.err


def test_start_without_active_run(mt, git_repo):
    r = mt("review", "start", "--round", "1", "--run", "x", "--round-dir", "/x", cwd=git_repo)
    assert r.code == 1 and "no active run" in r.err


# ---------------------------------------------------------------- result


def test_result_counts_verdict_and_unresolved(mt, review_run, run_state):
    run, _ = review_run
    r = mt("review", "result", str(FINDINGS), "--round", "1")
    assert r.code == 0, r.err
    state = run_state(run)
    r1 = rnd(state, 1)
    assert (r1["critical"], r1["major"], r1["minor"], r1["open_questions"]) == (1, 3, 2, 1)
    assert r1["degraded"] == [] and r1["verdict"] == "4 gating"
    assert r1["duration"] == pytest.approx(361.673)
    assert r1["finished"] >= r1["started"] and r1["milestone"] == "done"
    assert [u["id"] for u in state["unresolved"]] == ["bugs+impl-1", "bugs+impl-2", "adversarial-1", "adversarial-2"]
    assert state["unresolved"][0] == {
        "id": "bugs+impl-1",
        "severity": "critical",
        "file": "src/store.go",
        "line": 88,
        "title": "Session map written without the lock",
    }
    assert state["deferred"] == [] and state["not_gating"] == []
    assert state["activity"] == "Review round 1 · 4 gating"
    assert state["events"][-1]["text"] == "Review round 1: 4 gating"


def test_not_gating_and_deferred_leave_unresolved(mt, review_run, run_state):
    run, _ = review_run
    r = mt(
        "review",
        "result",
        str(FINDINGS),
        "--round",
        "1",
        "--not-gating",
        "adversarial-2=vendored code is outside scope per goal.md",
        "--deferred",
        "adversarial-1=prose-only; batch fix at the end per loop.md",
    )
    assert r.code == 0, r.err
    state = run_state(run)
    assert [u["id"] for u in state["unresolved"]] == ["bugs+impl-1", "bugs+impl-2"]
    assert state["not_gating"] == [{"id": "adversarial-2", "reason": "vendored code is outside scope per goal.md"}]
    assert state["deferred"] == [
        {
            "id": "adversarial-1",
            "severity": "major",
            "file": "README.md",
            "line": 0,
            "title": "README documents the old flag",
            "reason": "prose-only; batch fix at the end per loop.md",
            "round": 1,
        }
    ]
    assert rnd(state, 1)["verdict"] == "2 gating"


def test_all_gating_excluded_is_clean(mt, review_run, run_state):
    run, _ = review_run
    args = ["--not-gating", "bugs+impl-1=x", "--not-gating", "bugs+impl-2=y", "--deferred", "adversarial-1=prose"]
    assert mt("review", "result", str(FINDINGS), "--round", "1", *args, "--not-gating", "adversarial-2=z").code == 0
    state = run_state(run)
    assert state["unresolved"] == [] and rnd(state, 1)["verdict"] == "clean"
    assert [d["id"] for d in state["deferred"]] == ["adversarial-1"]


def test_degraded_fixture(mt, review_run, run_state):
    run, _ = review_run
    assert mt("review", "result", str(DEGRADED), "--round", "1").code == 0
    state = run_state(run)
    r1 = rnd(state, 1)
    assert r1["degraded"] == ["adversarial"] and r1["verdict"] == "degraded"
    assert (r1["critical"], r1["major"], r1["minor"], r1["open_questions"]) == (0, 0, 1, 0)
    assert r1["duration"] == pytest.approx(120.0)
    assert state["unresolved"] == []


def test_clean_report(mt, review_run, tmp_path, run_state):
    run, _ = review_run
    clean = json.loads(FINDINGS.read_text())
    clean["findings"] = [f for f in clean["findings"] if f["severity"] == "minor"]
    path = tmp_path / "clean.json"
    path.write_text(json.dumps(clean))
    assert mt("review", "result", str(path), "--round", "1").code == 0
    state = run_state(run)
    assert rnd(state, 1)["verdict"] == "clean" and state["unresolved"] == []


def test_verdict_is_always_computed(mt, review_run):
    r = mt("review", "result", str(FINDINGS), "--round", "1", "--verdict", "not converging")
    assert r.code == 2 and "unrecognized arguments: --verdict" in r.err


@pytest.mark.parametrize("flag", ["--not-gating", "--deferred"])
def test_minor_findings_cannot_be_listed(mt, review_run, run_state, flag):
    """Only critical/major gate: a deferred minor would block `clean` for the rest of the run."""
    run, _ = review_run
    before = run_state(run)
    r = mt("review", "result", str(FINDINGS), "--round", "1", flag, "bugs+impl-3=minor")
    assert r.code == 1 and "bugs+impl-3 not critical or major" in r.err
    assert run_state(run) == before


def test_result_twice_overwrites(mt, review_run, run_state):
    run, _ = review_run
    mt("review", "result", str(FINDINGS), "--round", "1")
    assert mt("review", "result", str(DEGRADED), "--round", "1").code == 0
    state = run_state(run)
    assert len(state["review"]) == 1 and rnd(state, 1)["verdict"] == "degraded"


def test_gate_rerecord_takes_findings_out_of_the_round(mt, review_run, run_state):
    """SKILL.md Each round step 8: a `review-round` gate that takes findings from the fixer re-runs step 6's call with
    the same lists plus a flag per finding; the second call replaces the round's result."""
    run, _ = review_run
    step6 = ["--round", "1", "--not-gating", "adversarial-2=vendored, outside scope"]
    assert mt("review", "result", str(FINDINGS), *step6).code == 0
    assert rnd(run_state(run), 1)["verdict"] == "3 gating"
    gate = [
        "--not-gating",
        "bugs+impl-2=gate: leave the retry loop",
        "--deferred",
        "adversarial-1=gate: docs at the end",
    ]
    assert mt("review", "result", str(FINDINGS), *step6, *gate).code == 0
    state = run_state(run)
    assert len(state["review"]) == 1 and rnd(state, 1)["verdict"] == "1 gating"
    assert [u["id"] for u in state["unresolved"]] == ["bugs+impl-1"]
    assert state["not_gating"] == [
        {"id": "adversarial-2", "reason": "vendored, outside scope"},
        {"id": "bugs+impl-2", "reason": "gate: leave the retry loop"},
    ]
    assert [(d["id"], d["reason"]) for d in state["deferred"]] == [("adversarial-1", "gate: docs at the end")]
    assert mt("status").json()["unresolved"] == 1
    md = (run / "review.md").read_text()
    assert "(bugs+impl-2) — not gating: gate: leave the retry loop" in md
    assert "(adversarial-1) — deferred: gate: docs at the end" in md
    # the gate took the last gating finding away: the round is clean
    last = ["--not-gating", "bugs+impl-1=gate: not this change"]
    assert mt("review", "result", str(FINDINGS), *step6, *gate, *last).code == 0
    state = run_state(run)
    assert rnd(state, 1)["verdict"] == "clean" and state["unresolved"] == []


def test_unresolved_follows_the_latest_round(mt, review_run, tmp_path, run_state):
    run, _ = review_run
    mt("review", "result", str(FINDINGS), "--round", "1")
    start(mt, 2, "02-after-fix", tmp_path / "rd2")
    assert mt("review", "result", str(DEGRADED), "--round", "2").code == 0
    assert run_state(run)["unresolved"] == []
    # re-recording an older round does not overwrite the latest round's unresolved list
    assert mt("review", "result", str(FINDINGS), "--round", "1", "--deferred", "adversarial-1=prose").code == 0
    state = run_state(run)
    assert state["unresolved"] == []
    assert len(rnd(state, 1)["unresolved"]) == 3


def test_deferred_is_carried_until_a_confirming_round(mt, review_run, tmp_path, run_state):
    """A prose-only major deferred in round 1 must not vanish because round 2's reviewers did not repeat it."""
    run, _ = review_run
    mt("review", "result", str(FINDINGS), "--round", "1", "--deferred", "adversarial-1=prose")
    start(mt, 2, "02-after-fix", tmp_path / "rd2")
    assert mt("review", "result", str(DEGRADED), "--round", "2").code == 0
    state = run_state(run)
    assert [(d["id"], d["round"]) for d in state["deferred"]] == [("adversarial-1", 1)]
    # round 3 defers the same finding again under the same id: one entry, the latest
    start(mt, 3, "03-again", tmp_path / "rd3")
    assert mt("review", "result", str(FINDINGS), "--round", "3", "--deferred", "adversarial-1=still prose").code == 0
    assert [(d["id"], d["round"]) for d in run_state(run)["deferred"]] == [("adversarial-1", 3)]
    assert mt("status").json()["deferred"] == 1
    # the leftovers fix and its confirming round settle what was carried
    start(mt, 4, "04-confirm", tmp_path / "rd4")
    assert mt("review", "result", str(DEGRADED), "--round", "4", "--confirming").code == 0
    state = run_state(run)
    assert state["deferred"] == [] and rnd(state, 4)["confirming"] is True
    assert "Round 4 · 04-confirm · degraded · confirming round" in (run / "review.md").read_text()


def test_review_md_content_and_artifact(mt, review_run, tmp_path, run_state):
    run, _ = review_run
    assert mt("set", "review_base=0123456789abcdef0123").code == 0
    mt(
        "review",
        "result",
        str(FINDINGS),
        "--round",
        "1",
        "--not-gating",
        "adversarial-2=vendored, outside scope",
        "--deferred",
        "adversarial-1=prose-only",
    )
    start(mt, 2, "02-after-fix", tmp_path / "rd2")
    mt("review", "result", str(DEGRADED), "--round", "2")
    md = (run / "review.md").read_text()
    assert md.startswith("# Review · Session store\n")
    assert "Scope: `0123456789ab..HEAD`" in md
    assert md.index("## Round 2 · 02-after-fix · degraded") < md.index("## Round 1 · 01-initial · 2 gating")
    assert "1 critical, 3 major, 2 minor, 1 open question; duration 6m02s; degraded sources: none." in md
    assert "degraded sources: adversarial." in md
    assert "- **critical** `src/store.go:88` Session map written without the lock (bugs+impl-1) — unresolved" in md
    assert "`src/login.go:12-20`" in md
    assert "Logout keeps the cookie \\<script\\>alert(1)\\</script\\>" in md and "<script>" not in md
    assert "`README.md` README documents the old flag (adversarial-1) — deferred: prose-only" in md
    assert "(adversarial-2) — not gating: vendored, outside scope" in md
    assert "- **minor** `src/util.go:7` Unused helper (bugs+impl-4)\n" in md
    assert "### Open questions\n\n- `src/store.go:100` Should sessions expire on restart?" in md

    state = run_state(run)
    art = next(a for a in state["artifacts"] if a["id"] == "review")
    assert art["source"] == str(run / "review.md") and art["hint"] == "revmux review rounds"
    assert art["html"] == str(run / "artifacts" / "review.html")
    assert "Round 2" in Path(art["html"]).read_text()


def test_review_md_skips_rounds_without_result(mt, review_run, tmp_path):
    run, _ = review_run
    mt("review", "result", str(FINDINGS), "--round", "1")
    start(mt, 2, "02-after-fix", tmp_path / "rd2")
    mt("review", "result", str(FINDINGS), "--round", "1")
    assert "## Round 2" not in (run / "review.md").read_text()


# ---------------------------------------------------------------- result errors


@pytest.mark.parametrize(
    "content, msg",
    [
        ("{not json", "is not valid JSON"),
        ("", "is not valid JSON"),
        ("[]", "has no findings list"),
        ('{"findings": [{"severity": "major"}]}', "a finding without an id"),
    ],
)
def test_result_bad_report(mt, review_run, tmp_path, run_state, content, msg):
    run, _ = review_run
    path = tmp_path / "bad.json"
    path.write_text(content)
    before = run_state(run)
    r = mt("review", "result", str(path), "--round", "1")
    assert r.code == 1 and msg in r.err
    assert run_state(run) == before
    assert not (run / "review.md").exists()


def test_result_missing_report(mt, review_run, tmp_path):
    r = mt("review", "result", str(tmp_path / "nope.json"), "--round", "1")
    assert r.code == 1 and "cannot read revmux report" in r.err


def test_result_for_round_never_started(mt, review_run, run_state):
    run, _ = review_run
    before = run_state(run)
    r = mt("review", "result", str(FINDINGS), "--round", "2")
    assert r.code == 1 and "round 2 was never started" in r.err
    assert run_state(run) == before


@pytest.mark.parametrize(
    "extra, msg",
    [
        (["--not-gating", "f99=x"], "no finding f99"),
        (["--deferred", "f98=x", "--not-gating", "f99=y"], "no finding f98, f99"),
        (["--not-gating", "adversarial-1=x", "--deferred", "adversarial-1=y"], "both --not-gating and --deferred"),
        (["--not-gating", "adversarial-1"], "expected --not-gating ID=REASON"),
        (["--deferred", "adversarial-1="], "expected --deferred ID=REASON"),
    ],
)
def test_result_bad_ids(mt, review_run, run_state, extra, msg):
    run, _ = review_run
    before = run_state(run)
    r = mt("review", "result", str(FINDINGS), "--round", "1", *extra)
    assert r.code == 1 and msg in r.err
    assert run_state(run) == before


# ---------------------------------------------------------------- wait


@pytest.fixture(autouse=True)
def fast_poll(monkeypatch):
    monkeypatch.setenv("MT_REVIEW_POLL", "0.05")


@pytest.fixture
def spawn() -> Iterator:
    """Start a sleeping process whose argv carries `--task T --run R ...` like the revmux launch."""
    procs: list[subprocess.Popen] = []

    def run(*argv: str) -> subprocess.Popen:
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", *argv])
        procs.append(p)
        deadline = time.monotonic() + 5
        while not MT.process_alive(argv[1], argv[3]) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert MT.process_alive(argv[1], argv[3]), f"pgrep never saw {argv}"
        return p

    yield run
    for p in procs:
        p.kill()
        p.wait()


def kill(p: subprocess.Popen) -> None:
    p.kill()
    p.wait()


def test_wait_running_then_failed_then_done(mt, review_run, spawn, run_state):
    run, round_dir = review_run
    shutil.copy(EVENTS, round_dir / "events.jsonl")
    p = spawn("--task", TASK, "--run", "01-initial", "--tasks-dir", "/x", "--no-tui")
    r = mt("review", "wait", "--round", "1", "--deadline", "0.3")
    assert (r.code, r.out) == (0, "running\n"), r.err
    state = run_state(run)
    assert rnd(state, 1)["milestone"] == "verify 2/3 · 1 degraded"
    assert state["activity"] == "Review round 1 · verify 2/3 · 1 degraded"

    # valid report while the process lives: still running
    (run / "revmux" / "round-1.json").write_text(FINDINGS.read_text())
    assert mt("review", "wait", "--round", "1", "--deadline", "0.2").out == "running\n"

    kill(p)
    (run / "revmux" / "round-1.json").write_text("")
    r = mt("review", "wait", "--round", "1", "--deadline", "5")
    assert r.out == "failed\n"
    state = run_state(run)
    assert rnd(state, 1)["milestone"] == "failed"
    assert state["events"][-1]["text"] == "Review round 1 failed · no report from 01-initial"

    (run / "revmux" / "round-1.json").write_text(FINDINGS.read_text())
    r = mt("review", "wait", "--round", "1")
    assert r.out == "done\n"
    assert rnd(run_state(run), 1)["milestone"] == "report ready"


def test_wait_returns_done_when_process_exits(mt, review_run, run_state):
    run, _ = review_run
    report = run / "revmux" / "round-1.json"
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            f"import time, pathlib; time.sleep(0.4); pathlib.Path({str(report)!r}).write_text({FINDINGS.read_text()!r})",
            "--task",
            TASK,
            "--run",
            "01-initial",
        ]
    )
    try:
        t0 = time.monotonic()
        r = mt("review", "wait", "--round", "1", "--deadline", "10")
        assert r.out == "done\n", r.err
        assert time.monotonic() - t0 < 8
    finally:
        proc.wait()


def test_wait_failed_on_invalid_report(mt, review_run):
    run, _ = review_run
    (run / "revmux" / "round-1.json").write_text('{"scope": {}}')
    assert mt("review", "wait", "--round", "1", "--deadline", "0").out == "failed\n"


def test_wait_failed_without_report(mt, review_run):
    assert mt("review", "wait", "--round", "1", "--deadline", "0").out == "failed\n"


def test_wait_ignores_longer_run_name(mt, review_run, spawn):
    run, _ = review_run
    spawn("--task", TASK, "--run", "01-initial-2")
    (run / "revmux" / "round-1.json").write_text(FINDINGS.read_text())
    assert mt("review", "wait", "--round", "1", "--deadline", "0").out == "done\n"


def test_wait_escapes_regex_in_task_and_run(mt, git_repo, tmp_path, spawn):
    init_run(mt, git_repo)
    mt("set", "revmux_task=pr+1.x")
    start(mt, 1, "01(a)", tmp_path / "rd")
    spawn("--task", "pr+1.x", "--run", "01(a)")
    assert mt("review", "wait", "--round", "1", "--deadline", "0").out == "running\n"


def test_wait_pattern_is_escaped_not_a_regex(spawn):
    """Unescaped, `pr+1.x` would match `prr1x` and `01(a)` would match `01a`."""
    spawn("--task", "prr1x", "--run", "01a")
    assert MT.process_alive("prr1x", "01a")
    assert not MT.process_alive("pr+1.x", "01(a)")


@pytest.mark.parametrize("bad", ["soon", "0", "-1"])
def test_wait_rejects_a_bad_poll_interval(mt, review_run, monkeypatch, bad):
    monkeypatch.setenv("MT_REVIEW_POLL", bad)
    r = mt("review", "wait", "--round", "1", "--deadline", "0")
    assert r.code == 1 and "MT_REVIEW_POLL must be a positive number" in r.err


def test_default_wait_deadline_stays_under_the_bash_limit():
    """`review wait` runs under the Bash tool's 600 s limit: its default wait ends before it."""
    assert MT.REVIEW_DEADLINE < MT.BASH_LIMIT
    assert MT.build_parser().parse_args(["review", "wait", "--round", "1"]).deadline == MT.REVIEW_DEADLINE


def test_wait_deadline_reached(mt, review_run, spawn):
    spawn("--task", TASK, "--run", "01-initial")
    t0 = time.monotonic()
    r = mt("review", "wait", "--round", "1", "--deadline", "0.5")
    took = time.monotonic() - t0
    assert (r.code, r.out) == (0, "running\n")
    assert 0.4 <= took < 3


def test_wait_milestone_before_any_event_and_partial_line(mt, review_run, spawn, run_state):
    run, round_dir = review_run
    spawn("--task", TASK, "--run", "01-initial")
    mt("review", "wait", "--round", "1", "--deadline", "0")
    assert rnd(run_state(run), 1)["milestone"] == "starting"
    (round_dir / "events.jsonl").write_text(
        '{"kind":"stage","stage":"find"}\n{"kind":"agent_started","agent":"a"}\n{"kind":"agent_do'
    )
    mt("review", "wait", "--round", "1", "--deadline", "0")
    assert rnd(run_state(run), 1)["milestone"] == "find 0/1"


def test_wait_uses_injected_process_check(mt, review_run, isolate, monkeypatch):
    run, _ = review_run
    log = isolate.tmp / "pgrep.log"
    fake = isolate.tmp / "bin" / "fake-pgrep"
    fake.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" >> "{log}"\nexit "${{FAKE_PGREP_EXIT:-0}}"\n')
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("MT_PGREP", str(fake))
    assert mt("review", "wait", "--round", "1", "--deadline", "0").out == "running\n"
    assert log.read_text().splitlines()[:3] == ["-f", "--", f"--task {TASK} --run 01-initial( |$)"]
    monkeypatch.setenv("FAKE_PGREP_EXIT", "1")
    (run / "revmux" / "round-1.json").write_text(FINDINGS.read_text())
    assert mt("review", "wait", "--round", "1", "--deadline", "0").out == "done\n"
    monkeypatch.setenv("FAKE_PGREP_EXIT", "3")
    r = mt("review", "wait", "--round", "1", "--deadline", "0")
    assert r.code == 1 and "failed (exit 3)" in r.err


def test_wait_round_never_started(mt, review_run):
    r = mt("review", "wait", "--round", "2", "--deadline", "0")
    assert r.code == 1 and "round 2 was never started" in r.err


def test_wait_without_revmux_task(mt, git_repo, tmp_path):
    init_run(mt, git_repo)
    start(mt, 1, "01-initial", tmp_path / "rd")
    r = mt("review", "wait", "--round", "1", "--deadline", "0")
    assert r.code == 1 and "revmux_task is not set" in r.err


def test_wait_negative_deadline(mt, review_run):
    r = mt("review", "wait", "--round", "1", "--deadline", "-1")
    assert r.code == 1 and "must not be negative" in r.err


def test_milestone_unit():
    events = [json.loads(line) for line in EVENTS.read_text().splitlines()]
    assert MT.milestone([]) == "starting"
    assert MT.milestone(events[:3]) == "find 0/2"
    assert MT.milestone(events[:9]) == "find 2/2"
    assert MT.milestone(events[:12]) == "synthesis 1/1"
    assert MT.milestone(events) == "verify 2/3 · 1 degraded"


# ---------------------------------------------------------------- verify-final


def write_lines(path: Path, lines: list[str]) -> None:
    path.write_text("".join(f"{line}\n" for line in lines))


TEN = [f"line {i}" for i in range(1, 11)]


@pytest.fixture
def origin_repo(git_repo: Path, tmp_path: Path) -> tuple[Path, Path]:
    """git_repo with a bare origin and a second clone `other` that pushes upstream edits."""
    write_lines(git_repo / "f.txt", TEN)
    commit(git_repo, "add f")
    bare = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    git(git_repo, "remote", "add", "origin", str(bare))
    git(git_repo, "push", "-q", "-u", "origin", "main")
    git(git_repo, "remote", "set-head", "origin", "main")
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", str(bare), str(other))
    return git_repo, other


def feature_edit(repo: Path) -> str:
    """On branch `feature`, change line 5 of f.txt; return the reviewed sha."""
    git(repo, "checkout", "-q", "-b", "feature")
    lines = list(TEN)
    lines[4] = "line 5 changed on the branch"
    write_lines(repo / "f.txt", lines)
    (repo / "new.txt").write_text("branch file\n")
    return commit(repo, "feature work")


def upstream_edit(other: Path, line_index: int | None = None, path: str = "other.txt") -> None:
    if line_index is not None:
        lines = list(TEN)
        lines[line_index] = f"line {line_index + 1} changed upstream"
        write_lines(other / "f.txt", lines)
    else:
        (other / path).write_text("upstream\n")
    commit(other, "upstream")
    git(other, "push", "-q", "origin", "main")


def verify(mt, cwd: Path):
    return mt("review", "verify-final", cwd=cwd)


def test_verify_unchanged_after_clean_rebase_onto_moved_origin(mt, origin_repo, run_state):
    repo, other = origin_repo
    run = init_run(mt, repo)
    reviewed = feature_edit(repo)
    mt("set", f"reviewed_sha={reviewed}")
    upstream_edit(other)
    git(repo, "fetch", "-q", "origin")
    git(repo, "rebase", "-q", "origin/main")
    assert git(repo, "rev-parse", "HEAD") != reviewed
    r = verify(mt, repo)
    assert (r.code, r.out) == (0, "unchanged\n"), r.err
    state = run_state(run)
    assert state["review_stale"] is False
    assert state["events"][-1]["text"].startswith("Reviewed content unchanged after finalize")


def test_verify_unchanged_after_upstream_edit_near_branch_hunk(mt, origin_repo, run_state):
    repo, other = origin_repo
    run = init_run(mt, repo)
    reviewed = feature_edit(repo)
    mt("set", f"reviewed_sha={reviewed}")
    upstream_edit(other, line_index=7)  # line 8: inside the default 3-line context of the line-5 hunk
    git(repo, "fetch", "-q", "origin")
    git(repo, "rebase", "-q", "origin/main")
    # a context-sensitive diff comparison would call this changed; the replay onto the new base does not
    old_base = git(repo, "merge-base", "origin/main", reviewed)
    new_base = git(repo, "merge-base", "origin/main", "HEAD")
    u3 = [
        subprocess.run(
            ["git", "patch-id", "--stable"],
            cwd=repo,
            input=subprocess.run(["git", "diff", b, t], cwd=repo, capture_output=True, check=True).stdout,
            capture_output=True,
            check=True,
        ).stdout.split()[0]
        for b, t in ((old_base, reviewed), (new_base, "HEAD"))
    ]
    assert u3[0] != u3[1]
    assert verify(mt, repo).out == "unchanged\n"
    assert run_state(run)["review_stale"] is False


def test_verify_changed_after_extra_content_commit(mt, origin_repo, run_state):
    repo, _ = origin_repo
    run = init_run(mt, repo)
    reviewed = feature_edit(repo)
    mt("set", f"reviewed_sha={reviewed}")
    (repo / "new.txt").write_text("branch file\nmore after review\n")
    commit(repo, "finalize fix")
    r = verify(mt, repo)
    assert (r.code, r.out) == (0, "changed\n")
    state = run_state(run)
    assert state["review_stale"] is True
    assert state["events"][-1]["text"].startswith("Review stale:")


def test_verify_unchanged_after_squash(mt, origin_repo, run_state):
    repo, _ = origin_repo
    run = init_run(mt, repo)
    feature_edit(repo)
    (repo / "second.txt").write_text("second\n")
    reviewed = commit(repo, "second commit")
    mt("set", f"reviewed_sha={reviewed}")
    git(repo, "reset", "-q", "--soft", "origin/main")
    commit(repo, "squashed")
    assert verify(mt, repo).out == "unchanged\n"
    assert run_state(run)["review_stale"] is False


def test_verify_without_origin(mt, git_repo, run_state):
    write_lines(git_repo / "f.txt", TEN)
    commit(git_repo, "add f")
    run = init_run(mt, git_repo)
    assert run_state(run)["has_origin"] is False
    reviewed = feature_edit(git_repo)
    mt("set", f"reviewed_sha={reviewed}")
    git(git_repo, "checkout", "-q", "main")
    (git_repo / "main.txt").write_text("main moved\n")
    commit(git_repo, "main moves")
    git(git_repo, "checkout", "-q", "feature")
    git(git_repo, "rebase", "-q", "main")
    assert verify(mt, git_repo).out == "unchanged\n"
    (git_repo / "new.txt").write_text("changed after review\n")
    commit(git_repo, "extra")
    assert verify(mt, git_repo).out == "changed\n"
    assert run_state(run)["review_stale"] is True


def test_verify_back_to_unchanged_clears_stale(mt, origin_repo, run_state):
    repo, _ = origin_repo
    run = init_run(mt, repo)
    reviewed = feature_edit(repo)
    mt("set", f"reviewed_sha={reviewed}")
    (repo / "new.txt").write_text("x\n")
    commit(repo, "extra")
    assert verify(mt, repo).out == "changed\n"
    git(repo, "reset", "-q", "--hard", reviewed)
    assert verify(mt, repo).out == "unchanged\n"
    assert run_state(run)["review_stale"] is False


def indent_change(repo: Path) -> None:
    """Move `y()` out of its `if` block: a whitespace-only change that patch-id ignores."""
    (repo / "code.py").write_text("if x:\n    a()\ny()\n")
    commit(repo, "dedent y")


def code_feature(repo: Path) -> str:
    git(repo, "checkout", "-q", "-b", "feature")
    (repo / "code.py").write_text("if x:\n    a()\n    y()\n")
    (repo / "blob.bin").write_bytes(bytes(range(256)) * 4)
    return commit(repo, "feature with code and a binary")


def binary_change(repo: Path) -> None:
    data = bytearray(bytes(range(256)) * 4)
    data[100] ^= 0xFF
    (repo / "blob.bin").write_bytes(bytes(data))
    commit(repo, "binary edit")


def moved_line(repo: Path) -> str:
    """Branch adds `X` after line 3 of f.txt; returns the reviewed sha. The change moves it after line 7."""
    git(repo, "checkout", "-q", "-b", "feature")
    write_lines(repo / "f.txt", TEN[:3] + ["X"] + TEN[3:])
    return commit(repo, "add X")


def move_x(repo: Path) -> None:
    write_lines(repo / "f.txt", TEN[:7] + ["X"] + TEN[7:])
    commit(repo, "move X")


@pytest.mark.parametrize("change", [indent_change, binary_change], ids=["indentation", "binary"])
@pytest.mark.parametrize("rebased", [False, True], ids=["same-base", "rebased"])
@pytest.mark.parametrize("replay", [True, False], ids=["merge-tree", "fingerprint"])
def test_verify_sees_indentation_and_binary_changes(mt, origin_repo, run_state, monkeypatch, change, rebased, replay):
    repo, other = origin_repo
    if not replay:
        monkeypatch.setattr(MT, "replay_tree", lambda *a: None)  # a git without merge-tree --merge-base
    run = init_run(mt, repo)
    reviewed = code_feature(repo)
    mt("set", f"reviewed_sha={reviewed}")
    if rebased:
        upstream_edit(other)
        git(repo, "fetch", "-q", "origin")
        git(repo, "rebase", "-q", "origin/main")
        assert verify(mt, repo).out == "unchanged\n", "a clean rebase alone keeps the reviewed content"
    change(repo)
    r = verify(mt, repo)
    assert (r.code, r.out) == (0, "changed\n"), r.err
    assert run_state(run)["review_stale"] is True


@pytest.mark.parametrize("rebased", [False, True], ids=["same-base", "rebased"])
def test_verify_sees_a_line_moved_within_its_file(mt, origin_repo, run_state, rebased):
    repo, other = origin_repo
    run = init_run(mt, repo)
    reviewed = moved_line(repo)
    mt("set", f"reviewed_sha={reviewed}")
    if rebased:
        upstream_edit(other)
        git(repo, "fetch", "-q", "origin")
        git(repo, "rebase", "-q", "origin/main")
    move_x(repo)
    assert verify(mt, repo).out == "changed\n"
    assert run_state(run)["review_stale"] is True


def test_verify_conflicting_replay_is_changed(mt, origin_repo, run_state):
    """Upstream edited the reviewed line: the finalizer resolved a conflict, so HEAD was never reviewed."""
    repo, other = origin_repo
    run = init_run(mt, repo)
    reviewed = feature_edit(repo)
    mt("set", f"reviewed_sha={reviewed}")
    upstream_edit(other, line_index=4)
    git(repo, "fetch", "-q", "origin")
    r = subprocess.run(["git", "rebase", "-q", "origin/main"], cwd=repo, capture_output=True, check=False)
    assert r.returncode != 0
    lines = list(TEN)
    lines[4] = "line 5 changed on the branch"
    write_lines(repo / "f.txt", lines)
    git(repo, "add", "f.txt")
    subprocess.run(["git", "-c", "core.editor=true", "rebase", "--continue"], cwd=repo, check=True)
    r = verify(mt, repo)
    assert (r.code, r.out) == (0, "changed\n")
    assert "conflicts" in run_state(run)["events"][-1]["text"]


def test_diff_fingerprint_keeps_whitespace_and_binary_and_drops_offsets(git_repo):
    write_lines(git_repo / "f.txt", TEN)
    base = commit(git_repo, "base")
    write_lines(git_repo / "f.txt", TEN[:5] + ["  new"] + TEN[5:])
    tip = commit(git_repo, "add indented")
    write_lines(git_repo / "f.txt", ["top"] + TEN)
    shifted_base = commit(git_repo, "shift")
    write_lines(git_repo / "f.txt", ["top"] + TEN[:5] + ["  new"] + TEN[5:])
    shifted_tip = commit(git_repo, "same change, moved down a line")
    fp = MT.diff_fingerprint
    assert fp(str(git_repo), base, tip) == fp(str(git_repo), shifted_base, shifted_tip)
    write_lines(git_repo / "f.txt", ["top"] + TEN[:5] + ["new"] + TEN[5:])
    dedented = commit(git_repo, "dedented")
    assert fp(str(git_repo), base, tip) != fp(str(git_repo), shifted_base, dedented)


def test_diff_fingerprint_ignores_the_users_diff_config(git_repo):
    """A redacting textconv driver, an external diff, no prefixes, color or copy detection must not reach it:
    the fingerprint is the same with and without them, and still sees the redacted word."""
    write_lines(git_repo / "f.txt", TEN)
    base = commit(git_repo, "base")
    write_lines(git_repo / "f.txt", TEN[:5] + ["SECRET"] + TEN[5:])
    tip = commit(git_repo, "add secret")
    fp = MT.diff_fingerprint
    plain = fp(str(git_repo), base, tip)
    (git_repo / ".git" / "info" / "attributes").write_text("* diff=redact\n")
    for key, value in [
        ("diff.redact.textconv", "sed s/SECRET/XXXXXX/"),
        ("diff.external", "true"),
        ("diff.noprefix", "true"),
        ("diff.mnemonicPrefix", "true"),
        ("color.ui", "always"),
        ("diff.renames", "copies"),
    ]:
        git(git_repo, "config", key, value)
    assert fp(str(git_repo), base, tip) == plain
    write_lines(git_repo / "f.txt", TEN[:5] + ["XXXXXX"] + TEN[5:])
    assert fp(str(git_repo), base, commit(git_repo, "redacted by hand")) != plain


@pytest.mark.parametrize(
    ("dirt", "path"),
    [
        ("modified", "src/a.txt"),
        ("staged", "src/a.txt"),
        ("untracked", "src/fix.txt"),
        ("deleted", "README.md"),
    ],
)
def test_verify_counts_uncommitted_task_changes_as_changed(mt, git_repo, run_state, dirt, path):
    """The finalizer left its fix uncommitted: HEAD equals the reviewed tree, but the tested tree is not HEAD's."""
    run = init_run(mt, git_repo)
    mt("set", f"reviewed_sha={git(git_repo, 'rev-parse', 'HEAD')}")
    target = git_repo / path
    if dirt == "deleted":
        target.unlink()
    else:
        target.write_text("uncommitted fix\n")
    if dirt == "staged":
        git(git_repo, "add", "--", path)
    r = verify(mt, git_repo)
    assert (r.code, r.out) == (0, "changed\n"), r.err
    assert f"uncommitted changes after finalize: {path}" in r.err
    state = run_state(run)
    assert state["review_stale"] is True and state["verified_sha"] == state["reviewed_sha"]
    assert f"uncommitted changes: {path}" in state["events"][-1]["text"]


def test_verify_ignores_revmux_and_the_restored_stash_paths(mt, git_repo, run_state):
    """In place `.revmux/` stays dirty (untracked files, edits put back), and once the restore is recorded
    (stash_restored) the user's stash_paths are dirty again: neither is the task's."""
    (git_repo / ".revmux").mkdir()
    (git_repo / ".revmux" / "profile.md").write_text("profile\n")
    commit(git_repo, "profile")
    run = init_run(mt, git_repo)
    mt("set", f"reviewed_sha={git(git_repo, 'rev-parse', 'HEAD')}")
    (git_repo / ".revmux" / "profile.md").write_text("the user's edit\n")
    (git_repo / ".revmux" / "tasks").mkdir()
    (git_repo / ".revmux" / "tasks" / "x.md").write_text("task\n")
    state = run_state(run)
    state["stash_paths"] = ["README.md"]
    state["stash_ref"] = "0" * 40
    (run / "state.json").write_text(json.dumps(state))
    (git_repo / "README.md").write_text("the user's stashed edit\n")
    r = verify(mt, git_repo)
    assert (r.code, r.out) == (0, "changed\n"), "while the stash holds README.md, a change to it is the task's"
    assert "uncommitted changes after finalize: README.md" in r.err
    assert mt("set", "stash_ref=", "stash_restored=ok").code == 0
    r = verify(mt, git_repo)
    assert (r.code, r.out) == (0, "unchanged\n"), r.err
    assert run_state(run)["review_stale"] is False


def test_verify_counts_stash_paths_changed_when_nothing_was_stashed(mt, git_repo, run_state):
    """`git rm --cached` left the path clean after the reset, so no stash was made and stash_ref was never
    set: an empty stash_ref is not a restore. A finalizer edit to that path is the task's until
    `stash_restored` records the restore."""
    run = init_run(mt, git_repo)
    mt("set", f"reviewed_sha={git(git_repo, 'rev-parse', 'HEAD')}")
    state = run_state(run)
    state["stash_paths"] = ["src/a.txt"]
    (run / "state.json").write_text(json.dumps(state))
    assert run_state(run)["stash_ref"] is None
    (git_repo / "src" / "a.txt").write_text("uncommitted finalizer fix\n")
    r = verify(mt, git_repo)
    assert (r.code, r.out) == (0, "changed\n"), r.err
    assert "uncommitted changes after finalize: src/a.txt" in r.err
    git(git_repo, "checkout", "-q", "--", "src/a.txt")
    git(git_repo, "rm", "-q", "--cached", "src/a.txt")  # restore=none staged the deletion again
    assert mt("set", "stash_restored=none").code == 0
    r = verify(mt, git_repo)
    assert (r.code, r.out) == (0, "unchanged\n"), r.err


# ---------------------------------------------------------------- verify-final errors


def test_verify_without_reviewed_sha(mt, git_repo, run_state):
    run = init_run(mt, git_repo)
    before = run_state(run)
    r = verify(mt, git_repo)
    assert r.code == 1 and "reviewed_sha is not set" in r.err
    assert run_state(run) == before


def test_verify_unknown_reviewed_sha(mt, git_repo):
    init_run(mt, git_repo)
    mt("set", "reviewed_sha=" + "f" * 40)
    r = verify(mt, git_repo)
    assert r.code == 1 and "is not a commit here" in r.err


@pytest.mark.parametrize(
    ("has_origin", "refs"), [(True, ["origin/trunk", "trunk"]), (False, ["trunk"])], ids=["origin", "no-origin"]
)
def test_default_branch_refs_one_order_for_merge_base_and_rebase_target(has_origin, refs):
    """`artifact changes` (merge_base) and verify-final (rebase_target) try the same refs in the same order."""
    assert MT.default_branch_refs({"default_branch": "trunk", "has_origin": has_origin}) == refs


def test_verify_without_any_rebase_target_compares_trees(mt, git_repo, run_state):
    """No origin/<default> and no <default>: nothing could be rebased, so the trees decide (exit 0, not a stop)."""
    run = init_run(mt, git_repo)
    state = run_state(run)
    state["default_branch"] = "trunk"
    (run / "state.json").write_text(json.dumps(state))
    reviewed = git(git_repo, "rev-parse", "HEAD")
    mt("set", f"reviewed_sha={reviewed}")
    r = verify(mt, git_repo)
    assert (r.code, r.out) == (0, "unchanged\n")
    assert "no rebase target (trunk)" in r.err
    assert run_state(run)["verified_sha"] == reviewed
    (git_repo / "README.md").write_text("after review\n")
    commit(git_repo, "extra")
    r = verify(mt, git_repo)
    assert (r.code, r.out) == (0, "changed\n")
    state = run_state(run)
    assert state["review_stale"] is True and state["verified_sha"] == reviewed


def test_verify_origin_without_its_default_falls_back_to_the_local_default(mt, git_repo, tmp_path, run_state):
    """origin added but never pushed (or a failed fetch): origin/main is missing, local main is the base."""
    write_lines(git_repo / "f.txt", TEN)
    commit(git_repo, "add f")
    bare = tmp_path / "never-pushed.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    git(git_repo, "remote", "add", "origin", str(bare))
    run = init_run(mt, git_repo)
    assert run_state(run)["has_origin"] is True
    reviewed = feature_edit(git_repo)
    mt("set", f"reviewed_sha={reviewed}")
    r = verify(mt, git_repo)
    assert (r.code, r.out, r.err) == (0, "unchanged\n", "")
    (git_repo / "new.txt").write_text("changed after review\n")
    commit(git_repo, "extra")
    assert verify(mt, git_repo).out == "changed\n"


def test_verify_from_main_checkout_in_worktree_mode(mt, git_repo, tmp_path):
    init_run(mt, git_repo)
    wt = tmp_path / "wt"
    git(git_repo, "worktree", "add", "-q", "-b", "wtb", str(wt))
    mt("set", f"worktree_path={wt.resolve()}", "reviewed_sha=" + git(git_repo, "rev-parse", "HEAD"))
    r = verify(mt, git_repo)
    assert r.code == 1 and "works in the worktree" in r.err
    assert verify(mt, wt).out == "unchanged\n"


def test_result_duration_falls_back_to_the_start_time(mt, review_run, tmp_path, run_state):
    run, _ = review_run
    report = json.loads(FINDINGS.read_text())
    del report["stats"]
    path = tmp_path / "nostats.json"
    path.write_text(json.dumps(report))
    assert mt("review", "result", str(path), "--round", "1").code == 0
    r1 = rnd(run_state(run), 1)
    assert r1["duration"] == pytest.approx(r1["finished"] - r1["started"])
