"""agterm wiring: the dashboard at init, sidebar status re-assertion, and the dashboard page itself.

Everything here goes through the fake agtermctl from conftest; no test touches the live terminal.
"""

from __future__ import annotations

import calendar
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
from conftest import MT, TESTS, init, init_args

DASHBOARD = TESTS.parent / "assets" / "dashboard.html"
SID = "S-1111"
PANE_ID = "P-left"


def tree(split: bool = False, has_split: bool = False, own_kind: str = "left") -> dict[str, Any]:
    session: dict[str, Any] = {
        "id": SID,
        "name": "agent",
        "split": split,
        "surfaces": [{"id": f"surface:{SID}:{own_kind}", "kind": own_kind, "paneID": PANE_ID}],
    }
    if has_split:
        session["hasSplit"] = True
        other = "right" if own_kind == "left" else "left"
        session["surfaces"].append({"id": f"surface:{SID}:{other}", "kind": other, "paneID": "P-other"})
    other_session = {"id": "S-other", "name": "other", "split": False, "surfaces": []}
    return {
        "ok": True,
        "result": {"tree": {"workspaces": [{"id": "W-1", "sessions": [other_session, session]}]}},
    }


@pytest.fixture
def in_agterm(monkeypatch, isolate):
    monkeypatch.setenv("AGTERM_ENABLED", "1")
    monkeypatch.setenv("AGTERM_SESSION_ID", SID)
    monkeypatch.setenv("AGTERM_PANE_ID", PANE_ID)

    def replies(**kw: Any) -> None:
        data = {"tree": {"stdout": json.dumps(tree(**kw))}}
        isolate.agterm_replies.write_text(json.dumps(data))

    replies()
    return replies


def overlay_argv(run: Path, pane: str = "right") -> list[str]:
    return [
        "session", "overlay", "open", "--html", str(run / "dashboard.html"), "--cwd", str(run),
        "--js", "--chromeless", "--pane", pane, "--target", SID,
    ]  # fmt: skip


# ---------------------------------------------------------------- init


def test_init_splits_opens_dashboard_and_refocuses(mt, git_repo, isolate, in_agterm, run_state):
    info = init(mt, git_repo)
    run = Path(info["state_dir"])
    assert isolate.agterm_calls() == [
        ["tree", "--json"],
        ["session", "split", "on", "--target", SID, "--axis", "vertical"],
        overlay_argv(run),
        ["session", "focus", "left", "--target", SID],
    ]
    assert info["agterm"] is True and info["created_split"] is True and info["dashboard_pane"] == "right"
    st = run_state(run)
    assert st["agterm"] is True and st["created_split"] is True
    assert st["agterm_session"] == SID
    assert (run / "dashboard.html").read_bytes() == DASHBOARD.read_bytes()
    assert (isolate.state_home / f"current-{SID}").read_text().strip() == str(run)
    assert info["warnings"] == []


def test_init_no_split_when_one_is_shown(mt, git_repo, isolate, in_agterm, run_state):
    in_agterm(split=True, has_split=True)
    info = init(mt, git_repo)
    run = Path(info["state_dir"])
    assert isolate.agterm_calls() == [["tree", "--json"], overlay_argv(run)]
    assert info["agterm"] is True and info["created_split"] is False


def test_init_shows_hidden_split_without_claiming_it(mt, git_repo, isolate, in_agterm, run_state):
    in_agterm(split=False, has_split=True)
    info = init(mt, git_repo)
    run = Path(info["state_dir"])
    calls = isolate.agterm_calls()
    assert calls[1] == ["session", "split", "on", "--target", SID]  # no --axis: keep the user's axis
    assert calls[2] == overlay_argv(run)
    assert info["created_split"] is False, "Close must not destroy a split the user made"


def test_init_dashboard_over_left_when_agent_runs_in_right_pane(mt, git_repo, isolate, in_agterm):
    in_agterm(split=True, has_split=True, own_kind="right")
    info = init(mt, git_repo)
    run = Path(info["state_dir"])
    assert isolate.agterm_calls()[-1] == overlay_argv(run, pane="left")
    assert info["dashboard_pane"] == "left"


