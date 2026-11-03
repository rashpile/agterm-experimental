"""`mt artifact`, rendering on mtime change, and `mt artifact changes`."""

from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path

import pytest
from conftest import MT, git, init_run, write_exec

LOGGING_VIEW = """#!{python}
import json, os, pathlib, sys
src, out = sys.argv[1], sys.argv[sys.argv.index("-o") + 1]
text = pathlib.Path(src).read_text()
with open({log!r}, "a") as f:
    f.write(json.dumps({{"argv": sys.argv[1:], "src": text}}) + "\\n")
if os.environ.get("VIEW_FAIL"):
    sys.stderr.write("boom\\n")
    sys.exit(3)
pathlib.Path(out).write_text("<html><body><pre>" + text + "</pre></body></html>")
"""


@pytest.fixture
def view_log(isolate, monkeypatch) -> Path:
    """A renderer that logs each call; VIEW_FAIL=1 makes it exit 3 without output."""
    log = isolate.tmp / "view.log"
    stub = write_exec(isolate.tmp / "bin2" / "view-logging", LOGGING_VIEW.format(python=sys.executable, log=str(log)))
    monkeypatch.setenv("MT_VIEW", str(stub))
    return log


def calls(log: Path) -> list[dict]:
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def artifact(state: dict, aid: str) -> dict:
    return next(a for a in state["artifacts"] if a["id"] == aid)


def bump_mtime(path: Path, delta: float = 10.0) -> None:
    st = path.stat()
    os.utime(path, (st.st_atime + delta, st.st_mtime + delta))


# ---------------------------------------------------------------- rendering


def test_artifact_renders_markdown(mt, git_repo, run_state, view_log):
    run = init_run(mt, git_repo)
    design = run / "design.md"
    design.write_text("# Design\n\nbody\n")
    r = mt("artifact", "design", str(design), "--hint", "design.md", cwd=git_repo)
    assert r.code == 0, r.err
    art = artifact(run_state(run), "design")
    assert art["html"] == str(run / "artifacts" / "design.html")
    assert "body" in Path(art["html"]).read_text()
    assert art["hint"] == "design.md" and art["source"] == str(design)
    assert art["mtime"] == design.stat().st_mtime and art["updated"] > 0
    assert art["command"] is None and art["cwd"] is None
    (call,) = calls(view_log)
    assert call["argv"][0] == str(design) and call["argv"][1] == "-o"
    # the renderer wrote to a temp file that was then moved into place: nothing else is left behind
    assert sorted(p.name for p in (run / "artifacts").iterdir()) == ["design.html"]


def test_render_once_per_mtime(mt, git_repo, run_state, view_log):
    run = init_run(mt, git_repo)
    design = run / "design.md"
    design.write_text("v1\n")
    assert mt("artifact", "design", str(design), cwd=git_repo).code == 0
    first = artifact(run_state(run), "design")

    assert mt("artifact", "design", str(design)).code == 0
    assert mt("refresh", cwd=git_repo).code == 0
    assert len(calls(view_log)) == 1
    assert artifact(run_state(run), "design")["updated"] == first["updated"]

    design.write_text("v2\n")
    bump_mtime(design)
    assert mt("refresh", cwd=git_repo).code == 0
    assert len(calls(view_log)) == 2
    art = artifact(run_state(run), "design")
    assert art["mtime"] == design.stat().st_mtime and art["updated"] >= first["updated"]
    assert "v2" in Path(art["html"]).read_text()

    assert mt("refresh", cwd=git_repo).code == 0
    assert len(calls(view_log)) == 2


def test_deleted_html_is_rendered_again(mt, git_repo, run_state, view_log):
    run = init_run(mt, git_repo)
    design = run / "design.md"
    design.write_text("v1\n")
    mt("artifact", "design", str(design), cwd=git_repo)
    Path(artifact(run_state(run), "design")["html"]).unlink()
    assert mt("refresh", cwd=git_repo).code == 0
    assert len(calls(view_log)) == 2
    assert Path(artifact(run_state(run), "design")["html"]).is_file()


