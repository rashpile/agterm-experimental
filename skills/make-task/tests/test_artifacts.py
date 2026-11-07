"""`mt artifact`, rendering on mtime change, the markdown renderer, the artifact data scripts, and
`mt artifact changes`."""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path

import pytest
from conftest import MT, git, init_run, write_exec


@pytest.fixture
def renders(monkeypatch) -> list[str]:
    """The markdown text of each render_markdown call, in order (the real renderer still runs)."""
    seen: list[str] = []
    real = MT.render_markdown

    def spy(text: str):
        seen.append(text)
        return real(text)

    monkeypatch.setattr(MT, "render_markdown", spy)
    return seen


def artifact(state: dict, aid: str) -> dict:
    return next(a for a in state["artifacts"] if a["id"] == aid)


def bump_mtime(path: Path, delta: float = 10.0) -> None:
    st = path.stat()
    os.utime(path, (st.st_atime + delta, st.st_mtime + delta))


def artifact_data(run: Path, aid: str) -> dict:
    """The payload in <run>/artifacts/<aid>.js, read without mt's own parser."""
    text = (run / "artifacts" / f"{aid}.js").read_text(encoding="utf-8")
    head, tail = f'window.mtArtifact("{aid}", ', ");\n"
    assert text.startswith(head) and text.endswith(tail), text[:80]
    return json.loads(text[len(head) : -len(tail)])


# ---------------------------------------------------------------- render_markdown and render_inline


def test_render_markdown_table():
    html, toc = MT.render_markdown("| a | b |\n|---|---|\n| 1 | `x` |\n")
    assert "<table>" in html and "<th>a</th>" in html and "<td><code>x</code></td>" in html
    assert toc == []


def test_render_markdown_strikethrough_and_links():
    html, _ = MT.render_markdown("~~gone~~ [ok](https://x.test) [bad](javascript:alert(1))\n")
    assert "<s>gone</s>" in html
    assert '<a href="https://x.test">ok</a>' in html
    assert 'href="javascript' not in html, "markdown-it refuses javascript: links"


def test_render_markdown_task_lists():
    html, _ = MT.render_markdown("- [ ] open\n- [x] done\n  - [ ] nested\n")
    assert '<ul class="contains-task-list">' in html
    assert html.count('class="task-list-item"') == 3
    boxes = [line for line in html.splitlines() if "task-list-item-checkbox" in line]
    assert len(boxes) == 3
    assert "checked" not in boxes[0] and 'checked="checked"' in boxes[1] and "checked" not in boxes[2]
    assert all('disabled="disabled"' in b for b in boxes), "the page shows the boxes, it does not tick them"


def test_render_markdown_footnotes():
    html, _ = MT.render_markdown("Text with a note[^n].\n\n[^n]: The note.\n")
    assert '<sup class="footnote-ref"><a href="#fn1" id="fnref1">[1]</a></sup>' in html
    assert '<section class="footnotes">' in html and "The note." in html
    assert html.index("Text with a note") < html.index('<section class="footnotes">')


def test_render_markdown_heading_ids_and_toc():
    html, toc = MT.render_markdown("# Title\n\n## Goal\n\n### The `mt` call\n\n## Goal\n\n#### Deep\n")
    assert "<h1>Title</h1>" in html, "# carries no id and stays out of the toc"
    assert '<h2 id="doc-goal">Goal</h2>' in html
    assert '<h3 id="doc-the-mt-call">The <code>mt</code> call</h3>' in html
    assert '<h2 id="doc-goal-1">Goal</h2>' in html, "a repeated heading gets its own id"
    assert "<h4>Deep</h4>" in html
    assert toc == [
        {"level": 2, "id": "doc-goal", "text": "Goal"},
        {"level": 3, "id": "doc-the-mt-call", "text": "The mt call"},
        {"level": 2, "id": "doc-goal-1", "text": "Goal"},
    ]


def test_doc_slug():
    assert MT.doc_slug("  Approach and why ") == "doc-approach-and-why"
    assert MT.doc_slug("What `mt` does: (1) <now>") == "doc-what-mt-does-1-now"
    assert MT.doc_slug("Été") == "doc-été"


def test_render_markdown_raw_html_is_text():
    html, _ = MT.render_markdown('<script>alert(1)</script>\n\nInline <b onclick="x">b</b>\n')
    assert "<script>" not in html and "<b " not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;b onclick=&quot;x&quot;&gt;b&lt;/b&gt;" in html


