"""The viewer page (assets/viewer.html) in a node harness with a stub DOM, its entry pages written by
`mt init`, and the static checks on viewer.html and doc.html."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from conftest import MT, TESTS, init, init_run

VIEWER = TESTS.parent / "assets" / "viewer.html"
DOC = TESTS.parent / "assets" / "doc.html"


# ---------------------------------------------------------------- entry pages


def test_init_writes_one_viewer_entry_per_tab(mt, git_repo):
    run = init_run(mt, git_repo)
    assert MT.VIEWER_TABS == ("design", "plan", "progress", "review", "stats"), "every artifact but changes"
    assert not (run / "viewer.html").exists(), "only the entry pages: each opens on its own tab"
    asset = VIEWER.read_text()
    same = asset.replace('<body data-tab="design">', '<body data-tab="">')
    for tab in MT.VIEWER_TABS:
        page = (run / f"viewer-{tab}.html").read_text()
        assert page.count(f'<body data-tab="{tab}">') == 1
        assert page.replace(f'<body data-tab="{tab}">', '<body data-tab="">') == same, "only data-tab differs"


def test_init_warns_when_the_viewer_pages_cannot_be_written(mt, git_repo, monkeypatch, run_state):
    def fail(run: Path) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(MT, "write_viewer_pages", fail)
    info = init(mt, git_repo)
    assert "cannot write the viewer pages: disk full" in info["warnings"]
    run = Path(info["state_dir"])
    assert (run / "dashboard.html").is_file(), "the run goes on"
    assert "cannot write the viewer pages: disk full" in run_state(run)["warnings"]


# ---------------------------------------------------------------- static checks


def viewer_script() -> str:
    m = re.search(r"<script>(.*?)</script>", VIEWER.read_text(), re.DOTALL)
    assert m
    return m.group(1)


def test_viewer_declares_no_agterm_variables_and_loads_scripts_only():
    text = VIEWER.read_text()
    assert not re.search(r"--agterm-[\w-]+\s*:", text), "agterm injects these; the page only reads them"
    assert "var(--agterm-background" in text and "var(--agterm-foreground" in text
    script = viewer_script()
    assert "load('state.js'" in script and "`artifacts/${id}.js`" in script
    assert "window.mtState" in script and "window.mtArtifact" in script
    assert not re.search(r"\bfetch\(", script), "a javascript page cannot fetch file:// URLs"
    assert not re.search(r"<script[^>]*\bsrc=", text), "no external scripts; the data scripts are injected at runtime"
    assert len(MT.VIEWER_BODY.findall(text)) == 1 and '<body data-tab="design">' in text
    assert "'changes'" not in script and "revdiff" not in text, "Changes stays revdiff, outside the viewer"


def test_doc_page_declares_no_agterm_variables_and_runs_no_script():
    text = DOC.read_text()
    assert not re.search(r"--agterm-[\w-]+\s*:", text)
    assert "var(--agterm-background" in text and "var(--agterm-foreground" in text
    assert "<script" not in text and not re.search(r"\bfetch\(", text), "the static page runs nothing"


# ---------------------------------------------------------------- the node harness

NODE_HARNESS = r"""
const fs = require('fs');
const script = fs.readFileSync(process.argv[2], 'utf8');
const steps = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const tab = process.argv[4];
function element(id) {
  let html = '';
  return {
    id, writes: 0, listeners: {},
    get innerHTML() { return html; },
    // a rewritten pane starts at the top here, so only the page's own scrollTo brings the position back
    set innerHTML(v) { html = String(v); this.writes++; if (id === 'pane') globalThis.scrollY = 0; },
    addEventListener(t, f) { this.listeners[t] = f; },
  };
}
const els = { app: element('app'), head: element('head'), pane: element('pane') };
const appended = [], scrolls = [], intoView = [];
let prevented = 0;
globalThis.window = globalThis;
globalThis.scrollY = 0;
globalThis.scrollTo = (x, y) => { scrolls.push(y); globalThis.scrollY = y; };
globalThis.document = {
  title: '',
  body: { dataset: { tab } },
  head: { appendChild(el) { appended.push(el); } },
  getElementById: id => els[id] || (id.startsWith('doc-') ? { scrollIntoView() { intoView.push(id); } } : null),
  createElement: () => ({ remove() {} }),
  querySelectorAll: () => [],
};
globalThis.setInterval = () => 0;
(0, eval)(script);
const snaps = [];
for (const step of steps) {
  if ('eval' in step) (0, eval)(step.eval);
  else if ('scroll' in step) globalThis.scrollY = step.scroll;
  else if ('fail' in step) [...appended].reverse().find(e => e.src.startsWith(step.fail + '?')).onerror();
  else if ('click' in step) {
    const match = { dataset: step.data || {}, getAttribute: k => (step.attrs || {})[k], closest: () => null };
    const target = { closest: s => (s === step.click ? match : null) };
    els.app.listeners.click({ target, preventDefault() { prevented++; } });
  } else if ('snap' in step) {
    snaps.push({ head: els.head.innerHTML, pane: els.pane.innerHTML, paneWrites: els.pane.writes,
                 scrollY: globalThis.scrollY, title: document.title });
  }
}
process.stdout.write(JSON.stringify({ snaps, appended: appended.map(e => e.src), scrolls, intoView, prevented }));
"""

SNAP: dict[str, Any] = {"snap": True}


def run_viewer(tmp_path: Path, steps: list[dict[str, Any]], tab: str = "design") -> dict[str, Any]:
    """Run the viewer's script for the entry page of `tab`, then each step: {"eval": js} (a state.js or
    artifacts/<id>.js text), {"click": selector, "data": dataset, "attrs": attributes}, {"scroll": y},
    {"fail": src} (the last injected script with that src fails to load) or {"snap": true}."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    harness, script, inp = tmp_path / "harness.js", tmp_path / "viewer.js", tmp_path / "steps.json"
    harness.write_text(NODE_HARNESS)
    script.write_text(viewer_script())
    inp.write_text(json.dumps(steps))
    r = subprocess.run(
        [node, str(harness), str(script), str(inp), tab], capture_output=True, text=True, check=False, timeout=30
    )
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def art(aid: str, updated: float = 1300.0, **kw: Any) -> dict[str, Any]:
    entry = {
        "id": aid,
        "hint": f"{aid}.md",
        "html": f"/state/run/artifacts/{aid}.html",
        "source": f"/state/run/{aid}.md",
    }
    return {**entry, "command": None, "cwd": None, "updated": updated, **kw}