def test_text_source_wrapped_in_fence(mt, git_repo, run_state, view_log, tmp_path):
    run = init_run(mt, git_repo)
    progress = tmp_path / "progress-x.txt"
    progress.write_text("# progress\n[decision] keep it\n```\nnot a fence end\n```\n")
    assert mt("artifact", "progress", str(progress), cwd=git_repo).code == 0
    (call,) = calls(view_log)
    assert call["argv"][0].endswith(".md") and call["argv"][0] != str(progress)
    assert call["src"] == (
        "# progress-x.txt\n\n````text\n# progress\n[decision] keep it\n```\nnot a fence end\n```\n````\n"
    )
    assert not Path(call["argv"][0]).exists()  # temp markdown removed
    html = Path(artifact(run_state(run), "progress")["html"]).read_text()
    assert "[decision] keep it" in html
    assert sorted(p.name for p in (run / "artifacts").iterdir()) == ["progress.html"]


def test_text_as_markdown_plain():
    assert MT.text_as_markdown("log", "a\nb\n\n") == "# log\n\n```text\na\nb\n```\n"


def test_relative_path_and_default_hint(mt, git_repo, run_state, view_log):
    run = init_run(mt, git_repo)
    sub = git_repo / "src"
    assert mt("artifact", "stats", "../README.md", cwd=sub).code == 0
    art = artifact(run_state(run), "stats")
    assert art["source"] == str(git_repo / "README.md")
    assert art["hint"] == MT.tilde(str(git_repo / "README.md"))


def test_new_source_path_renders_again(mt, git_repo, run_state, view_log, tmp_path):
    run = init_run(mt, git_repo)
    a, b = tmp_path / "a.md", tmp_path / "b.md"
    a.write_text("a\n")
    b.write_text("b\n")
    os.utime(b, (a.stat().st_atime, a.stat().st_mtime))  # same mtime, different file
    mt("artifact", "stats", str(a), cwd=git_repo)
    mt("artifact", "stats", str(b))
    assert len(calls(view_log)) == 2
    state = run_state(run)
    assert [x["id"] for x in state["artifacts"]].count("stats") == 1
    assert "b" in Path(artifact(state, "stats")["html"]).read_text()


def test_plan_and_refresh_render_plan_and_progress(mt, git_repo, run_state, view_log, tmp_path):
    run = init_run(mt, git_repo)
    plan = git_repo / "docs" / "plans" / "p.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("### Task 1: A\n- [ ] one\n")
    progress = tmp_path / "progress.txt"  # not written yet
    assert mt("plan", str(plan), "--progress", str(progress), cwd=git_repo).code == 0
    state = run_state(run)
    assert artifact(state, "plan")["html"] == str(run / "artifacts" / "plan.html")
    assert artifact(state, "progress")["html"] is None  # no source yet, no failure
    progress.write_text("started\n")
    r = mt("refresh", cwd=git_repo)
    assert r.code == 0 and r.err == ""
    assert artifact(run_state(run), "progress")["html"] == str(run / "artifacts" / "progress.html")
    assert len(calls(view_log)) == 2


def test_no_renderer(mt, git_repo, run_state, monkeypatch, isolate):
    run = init_run(mt, git_repo)
    monkeypatch.setenv("MT_VIEW", str(isolate.tmp / "missing-view"))
    design = run / "design.md"
    design.write_text("x\n")
    r = mt("artifact", "design", str(design), cwd=git_repo)
    assert r.code == 0, r.err
    art = artifact(run_state(run), "design")
    assert art["html"] is None and art["source"] == str(design)
    assert art["mtime"] == design.stat().st_mtime and art["updated"] > 0
    assert not (run / "artifacts").exists()

    # a renderer that shows up later renders it on the next refresh
    monkeypatch.setenv("MT_VIEW", str(isolate.view))
    assert mt("refresh", cwd=git_repo).code == 0
    assert artifact(run_state(run), "design")["html"] == str(run / "artifacts" / "design.html")