@pytest.mark.parametrize("text", ["", "\n\n", "   \n"])
def test_render_markdown_empty(text):
    assert MT.render_markdown(text) == ("", [])


def test_render_markdown_code_block_escapes():
    html, _ = MT.render_markdown("```text\n<b>x</b> & y\n```\n")
    assert '<pre><code class="language-text">&lt;b&gt;x&lt;/b&gt; &amp; y\n</code></pre>' in html


def test_render_inline():
    assert MT.render_inline("Add `render_markdown` and **bold**") == (
        "Add <code>render_markdown</code> and <strong>bold</strong>"
    )
    assert MT.render_inline("a <i>raw</i> & b") == "a &lt;i&gt;raw&lt;/i&gt; &amp; b"
    assert "<p>" not in MT.render_inline("one line")
    assert MT.render_inline("") == ""


# ---------------------------------------------------------------- the page


def test_doc_page_fills_title_toc_and_body():
    toc = [{"level": 2, "id": "doc-a", "text": "A <b>"}, {"level": 3, "id": "doc-b", "text": "B"}]
    page = MT.doc_page("Design · <x>.md", "<p>body <!-- mt:toc --></p>", toc)
    assert "<title>Design · &lt;x&gt;.md</title>" in page
    assert '<article class="doc"><p>body <!-- mt:toc --></p></article>' in page, "inserted text is not read again"
    assert '<nav class="toc"><a class="l2" href="#doc-a">A &lt;b&gt;</a><a class="l3" href="#doc-b">B</a></nav>' in page
    assert "<!-- mt:title -->" not in page and "<!-- mt:body -->" not in page


def test_doc_page_without_headings_has_no_toc():
    page = MT.doc_page("t", "<p>x</p>", [])
    assert 'class="toc"' not in page and "<!-- mt:toc -->" not in page


# ---------------------------------------------------------------- rendering


def test_artifact_renders_markdown(mt, git_repo, run_state, renders):
    run = init_run(mt, git_repo)
    design = run / "design.md"
    design.write_text("# Design\n\n## Goal\n\nbody\n")
    r = mt("artifact", "design", str(design), "--hint", "design.md", cwd=git_repo)
    assert r.code == 0, r.err
    art = artifact(run_state(run), "design")
    assert art["html"] == str(run / "artifacts" / "design.html")
    page = Path(art["html"]).read_text()
    assert "<title>Design · design.md</title>" in page
    assert '<h2 id="doc-goal">Goal</h2>' in page and "<p>body</p>" in page
    assert '<a class="l2" href="#doc-goal">Goal</a>' in page
    assert "var(--agterm-background" in page
    assert art["hint"] == "design.md" and art["source"] == str(design)
    assert art["mtime"] == design.stat().st_mtime and art["updated"] > 0
    assert art["command"] is None and art["cwd"] is None
    assert renders == [design.read_text()], "the page and the data script share one render"
    # page and data script went through temp files renamed into place: nothing else is left behind
    assert sorted(p.name for p in (run / "artifacts").iterdir()) == ["design.html", "design.js"]


def test_render_once_per_mtime(mt, git_repo, run_state, renders):
    run = init_run(mt, git_repo)
    design = run / "design.md"
    design.write_text("v1\n")
    assert mt("artifact", "design", str(design), cwd=git_repo).code == 0
    first = artifact(run_state(run), "design")

    assert mt("artifact", "design", str(design)).code == 0
    assert mt("refresh", cwd=git_repo).code == 0
    assert len(renders) == 1
    assert artifact(run_state(run), "design")["updated"] == first["updated"]

    design.write_text("v2\n")
    bump_mtime(design)
    assert mt("refresh", cwd=git_repo).code == 0
    assert len(renders) == 2
    art = artifact(run_state(run), "design")
    assert art["mtime"] == design.stat().st_mtime and art["updated"] >= first["updated"]
    assert "v2" in Path(art["html"]).read_text()

    assert mt("refresh", cwd=git_repo).code == 0
    assert len(renders) == 2