def base_state(**kw: Any) -> dict[str, Any]:
    s: dict[str, Any] = {
        "title": "Persist sessions",
        "repo": "~/workspace/api",
        "branch": "session-store",
        "worktree": False,
        "profile_name": "default",
        "autonomy": "interactive",
        "state_dir": "/state/run",
        "started": 1000.0,
        "finished": None,
        "tasks": [],
        "review": [],
        "artifacts": [art("design"), art("stats")],
    }
    s.update(kw)
    return s


def state_step(s: dict[str, Any]) -> dict[str, Any]:
    return {"eval": MT.state_js(s)}


def artifact_step(aid: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"eval": f"window.mtArtifact({json.dumps(aid)}, {MT.script_json(payload)});\n"}


def doc_payload(text: str, updated: float = 2000.0) -> dict[str, Any]:
    html, toc = MT.render_markdown(text)
    return {"title": MT.first_heading(html), "html": html, "toc": toc, "source": "/state/run/x.md", "updated": updated}


DESIGN_MD = "# Design: session store\n\n## Goal\n\nKeep sessions.\n\n### Why <now>\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"
STATS_MD = "# Run stats\n\n## Agents\n\n- exec: 4m\n"


def tab_button(head: str, tab: str) -> str:
    m = re.search(rf'<button type="button" class="[^"]*" data-show="{tab}"[^>]*>.*?</button>', head, re.DOTALL)
    assert m, head
    return m.group(0)


# ---------------------------------------------------------------- Design and Stats


def test_design_tab_renders_the_document_and_its_toc(tmp_path):
    design = doc_payload(DESIGN_MD)
    out = run_viewer(tmp_path, [state_step(base_state()), artifact_step("design", design), SNAP])
    snap = out["snaps"][0]
    assert f'<article class="doc">{design["html"]}</article>' in snap["pane"], "renderer HTML goes in as HTML"
    assert '<h2 id="doc-goal">Goal</h2>' in snap["pane"] and "<table>" in snap["pane"]
    assert (
        '<nav class="toc"><a class="l2" href="#doc-goal">Goal</a><a class="l3" href="#doc-why-now">Why &lt;now&gt;</a></nav>'
        in snap["pane"]
    )
    assert out["appended"][0].startswith("state.js?") and out["appended"][1].startswith("artifacts/design.js?")
    assert snap["title"] == "make-task · Design · Persist sessions"
    head = snap["head"]
    assert "<h1>Persist sessions</h1>" in head and "session-store" in head and "<span>in place</span>" in head
    assert 'class="file cur" data-show="design" aria-current="page"' in tab_button(head, "design")
    labels = re.findall(r'data-show="(\w+)"[^>]*>([^<]+)<', head)
    assert labels == [("design", "Design"), ("plan", "Plan"), ("progress", "Progress log"), ("review", "Review"),
                      ("stats", "Stats")], "no Changes tab"  # fmt: skip


def test_stats_tab_renders_from_its_entry_page(tmp_path):
    stats = doc_payload(STATS_MD)
    out = run_viewer(tmp_path, [state_step(base_state()), artifact_step("stats", stats), SNAP], tab="stats")
    snap = out["snaps"][0]
    assert out["appended"][1].startswith("artifacts/stats.js?"), "the entry page's tab loads first"
    assert (
        '<h2 id="doc-agents">Agents</h2>' in snap["pane"]
        and '<a class="l2" href="#doc-agents">Agents</a>' in snap["pane"]
    )
    assert "aria-current" in tab_button(snap["head"], "stats") and "aria-current" not in tab_button(
        snap["head"], "design"
    )
    assert snap["title"] == "make-task · Stats · Persist sessions"