def test_init_failing_agtermctl_sets_agterm_false(mt, git_repo, isolate, in_agterm, monkeypatch, run_state):
    monkeypatch.setenv("FAKE_AGTERMCTL_FAIL", "1")
    info = init(mt, git_repo)
    assert info["agterm"] is False and info["created_split"] is False
    assert isolate.agterm_calls() == [["tree", "--json"]], "stops at the first failing call"
    assert any("agtermctl tree failed" in w for w in info["warnings"])
    st = run_state(Path(info["state_dir"]))
    assert st["agterm"] is False
    assert any("No dashboard" in e["text"] for e in st["events"])


def test_init_failing_split_sets_agterm_false(mt, git_repo, isolate, in_agterm, monkeypatch):
    monkeypatch.setenv("FAKE_AGTERMCTL_FAIL", "session split")
    info = init(mt, git_repo)
    assert info["agterm"] is False
    assert [c[:3] for c in isolate.agterm_calls()] == [["tree", "--json"], ["session", "split", "on"]]


def test_init_failing_overlay_closes_the_split_it_made(mt, git_repo, isolate, in_agterm, monkeypatch):
    monkeypatch.setenv("FAKE_AGTERMCTL_FAIL", "session overlay open")
    info = init(mt, git_repo)
    assert info["agterm"] is False and info["created_split"] is False
    assert isolate.agterm_calls()[-1] == ["session", "split", "close", "--target", SID]
    assert any("overlay open failed" in w for w in info["warnings"])


def test_init_failing_overlay_keeps_a_shown_split(mt, git_repo, isolate, in_agterm, monkeypatch):
    in_agterm(split=True, has_split=True)
    monkeypatch.setenv("FAKE_AGTERMCTL_FAIL", "session overlay open")
    info = init(mt, git_repo)
    assert info["agterm"] is False
    assert not any(c[:3] == ["session", "split", "close"] for c in isolate.agterm_calls())


@pytest.mark.parametrize(
    "reply",
    ["not json", json.dumps({"ok": True, "result": {"tree": {"workspaces": []}}})],
    ids=["garbage", "session-missing"],
)
def test_init_unusable_tree_sets_agterm_false(mt, git_repo, isolate, in_agterm, reply):
    isolate.agterm_replies.write_text(json.dumps({"tree": {"stdout": reply}}))
    info = init(mt, git_repo)
    assert info["agterm"] is False
    assert isolate.agterm_calls() == [["tree", "--json"]]


def test_init_focus_failure_only_warns(mt, git_repo, isolate, in_agterm, monkeypatch):
    monkeypatch.setenv("FAKE_AGTERMCTL_FAIL", "session focus")
    r = mt(*init_args("Dash test"), cwd=git_repo)
    assert r.code == 0
    assert json.loads(r.out)["agterm"] is True
    assert "session focus" in r.err


def test_init_agtermctl_timeout_sets_agterm_false(mt, git_repo, isolate, in_agterm, monkeypatch):
    monkeypatch.setattr(MT, "AGTERM_TIMEOUT", 0.2)
    monkeypatch.setenv("FAKE_AGTERMCTL_SLEEP", "2")
    info = init(mt, git_repo)
    assert info["agterm"] is False
    assert any("timed out" in w for w in info["warnings"])


def test_init_agtermctl_missing_sets_agterm_false(mt, git_repo, isolate, in_agterm, monkeypatch):
    which = MT.shutil.which
    monkeypatch.setattr(
        MT.shutil, "which", lambda name, *a, **kw: None if name == "agtermctl" else which(name, *a, **kw)
    )
    info = init(mt, git_repo)
    assert info["agterm"] is False
    assert any("not on PATH" in w for w in info["warnings"])


@pytest.mark.parametrize("env", [{}, {"AGTERM_SESSION_ID": SID}, {"AGTERM_ENABLED": "1"}])
def test_nothing_called_outside_agterm(mt, git_repo, isolate, monkeypatch, env, run_state):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    info = init(mt, git_repo)
    run = Path(info["state_dir"])
    for args in (("phase", "brainstorm"), ("activity", "x"), ("fail", "boom"), ("phase", "plan")):
        assert mt(*args).code == 0
    assert isolate.agterm_calls() == []
    assert info["agterm"] is False
    assert (run / "dashboard.html").is_file(), "the page is copied for a manual look even outside agterm"


# ---------------------------------------------------------------- status re-assertion