def test_deleted_html_is_rendered_again(mt, git_repo, run_state, renders):
    run = init_run(mt, git_repo)
    design = run / "design.md"
    design.write_text("v1\n")
    mt("artifact", "design", str(design), cwd=git_repo)
    Path(artifact(run_state(run), "design")["html"]).unlink()
    assert mt("refresh", cwd=git_repo).code == 0
    assert len(renders) == 2
    assert Path(artifact(run_state(run), "design")["html"]).is_file()


def test_text_source_wrapped_in_fence(mt, git_repo, run_state, renders, tmp_path):
    run = init_run(mt, git_repo)
    progress = tmp_path / "progress-x.txt"
    progress.write_text("# progress\n[decision] keep <it>\n```\nnot a fence end\n```\n")
    assert mt("artifact", "progress", str(progress), cwd=git_repo).code == 0
    assert renders == [
        "# progress-x.txt\n\n````text\n# progress\n[decision] keep <it>\n```\nnot a fence end\n```\n````\n"
    ]
    page = Path(artifact(run_state(run), "progress")["html"]).read_text()
    assert "<title>Progress log · progress-x.txt</title>" in page
    assert "<h1>progress-x.txt</h1>" in page
    assert (
        '<pre><code class="language-text"># progress\n[decision] keep &lt;it&gt;\n```\nnot a fence end\n```\n' in page
    )
    assert sorted(p.name for p in (run / "artifacts").iterdir()) == ["progress.html"]


def test_text_as_markdown_plain():
    assert MT.text_as_markdown("log", "a\nb\n\n") == "# log\n\n```text\na\nb\n```\n"


def test_relative_path_and_default_hint(mt, git_repo, run_state, renders):
    run = init_run(mt, git_repo)
    sub = git_repo / "src"
    assert mt("artifact", "stats", "../README.md", cwd=sub).code == 0
    art = artifact(run_state(run), "stats")
    assert art["source"] == str(git_repo / "README.md")
    assert art["hint"] == MT.tilde(str(git_repo / "README.md"))


def test_new_source_path_renders_again(mt, git_repo, run_state, renders, tmp_path):
    run = init_run(mt, git_repo)
    a, b = tmp_path / "a.md", tmp_path / "b.md"
    a.write_text("a\n")
    b.write_text("b\n")
    os.utime(b, (a.stat().st_atime, a.stat().st_mtime))  # same mtime, different file
    mt("artifact", "stats", str(a), cwd=git_repo)
    mt("artifact", "stats", str(b))
    assert len(renders) == 2
    state = run_state(run)
    assert [x["id"] for x in state["artifacts"]].count("stats") == 1
    assert "<p>b</p>" in Path(artifact(state, "stats")["html"]).read_text()


def test_plan_and_refresh_render_plan_and_progress(mt, git_repo, run_state, renders, tmp_path):
    run = init_run(mt, git_repo)
    plan = git_repo / "docs" / "plans" / "p.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("### Task 1: A\n- [ ] one\n")
    progress = tmp_path / "progress.txt"  # not written yet
    assert mt("plan", str(plan), "--progress", str(progress), cwd=git_repo).code == 0
    state = run_state(run)
    assert artifact(state, "plan")["html"] == str(run / "artifacts" / "plan.html")
    assert 'class="task-list-item-checkbox"' in Path(artifact(state, "plan")["html"]).read_text()
    assert artifact(state, "progress")["html"] is None  # no source yet, no failure
    progress.write_text("started\n")
    r = mt("refresh", cwd=git_repo)
    assert r.code == 0 and r.err == ""
    assert artifact(run_state(run), "progress")["html"] == str(run / "artifacts" / "progress.html")
    assert len(renders) == 2


def test_render_artifact_missing_source_warns(tmp_path):
    art = {"id": "design", "source": str(tmp_path / "gone.md"), "html": None, "mtime": 0, "updated": 0}
    warning = MT.render_artifact(tmp_path, {}, art)
    assert warning == f"artifact design: cannot stat {tmp_path / 'gone.md'}: No such file or directory"
    assert art["html"] is None and art["mtime"] == 0
    assert not (tmp_path / "artifacts").exists()


# ---------------------------------------------------------------- data scripts