def test_document_without_headings_has_no_toc(tmp_path):
    out = run_viewer(tmp_path, [state_step(base_state()), artifact_step("design", doc_payload("just text\n")), SNAP])
    assert 'class="toc"' not in out["snaps"][0]["pane"] and "<p>just text</p>" in out["snaps"][0]["pane"]


def test_unknown_entry_tab_opens_design(tmp_path):
    out = run_viewer(tmp_path, [state_step(base_state()), SNAP], tab="changes")
    assert out["appended"][1].startswith("artifacts/design.js?")
    assert "aria-current" in tab_button(out["snaps"][0]["head"], "design")


# ---------------------------------------------------------------- switching tabs


def test_tab_switch_loads_the_tab_and_reuses_what_it_loaded(tmp_path):
    design, stats = doc_payload(DESIGN_MD), doc_payload(STATS_MD)
    steps = [
        state_step(base_state()),
        artifact_step("design", design),
        {"click": "[data-show]", "data": {"show": "stats"}},
        SNAP,
        artifact_step("stats", stats),
        SNAP,
        {"click": "[data-show]", "data": {"show": "design"}},
        SNAP,
        {"click": "[data-show]", "data": {"show": "design"}},
        SNAP,
    ]
    out = run_viewer(tmp_path, steps)
    switched, loaded, back, again = out["snaps"]
    assert "aria-current" in tab_button(switched["head"], "stats")
    assert switched["pane"] == '<p class="empty">Loading…</p>'
    assert switched["title"] == "make-task · Stats · Persist sessions"
    assert '<h2 id="doc-agents">' in loaded["pane"]
    assert '<h2 id="doc-goal">' in back["pane"], "a tab loaded before shows at once"
    assert "aria-current" in tab_button(back["head"], "design")
    srcs = [s.split("?")[0] for s in out["appended"]]
    assert srcs == ["state.js", "artifacts/design.js", "artifacts/stats.js", "artifacts/design.js"], (
        "a switch loads the tab's script at once; clicking the current tab does nothing"
    )
    assert again == back


def test_each_tab_keeps_its_scroll(tmp_path):
    steps = [
        state_step(base_state()),
        artifact_step("design", doc_payload(DESIGN_MD)),
        {"scroll": 300},
        {"click": "[data-show]", "data": {"show": "stats"}},
        artifact_step("stats", doc_payload(STATS_MD)),
        SNAP,
        {"scroll": 50},
        {"click": "[data-show]", "data": {"show": "design"}},
        SNAP,
        {"click": "[data-show]", "data": {"show": "stats"}},
        SNAP,
    ]
    stats, design, stats_again = run_viewer(tmp_path, steps)["snaps"]
    assert stats["scrollY"] == 0, "a tab never shown starts at the top"
    assert design["scrollY"] == 300 and stats_again["scrollY"] == 50


# ---------------------------------------------------------------- missing payloads


def test_tab_without_an_artifact_shows_not_yet(tmp_path):
    s = base_state(artifacts=[art("design")])
    out = run_viewer(tmp_path, [state_step(s), SNAP], tab="stats")
    snap = out["snaps"][0]
    assert snap["pane"] == '<p class="empty">Stats: not yet</p>', "no artifact entry: nothing to wait for"
    button = tab_button(snap["head"], "stats")
    assert 'class="file cur none"' in button and 'title="not yet"' in button and '<span class="ago">—</span>' in button


def test_payload_that_does_not_load_shows_not_yet_until_it_does(tmp_path):
    design = doc_payload(DESIGN_MD)
    steps = [
        state_step(base_state()),
        SNAP,
        {"fail": "artifacts/design.js"},
        SNAP,
        artifact_step("design", design),
        SNAP,
        {"fail": "artifacts/design.js"},
        SNAP,
    ]
    waiting, failed, loaded, failed_later = run_viewer(tmp_path, steps)["snaps"]
    assert waiting["pane"] == '<p class="empty">Loading…</p>'
    assert failed["pane"] == '<p class="empty">Design: not yet · <code>design.md</code></p>'
    assert '<h2 id="doc-goal">' in loaded["pane"]
    assert failed_later["pane"] == loaded["pane"], "a payload once loaded stays"


def test_before_state_js_loads(tmp_path):
    out = run_viewer(tmp_path, [SNAP, {"fail": "state.js"}, SNAP])
    before, failed = out["snaps"]
    assert (
        before["head"] == '<p class="empty">Loading run state…</p>'
        and before["pane"] == '<p class="empty">Loading…</p>'
    )
    assert failed["head"] == '<p class="empty">state.js not loading</p>'
    assert before["title"] == "make-task · Design"


# ---------------------------------------------------------------- re-rendering


