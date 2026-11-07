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


# ---------------------------------------------------------------- the other tabs until they get their own view


def test_progress_raw_text_is_escaped_and_review_lists_rounds(tmp_path):
    s = base_state(artifacts=[art("progress"), art("review")])
    progress = {"source": "/p.txt", "header": {}, "entries": [], "raw": "task 1: <script>x</script>\n", "updated": 1.0}
    review = {"rounds": [{"round": 1, "state": "done"}, {"round": 2, "state": "<b>running</b>"}], "updated": 1.0}
    steps = [
        state_step(s),
        artifact_step("progress", progress),
        SNAP,
        {"click": "[data-show]", "data": {"show": "review"}},
        artifact_step("review", review),
        SNAP,
    ]
    prog, rev = run_viewer(tmp_path, steps, tab="progress")["snaps"]
    assert prog["pane"] == '<pre class="raw">task 1: &lt;script&gt;x&lt;/script&gt;\n</pre>'
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