def test_design_payload(mt, git_repo, run_state):
    run = init_run(mt, git_repo)
    design = run / "design.md"
    design.write_text("# Session store: `mt` & co\n\n## Goal\n\nbody\n")
    assert mt("artifact", "design", str(design), cwd=git_repo).code == 0
    data = artifact_data(run, "design")
    assert sorted(data) == ["html", "source", "title", "toc", "updated"]
    html, toc = MT.render_markdown(design.read_text())
    assert data["title"] == "Session store: mt & co", "the first # heading as plain text"
    assert data["html"] == html and data["toc"] == toc == [{"level": 2, "id": "doc-goal", "text": "Goal"}]
    assert data["source"] == str(design)
    assert isinstance(data["updated"], float) and data["updated"] > 0


def test_stats_payload_title_falls_back_to_the_label(mt, git_repo, run_state, tmp_path):
    run = init_run(mt, git_repo)
    stats = tmp_path / "stats.md"
    stats.write_text("## Totals\n\n- tasks: 3\n")
    assert mt("artifact", "stats", str(stats), cwd=git_repo).code == 0
    data = artifact_data(run, "stats")
    assert data["title"] == "Stats" and "<h2" in data["html"]
    assert sorted(p.name for p in (run / "artifacts").iterdir()) == ["stats.html", "stats.js"]


@pytest.mark.parametrize(
    ("html", "title"),
    [
        ("<h1>Plain</h1>", "Plain"),
        (
            '<p>x</p>\n<h2 id="doc-a">A</h2>\n<h1>Later <code>mt</code> &amp; <a href="x">link</a></h1>',
            "Later mt & link",
        ),
        ("<h1>a &lt;b&gt;</h1><h1>second</h1>", "a <b>"),
        ("<h1></h1>", None),
        ('<h2 id="doc-x">X</h2>', None),
        ("", None),
    ],
)
def test_first_heading(html, title):
    assert MT.first_heading(html) == title


def test_document_payload_of_a_text_file(tmp_path):
    log = tmp_path / "notes.txt"
    log.write_text("# not a heading here\n")
    doc = MT.document_payload({"id": "stats", "source": str(log)})
    assert doc["title"] == "notes.txt", "a text file is titled by text_as_markdown's heading"
    assert "<pre><code" in doc["html"] and doc["toc"] == [] and doc["source"] == str(log)


def test_document_payload_setext_heading(tmp_path):
    design = tmp_path / "d.md"
    design.write_text("Big title\n=========\n\ntext\n")
    assert MT.document_payload({"id": "design", "source": str(design)})["title"] == "Big title"


def test_artifact_payload_only_for_payload_ids(tmp_path):
    source = tmp_path / "x.md"
    source.write_text("# X\n")
    for aid in MT.ARTIFACT_IDS:
        payload = MT.artifact_payload(tmp_path, {}, {"id": aid, "source": str(source)})
        assert (payload is not None) == (aid in MT.PAYLOAD_IDS), aid
    assert set(MT.PAYLOAD_IDS) >= {"design", "stats"}
    assert "changes" not in MT.PAYLOAD_IDS


def test_artifact_payload_reuses_a_rendered_document(tmp_path, renders):
    doc = {"title": "T", "html": "<p>x</p>", "toc": [], "source": "/s.md"}
    payload = MT.artifact_payload(tmp_path, {}, {"id": "design", "source": "/s.md"}, doc)
    assert payload == doc and payload is not doc
    assert renders == []


def test_data_script_is_ascii_and_escapes_markup(mt, git_repo, run_state):
    run = init_run(mt, git_repo)
    design = run / "design.md"
    design.write_text("# Été </script> & “quotes” 🚀\n\n<!-- x --> ü\n")
    assert mt("artifact", "design", str(design), cwd=git_repo).code == 0
    raw = (run / "artifacts" / "design.js").read_bytes()
    assert raw.isascii(), "non-ASCII is \\u-escaped: the script reads the same in any charset"
    text = raw.decode()
    body = text.removeprefix('window.mtArtifact("design", ').removesuffix(");\n")
    assert "<" not in body and ">" not in body and "&" not in body
    assert "\\u003c/script\\u003e" in body and "\\u0026" in body and "\\u00e9" in body and "\\ud83d\\ude80" in body
    data = artifact_data(run, "design")
    assert data["title"] == "Été </script> & “quotes” 🚀", "the title is plain text, unescaped"
    assert "&lt;!-- x --&gt; ü" in data["html"]