def test_rerender_keeps_the_scroll_and_an_expanded_item(tmp_path):
    first, second = doc_payload("# Plan\n\nv1\n", 2000.0), doc_payload("# Plan\n\nv2\n", 2001.0)
    steps = [
        state_step(base_state(artifacts=[art("plan")])),
        artifact_step("plan", first),
        SNAP,
        {"click": "[data-fold]", "data": {"fold": "doc"}},
        {"scroll": 420},
        artifact_step("plan", second),
        SNAP,
        artifact_step("plan", {**second, "html": "<p>same updated</p>"}),
        state_step(base_state(artifacts=[art("plan")], title="Renamed")),
        SNAP,
        {"click": "[data-fold]", "data": {"fold": "doc"}},
        artifact_step("plan", {**second, "updated": 2002.0}),
        SNAP,
    ]
    out = run_viewer(tmp_path, steps, tab="plan")
    closed, rerendered, unchanged, folded = out["snaps"]
    assert '<details class="more"><summary data-fold="doc">Show the plan document</summary>' in closed["pane"]
    assert out["prevented"] == 2, "the page toggles the item itself"
    assert "<p>v2</p>" in rerendered["pane"]
    assert '<details class="more" open><summary data-fold="doc">' in rerendered["pane"], "the expanded item stays open"
    assert rerendered["scrollY"] == 420, "the scroll position is restored after the rewrite"
    assert unchanged["paneWrites"] == rerendered["paneWrites"], (
        "same updated, no re-render; a new state leaves the pane"
    )
    assert "<p>same updated</p>" not in unchanged["pane"] and "<h1>Renamed</h1>" in unchanged["head"]
    assert '<details class="more"><summary' in folded["pane"], "collapsed again, it stays collapsed"


def test_in_page_link_scrolls_without_navigating(tmp_path):
    steps = [
        state_step(base_state()),
        artifact_step("design", doc_payload(DESIGN_MD)),
        {"click": 'a[href^="#"]', "attrs": {"href": "#doc-goal"}},
        {"click": 'a[href^="#"]', "attrs": {"href": "#fn1"}},
    ]
    out = run_viewer(tmp_path, steps)
    assert out["intoView"] == ["doc-goal"] and out["prevented"] == 2, "an unknown target scrolls nowhere"


# ---------------------------------------------------------------- header


def test_header_escapes_and_shows_ages_and_badges(tmp_path):
    evil = "<script>alert(1)</script>"
    s = base_state(title=evil, branch='"><b>', worktree=True)
    s["artifacts"] = [art("design", hint='" onmouseover="x'), art("plan", 1300.0), art("stats", 0)]
    s["tasks"] = [{"n": 1, "status": "done"}, {"n": 2, "status": "run"}]
    s["review"] = [{"round": 1, "started": 1500, "finished": None, "milestone": "find 2/4"}]
    snap = run_viewer(tmp_path, [state_step(s), SNAP])["snaps"][0]
    head = snap["head"]
    for raw in ("<script>alert", '"><b>', '" onmouseover="x'):
        assert raw not in head, raw
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in head and '<span class="tree">worktree</span>' in head
    assert snap["title"] == f"make-task · Design · {evil}", "document.title is text, not markup"
    assert '<span class="ago" data-ago="1300">' in tab_button(head, "design")
    assert '<span class="ago">1 / 2</span>' in tab_button(head, "plan"), "the plan shows its tasks done"
    assert '<span class="live">live</span>' in tab_button(head, "review")
    assert '<span class="ago">—</span>' in tab_button(head, "stats"), "never rendered"
    assert 'class="file none"' in tab_button(head, "progress")


@pytest.mark.parametrize(
    "rounds, finished",
    [
        ([{"round": 1, "started": 1500, "finished": 1600}], None),
        ([{"round": 1, "started": 1500, "finished": None, "milestone": "failed"}], None),
        ([{"round": 2, "started": 1700, "finished": 1800}, {"round": 1, "started": 1500, "finished": None}], None),
        ([{"round": 1, "started": 1500, "finished": None}], 1900.0),
        ([{"round": 1, "started": None, "finished": None}], None),
    ],
    ids=["finished", "failed", "older-round", "run-finished", "not-started"],
)
def test_review_is_live_only_while_its_last_round_runs(tmp_path, rounds, finished):
    s = base_state(review=rounds, finished=finished)
    s["artifacts"].append(art("review"))
    head = run_viewer(tmp_path, [state_step(s), SNAP])["snaps"][0]["head"]
    assert 'class="live"' not in tab_button(head, "review")


def test_header_after_the_run_shows_when_each_tab_was_updated(tmp_path):
    s = base_state(finished=5000.0)
    head = run_viewer(tmp_path, [state_step(s), SNAP])["snaps"][0]["head"]
    assert "data-ago" not in head, "nothing ticks after the run"
    assert re.search(r'data-show="design"[^>]*>Design<span class="ago">[^<—]+</span>', head)


# ---------------------------------------------------------------- Plan


def task(n: int, title: str, status: str, done: int, total: int, started=None, finished=None) -> dict[str, Any]:
    return {"n": n, "title": title, "status": status, "done": done, "total": total, "started": started,
            "finished": finished}  # fmt: skip


def boxes(*items: tuple[str, bool]) -> list[dict[str, Any]]:
    return [{"text": text, "html": MT.render_inline(text), "checked": checked} for text, checked in items]


