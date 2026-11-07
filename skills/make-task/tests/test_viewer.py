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
from conftest import FIXTURES, MT, TESTS, init, init_run

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
const timers = [];
globalThis.setInterval = f => { timers.push(f); return timers.length; };
let clock = null;
const realNow = Date.now.bind(Date);
Date.now = () => (clock == null ? realNow() : clock * 1000);
(0, eval)(script);
const snaps = [];
for (const step of steps) {
  if ('eval' in step) (0, eval)(step.eval);
  else if ('scroll' in step) globalThis.scrollY = step.scroll;
  else if ('now' in step) clock = step.now;
  else if ('timers' in step) timers.forEach(f => f());
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
    {"fail": src} (the last injected script with that src fails to load), {"now": epoch seconds} (what
    Date.now says from then on), {"timers": true} (every setInterval callback runs once) or {"snap": true}."""
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


# ---------------------------------------------------------------- Review

ROUND_LINES = (FIXTURES / "events-round.jsonl").read_text(encoding="utf-8").splitlines(keepends=True)
REPORT = json.loads((FIXTURES / "revmux-findings.json").read_text(encoding="utf-8"))
EVIL = "<script>alert(1)</script>"


def fmt_dur(s: float) -> str:
    """The page's fmtDur."""
    s = max(0, int(s))
    h, m, x = s // 3600, s % 3600 // 60, s % 60
    return f"{h}h {m}m" if h else f"{m}m {x:02d}s" if m else f"{x}s"


def upto(kind: str, agent: str) -> list[str]:
    """The fixture's lines up to and with the first `kind` event of `agent`."""
    i = next(i for i, line in enumerate(ROUND_LINES) if json.loads(line).get("kind") == kind
             and json.loads(line).get("agent") == agent)  # fmt: skip
    return ROUND_LINES[: i + 1]


def round_entry(n: int, status: str, **kw: Any) -> dict[str, Any]:
    """One round of the review payload, as mt's round_payload builds it."""
    r: dict[str, Any] = {
        "round": n,
        "run": f"0{n}-run",
        "state": status,
        "verdict": None,
        "milestone": "",
        "counts": {"critical": 0, "major": 0, "minor": 0},
        "findings": [],
        "questions": [],
        "not_gating": [],
        "deferred": [],
        "dropped": [],
        "degraded": [],
        "confirming": False,
        "started": 1000.0,
        "finished": None,
        "duration": None,
        "events": f"/repo/.revmux/tasks/store/0{n}-run/events.jsonl",
        "log": f"/state/run/revmux/round-{n}.log",
        "live": None,
    }
    r.update(kw)
    return r


def live_entry(n: int, lines: list[str], **kw: Any) -> dict[str, Any]:
    snap = MT.fold_events(lines)
    events = [json.loads(line) for line in lines if line.endswith("\n")]
    base = {"live": snap, "started": snap["started"] or 1000.0, "dropped": snap["dropped"], "milestone": MT.milestone(events)}
    return round_entry(n, "running", **{**base, **kw})


def done_entry(n: int, report: dict[str, Any] = REPORT, **kw: Any) -> dict[str, Any]:
    findings = [MT.fold_finding(f, "") for f in report["findings"]]
    questions = [MT.fold_finding(q, "") for q in report.get("open_questions") or []]
    counts = {sev: sum(f["severity"] == sev for f in findings) for sev in MT.SEVERITIES}
    base = {"verdict": "4 gating", "milestone": "done", "counts": counts, "findings": findings, "questions": questions,
            "finished": 1361.673, "duration": 361.673}  # fmt: skip
    return round_entry(n, "done", **{**base, **kw})


def review_steps(rounds: list[dict[str, Any]], updated: float = 5000.0, **state_kw: Any) -> list[dict[str, Any]]:
    s = base_state(artifacts=[art("review", hint="revmux review rounds")], **state_kw)
    return [state_step(s), artifact_step("review", {"rounds": rounds, "updated": updated})]


def review_pane(tmp_path: Path, rounds: list[dict[str, Any]], now: float | None = None, **state_kw: Any) -> str:
    steps = ([{"now": now}] if now is not None else []) + review_steps(rounds, **state_kw) + [SNAP]
    return run_viewer(tmp_path, steps, tab="review")["snaps"][0]["pane"]


def strip(pane: str) -> list[tuple[str, str, str]]:
    """(round, text, "cur" or "") of each chip in the rounds strip."""
    chips = re.findall(r'<button type="button" class="rchip( cur)?" data-round="(\d+)" aria-pressed="\w+">'
                       r"<b>Round \d+</b>([^<]*)</button>", pane)  # fmt: skip
    return [(n, text, "cur" if cur else "") for cur, n, text in chips]


def stage_steps(pane: str) -> list[tuple[str, str, str]]:
    """(state, name, description) of each stage step, the description's ticking span kept as is."""
    return re.findall(
        r'<div class="stage (\w+)"><span class="node"></span><span class="sn">([^<]*)</span>'
        r'<span class="sd">(.*?)</span></div>(?=<div class="stage |</div>)',
        pane,
    )


def agent_rows(pane: str) -> dict[str, tuple[str, str, str, str]]:
    """Agent name -> (row class, lens, state text, activity line)."""
    rows = re.findall(
        r'<li class="row (\w+)"><span class="dot"></span><span class="name">([^<]*)</span><span class="lens">([^<]*)'
        r'</span><span class="state">([^<]*)</span><span class="dur[^"]*"[^>]*>[^<]*</span>'
        r'(?:<span class="act">([^<]*)</span>)?</li>',
        pane,
    )
    return {name: (cls, lens, st, act) for cls, name, lens, st, act in rows}


def finding_fold(pane: str, key: str) -> str:
    """"open" or "closed": the details whose summary folds `key`."""
    m = re.search(rf'<details class="([^"]*)"( open)?><summary data-fold="{re.escape(key)}">', pane)
    assert m, key
    return "open" if m.group(2) else "closed"


def test_review_live_round_shows_stages_agents_and_findings(tmp_path):
    lines = upto("agent_done", "verify docs")
    r2 = live_entry(2, lines, run="02-after-fix")
    live = r2["live"]
    now = live["updated"] + 5.5  # past the second: the stub clock divides by 1000
    pane = review_pane(tmp_path, [done_entry(1), r2], now=now)

    assert strip(pane) == [("1", "1 critical · 3 major · 2 minor · 4 gating", ""), ("2", "02-after-fix · live", "cur")]
    activity = MT.html_escape(live["activity"]["text"]).replace("&#x27;", "&#39;")
    assert (
        '<div class="now"><span>▸</span><span class="text">Verify · 1 of 2 agents done'
        f'<span class="sub">verify docs · {activity}</span></span>'
        f'<span class="fresh">updated <span data-since="{live["updated"]}">5s</span> ago</span>'
        f'<span class="clock" data-since="{r2["started"]}">{fmt_dur(now - r2["started"])}</span></div>' in pane
    )
    assert 'class="warnline"' not in pane, "fresh data: no warning"

    find, synthesis, verify = live["stages"]
    assert stage_steps(pane) == [
        ("done", "Find", f"4 agents · 13 findings · {fmt_dur(find['finished'] - find['started'])}"),
        ("done", "Synthesis", f"1 agent · 13 → 10 · {fmt_dur(synthesis['finished'] - synthesis['started'])}"),
        ("run", "Verify", (f'1 of 2 agents done · 1 of 1 kept · <span data-since="{verify["started"]}">'
                           f"{fmt_dur(now - verify['started'])}</span>")),
        ("pending", "Result", "verdict and final findings"),
    ]  # fmt: skip

    assert re.findall(r'<li class="group">([^<]*)</li>', pane) == ["Verify", "Synthesis", "Find"], "latest stage first"
    assert '<h2 class="sect">Agents <span class="count">7</span><span class="aside">1 running · 6 done</span></h2>' in pane
    rows = agent_rows(pane)
    assert list(rows) == ["verify docs", "verify go-ask", "synthesis", "bugs+impl", "arch+quality", "docs+tests",
                          "adversarial"]  # fmt: skip
    go = next(a for a in live["agents"] if a["name"] == "verify go-ask")
    assert rows["verify go-ask"] == (
        "run", "mcps/go-ask", "running", MT.html_escape(f"{go['progress']} · {go['activity']}").replace("&#x27;", "&#39;")
    )  # fmt: skip
    assert '<span class="dur live" data-since="' in pane, "a running agent's time ticks"
    assert rows["verify docs"] == ("done", "mcps/go-ask/docs", "1 of 1 kept", "")
    assert rows["adversarial"] == ("done", "adversarial · gpt-6.1-sol", "4 findings", ""), "lenses and model"
    assert rows["synthesis"][2] == "10 findings"

    assert (
        '<h2 class="sect">Findings <span class="count">10</span><span class="aside">merged by synthesis · not verified yet'
        '</span></h2><div class="chips"><span class="sev major">1 major</span><span class="sev minor">9 minor</span></div>'
        in pane
    )
    assert finding_fold(pane, "r2:f:adversarial-1") == "closed", "only a critical finding opens by itself"
    assert re.search(
        r'<summary data-fold="r2:f:adversarial-1"><span class="sev major">major</span><span class="title">Message queued'
        r'[^<]*</span><span class="meta">conf 99</span><span class="loc"><code>mcps/go-ask/answer\.go:117-118</code> · '
        r"adversarial, bugs\+impl · adversarial-1</span></summary><div class=\"body\">",
        pane,
    ), "location, sources and id under the title; body and fix behind it"
    assert '<div class="fix"><b>fix</b>In `matchedTurnState`' in pane, "body and fix are text, not markdown"
    assert finding_fold(pane, "r2:f-rest") == "closed" and "9 minor findings</summary>" in pane
    assert pane.index("9 minor findings") < pane.index('data-fold="r2:f:adversarial-2"'), "the minors are folded"
    assert "dropped by synthesis" not in pane, "nothing dropped"
    assert pane.endswith(
        '<footer class="paths"><span><code>/repo/.revmux/tasks/store/02-run/events.jsonl</code></span>'
        "<span><code>/state/run/revmux/round-2.log</code></span>"
        '<span class="safe">Closing this view does not stop the review</span></footer>'
    )


def test_review_live_round_during_find_and_after_its_last_stage(tmp_path):
    early = live_entry(1, upto("agent_done", "arch+quality"))
    pane = review_pane(tmp_path, [early], now=early["live"]["updated"] + 2.5)
    steps = stage_steps(pane)
    assert [s[:2] for s in steps] == [("run", "Find"), ("pending", "Result")]
    assert steps[0][2].startswith("1 of 4 agents done · 1 finding · <span data-since=")
    assert "Find · 1 of 4 agents done" in pane
    assert '<span class="aside">as the finders report them</span>' in pane
    assert agent_rows(pane)["adversarial"][:3] == ("run", "adversarial · gpt-6.1-sol", "running")

    whole = live_entry(1, ROUND_LINES)
    pane = review_pane(tmp_path, [whole], now=whole["live"]["updated"] + 2.5)
    steps = stage_steps(pane)
    verify = whole["live"]["stages"][2]
    assert steps[2] == ("done", "Verify", f"2 agents · 9 of 10 kept · {fmt_dur(verify['finished'] - verify['started'])}")
    assert steps[3] == ("run", "Result", "waiting for the report"), "every stage over: the report comes next"
    assert "Verify · 2 of 2 agents done" in pane and '<span class="aside">0 running · 7 done</span>' in pane


def test_review_live_agents_retried_rate_limited_and_degraded(tmp_path):
    def ev(kind: str, agent: str = "", text: str = "", **kw: Any) -> str:
        return json.dumps({"kind": kind, "agent": agent, "text": text, "at": "2026-11-06T21:00:00+00:00", **kw}) + "\n"

    lines = [
        ev("stage", stage="find"),
        *(ev("agent_started", a, "bugs") for a in ("a1", "a2", "a3", "a4")),
        ev("agent_retried", "a1", "agent a1 stalled for 2m"),
        ev("agent_progress", "a1", "Read x.go"),
        ev("agent_retried", "a2", "stalled"),
        ev("agent_retried", "a2", "stalled again"),
        ev("agent_done", "a2", "3 findings"),
        ev("rate_limit", "a3", "429, waiting 30s"),
        ev("agent_degraded", "a4", "gave up after 3 tries"),
    ]
    entry = live_entry(1, lines)
    rows = agent_rows(review_pane(tmp_path, [entry], now=entry["live"]["updated"] + 1))
    assert rows["a1"] == ("warn", "bugs", "running · retried once", "Read x.go"), "running again: what it does"
    assert rows["a2"] == ("warn", "bugs", "3 findings · retried 2 times", "retrying: stalled again")
    assert rows["a3"] == ("warn", "bugs", "rate limited", "rate limited: 429, waiting 30s")
    assert rows["a4"] == ("warn", "bugs", "degraded", "degraded: gave up after 3 tries")


def test_review_finished_round_shows_its_verdict_and_final_findings(tmp_path):
    dropped = [MT.fold_finding({"id": "docs-3", "severity": "minor", "title": "Same as bugs+impl-3"}, "synthesis")]
    r1 = done_entry(
        1,
        verdict="2 gating",
        not_gating=[{"id": "adversarial-2", "reason": "vendored"}],
        deferred=[{"id": "adversarial-1", "severity": "major", "file": "README.md", "line": 0, "title": "t",
                   "reason": "docs at the end", "round": 1}],
        dropped=dropped,
    )  # fmt: skip
    pane = review_pane(tmp_path, [r1])
    assert strip(pane) == [("1", "1 critical · 3 major · 2 minor · 2 gating", "cur")]
    assert (
        '<div class="now warn"><span>!</span><span class="text">Round 1 · 2 gating</span>'
        '<span class="clock">6m 01s</span></div>' in pane
    )
    assert (
        '<div class="chips"><span class="sev critical">1 critical</span><span class="sev major">3 major</span>'
        '<span class="sev minor">2 minor</span><span class="sev minor">1 open</span></div>' in pane
    )
    assert '<h2 class="sect">Findings <span class="count">6</span><span class="aside">the recorded result</span></h2>' in pane
    assert finding_fold(pane, "r1:f:bugs+impl-1") == "open", "a critical finding opens by itself"
    assert (
        '<summary data-fold="r1:f:bugs+impl-1"><span class="sev critical">critical</span><span class="title">'
        'Session map written without the lock</span><span class="meta">conf 95 · confirmed</span><span class="loc">'
        '<code>src/store.go:88</code> · bugs+impl, adversarial · bugs+impl-1</span><span class="note">unresolved</span>'
        '</summary><div class="body">...</div><div class="fix"><b>fix</b>...</div></details>' in pane
    )
    assert '<code>src/login.go:12-20</code>' in pane and "<code>README.md</code> · adversarial · adversarial-1" in pane
    assert '<span class="title">Logout keeps the cookie &lt;script&gt;alert(1)&lt;/script&gt;</span>' in pane
    notes = dict(re.findall(r'data-fold="r1:f:([^"]+)">.*?(?:<span class="note">([^<]*)</span>)?</summary>', pane))
    assert notes == {"bugs+impl-1": "unresolved", "bugs+impl-2": "unresolved", "adversarial-1": "deferred: docs at the end",
                     "adversarial-2": "not gating: vendored", "bugs+impl-3": "", "bugs+impl-4": ""}  # fmt: skip
    assert finding_fold(pane, "r1:f-rest") == "closed" and "2 minor findings</summary>" in pane
    assert (
        '<h2 class="sect">Open questions <span class="count">1</span></h2><details class="finding minor">'
        '<summary data-fold="r1:q:q1"><span class="sev minor">question</span><span class="title">'
        "Should sessions expire on restart?</span>" in pane
    )
    assert '<summary data-fold="r1:dropped">1 dropped by synthesis</summary>' in pane
    assert '<summary data-fold="r1:d:docs-3">' in pane and "Same as bugs+impl-3" in pane
    assert '<div class="body empty">No detail.</div>' in pane, "a dropped finding without body or fix"
    assert "/state/run/revmux/round-1.log" in pane and "Closing this view" not in pane, "nothing runs"
    assert 'class="stages"' not in pane and "warnline" not in pane


def test_review_clean_and_degraded_rounds(tmp_path):
    clean = done_entry(2, report={"findings": []}, verdict="clean", confirming=True, duration=None, finished=1090.0)
    pane = review_pane(tmp_path, [clean])
    assert (
        '<div class="now done"><span>✓</span><span class="text">Round 2 · clean · confirming round</span>'
        '<span class="clock">1m 30s</span></div>' in pane
    ), "no duration from the report: the round's own times"
    assert '<p class="empty">No findings.</p>' in pane and 'class="chips"' not in pane
    assert strip(pane) == [("2", "clean", "cur")]

    degraded = done_entry(1, report=json.loads((FIXTURES / "revmux-degraded.json").read_text()), verdict="degraded",
                          degraded=["adversarial"])  # fmt: skip
    pane = review_pane(tmp_path, [degraded])
    assert '<div class="now warn"><span>!</span><span class="text">Round 1 · degraded</span>' in pane
    assert '<p class="warnline">Degraded sources: adversarial</p>' in pane
    assert strip(pane) == [("1", "1 minor · degraded", "cur")]


@pytest.mark.parametrize(
    "entry, run_finished, band, body",
    [
        (
            round_entry(2, "failed", milestone="verify 1/2"),
            None,
            '<div class="now error"><span>✕</span><span class="text">Round 2 · no result · last seen at verify 1/2</span></div>',
            "No result was recorded for this round",
        ),
        (
            round_entry(2, "failed", milestone="failed"),
            None,
            '<div class="now error"><span>✕</span><span class="text">Round 2 · no result</span></div>',
            "revmux ended without a report",
        ),
        (
            round_entry(2, "running", milestone="find 2/4", live=MT.fold_events([])),
            1900.0,
            '<div class="now error"><span>✕</span><span class="text">Round 2 · no result · last seen at find 2/4</span></div>',
            "or the run ended",
        ),
        (
            round_entry(2, "running", milestone="starting", live=MT.fold_events(['{"kind":"agent_tokens"}\n'])),
            None,
            '<div class="now"><span>▸</span><span class="text">Round 2 · starting</span><span class="clock" data-since="1000">',
            "Live detail unavailable: <code>events.jsonl</code> holds no event this view can read. The round goes on",
        ),
        (
            round_entry(2, "running", milestone="find 2/4", live={"truncated": True}),
            None,
            '<div class="now"><span>▸</span><span class="text">Round 2 · find 2/4</span><span class="clock" data-since="1000">',
            "Live detail unavailable: <code>events.jsonl</code> is larger than 8 MB.",
        ),
    ],
    ids=["failed", "failed-at-once", "run-ended", "format-not-ok", "truncated"],
)
def test_review_round_states(tmp_path, entry, run_finished, band, body):
    pane = review_pane(tmp_path, [entry], now=5000.0, finished=run_finished)
    assert band in pane and body in pane
    live = entry["state"] == "running" and run_finished is None
    assert ("Closing this view does not stop the review" in pane) is live
    assert strip(pane)[0][1] == ("02-run · live" if live else "02-run · failed")
    assert "No update for" not in pane and 'class="fresh' not in pane, "no news to wait for"
    assert 'class="stages"' not in pane and "Agents" not in pane


def test_review_starting_round_and_its_stale_warning(tmp_path):
    entry = round_entry(1, "running", milestone="starting", live=MT.fold_events([]))
    pane = review_pane(tmp_path, [entry], now=1005.0)
    assert (
        '<div class="now"><span>▸</span><span class="text">Starting revmux…</span><span class="fresh">updated '
        '<span data-since="1000">5s</span> ago</span><span class="clock" data-since="1000">5s</span></div>'
        '<p class="empty">Waiting for the first event of the round.</p>' in pane
    )
    pane = review_pane(tmp_path, [{**entry, "live": None}], now=1040.0)
    assert (
        '<span class="fresh stale">updated <span data-since="1000">40s</span> ago</span>' in pane
        and '<p class="warnline">No update for <span data-since="1000">40s</span>: what follows may be out of date. '
        "The review goes on.</p>" in pane
    ), "no event 30 s after the start"
    assert "Starting revmux…" in pane, "a round with no snapshot yet is starting too"


def test_review_stale_warning_comes_with_time_alone(tmp_path):
    entry = live_entry(1, upto("agent_done", "arch+quality"))
    t = entry["live"]["updated"]
    steps = [{"now": t + 10}, *review_steps([entry]), SNAP, {"now": t + 29}, {"timers": True}, SNAP,
             {"now": t + 31.5}, {"timers": True}, SNAP, {"timers": True}, SNAP]  # fmt: skip
    fresh, quiet, stale, again = run_viewer(tmp_path, steps, tab="review")["snaps"]
    assert "warnline" not in fresh["pane"] and quiet["paneWrites"] == fresh["paneWrites"], "within 30 s: nothing"
    assert stale["paneWrites"] == fresh["paneWrites"] + 1, "no new payload: the clock re-renders the tab"
    assert f'<p class="warnline">No update for <span data-since="{t}">31s</span>' in stale["pane"]
    assert '<span class="fresh stale">' in stale["pane"] and "Find · 1 of 4 agents done" in stale["pane"]
    assert again["paneWrites"] == stale["paneWrites"]


def test_review_without_rounds(tmp_path):
    assert review_pane(tmp_path, []) == '<p class="empty">No review round yet.</p>'


def test_review_strip_follows_the_live_round_until_the_user_picks_another(tmp_path):
    lines = upto("agent_done", "arch+quality")
    r1, r2, r3 = done_entry(1), live_entry(2, lines), live_entry(3, lines)
    t = r2["live"]["updated"]

    def payload(rounds: list[dict[str, Any]], updated: float) -> dict[str, Any]:
        return artifact_step("review", {"rounds": rounds, "updated": updated})

    def pick(n: int) -> dict[str, Any]:
        return {"click": "[data-round]", "data": {"round": str(n)}}

    steps = [
        {"now": t + 1},
        *review_steps([r1, r2]),
        SNAP,
        pick(1),
        SNAP,
        payload([r1, done_entry(2, verdict="clean"), r3], 5001.0),
        SNAP,
        pick(9),
        SNAP,
        pick(3),
        SNAP,
        payload([r1, done_entry(2, verdict="clean"), done_entry(3, verdict="clean"), live_entry(4, lines)], 5002.0),
        SNAP,
    ]
    live2, picked1, still1, unknown, followed3, followed4 = run_viewer(tmp_path, steps, tab="review")["snaps"]

    def cur(snap: dict[str, Any]) -> list[str]:
        return [n for n, _, c in strip(snap["pane"]) if c]

    assert cur(live2) == ["2"] and "Find · 1 of 4 agents done" in live2["pane"]
    assert cur(picked1) == ["1"] and "Round 1 · 4 gating" in picked1["pane"]
    assert picked1["paneWrites"] == live2["paneWrites"] + 1
    assert cur(still1) == ["1"] and "Round 1 · 4 gating" in still1["pane"], "a picked round stays shown"
    assert strip(still1["pane"])[2] == ("3", "03-run · live", "")
    assert unknown["paneWrites"] == still1["paneWrites"], "a round not in the payload changes nothing"
    assert cur(followed3) == ["3"] and "Find · 1 of 4 agents done" in followed3["pane"]
    assert cur(followed4) == ["4"], "clicking the followed round follows again"


def test_review_rerender_keeps_expanded_and_closed_findings(tmp_path):
    first = live_entry(2, upto("agent_done", "synthesis"))
    later = live_entry(2, upto("agent_done", "verify docs"))
    crit = done_entry(1)
    steps = [
        {"now": later["live"]["updated"] + 1},
        *review_steps([crit, first]),
        SNAP,
        fold_click("r2:f:adversarial-1"),
        fold_click("r2:f-rest"),
        SNAP,
        artifact_step("review", {"rounds": [crit, later], "updated": 5001.0}),
        SNAP,
        {"click": "[data-round]", "data": {"round": "1"}},
        fold_click("r1:f:bugs+impl-1"),
        artifact_step("review", {"rounds": [crit, later], "updated": 5002.0}),
        SNAP,
    ]
    out = run_viewer(tmp_path, steps, tab="review")
    before, toggled, rerendered, closed = out["snaps"]
    assert out["prevented"] == 3
    assert finding_fold(before["pane"], "r2:f:adversarial-1") == "closed"
    assert toggled["paneWrites"] == before["paneWrites"], "a click toggles the details in place"
    assert "Verify · 1 of 2 agents done" in rerendered["pane"]
    assert rerendered["paneWrites"] == toggled["paneWrites"] + 1
    assert finding_fold(rerendered["pane"], "r2:f:adversarial-1") == "open"
    assert finding_fold(rerendered["pane"], "r2:f-rest") == "open"
    assert finding_fold(rerendered["pane"], "r2:f:adversarial-2") == "closed"
    assert finding_fold(closed["pane"], "r1:f:bugs+impl-1") == "closed", "the user closed the critical finding"


def test_review_strings_are_text(tmp_path):
    def ev(kind: str, **kw: Any) -> str:
        return json.dumps({"kind": kind, "agent": EVIL, "text": EVIL, "at": "2026-11-06T21:00:00+00:00", **kw}) + "\n"

    found = {"id": EVIL, "file": EVIL, "line": 3, "severity": "critical", "title": EVIL, "body": EVIL, "fix": EVIL,
             "sources": [EVIL], "verdict": EVIL}  # fmt: skip
    lines = [
        ev("stage", stage=EVIL),
        ev("agent_started"),
        ev("agent_state", text=f"model: {EVIL}"),
        ev("agent_retried"),
        ev("agent_progress"),
        ev("agent_activity"),
        ev("findings", findings=[found]),
        ev("dropped", findings=[found]),
    ]
    live = live_entry(3, lines, run=EVIL, milestone=EVIL, events=EVIL, log=EVIL)
    done = done_entry(1, report={"findings": [found], "open_questions": [found]}, run=EVIL, verdict=EVIL,
                      not_gating=[{"id": EVIL, "reason": EVIL}], degraded=[EVIL], dropped=[found], events=EVIL,
                      log=EVIL)  # fmt: skip
    failed = round_entry(2, "failed", run=EVIL, milestone=EVIL, dropped=[found], events=EVIL, log=EVIL)
    steps = [
        {"now": MT.event_time("2026-11-06T21:00:05+00:00")},
        *review_steps([done, failed, live]),
        SNAP,
        {"click": "[data-round]", "data": {"round": "1"}},
        SNAP,
        {"click": "[data-round]", "data": {"round": "2"}},
        SNAP,
    ]
    shown = run_viewer(tmp_path, steps, tab="review")["snaps"]
    for snap in shown:
        assert "<script" not in snap["pane"], snap["pane"]
    esc = "&lt;script&gt;alert(1)&lt;/script&gt;"
    live_pane, done_pane, failed_pane = (s["pane"] for s in shown)
    for part in (f'<span class="title">{esc}</span>', f'<div class="body">{esc}</div>', f"<b>fix</b>{esc}</div>",
                 f'<span class="name">{esc}</span>', f'<span class="lens">{esc} · {esc}</span>',
                 f'<span class="act">{esc} · {esc}</span>', f'<span class="sub">{esc} · {esc}</span>',
                 f'<span class="sn">{esc}</span>', f"<code>{esc}:3</code>", f'data-fold="r3:f:{esc}"'):  # fmt: skip
        assert part in live_pane, part
    assert f"<b>Round 3</b>{esc} · live</button>" in live_pane
    for part in (f'<span class="text">Round 1 · {esc}</span>', f'<span class="note">not gating: {esc}</span>',
                 f"Degraded sources: {esc}</p>", f'<span class="meta">{esc}</span>',
                 f"<span><code>{esc}</code></span>"):  # fmt: skip
        assert part in done_pane, part
    assert f"last seen at {esc}</span>" in failed_pane


def test_viewer_renders_the_review_mt_writes(mt, git_repo, tmp_path, monkeypatch):
    run = init_run(mt, git_repo)
    assert mt("set", "revmux_task=store").code == 0
    round_dir = tmp_path / "tasks" / "store" / "01-initial"
    round_dir.mkdir(parents=True)
    r = mt("review", "start", "--round", "1", "--run", "01-initial", "--round-dir", str(round_dir))
    assert r.code == 0, r.err
    monkeypatch.setattr(MT, "process_alive", lambda task, name: True)

    def snap(*scripts: str) -> str:
        steps = [{"eval": (run / s).read_text()} for s in scripts]
        return run_viewer(tmp_path, [*steps, SNAP], tab="review")["snaps"][0]

    started = snap("state.js", "artifacts/review.js")
    assert "Starting revmux…" in started["pane"] and '<span class="live">live</span>' in started["head"]

    (round_dir / "events.jsonl").write_text("".join(upto("agent_done", "verify docs")))
    assert mt("review", "wait", "--round", "1", "--deadline", "0").out == "running\n"
    pane = snap("state.js", "artifacts/review.js")["pane"]
    assert "Verify · 1 of 2 agents done" in pane and strip(pane) == [("1", "01-initial · live", "cur")]
    assert [s[1] for s in stage_steps(pane)] == ["Find", "Synthesis", "Verify", "Result"]
    assert f"<code>{round_dir / 'events.jsonl'}</code>" in pane and f"<code>{run / 'revmux' / 'round-1.log'}</code>" in pane

    assert mt("review", "result", str(FIXTURES / "revmux-findings.json"), "--round", "1").code == 0
    result = snap("state.js", "artifacts/review.js")
    assert "Round 1 · 4 gating" in result["pane"] and 'class="live"' not in result["head"]
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in result["pane"] and "<script" not in result["pane"]


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