def test_unchanged_content_keeps_the_data_script(mt, git_repo, run_state, renders):
    run = init_run(mt, git_repo)
    design = run / "design.md"
    design.write_text("# D\n\nv1\n")
    assert mt("artifact", "design", str(design), cwd=git_repo).code == 0
    js = run / "artifacts" / "design.js"
    first, first_ns = js.read_text(), js.stat().st_mtime_ns
    updated = artifact_data(run, "design")["updated"]

    # a new mtime, the same content: the source renders again, the data script stays as it was
    bump_mtime(design)
    assert mt("refresh", cwd=git_repo).code == 0
    assert len(renders) == 2
    assert js.read_text() == first and js.stat().st_mtime_ns == first_ns
    assert artifact_data(run, "design")["updated"] == updated

    # changed content: written again with a new `updated`
    design.write_text("# D\n\nv2\n")
    bump_mtime(design, 20)
    assert mt("refresh", cwd=git_repo).code == 0
    data = artifact_data(run, "design")
    assert data["updated"] > updated and "<p>v2</p>" in data["html"]


def test_deleted_data_script_is_written_again(mt, git_repo, run_state, renders, tmp_path):
    run = init_run(mt, git_repo)
    design = run / "design.md"
    design.write_text("# D\n")
    progress = tmp_path / "progress.txt"
    progress.write_text("task 1: a\n")
    assert mt("artifact", "design", str(design), cwd=git_repo).code == 0
    assert mt("artifact", "progress", str(progress), cwd=git_repo).code == 0
    assert len(renders) == 2
    (run / "artifacts" / "design.js").unlink()

    assert mt("refresh", cwd=git_repo).code == 0
    assert len(renders) == 3, "design renders again; progress has no data script to miss"
    assert artifact_data(run, "design")["title"] == "D"
    assert not (run / "artifacts" / "progress.js").exists()


def test_write_artifact_data(tmp_path, monkeypatch):
    clock = iter([100.0, 200.0, 300.0])
    monkeypatch.setattr(MT, "now", lambda: next(clock))
    js = tmp_path / "artifacts" / "design.js"

    assert MT.write_artifact_data(tmp_path, "design", {"title": "A", "toc": []}) is True
    assert js.read_text() == 'window.mtArtifact("design", {"title": "A", "toc": [], "updated": 100.0});\n'

    # the same rest, whatever `updated` the caller passes: nothing written
    assert MT.write_artifact_data(tmp_path, "design", {"title": "A", "toc": [], "updated": 5}) is False
    assert MT.write_artifact_data(tmp_path, "design", {"toc": (), "title": "A"}) is False, "key order, tuple"
    assert MT.read_artifact_data(js) == {"title": "A", "toc": [], "updated": 100.0}

    assert MT.write_artifact_data(tmp_path, "design", {"title": "B", "toc": []}) is True
    assert MT.read_artifact_data(js) == {"title": "B", "toc": [], "updated": 200.0}

    # a file mt cannot read back is replaced
    js.write_text("garbage")
    assert MT.write_artifact_data(tmp_path, "design", {"title": "B", "toc": []}) is True
    assert MT.read_artifact_data(js) == {"title": "B", "toc": [], "updated": 300.0}
    assert sorted(p.name for p in js.parent.iterdir()) == ["design.js"]


@pytest.mark.parametrize(
    "text",
    [
        "",
        "garbage",
        'window.mtArtifact("design", [1]);\n',
        'window.mtArtifact("design", {"a": );\n',
        'window.mtArtifact("design", {"a": 1});',
    ],
)
def test_read_artifact_data_refuses(tmp_path, text):
    js = tmp_path / "x.js"
    js.write_text(text)
    assert MT.read_artifact_data(js) is None


def test_read_artifact_data_missing(tmp_path):
    assert MT.read_artifact_data(tmp_path / "none.js") is None


def test_data_script_write_failure_keeps_the_entry(mt, git_repo, run_state, monkeypatch):
    run = init_run(mt, git_repo)
    design = run / "design.md"
    design.write_text("# D\n")

    real, full = MT.write_artifact_data, [True]

    def write(*a, **kw):
        if full[0]:
            raise OSError("disk full")
        return real(*a, **kw)

    monkeypatch.setattr(MT, "write_artifact_data", write)
    r = mt("artifact", "design", str(design), cwd=git_repo)
    assert r.code == 0 and f"mt: warning: artifact design: cannot render {design}: disk full" in r.err
    art = artifact(run_state(run), "design")
    assert art["html"] is None and art["mtime"] == 0, "the next call retries"

    full[0] = False
    assert mt("refresh", cwd=git_repo).code == 0
    assert artifact_data(run, "design")["title"] == "D"