PLAN_TASKS = [
    task(1, "Store", "done", 2, 2, 1000.0, 1090.0),
    task(2, "Load <sessions>", "run", 1, 3, 1100.0),
    task(3, "Docs", "pending", 0, 2),
]


def plan_payload(updated: float = 2000.0) -> dict[str, Any]:
    p = doc_payload("# Plan\n\n### Task 1: Store\n\n- [x] write\n", updated)
    p["items"] = {
        "1": boxes(("write `store`", True), ("test it", True)),
        "2": boxes(("read `load`", True), ("cache <it>", False), ("test", False)),
        "3": boxes(("readme", False), ("docs", False)),
    }
    return p


def plan_state(tasks: list[dict[str, Any]] = PLAN_TASKS, **kw: Any) -> dict[str, Any]:
    return base_state(artifacts=[art("plan", hint="docs/plans/p.md")], tasks=tasks, **kw)


def plan_rows(pane: str) -> dict[int, tuple[str, str]]:
    """Task n -> (status, the row's html), from the Plan tab's task list."""
    rows = re.findall(r'<li class="task (\w+)">(.*?)(?=<li class="task |</ul><details class="more")', pane, re.DOTALL)
    out = {}
    for status, html in rows:
        m = re.search(r'<span class="k">(\d+)</span>', html)
        assert m, html
        out[int(m.group(1))] = (status, html)
    return out


def task_fold(row: str) -> str:
    m = re.match(r'<details class="tfold"( open)?><summary class="trow" data-fold="task:\d+">', row)
    assert m, row
    return "open" if m.group(1) else "closed"


def fold_click(key: str) -> dict[str, Any]:
    return {"click": "[data-fold]", "data": {"fold": key}}


def test_plan_tab_rows_join_the_tasks_with_their_items(tmp_path):
    out = run_viewer(tmp_path, [state_step(plan_state()), artifact_step("plan", plan_payload()), SNAP], tab="plan")
    pane = out["snaps"][0]["pane"]
    rows = plan_rows(pane)
    assert {n: status for n, (status, _) in rows.items()} == {1: "done", 2: "run", 3: "pending"}
    done, run, pending = rows[1][1], rows[2][1], rows[3][1]
    assert '<span class="bar">2/2<i><b style="width:100%"></b></i></span><span class="dur">1m 30s</span>' in done
    assert (
        '<span class="bar">1/3<i><b style="width:33%"></b></i></span><span class="dur live" data-since="1100">' in run
    )
    assert '<span class="bar">0/2<i><b style="width:0%"></b></i></span><span class="dur"></span>' in pending
    assert '<span class="t"><span class="k">2</span>Load &lt;sessions&gt;</span>' in run, "a title is text"
    assert '<li class="x">read <code>load</code></li><li>cache &lt;it&gt;</li><li>test</li>' in run, (
        "item html goes in as HTML, a checked item marked"
    )
    assert '<li class="x">write <code>store</code></li><li class="x">test it</li>' in done
    assert (
        '<div class="now"><span>▸</span><span class="text">Task 2 of 3 · Load &lt;sessions&gt;</span>'
        '<span class="clock">3 of 7 checks</span></div>' in pane
    )
    assert (
        '<h2 class="sect">Tasks <span class="count">3</span><span class="aside"><code>docs/plans/p.md</code></span></h2>'
        in pane
    )
    assert pane.index('<ul class="rows">') < pane.index('<details class="more"><summary data-fold="doc">')


def test_plan_tab_opens_the_running_tasks_checklist_and_keeps_what_the_user_toggled(tmp_path):
    moved = [
        task(1, "Store", "done", 2, 2, 1000.0, 1090.0),
        task(2, "Load <sessions>", "done", 3, 3, 1100.0, 1200.0),
        task(3, "Docs", "run", 0, 2, 1200.0),
    ]
    steps = [
        state_step(plan_state()),
        artifact_step("plan", plan_payload()),
        SNAP,
        fold_click("task:1"),
        fold_click("task:2"),
        SNAP,
        state_step(plan_state(tasks=moved)),
        SNAP,
        fold_click("task:3"),
        state_step(plan_state(tasks=moved, title="Renamed")),
        SNAP,
    ]
    first, toggled, next_task, closed = run_viewer(tmp_path, steps, tab="plan")["snaps"]

    def folds(snap: dict[str, Any]) -> dict[int, str]:
        return {n: task_fold(row) for n, (_, row) in plan_rows(snap["pane"]).items()}

    assert folds(first) == {1: "closed", 2: "open", 3: "closed"}, "only the running task's checklist is open"
    assert folds(toggled) == {1: "closed", 2: "open", 3: "closed"}, "a click toggles the details in place"
    assert toggled["paneWrites"] == first["paneWrites"]
    assert folds(next_task) == {1: "open", 2: "closed", 3: "open"}, (
        "the new state re-renders the rows: what the user toggled stays, the next running task opens"
    )
    assert next_task["paneWrites"] == first["paneWrites"] + 1
    assert plan_rows(next_task["pane"])[2][0] == "done"
    assert "Task 3 of 3 · Docs" in next_task["pane"]
    assert closed["paneWrites"] == next_task["paneWrites"], "a state that leaves the tasks leaves the pane"