def status_calls(isolate) -> list[list[str]]:
    return [c for c in isolate.agterm_calls() if c[:2] == ["session", "status"]]


def test_phase_and_activity_reassert_active(mt, git_repo, isolate, in_agterm):
    init(mt, git_repo)
    active = ["session", "status", "active", "--target", SID, "--pane-id", PANE_ID]
    assert mt("phase", "brainstorm").code == 0
    assert status_calls(isolate) == [active]
    assert mt("activity", "Exploring").code == 0
    assert status_calls(isolate) == [active, active]
    assert mt("activity", "Waiting for you", "--wait").code == 0
    assert status_calls(isolate) == [active, active], "--wait leaves the status alone"
    assert mt("phase", "plan", "--skipped").code == 0
    assert len(status_calls(isolate)) == 3


def test_status_without_pane_id(mt, git_repo, isolate, in_agterm, monkeypatch):
    init(mt, git_repo)
    monkeypatch.delenv("AGTERM_PANE_ID")
    assert mt("phase", "brainstorm").code == 0
    assert status_calls(isolate) == [["session", "status", "active", "--target", SID]]


def test_fail_sets_blocked_then_phase_resumes_active(mt, git_repo, isolate, in_agterm):
    init(mt, git_repo)
    assert mt("phase", "exec").code == 0
    assert mt("fail", "task 2 failed twice").code == 0
    assert status_calls(isolate)[-1][:3] == ["session", "status", "blocked"]
    assert mt("activity", "Retrying task 2").code == 0
    assert status_calls(isolate)[-1][:3] == ["session", "status", "active"]


def test_status_failure_only_warns(mt, git_repo, isolate, in_agterm, monkeypatch, run_state):
    info = init(mt, git_repo)
    monkeypatch.setenv("FAKE_AGTERMCTL_FAIL", "session status")
    r = mt("phase", "brainstorm")
    assert r.code == 0
    assert "mt: warning: agtermctl session status active" in r.err
    st = run_state(Path(info["state_dir"]))
    assert st["agterm"] is True, "a failing status call does not switch the dashboard off"
    assert next(p for p in st["phases"] if p["id"] == "brainstorm")["status"] == "run"


def test_no_status_when_init_fell_back(mt, git_repo, isolate, in_agterm, monkeypatch):
    monkeypatch.setenv("FAKE_AGTERMCTL_FAIL", "tree")
    init(mt, git_repo)
    monkeypatch.delenv("FAKE_AGTERMCTL_FAIL")
    assert mt("phase", "brainstorm").code == 0
    assert status_calls(isolate) == []


# ---------------------------------------------------------------- run completion


def edit_state(run: Path, **kw: Any) -> None:
    st = MT.load(run)
    st.update(kw)
    MT.save(run, st)


CLEAN_ROUND = {
    "round": 1,
    "run": "01-initial",
    "head": "abc123",
    "started": 5,
    "finished": 10,
    "degraded": [],
    "verdict": "clean",
}


def reviewed_run(mt, git_repo) -> Path:
    """A run that went through every phase up to finalize (running) and may end clean: one clean round,
    reviewed_sha checked by verify-final, the finalize checkpoint ended ok."""
    run = Path(init(mt, git_repo)["state_dir"])
    for pid in ("brainstorm", "plan", "exec", "review", "finalize"):
        assert mt("phase", pid).code == 0
    assert mt("checkpoint", "begin", "finalize").code == 0
    assert mt("checkpoint", "end", "finalize", "--result", "ok").code == 0
    edit_state(run, review=[dict(CLEAN_ROUND)], reviewed_sha="abc123", verified_sha="abc123")
    return run


def test_done_sets_final_fields_and_completed(mt, git_repo, isolate, in_agterm, run_state):
    run = reviewed_run(mt, git_repo)
    r = mt("done", "--status", "clean", "--note", "all green")
    assert r.code == 0, r.err
    st = run_state(run)
    assert st["final_status"] == "clean" and st["final_note"] == "all green"
    assert st["now_kind"] == "done" and st["activity"] == "all green"
    assert isinstance(st["finished"], float) and st["finished"] >= st["started"]
    assert all(p["status"] == "done" and p["finished"] for p in st["phases"])
    assert st["events"][-1]["text"] == "Run finished: clean · all green"
    assert status_calls(isolate)[-1] == ["session", "status", "completed", "--target", SID, "--pane-id", PANE_ID]
    js_text = (run / "state.js").read_text()
    assert '"final_status": "clean"' in js_text or '"final_status":"clean"' in js_text