# ---------------------------------------------------------------- changes


def feature_branch(repo: Path) -> tuple[str, str]:
    """Branch `feat` off main with one commit; returns (merge-base, head)."""
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-b", "feat")
    (repo / "src" / "b.txt").write_text("b\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "feat")
    return base, git(repo, "rev-parse", "HEAD")


@pytest.fixture
def revdiff(isolate, monkeypatch) -> Path:
    """A fake revdiff in a directory with a space, first on PATH."""
    path = write_exec(isolate.tmp / "my bin" / "revdiff", "#!/bin/sh\nexit 0\n")
    monkeypatch.setenv("PATH", f"{path.parent}{os.pathsep}{os.environ['PATH']}")
    return path


def test_changes_uses_merge_base(mt, git_repo, run_state, revdiff):
    base, _ = feature_branch(git_repo)
    run = init_run(mt, git_repo)
    r = mt("artifact", "changes", cwd=git_repo / "src")
    assert r.code == 0, r.err
    art = artifact(run_state(run), "changes")
    assert art["cwd"] == str(git_repo)
    assert shlex.split(art["command"]) == [str(revdiff), base]
    assert art["command"] == f"'{revdiff}' {base}"
    assert art["html"] is None and art["source"] is None and art["updated"] > 0
    assert art["hint"] == f"revdiff {base[:12]}"


def test_changes_prefers_review_base(mt, git_repo, run_state, revdiff):
    _, head = feature_branch(git_repo)
    run = init_run(mt, git_repo)
    assert mt("set", f"review_base={head}").code == 0
    assert mt("artifact", "changes", "--hint", "Changes", cwd=git_repo).code == 0
    art = artifact(run_state(run), "changes")
    assert shlex.split(art["command"]) == [str(revdiff), head] and art["hint"] == "Changes"

    # registering again updates the one entry
    assert mt("set", "review_base=HEAD~1").code == 0
    assert mt("artifact", "changes", cwd=git_repo).code == 0
    state = run_state(run)
    assert [a["id"] for a in state["artifacts"]] == ["changes"]
    assert shlex.split(artifact(state, "changes")["command"])[1] == "HEAD~1"


def test_changes_appear_once_review_base_is_set(mt, git_repo, run_state, revdiff):
    base, _ = feature_branch(git_repo)
    run = init_run(mt, git_repo)
    plan = git_repo / "plan.md"
    plan.write_text("### Task 1: a\n- [ ] x\n")
    assert mt("plan", str(plan), cwd=git_repo).code == 0
    assert all(a["id"] != "changes" for a in run_state(run)["artifacts"]), "no review_base yet: Changes stays off"

    assert mt("set", f"review_base={base}").code == 0
    assert mt("refresh", cwd=git_repo).code == 0
    art = artifact(run_state(run), "changes")
    assert shlex.split(art["command"]) == [str(revdiff), base] and art["cwd"] == str(git_repo)

    # later refreshes keep the entry as registered; `mt artifact changes` still updates it
    updated = art["updated"]
    assert mt("refresh", cwd=git_repo).code == 0
    assert artifact(run_state(run), "changes")["updated"] == updated


def test_changes_without_revdiff(mt, git_repo, run_state, monkeypatch):
    which = MT.shutil.which
    monkeypatch.setattr(MT.shutil, "which", lambda name, *a, **kw: None if name == "revdiff" else which(name, *a, **kw))
    feature_branch(git_repo)
    run = init_run(mt, git_repo)
    assert mt("artifact", "changes", cwd=git_repo).code == 0
    art = artifact(run_state(run), "changes")
    assert art["command"] is None and art["cwd"] == str(git_repo)
    assert "not installed" in art["hint"]


def test_changes_in_worktree_runs_there(mt, git_repo, run_state, revdiff):
    base, _ = feature_branch(git_repo)
    git(git_repo, "checkout", "-q", "main")
    wt = git_repo.parent / "wt"
    git(git_repo, "worktree", "add", "-q", str(wt), "feat")
    run = init_run(mt, wt)
    assert mt("artifact", "changes", cwd=wt).code == 0
    art = artifact(run_state(run), "changes")
    assert art["cwd"] == str(wt) and shlex.split(art["command"])[1] == base
    # from the main checkout the worktree check refuses
    r = mt("artifact", "changes", cwd=git_repo)
    assert r.code == 1 and "worktree" in r.err


# ---------------------------------------------------------------- errors


def test_missing_source_file(mt, git_repo, run_state, renders):
    run = init_run(mt, git_repo)
    r = mt("artifact", "design", str(run / "nope.md"), cwd=git_repo)
    assert r.code == 1 and "no such file" in r.err
    assert run_state(run)["artifacts"] == []
    assert renders == []


def test_unreadable_source_keeps_previous_html(mt, git_repo, run_state, renders):
    run = init_run(mt, git_repo)
    design = run / "design.md"
    design.write_text("good\n")
    assert mt("artifact", "design", str(design), cwd=git_repo).code == 0
    before = artifact(run_state(run), "design")
    html_before = Path(before["html"]).read_text()

    # the source turns into something mt cannot read: the page and the entry stay as they were
    design.unlink()
    design.mkdir()
    bump_mtime(design)
    r = mt("refresh", cwd=git_repo)
    assert r.code == 0
    assert f"mt: warning: artifact design: cannot render {design}" in r.err
    after = artifact(run_state(run), "design")
    assert after["html"] == before["html"] and after["mtime"] == before["mtime"]
    assert after["updated"] == before["updated"]
    assert Path(after["html"]).read_text() == html_before
    assert sorted(p.name for p in (run / "artifacts").iterdir()) == ["design.html", "design.js"]

    # readable again: the next refresh retries and succeeds
    design.rmdir()
    design.write_text("bad\n")
    bump_mtime(design, 20)
    assert mt("refresh", cwd=git_repo).code == 0
    assert "<p>bad</p>" in Path(artifact(run_state(run), "design")["html"]).read_text()


def test_unreadable_source_on_first_render(mt, git_repo, run_state, monkeypatch):
    run = init_run(mt, git_repo)
    design = run / "design.md"
    design.write_text("x\n")
    monkeypatch.setattr(MT, "ASSETS", run / "no-assets")  # doc.html missing
    r = mt("artifact", "design", str(design), cwd=git_repo)
    assert r.code == 0 and "mt: warning: artifact design: cannot render" in r.err
    art = artifact(run_state(run), "design")
    assert art["html"] is None and art["mtime"] == 0


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("bogus", "x.md"), "unknown id 'bogus'"),
        (("changes", "x.md"), "takes no PATH"),
        (("design",), "PATH is required"),
    ],
)
def test_bad_arguments(mt, git_repo, run_state, args, message):
    run = init_run(mt, git_repo)
    r = mt("artifact", *args, cwd=git_repo)
    assert r.code == 1 and message in r.err
    assert run_state(run)["artifacts"] == []