def test_plan_tab_rerenders_with_closed_items_kept_closed(tmp_path):
    steps = [
        state_step(plan_state()),
        artifact_step("plan", plan_payload()),
        fold_click("task:2"),
        artifact_step("plan", plan_payload(2001.0)),
        SNAP,
    ]
    snap = run_viewer(tmp_path, steps, tab="plan")["snaps"][0]
    assert task_fold(plan_rows(snap["pane"])[2][1]) == "closed", "the user closed the running task's checklist"


def test_plan_tab_document_toggle(tmp_path):
    p = plan_payload()
    steps = [
        state_step(plan_state()),
        artifact_step("plan", p),
        SNAP,
        fold_click("doc"),
        artifact_step("plan", {**p, "updated": 2001.0}),
        SNAP,
    ]
    closed, opened = run_viewer(tmp_path, steps, tab="plan")["snaps"]
    doc = f'<div class="docwrap"><article class="doc">{p["html"]}</article>'
    assert f'<details class="more"><summary data-fold="doc">Show the plan document</summary>{doc}' in closed["pane"]
    assert (
        f'<details class="more" open><summary data-fold="doc">Show the plan document</summary>{doc}' in opened["pane"]
    )


def test_plan_tab_edge_states(tmp_path):
    all_done = [task(1, "Store", "done", 2, 2, 1000.0, 1090.0), task(4, "Empty", "done", 0, 0)]
    left_running = [task(1, "Store", "run", 1, 2, 1000.0)]
    steps = [
        state_step(plan_state(tasks=all_done)),
        artifact_step("plan", plan_payload()),
        SNAP,
        state_step(plan_state(tasks=[])),
        SNAP,
        state_step(plan_state(tasks=left_running, finished=1300.0)),
        SNAP,
    ]
    done, no_tasks, stopped = run_viewer(tmp_path, steps, tab="plan")["snaps"]
    assert '<div class="now done"><span>✓</span><span class="text">All 2 tasks done</span>' in done["pane"]
    assert '<span class="clock">2 of 2 checks</span>' in done["pane"]
    assert (
        '<li class="task done"><div class="trow"><span class="dot"></span><span class="t"><span class="k">4</span>Empty'
        in done["pane"]
    ), "a task without items has no checklist to open"
    assert '<p class="empty">No task sections in the plan yet.</p>' in no_tasks["pane"]
    assert 'class="now' not in no_tasks["pane"] and "Show the plan document" in no_tasks["pane"]
    assert '<span class="dur">5m 00s</span>' in plan_rows(stopped["pane"])[1][1], "the run's end stops the clock"
    assert "data-since" not in stopped["pane"]


# ---------------------------------------------------------------- Progress


def pline(kind: str, text: str, at: str | None = None, children: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"at": at, "kind": kind, "text": text, "children": [pline("item", c) for c in children]}


def pentry(n: int | None, title: str, lines: list[dict[str, Any]], at: str | None = None) -> dict[str, Any]:
    return {"at": at, "task": n, "title": title, "lines": lines}


PROGRESS = {
    "source": "/state/progress.txt",
    "header": {"plan": "docs/plans/p.md", "started": "2026-11-06 20:46:02"},
    "entries": [
        pentry(
            1,
            "Store",
            [pline("item", "modified: a.py", children=("nested",)), pline("decision", "task 1: kept it - simpler")],
            "2026-11-06 20:50:16",
        ),
        pentry(
            2,
            "Load",
            [pline("deviation", "task 2: skipped x - why"), pline("text", "free line")],
            "2026-11-06 21:10:00",
        ),
        pentry(
            None,
            "Review",
            [
                pline("review", "review round 1: 0 critical, 2 major, 1 gating"),
                pline("item", "fixes: b"),
                pline("review", "review round 2 fix (commit abc), the user's rule"),
            ],
        ),
        pentry(None, "fixer: done", [pline("decision", "fixer: one helper", "2026-11-06 21:49:33")]),
    ],
    "raw": "not shown",
    "updated": 3000.0,
}


def progress_steps(payload: dict[str, Any] = PROGRESS) -> list[dict[str, Any]]:
    return [state_step(base_state(artifacts=[art("progress")])), artifact_step("progress", payload)]


def sections(pane: str) -> list[str]:
    return re.findall(r'<li class="sec"><time[^>]*>[^<]*</time><span>([^<]*)</span></li>', pane)


def chip(pane: str, key: str) -> str:
    m = re.search(rf'<button type="button" class="[^"]*" data-filter="{key}"[^>]*>.*?</button>', pane)
    assert m, pane
    return m.group(0)


def filter_click(key: str) -> dict[str, Any]:
    return {"click": "[data-filter]", "data": {"filter": key}}