def test_done_without_note_and_pending_phases_stay_pending(mt, git_repo, run_state):
    run = Path(init(mt, git_repo)["state_dir"])
    assert mt("phase", "brainstorm").code == 0
    assert mt("done", "--status", "stopped").code == 0
    st = run_state(run)
    phases = {p["id"]: p["status"] for p in st["phases"]}
    assert phases == {"brainstorm": "done", "plan": "pending", "exec": "pending", "review": "pending",
                      "finalize": "pending"}  # fmt: skip
    assert st["activity"] == "Finished · stopped"
    assert st["events"][-1]["text"] == "Run finished: stopped"


def test_done_twice_is_harmless(mt, git_repo, isolate, in_agterm, run_state):
    run = reviewed_run(mt, git_repo)
    assert mt("done", "--status", "not clean").code == 0
    first = run_state(run)
    assert mt("done", "--status", "not clean").code == 0
    second = run_state(run)
    assert second["finished"] == first["finished"]
    assert second["phases"] == first["phases"]
    assert second["events"] == first["events"], "a repeated call adds no event"
    assert status_calls(isolate)[-2:] == [status_calls(isolate)[-1]] * 2
    assert mt("done", "--status", "stopped").code == 0
    third = run_state(run)
    assert third["finished"] == first["finished"] and third["final_status"] == "stopped"
    assert third["events"][-1]["text"] == "Final status: stopped"


@pytest.mark.parametrize(
    ("change", "msg"),
    [
        ({"unresolved": [{"id": "f1", "severity": "major", "file": "a", "line": 1, "title": "t"}]},
         "1 unresolved gating"),
        ({"deferred": [{"id": "f4", "severity": "major", "file": "README.md", "line": 0, "title": "t",
                        "reason": "prose"}]}, "1 deferred"),
        ({"review_stale": True}, "review is stale"),
        ({"stop_reason": "skipped"}, "stop_reason is skipped"),
        ({"review": [{"round": 1, "finished": 10, "degraded": []}, {"round": 2, "finished": 20, "degraded": ["codex"]}]},
         "last review round was degraded"),
        ({"error": "boom"}, "error state (boom)"),
        ({"review": []}, "no review round has a result"),
        ({"review": [{"round": 1, "started": 1, "finished": None}]}, "no review round has a result"),
        ({"review": [dict(CLEAN_ROUND), {"round": 2, "run": "02-confirm", "started": 20, "finished": None}]},
         "review round 2 has no result"),
        ({"reviewed_sha": None}, "reviewed_sha is not set"),
        ({"verified_sha": None}, "verify-final has not checked reviewed_sha"),
        ({"reviewed_sha": "def456"}, "verify-final has not checked reviewed_sha"),
        ({"reviewed_sha": "def456", "verified_sha": "def456"},
         "reviewed_sha def456 is not the HEAD review round 1 reviewed (abc123)"),
        ({"review": [{**CLEAN_ROUND, "head": None}]}, "review round 1 recorded no head"),
        ({"review": [dict(CLEAN_ROUND), {**CLEAN_ROUND, "round": 2, "head": "def456"}]},
         "is not the HEAD review round 2 reviewed (def456)"),
        ({"checkpoints": []}, "finalize checkpoint did not end ok (never begun)"),
        ({"checkpoints": [{"key": "finalize", "state": "ended", "result": "rebase failed"}]},
         "did not end ok (ended, result 'rebase failed')"),
        ({"checkpoints": [{"key": "finalize", "state": "begun", "result": None}]}, "did not end ok (begun"),
    ],
    ids=["unresolved", "deferred", "stale", "stop-skipped", "degraded", "error", "no-round", "round-unfinished",
         "latest-round-unfinished", "no-reviewed-sha", "not-verified", "verified-other-sha", "unreviewed-commits",
         "round-without-head", "latest-round-head-counts", "no-finalize",
         "finalize-failed", "finalize-begun"],
)  # fmt: skip
def test_done_refuses_clean(mt, git_repo, isolate, in_agterm, run_state, change, msg):
    run = reviewed_run(mt, git_repo)
    edit_state(run, **change)
    before = (run / "state.json").read_text()
    calls = len(isolate.agterm_calls())
    r = mt("done", "--status", "clean")
    assert r.code == 1
    assert "cannot finish as clean" in r.err and msg in r.err
    assert (run / "state.json").read_text() == before, "a refused done changes nothing"
    assert len(isolate.agterm_calls()) == calls
    assert mt("done", "--status", "not clean").code == 0
    assert run_state(run)["final_status"] == "not clean"