def test_artifact_without_run(mt, git_repo):
    r = mt("artifact", "design", "README.md", cwd=git_repo)
    assert r.code == 1 and "no active run" in r.err


def test_changes_without_merge_base(mt, git_repo, run_state, revdiff):
    run = init_run(mt, git_repo)
    git(git_repo, "checkout", "-q", "--orphan", "lonely")
    (git_repo / "x").write_text("x\n")
    git(git_repo, "add", ".")
    git(git_repo, "commit", "-q", "-m", "orphan")
    r = mt("artifact", "changes", cwd=git_repo)
    assert r.code == 1 and "no merge-base" in r.err
    assert run_state(run)["artifacts"] == []


def test_changes_falls_back_to_origin_default(mt, git_repo, run_state, revdiff, tmp_path):
    """No local default branch (only origin/main): the merge-base comes from origin/<default>."""
    bare = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", str(bare))
    git(git_repo, "remote", "add", "origin", str(bare))
    git(git_repo, "push", "-q", "origin", "main")
    git(git_repo, "remote", "set-head", "origin", "main")
    base, _ = feature_branch(git_repo)
    git(git_repo, "branch", "-q", "-D", "main")
    run = init_run(mt, git_repo)
    assert mt("artifact", "changes", cwd=git_repo).code == 0
    assert artifact(run_state(run), "changes")["command"].endswith(shlex.quote(base))