def test_progress_timeline_marks_decisions_deviations_and_review(tmp_path):
    pane = run_viewer(tmp_path, [*progress_steps(), SNAP], tab="progress")["snaps"][0]["pane"]
    assert sections(pane) == ["fixer: done", "Review", "Task 2 · Load", "Task 1 · Store"], "newest entry first"
    assert '<li class="sec"><time title="2026-11-06 20:50:16">20:50:16</time><span>Task 1 · Store</span></li>' in pane
    assert (
        '<div class="loghead"><span>plan <code>docs/plans/p.md</code></span>'
        "<span>started <code>2026-11-06 20:46:02</code></span></div>" in pane
    )
    task1 = pane[pane.index("Task 1 · Store") :]
    assert task1.index("modified: a.py") < task1.index("kept it"), "an entry's lines stay in file order"
    assert '<li><time></time><div class="line">modified: a.py<ul class="kids"><li>nested</li></ul></div></li>' in pane
    assert '<span class="mark dec"><span class="tag">decision</span>task 1: kept it - simpler</span>' in pane
    assert (
        '<li><time title="2026-11-06 21:49:33">21:49:33</time><div class="line"><span class="mark dec">'
        '<span class="tag">decision</span>fixer: one helper</span></div></li>' in pane
    )
    assert '<span class="mark dev"><span class="tag">deviation</span>task 2: skipped x - why</span>' in pane
    assert '<span class="mark rev"><span class="tag">round 1</span>0 critical, 2 major, 1 gating</span>' in pane
    assert (
        '<span class="mark rev"><span class="tag">review</span>review round 2 fix (commit abc), the user&#39;s rule</span>'
        in pane
    ), "a review line without `: ` keeps its text whole"
    assert '<div class="line">free line</div>' in pane and '<div class="line">fixes: b</div>' in pane
    assert "not shown" not in pane, "the raw text is the fallback only"


@pytest.mark.parametrize(
    "key, label, count, texts, entries",
    [
        ("all", "All", 8, ["modified: a.py", "free line", "fixes: b"], ["fixer: done", "Review", "Task 2 · Load",
                                                                         "Task 1 · Store"]),
        ("decision", "Decisions", 2, ["kept it", "one helper"], ["fixer: done", "Task 1 · Store"]),
        ("deviation", "Deviations", 1, ["skipped x"], ["Task 2 · Load"]),
        ("review", "Review", 2, ["0 critical", "review round 2 fix"], ["Review"]),
    ],
)  # fmt: skip
def test_progress_filters(tmp_path, key, label, count, texts, entries):
    steps = [
        *progress_steps(),
        filter_click(key),
        SNAP,
        artifact_step("progress", {**PROGRESS, "updated": 3001.0}),
        SNAP,
    ]
    filtered, reloaded = run_viewer(tmp_path, steps, tab="progress")["snaps"]
    pane = filtered["pane"]
    assert chip(pane, key) == (
        f'<button type="button" class="rchip cur" data-filter="{key}" aria-pressed="true"><b>{label}</b>{count}</button>'
    )
    assert [m for m in re.findall(r'data-filter="(\w+)"', pane)] == ["all", "decision", "deviation", "review"]
    assert all('aria-pressed="false"' in chip(pane, k) for k in ("all", "decision", "deviation", "review") if k != key)
    assert sections(pane) == entries, "an entry with no matching line is left out"
    for text in texts:
        assert text in pane, text
    if key != "all":
        kinds = {"decision": "dec", "deviation": "dev", "review": "rev"}
        assert re.findall(r'class="mark (\w+)"', pane) == [kinds[key]] * count
        assert "modified: a.py" not in pane and "free line" not in pane
    assert reloaded["paneWrites"] == filtered["paneWrites"] + 1
    assert reloaded["pane"] == pane, "the filter stays across a new payload"


def test_progress_filter_with_no_match_and_back_to_all(tmp_path):
    payload = {**PROGRESS, "entries": [pentry(1, "Store", [pline("item", "modified: a.py")])]}
    steps = [*progress_steps(payload), SNAP, filter_click("deviation"), SNAP, filter_click("all"), SNAP]
    first, none, back = run_viewer(tmp_path, steps, tab="progress")["snaps"]
    assert '<p class="empty">No deviations logged.</p>' in none["pane"] and '<ol class="tl">' not in none["pane"]
    assert "<b>Deviations</b>0</button>" in none["pane"]
    assert back["pane"] == first["pane"]
    assert none["paneWrites"] == first["paneWrites"] + 1 and back["paneWrites"] == none["paneWrites"] + 1


def test_progress_raw_fallback(tmp_path):
    raw = {"source": "/p.txt", "header": {}, "entries": [], "raw": "task 1: <script>x</script>\n", "updated": 1.0}
    empty = {**raw, "raw": "", "updated": 2.0}
    steps = [*progress_steps(raw), SNAP, artifact_step("progress", empty), SNAP]
    shown, nothing = run_viewer(tmp_path, steps, tab="progress")["snaps"]
    assert shown["pane"] == '<pre class="raw">task 1: &lt;script&gt;x&lt;/script&gt;\n</pre>', (
        "no entries: the raw text"
    )
    assert nothing["pane"] == '<p class="empty">The progress log is empty.</p>'