def test_done_clean_after_degraded_round_was_rerun(mt, git_repo, run_state):
    run = reviewed_run(mt, git_repo)
    edit_state(
        run,
        review=[
            {"round": 1, "head": "abc123", "finished": 10, "degraded": ["codex"]},
            {"round": 2, "head": "abc123", "finished": 20, "degraded": []},
        ],
    )
    assert mt("done", "--status", "clean").code == 0


def test_done_clean_after_an_abandoned_round_was_rerun(mt, git_repo):
    """A crashed round n stays unfinished; its re-run n+1 has the result that counts."""
    run = reviewed_run(mt, git_repo)
    edit_state(run, review=[{"round": 1, "started": 1, "finished": None}, {**CLEAN_ROUND, "round": 2}])
    assert mt("done", "--status", "clean").code == 0


@pytest.mark.parametrize("state", ["skipped", "pending"])
def test_done_refuses_clean_without_review(mt, git_repo, run_state, state):
    run = Path(init(mt, git_repo)["state_dir"])
    for pid in ("brainstorm", "plan", "exec"):
        assert mt("phase", pid).code == 0
    if state == "skipped":
        assert mt("phase", "review", "--skipped").code == 0
    assert mt("phase", "finalize").code == 0
    r = mt("done", "--status", "clean")
    assert r.code == 1
    assert ("was skipped" if state == "skipped" else "never ran") in r.err
    assert mt("done", "--status", "review skipped").code == 0
    assert run_state(run)["final_status"] == "review skipped"


def test_done_stopped_from_error_state(mt, git_repo, isolate, in_agterm, run_state):
    run = Path(init(mt, git_repo)["state_dir"])
    assert mt("phase", "exec").code == 0
    assert mt("fail", "task 2 failed after one retry").code == 0
    assert mt("done", "--status", "stopped", "--note", "user stopped").code == 0
    st = run_state(run)
    exec_phase = next(p for p in st["phases"] if p["id"] == "exec")
    assert exec_phase["status"] == "error" and exec_phase["finished"] == st["finished"]
    assert st["final_status"] == "stopped" and st["now_kind"] == "done"
    assert st["error"] == "task 2 failed after one retry", "the error stays on record"
    assert status_calls(isolate)[-1][:3] == ["session", "status", "completed"]


@pytest.mark.parametrize("status", ["not clean", "review skipped", "review stale", "stopped"])
def test_done_other_statuses_accepted_with_findings(mt, git_repo, run_state, status):
    run = reviewed_run(mt, git_repo)
    edit_state(run, unresolved=[{"id": "f1"}], review_stale=True)
    assert mt("done", "--status", status).code == 0
    assert run_state(run)["final_status"] == status


def test_done_errors(mt, git_repo):
    assert mt("done", "--status", "clean").code == 1, "no active run"
    init(mt, git_repo)
    r = mt("done", "--status", "great")
    assert r.code == 1 and "unknown status 'great'" in r.err
    assert mt("done").code == 2, "--status is required"


def test_done_outside_agterm_calls_nothing(mt, git_repo, isolate):
    reviewed_run(mt, git_repo)
    assert mt("done", "--status", "clean").code == 0
    assert isolate.agterm_calls() == []


def test_done_status_failure_only_warns(mt, git_repo, isolate, in_agterm, monkeypatch, run_state):
    run = reviewed_run(mt, git_repo)
    monkeypatch.setenv("FAKE_AGTERMCTL_FAIL", "session status")
    r = mt("done", "--status", "clean")
    assert r.code == 0
    assert "agtermctl session status completed" in r.err
    assert run_state(run)["final_status"] == "clean"


def test_page_renders_done_run(mt, git_repo, isolate, tmp_path):
    run = reviewed_run(mt, git_repo)
    assert mt("done", "--status", "stopped", "--note", "Stopped by you").code == 0
    html = run_page(tmp_path, (run / "state.js").read_text())["html"]
    assert 'class="now done"' in html and '<span class="final bad">stopped</span>' in html
    assert "Stopped by you" in html