def test_renderer_argv():
    assert MT.renderer_argv("/x/view.py", "s.md", "o.html") == [
        "uv",
        "run",
        "-q",
        "--script",
        "/x/view.py",
        "s.md",
        "-o",
        "o.html",
    ]


def test_renderer_argv_executable_mt_view(isolate):
    assert MT.renderer_argv(str(isolate.view), "s.md", "o.html") == [str(isolate.view), "s.md", "-o", "o.html"]


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


def test_missing_source_file(mt, git_repo, run_state, view_log):
    run = init_run(mt, git_repo)
    r = mt("artifact", "design", str(run / "nope.md"), cwd=git_repo)
    assert r.code == 1 and "no such file" in r.err
    assert run_state(run)["artifacts"] == []
    assert calls(view_log) == []


def test_renderer_failure_keeps_previous_html(mt, git_repo, run_state, view_log, monkeypatch):
    run = init_run(mt, git_repo)
    design = run / "design.md"
    design.write_text("good\n")
    assert mt("artifact", "design", str(design), cwd=git_repo).code == 0
    before = artifact(run_state(run), "design")
    html_before = Path(before["html"]).read_text()

    monkeypatch.setenv("VIEW_FAIL", "1")
    design.write_text("bad\n")
    bump_mtime(design)
    r = mt("refresh", cwd=git_repo)
    assert r.code == 0
    assert "renderer failed (exit 3): boom" in r.err
    after = artifact(run_state(run), "design")
    assert after["html"] == before["html"] and after["mtime"] == before["mtime"]
    assert after["updated"] == before["updated"]
    assert Path(after["html"]).read_text() == html_before
    assert sorted(p.name for p in (run / "artifacts").iterdir()) == ["design.html"]

    # fixed renderer: the next refresh retries and succeeds
    monkeypatch.delenv("VIEW_FAIL")
    assert mt("refresh", cwd=git_repo).code == 0
    assert "bad" in Path(artifact(run_state(run), "design")["html"]).read_text()


def test_renderer_failure_on_first_render(mt, git_repo, run_state, view_log, monkeypatch):
    run = init_run(mt, git_repo)
    monkeypatch.setenv("VIEW_FAIL", "1")
    design = run / "design.md"
    design.write_text("x\n")
    r = mt("artifact", "design", str(design), cwd=git_repo)
    assert r.code == 0 and "mt: warning: artifact design: renderer failed" in r.err
    art = artifact(run_state(run), "design")
    assert art["html"] is None and art["mtime"] == 0


def test_renderer_writes_nothing(mt, git_repo, run_state, monkeypatch, isolate):
    run = init_run(mt, git_repo)
    monkeypatch.setenv("MT_VIEW", str(write_exec(isolate.tmp / "bin2" / "silent", "#!/bin/sh\nexit 0\n")))
    design = run / "design.md"
    design.write_text("x\n")
    r = mt("artifact", "design", str(design), cwd=git_repo)
    assert r.code == 0 and "renderer failed (exit 0)" in r.err
    assert artifact(run_state(run), "design")["html"] is None


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


def test_renderer_timeout_keeps_no_html_and_warns(mt, git_repo, run_state, isolate, monkeypatch):
    slow = write_exec(isolate.tmp / "bin3" / "view-slow", f"#!{sys.executable}\nimport time; time.sleep(30)\n")
    monkeypatch.setenv("MT_VIEW", str(slow))
    monkeypatch.setattr(MT, "RENDER_TIMEOUT", 0.3)
    run = init_run(mt, git_repo)
    design = run / "design.md"
    design.write_text("d\n")
    r = mt("artifact", "design", str(design), cwd=git_repo)
    assert r.code == 0 and "renderer timed out after 0.3s" in r.err
    art = artifact(run_state(run), "design")
    assert art["html"] is None and art["mtime"] == 0
    assert not any(p.name.endswith(".html") for p in (run / "artifacts").iterdir())


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