def test_progress_strings_are_text(tmp_path):
    evil = "<script>alert(1)</script>"
    payload = {
        "source": evil,
        "header": {evil: evil},
        "entries": [
            pentry(1, evil, [pline("item", evil, evil, (evil,)), pline("decision", evil)], evil),
            pentry(None, evil, [pline("review", f"review {evil}: {evil}"), pline("text", evil)]),
        ],
        "raw": evil,
        "updated": 1.0,
    }
    pane = run_viewer(tmp_path, [*progress_steps(payload), SNAP], tab="progress")["snaps"][0]["pane"]
    assert "<script" not in pane
    # the header's key and value; two entry titles; two stamps, each as text and title; four line texts, the
    # review tag and a child
    assert pane.count("&lt;script&gt;alert(1)&lt;/script&gt;") == 14
    assert '<span class="tag">&lt;script&gt;alert(1)&lt;/script&gt;</span>' in pane, "the review tag too"


# ---------------------------------------------------------------- Review until it gets its own view


def test_review_lists_rounds(tmp_path):
    s = base_state(artifacts=[art("review")])
    review = {"rounds": [{"round": 1, "state": "done"}, {"round": 2, "state": "<b>running</b>"}], "updated": 1.0}
    rev = run_viewer(tmp_path, [state_step(s), artifact_step("review", review), SNAP], tab="review")["snaps"][0]
    assert '<span class="rchip"><b>Round 1</b>done</span>' in rev["pane"]
    assert "&lt;b&gt;running&lt;/b&gt;" in rev["pane"] and "<b>running" not in rev["pane"]


# ---------------------------------------------------------------- with mt's own files


def test_viewer_renders_what_mt_writes(mt, git_repo, tmp_path):
    run = init_run(mt, git_repo, "Real </script> state")
    design = run / "design.md"
    design.write_text("# Design\n\n## Goal\n\nKeep <b>sessions</b> & more.\n")
    assert mt("artifact", "design", str(design), "--hint", "design.md", cwd=git_repo).code == 0
    steps = [{"eval": (run / "state.js").read_text()}, {"eval": (run / "artifacts" / "design.js").read_text()}, SNAP]
    snap = run_viewer(tmp_path, steps)["snaps"][0]
    assert "Real &lt;/script&gt; state" in snap["head"]
    assert (
        '<h2 id="doc-goal">Goal</h2>' in snap["pane"] and "Keep &lt;b&gt;sessions&lt;/b&gt; &amp; more." in snap["pane"]
    )
    assert '<a class="l2" href="#doc-goal">Goal</a>' in snap["pane"]
    assert 'data-ago="' in tab_button(snap["head"], "design")


def test_viewer_renders_the_plan_and_progress_mt_writes(mt, git_repo, tmp_path):
    run = init_run(mt, git_repo)
    plan = git_repo / "docs" / "plans" / "p.md"
    plan.parent.mkdir(parents=True)
    plan.write_text(
        "# Plan\n\n### Task 1: Store `sessions`\n- [x] write the store\n- [x] test it\n"
        "### Task 2: Load\n- [x] read `load`\n- [ ] cache <it>\n### Task 3: Docs\n- [ ] readme\n"
    )
    fixture = TESTS / "fixtures" / "progress.txt"
    assert mt("plan", str(plan), "--progress", str(fixture), cwd=git_repo).code == 0
    steps = [
        {"eval": (run / "state.js").read_text()},
        {"eval": (run / "artifacts" / "plan.js").read_text()},
        SNAP,
        {"click": "[data-show]", "data": {"show": "progress"}},
        {"eval": (run / "artifacts" / "progress.js").read_text()},
        SNAP,
    ]
    plan_snap, progress_snap = run_viewer(tmp_path, steps, tab="plan")["snaps"]
    rows = plan_rows(plan_snap["pane"])
    assert {n: status for n, (status, _) in rows.items()} == {1: "done", 2: "run", 3: "pending"}
    assert "Store `sessions`" in rows[1][1], "the state's title is text"
    assert (
        task_fold(rows[2][1]) == "open"
        and '<li class="x">read <code>load</code></li><li>cache &lt;it&gt;</li>' in rows[2][1]
    )
    assert "Task 2 of 3 · Load" in plan_snap["pane"] and "3 of 5 checks" in plan_snap["pane"]

    parsed = MT.parse_progress(fixture.read_text())
    pane = progress_snap["pane"]
    titles = [f"Task {e['task']} · {e['title']}" if e["task"] is not None else e["title"] for e in parsed["entries"]]
    assert sections(pane) == [MT.html_escape(t).replace("&#x27;", "&#39;") for t in reversed(titles)]
    lines = [line for e in parsed["entries"] for line in e["lines"]]
    for key, kind in (("decision", "dec"), ("deviation", "dev"), ("review", "rev")):
        n = sum(line["kind"] == key for line in lines)
        assert n and f"</b>{n}</button>" in chip(pane, key) and pane.count(f'class="mark {kind}"') == n
    assert f"<b>All</b>{len(lines)}</button>" in chip(pane, "all")
    assert "&lt;pasted_content&gt;" in pane and "<pasted_content>" not in pane