def _clock(t: float) -> str:
    lt = time.localtime(t)
    hm = time.strftime("%H:%M", lt)
    if time.strftime("%Y-%m-%d", lt) == time.strftime("%Y-%m-%d"):
        return hm
    return f"{lt.tm_mday} {calendar.month_abbr[lt.tm_mon]} {hm}"


def test_page_artifact_buttons_show_update_time_after_the_run(tmp_path):
    today = float(int(time.time()))
    s = base_state(finished=today, final_status="clean", now_kind="done")
    s["artifacts"][0]["updated"] = today
    s["artifacts"].append(
        {"id": "review", "hint": "review.md", "html": "/state/run/artifacts/review.html", "updated": 1300}
    )
    html = run_page(tmp_path, js(s))["html"]
    assert f'<span class="ago">{_clock(today)}</span>' in html
    assert f'<span class="ago">{_clock(1300)}</span>' in html, "an older day carries the date"
    assert "data-ago" not in html, "nothing ticks after the run"


def test_page_artifact_buttons_tick_while_running(tmp_path):
    html = run_page(tmp_path, js(base_state()))["html"]
    assert 'data-ago="1300"' in html


# ---------------------------------------------------------------- the page


def page_script() -> str:
    m = re.search(r"<script>(.*?)</script>", DASHBOARD.read_text(), re.DOTALL)
    assert m
    return m.group(1)


def test_dashboard_declares_no_agterm_variables_and_loads_state_js():
    text = DASHBOARD.read_text()
    assert not re.search(r"--agterm-[\w-]+\s*:", text), "agterm injects these; the page only reads them"
    assert "var(--agterm-background" in text and "var(--agterm-foreground" in text
    assert "'state.js?'" in page_script()
    assert "window.mtState" in page_script()
    assert "session.overlay.close" in text and "session.split.close" in text
    assert not re.search(r"\bfetch\(", page_script()), "a --js page cannot fetch file:// URLs"
    assert not re.search(r"<script[^>]*\bsrc=", text), "no external scripts; state.js is injected at runtime"


NODE_HARNESS = r"""
const fs = require('fs');
const [script, input, clicks] = [fs.readFileSync(process.argv[2], 'utf8'), fs.readFileSync(process.argv[3], 'utf8'),
                                 JSON.parse(process.argv[4] || '[]')];
const app = { innerHTML: '', listeners: {}, addEventListener(t, f) { this.listeners[t] = f; } };
const requests = [], appended = [];
globalThis.window = globalThis;
globalThis.agterm = { request(cmd, opts) { requests.push([cmd, opts]); return Promise.resolve({}); } };
globalThis.document = {
  title: '', head: { appendChild(el) { appended.push(el.src); } },
  getElementById: id => (id === 'app' ? app : null), createElement: () => ({ remove() {} }), querySelectorAll: () => [],
};
globalThis.setInterval = () => 0;
(0, eval)(script);
(0, eval)(input);
for (const [sel, data] of clicks) {
  const target = { closest: s => (s === sel ? { dataset: data || {}, title: '' } : null) };
  app.listeners.click({ target });
}
process.stdout.write(JSON.stringify({ html: app.innerHTML, title: document.title, requests, appended }));
"""


def run_page(tmp_path: Path, state_js: str, clicks: list[Any] | None = None) -> dict[str, Any]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    harness, script, inp = tmp_path / "harness.js", tmp_path / "page.js", tmp_path / "input.js"
    harness.write_text(NODE_HARNESS)
    script.write_text(page_script())
    inp.write_text(state_js)
    r = subprocess.run(
        [node, str(harness), str(script), str(inp), json.dumps(clicks or [])],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def base_state(**kw: Any) -> dict[str, Any]:
    s: dict[str, Any] = {
        "title": "Persist sessions",
        "repo": "~/workspace/api",
        "branch": "session-store",
        "worktree": True,
        "profile_name": "default",
        "autonomy": "interactive",
        "state_dir": "/state/run",
        "started": 1000.0,
        "finished": None,
        "final_status": None,
        "error": None,
        "activity": "Task 2 · Repository",
        "now_kind": "",
        "created_split": False,
        "agterm_session": SID,
        "phases": [
            {
                "id": "brainstorm",
                "label": "Brainstorm",
                "status": "done",
                "started": 1000,
                "finished": 1100,
                "note": "",
            },
            {"id": "plan", "label": "Plan", "status": "done", "started": 1100, "finished": 1200, "note": ""},
            {"id": "exec", "label": "Execute", "status": "run", "started": 1200, "finished": None, "note": ""},
            {"id": "review", "label": "Review", "status": "pending", "started": None, "finished": None, "note": ""},
            {"id": "finalize", "label": "Finalize", "status": "pending", "started": None, "finished": None, "note": ""},
        ],
        "tasks": [
            {"n": 1, "title": "Schema", "done": 3, "total": 3, "status": "done", "started": 1200, "finished": 1300},
            {"n": 2, "title": "Repository", "done": 1, "total": 4, "status": "run", "started": 1300, "finished": None},
        ],
        "review": [],
        "assumptions": [{"text": "SQLite", "why": "already in go.mod"}],
        "decisions": [{"q": "Session length?", "a": "30 days"}],
        "artifacts": [
            {"id": "plan", "hint": "docs/plans/x.md", "html": "/state/run/artifacts/plan.html", "updated": 1300},
            {"id": "design", "hint": "design.md", "html": None, "source": "/state/run/design.md", "updated": 0},
            {"id": "changes", "hint": "revdiff abc", "html": None, "command": "revdiff abc", "cwd": "/wt"},
        ],
        "events": [{"t": "10:00:00", "text": "make-task started"}],
        "unresolved": [],
        "deferred": [],
    }
    s.update(kw)
    return s


def js(state: dict[str, Any]) -> str:
    return MT.state_js(state)


def test_page_renders_state_and_escapes_outside_text(tmp_path):
    evil = "<script>alert(1)</script>"
    s = base_state(title=evil, activity="<img src=x onerror=alert(2)>", branch='"><b>')
    s["events"].append({"t": "<i>", "text": "</ol><h1>pwn</h1>"})
    s["assumptions"].append({"text": "<b>bold</b>", "why": "it's & so"})
    s["tasks"][1]["title"] = "<u>task</u>"
    s["artifacts"][0]["hint"] = '" onmouseover="x'
    out = run_page(tmp_path, js(s))
    html = out["html"]
    for raw in ("<script>alert", "<img src=x", "<h1>pwn", "<b>bold", "<u>task", '" onmouseover="x', '"><b>'):
        assert raw not in html, raw
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "it&#39;s &amp; so" in html
    assert out["title"] == f"make-task · {evil}"  # document.title is text, not markup
    assert out["appended"] and out["appended"][0].startswith("state.js?")


def test_page_structure(tmp_path):
    s = base_state()
    html = run_page(tmp_path, js(s))["html"]
    assert "Persist sessions" in html and "session-store" in html and "worktree" in html
    assert "1 of 2 tasks" in html and "1/4" in html and "3/3" in html
    assert 'data-since="1300"' in html, "the running task has a live timer"
    assert "1m 40s" in html, "a finished phase shows its duration"
    assert 'data-open="plan"' in html
    assert re.search(r'<button type="button" class="file" disabled title="design.md">Design</button>', html)
    assert 'data-open="changes"' in html
    assert re.search(r'disabled title="not yet">Stats</button>', html)
    assert "SQLite" in html and "30 days" in html and "make-task started" in html


def where_line(html: str) -> str:
    m = re.search(r'<div class="where">(.*?)</div>', html, re.DOTALL)
    assert m, html
    return m.group(1)


def test_page_header_shows_profile_and_autonomy(tmp_path):
    where = where_line(run_page(tmp_path, js(base_state(profile_name="team-x", autonomy="autonomous")))["html"])
    assert "<span>profile <code>team-x</code></span>" in where
    assert "<span>autonomy <code>autonomous</code></span>" in where
    assert where.index("session-store") < where.index("team-x"), "after the branch and worktree"


def test_page_header_escapes_profile_name(tmp_path):
    evil = "<b>x</b> & \"q\" 'r'"
    html = run_page(tmp_path, js(base_state(profile_name=evil, autonomy='"><i>')))["html"]
    for raw in ("<b>x</b>", '"q"', "'r'", '"><i>'):
        assert raw not in html, raw
    where = where_line(html)
    assert "profile <code>&lt;b&gt;x&lt;/b&gt; &amp; &quot;q&quot; &#39;r&#39;</code>" in where
    assert "autonomy <code>&quot;&gt;&lt;i&gt;</code>" in where


def test_page_header_without_profile_keys(tmp_path):
    s = base_state()
    del s["profile_name"], s["autonomy"]  # a run started before profiles
    where = where_line(run_page(tmp_path, js(s))["html"])
    assert "profile" not in where and "autonomy" not in where
    assert "session-store" in where


def test_page_phase_states_rounds_and_final_status(tmp_path):
    s = base_state(now_kind="done", finished=5000.0, final_status="not clean", stop_reason="round-cap")
    s["phases"][1]["status"] = "skipped"
    s["phases"][2].update(status="error", finished=None)
    s["phases"][3].update(status="done", started=2000, finished=3000)
    s["unresolved"] = [{"id": "f1"}]
    s["review"] = [
        {"round": 1, "run": "01-initial", "critical": 0, "major": 2, "minor": 1, "open_questions": 1,
         "verdict": "2 gating", "started": 2000, "finished": 2600, "duration": 600},
        {"round": 2, "run": "02-fix", "critical": 0, "major": 0, "minor": 0, "milestone": "find 3/4",
         "started": 2700, "finished": None},
    ]  # fmt: skip
    html = run_page(tmp_path, js(s))["html"]
    assert 'class="phase skipped"' in html and ">skipped</span>" in html
    assert 'class="phase error"' in html
    assert "2 major" in html and "1 minor" in html and "1 open" in html and "2 gating" in html
    assert "10m 00s" in html, "round duration from the report"
    assert "find 3/4" in html, "a running round shows its live milestone"
    assert '<span class="final warn">not clean</span>' in html
    assert 'class="now done"' in html
    assert "stopped: round-cap" in html and "1 unresolved" in html


def test_page_error_and_wait_lines(tmp_path):
    s = base_state(now_kind="error", error="Task 2 failed after one retry")
    html = run_page(tmp_path, js(s))["html"]
    assert 'class="now error"' in html and "Task 2 failed after one retry" in html
    s = base_state(now_kind="wait", activity="Waiting for your answer")
    html = run_page(tmp_path, js(s))["html"]
    assert 'class="now wait"' in html and 'class="phase run wait"' in html


def test_page_close_closes_overlay_then_created_split(tmp_path):
    out = run_page(tmp_path, js(base_state(created_split=True)), [["[data-close]", {}]])
    assert out["requests"] == [["session.overlay.close", {}], ["session.split.close", {"target": SID}]]
    out = run_page(tmp_path, js(base_state(created_split=False)), [["[data-close]", {}]])
    assert out["requests"] == [["session.overlay.close", {}]]


def test_page_artifact_clicks(tmp_path):
    clicks = [
        ["[data-open]", {"open": "plan"}],
        ["[data-open]", {"open": "changes"}],
        ["[data-open]", {"open": "design"}],
    ]
    out = run_page(tmp_path, js(base_state()), clicks)
    assert out["requests"] == [
        [
            "session.overlay.open",
            {"args": {"html": "/state/run/artifacts/plan.html", "sizePercent": 85, "follow": True}},
        ],
        ["session.overlay.open", {"args": {"command": "revdiff abc", "cwd": "/wt", "sizePercent": 90, "follow": True}}],
    ], "design has no html yet, so its click does nothing"


def test_page_renders_real_mt_state(mt, git_repo, isolate, tmp_path):
    r = mt(*init_args("Real </script> state"), cwd=git_repo)
    run = Path(json.loads(r.out)["state_dir"])
    assert mt("phase", "brainstorm", "--activity", "Reading").code == 0
    assert mt("assume", "x <y>", "--why", "z").code == 0
    assert mt("phase", "plan", "--skipped").code == 0
    html = run_page(tmp_path, (run / "state.js").read_text())["html"]
    assert "Real &lt;/script&gt; state" in html
    where = where_line(html)
    assert f"profile <code>{isolate.profile_dir.name}</code>" in where, "init stores profile_name in state.js"
    assert "autonomy <code>default</code>" in where
    assert "x &lt;y&gt;" in html and "Reading" in html
    assert 'class="phase skipped"' in html
