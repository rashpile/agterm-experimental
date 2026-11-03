"""Run dir, pointer, state io, init detection and the simple state commands."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from conftest import MT, git, init, init_args, profile_config, write_run_profile


def parse_state_js(text: str) -> dict:
    m = re.fullmatch(r"window\.mtState\((.*)\);\n", text, re.DOTALL)
    assert m, text[:80]
    return json.loads(m.group(1))


# ---------------------------------------------------------------- init


def usage_command(line: str) -> str:
    """The command word of a docstring usage line: `mt [--run DIR] CMD ...` or (no run) `mt CMD ...`."""
    words = line.split()[1:]
    if words[:2] == ["[--run", "DIR]"]:
        words = words[2:]
    return words[0]


def test_help_lists_commands_in_docstring_usage_order():
    usage = MT.__doc__.split("Usage:")[1].split("\n\n")[0]
    lines = [line for line in usage.splitlines() if line.strip().startswith("mt ")]
    documented = list(dict.fromkeys(usage_command(line) for line in lines))
    sub = next(a for a in MT.build_parser()._actions if isinstance(a, argparse._SubParsersAction))
    assert list(sub.choices) == documented


def test_init_creates_run_dir_pointer_and_json_line(mt, git_repo, isolate, run_state):
    info = init(mt, git_repo, "Persist API sessions")
    run = Path(info["state_dir"])
    assert run.parent == isolate.state_home
    assert re.fullmatch(r"repo-persist-api-sessions-\d{8}-\d{4}", run.name)
    assert (isolate.state_home / "current-default").read_text().strip() == str(run)

    assert info["title"] == "Persist API sessions"
    assert info["main_checkout"] == str(git_repo)
    assert info["toplevel"] == str(git_repo)
    assert info["branch"] == "main"
    assert info["default_branch"] == "main"
    assert info["has_origin"] is False
    assert info["linked_worktree"] is False and info["worktree"] is False
    assert info["worktree_path"] is None
    assert info["dirty_paths"] == []
    assert info["start_cwd"] == str(git_repo)
    assert info["agterm"] is False
    assert info["renderer"] == str(isolate.view)
    assert info["revdiff"] is (MT.shutil.which("revdiff") is not None)

    state = run_state(run)
    assert state["state_dir"] == str(run)
    assert [p["id"] for p in state["phases"]] == ["brainstorm", "plan", "exec", "review", "finalize"]
    assert all(p["status"] == "pending" for p in state["phases"])
    assert state["started"] and state["finished"] is None
    assert state["events"][0]["text"] == "make-task started"


def test_init_pointer_per_agterm_session(mt, git_repo, isolate, monkeypatch):
    monkeypatch.setenv("AGTERM_SESSION_ID", "s-42")
    info = init(mt, git_repo)
    assert (isolate.state_home / "current-s-42").read_text().strip() == info["state_dir"]
    assert not (isolate.state_home / "current-default").exists()


def test_init_same_minute_gets_a_new_dir(mt, git_repo):
    a = init(mt, git_repo, "Same title")
    b = init(mt, git_repo, "Same title")
    assert a["state_dir"] != b["state_dir"]
    assert Path(b["state_dir"]).is_dir()


def test_init_slug_fallback_and_length(mt, git_repo):
    assert re.search(r"/repo-task-\d{8}-\d{4}$", init(mt, git_repo, "!!!")["state_dir"])
    long = init(mt, git_repo, "A " * 60)["state_dir"]
    slug = re.fullmatch(r"repo-(.*)-\d{8}-\d{4}", Path(long).name).group(1)
    assert len(slug) <= 40 and not slug.endswith("-")


def test_init_from_subdirectory_of_linked_worktree(mt, git_repo, tmp_path):
    wt = (tmp_path / "wt").resolve()
    git(git_repo, "worktree", "add", "-q", "-b", "feature", str(wt))
    sub = wt / "src"
    info = init(mt, sub)
    assert info["main_checkout"] == str(git_repo)
    assert info["toplevel"] == str(wt)
    assert info["linked_worktree"] is True and info["worktree"] is True
    assert info["worktree_path"] == str(wt)
    assert info["branch"] == "feature"
    assert info["default_branch"] == "main"
    assert info["start_cwd"] == str(sub)
    assert Path(info["state_dir"]).name.startswith("repo-")


def test_init_from_subdirectory_of_main_checkout(mt, git_repo):
    info = init(mt, git_repo / "src")
    assert info["toplevel"] == str(git_repo)
    assert info["linked_worktree"] is False


def test_main_checkout_of_a_separate_git_dir_repo(mt, tmp_path):
    """The common dir is not <checkout>/.git here (as in a submodule): main_checkout is still the checkout."""
    repo, gitdir = (tmp_path / "sep").resolve(), (tmp_path / "store" / "sep.git").resolve()
    repo.mkdir()
    gitdir.parent.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main", f"--separate-git-dir={gitdir}")
    (repo / "f.txt").write_text("f\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "init")
    info = init(mt, repo)
    assert info["main_checkout"] == str(repo) and info["repo"].endswith("sep")
    # git records no main checkout here (`worktree list` names the git dir): the worktree, with a warning
    wt = (tmp_path / "sep-wt").resolve()
    git(repo, "worktree", "add", "-q", "-b", "feat", str(wt))
    info = init(mt, wt)
    assert info["main_checkout"] == str(wt) and info["linked_worktree"] is True
    assert any("main checkout of" in w for w in info["warnings"])


def test_main_checkout_of_a_submodule_and_its_linked_worktree(mt, git_repo, tmp_path):
    lib = (tmp_path / "lib").resolve()
    lib.mkdir()
    git(lib, "init", "-q", "-b", "main")
    git(lib, "commit", "-q", "--allow-empty", "-m", "lib")
    git(git_repo, "-c", "protocol.file.allow=always", "submodule", "-q", "add", str(lib), "lib")
    sub = git_repo / "lib"
    info = init(mt, sub)
    assert info["main_checkout"] == str(sub) and info["toplevel"] == str(sub)
    wt = (tmp_path / "lib-wt").resolve()
    git(sub, "worktree", "add", "-q", "-b", "feat", str(wt))
    info = init(mt, wt)
    assert info["main_checkout"] == str(sub) and info["toplevel"] == str(wt)
    assert not any("main checkout of" in w for w in info["warnings"])


def test_dirty_paths_untracked_modified_and_renamed(mt, git_repo):
    (git_repo / "README.md").write_text("changed\n")
    (git_repo / "new file.txt").write_text("x\n")
    git(git_repo, "mv", "src/a.txt", "src/b.txt")
    info = init(mt, git_repo / "src")
    assert sorted(info["dirty_paths"]) == sorted(["README.md", "new file.txt", "src/b.txt", "src/a.txt"])


def test_dirty_paths_list_each_untracked_file_not_its_directory(mt, git_repo):
    """`?? docs/` would later sweep the plan written into docs/plans/ into the stash."""
    (git_repo / "docs" / "notes").mkdir(parents=True)
    (git_repo / "docs" / "notes" / "n.md").write_text("n\n")
    (git_repo / "docs" / "todo.md").write_text("t\n")
    assert sorted(init(mt, git_repo)["dirty_paths"]) == ["docs/notes/n.md", "docs/todo.md"]


def test_dirty_rereads_the_tree_and_leaves_out_revmux_and_excluded(mt, git_repo, run_state):
    (git_repo / "gone.txt").write_text("deleted before exec\n")
    (git_repo / ".revmux").mkdir()
    (git_repo / ".revmux" / "profile.md").write_text("p\n")
    run = Path(init(mt, git_repo)["state_dir"])
    assert "gone.txt" in run_state(run)["dirty_paths"]
    # between init and Exec: one path vanishes, one becomes dirty, the plan is written
    (git_repo / "gone.txt").unlink()
    (git_repo / "README.md").write_text("edited later\n")
    (git_repo / "docs" / "plans").mkdir(parents=True)
    (git_repo / "docs" / "plans" / "p.md").write_text("plan\n")
    (git_repo / "my file.txt").write_text("x\n")
    r = mt("dirty", "--exclude", "docs/plans/p.md", cwd=git_repo)
    assert r.code == 0, r.err
    out = r.json()
    assert sorted(out["paths"]) == ["README.md", "my file.txt"]
    assert sorted(out["quoted"].split(" ", 1)) == sorted(["README.md", "'my file.txt'"])
    assert run_state(run)["stash_paths"] == out["paths"]


def test_dirty_on_a_clean_tree_and_outside_exclude(mt, git_repo, tmp_path):
    init(mt, git_repo)
    assert mt("dirty", cwd=git_repo).json() == {"paths": [], "quoted": "", "unstage": [], "unstage_quoted": ""}
    r = mt("dirty", "--exclude", str(tmp_path / "elsewhere.md"), cwd=git_repo)
    assert r.code == 1 and "outside the checkout" in r.err


def test_dirty_unstage_lists_only_paths_in_head_missing_from_the_index(mt, git_repo):
    """Staged deletions and rename sources need `git reset` before `stash push -- <paths>`; staged edits, staged
    new files (also one deleted again in the tree), worktree deletions and untracked files must stay staged."""
    for name in ("del me.txt", "wt-del.txt", "staged.txt"):
        (git_repo / name).write_text("base\n")
    git(git_repo, "add", ".")
    git(git_repo, "commit", "-q", "-m", "more")
    init(mt, git_repo)
    git(git_repo, "rm", "-q", "--", "del me.txt")
    git(git_repo, "mv", "src/a.txt", "src/b.txt")
    (git_repo / "wt-del.txt").unlink()
    (git_repo / "staged.txt").write_text("staged\n")
    git(git_repo, "add", "staged.txt")
    (git_repo / "staged.txt").write_text("worktree\n")
    (git_repo / "ad.txt").write_text("added\n")
    git(git_repo, "add", "ad.txt")
    (git_repo / "ad.txt").unlink()
    (git_repo / "u.txt").write_text("untracked\n")
    out = mt("dirty", cwd=git_repo).json()
    assert sorted(out["paths"]) == sorted(
        ["del me.txt", "src/a.txt", "src/b.txt", "wt-del.txt", "staged.txt", "ad.txt", "u.txt"]
    )
    assert sorted(out["unstage"]) == ["del me.txt", "src/a.txt"]
    assert sorted(shlex.split(out["unstage_quoted"])) == ["del me.txt", "src/a.txt"]
    assert "'del me.txt'" in out["unstage_quoted"]


def test_has_origin_with_remote(mt, git_repo, tmp_path):
    bare = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", str(bare))
    git(git_repo, "remote", "add", "origin", str(bare))
    git(git_repo, "push", "-q", "origin", "main")
    git(git_repo, "remote", "set-head", "origin", "main")
    info = init(mt, git_repo)
    assert info["has_origin"] is True
    assert info["default_branch"] == "main"


def companion_path(profile_dir: Path, pid: str, key: str | None = None) -> Path:
    """The profile_config() phase pid's companion dir in profile_dir, or the path its field key names there."""
    spec = profile_config()["phases"][pid]
    root = profile_dir / "companions" / spec["companion"]
    return root / spec[key] if key else root


def exec_scripts_dir(profile_dir: Path) -> Path:
    return companion_path(profile_dir, "exec", "scripts")


def test_default_branch_comes_from_the_profile_detect_branch_script(mt, git_repo, isolate):
    (exec_scripts_dir(isolate.profile_dir) / "detect-branch.sh").write_text("#!/bin/bash\necho develop\n")
    assert init(mt, git_repo)["default_branch"] == "develop"


def test_default_branch_fallback_when_the_script_fails(mt, git_repo, isolate):
    (exec_scripts_dir(isolate.profile_dir) / "detect-branch.sh").write_text("#!/bin/bash\nexit 1\n")
    git(git_repo, "branch", "-m", "main", "trunk")
    assert init(mt, git_repo)["default_branch"] == "trunk"


def test_default_branch_fallback_without_the_script(git_repo, tmp_path):
    git(git_repo, "branch", "-m", "main", "trunk")
    assert MT.default_branch(str(git_repo), str(tmp_path / "no-scripts")) == "trunk"


def test_default_branch_fallback_when_the_script_hangs(git_repo, tmp_path, monkeypatch):
    """The script is the profile's copy and its last step asks the remote: a hang falls back to the local steps."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "detect-branch.sh").write_text("#!/bin/bash\nexec sleep 30\n")
    git(git_repo, "branch", "-m", "main", "trunk")
    monkeypatch.setattr(MT, "DETECT_BRANCH_TIMEOUT", 0.5)
    started = time.monotonic()
    assert MT.default_branch(str(git_repo), str(scripts)) == "trunk"
    assert time.monotonic() - started < 10


# ---------------------------------------------------------------- init: the profile


def run_profile_values(d: Path) -> dict:
    """The PROFILE_STATE_KEYS values init stores for a profile_config() profile in d."""
    config = profile_config()
    phases = config["phases"]
    return {
        "profile_name": d.name,
        "profile_dir": str(d),
        "autonomy": config["autonomy"],
        "gates": [],
        "defaults": config["defaults"],
        "brainstorm_entry": str(companion_path(d, "brainstorm", "entry")),
        "plan_entry": str(companion_path(d, "plan", "entry")),
        "plan_reviewer": str(companion_path(d, "plan", "reviewer")),
        "plan_reviewer_model": "opus",
        "exec_root": str(companion_path(d, "exec")),
        "exec_scripts": str(exec_scripts_dir(d)),
        "exec_rules": str(companion_path(d, "exec", "rules")),
        "exec_data": str(d / "data" / phases["exec"]["companion"]),
        "review_entry": str(companion_path(d, "review", "entry")),
        "review_scripts": str(companion_path(d, "review", "scripts")),
        "review_data": str(d / "data" / phases["review"]["companion"]),
    }


def test_init_stores_the_profile_keys_with_absolute_paths(mt, git_repo, isolate, run_state):
    info = init(mt, git_repo)
    d = isolate.profile_dir
    expected = run_profile_values(d)
    assert {k: info[k] for k in MT.PROFILE_STATE_KEYS} == expected
    assert info["profile_name"] == "default"
    for key in ("brainstorm_entry", "plan_entry", "plan_reviewer", "exec_rules", "review_entry"):
        assert os.path.isabs(info[key]) and Path(info[key]).is_file(), key
    for key in ("profile_dir", "exec_root", "exec_scripts", "review_scripts"):
        assert os.path.isabs(info[key]) and Path(info[key]).is_dir(), key
    for key in ("exec_data", "review_data"):
        assert os.path.isabs(info[key]), key
    assert not (d / "data").exists(), "init writes nothing into the profile"
    assert info["warnings"] == []

    state = run_state(Path(info["state_dir"]))
    assert {k: state[k] for k in MT.PROFILE_STATE_KEYS} == expected
    for gone in ("planning_root", "planning_data", "revmux_root"):
        assert gone not in state and gone not in info


def test_init_keeps_phases_the_five_entry_status_list(mt, git_repo, run_state):
    """The profile's phases map is never stored as `phases`: that is the dashboard's, mt phase's and status's."""
    run = Path(init(mt, git_repo)["state_dir"])
    phases = run_state(run)["phases"]
    assert [p["id"] for p in phases] == MT.PHASE_IDS
    for p in phases:
        assert set(p) == {"id", "label", "status", "started", "finished", "note"} and p["status"] == "pending"
    assert mt("status").json()["phases"] == dict.fromkeys(MT.PHASE_IDS, "pending")
    assert mt("phase", "brainstorm").code == 0
    assert run_state(run)["phases"][0]["status"] == "run"


def test_init_with_a_relative_profile_dir_stores_absolute_paths(mt, git_repo, isolate):
    rel = Path(os.path.relpath(isolate.profile_dir, git_repo))
    assert not rel.is_absolute()
    info = init(mt, git_repo, profile=rel)
    assert {k: info[k] for k in MT.PROFILE_STATE_KEYS} == run_profile_values(isolate.profile_dir)


def test_init_names_the_profile_after_its_dir(mt, git_repo, isolate, profile_factory):
    d = profile_factory("team", root=isolate.tmp / "elsewhere")
    info = init(mt, git_repo, profile=d)
    assert {k: info[k] for k in MT.PROFILE_STATE_KEYS} == run_profile_values(d)
    assert info["profile_name"] == "team" and info["warnings"] == []


@pytest.mark.parametrize(
    ("config", "gates"),
    [
        ({"autonomy": "interactive"}, ["design", "plan", "review-round"]),
        ({"autonomy": "interactive", "gates": ["plan"]}, ["plan"]),
        ({"autonomy": "interactive", "gates": []}, []),
        ({"autonomy": "autonomous"}, []),
        ({"autonomy": "autonomous", "gates": ["review-round", "design"]}, ["review-round", "design"]),
    ],
)
def test_init_resolves_autonomy_and_gates(mt, git_repo, profile_factory, run_state, config, gates):
    info = init(mt, git_repo, profile=profile_factory("p", config=config))
    assert info["autonomy"] == config["autonomy"] and info["gates"] == gates
    assert run_state(Path(info["state_dir"]))["gates"] == gates


def test_init_without_defaults_stores_null(mt, git_repo, isolate, profile_factory):
    d = profile_factory("p")
    config = {k: v for k, v in profile_config().items() if k != "defaults"}
    (d / "profile.md").write_text(MT.write_frontmatter(config, "\n"))
    assert init(mt, git_repo, profile=d)["defaults"] is None


def test_init_skipped_phases_have_null_keys_and_a_review_warning(mt, git_repo, profile_factory):
    phases = {**profile_config()["phases"], "brainstorm": "skip", "review": "skip"}
    d = profile_factory("p", config={"phases": phases})
    info = init(mt, git_repo, profile=d)
    expected = run_profile_values(d)
    for key in ("brainstorm_entry", "review_entry", "review_scripts", "review_data"):
        expected[key] = None
    assert {k: info[k] for k in MT.PROFILE_STATE_KEYS} == expected
    assert info["warnings"] == ["profile p: phases.review is skip; the review phase does not run"]


def test_init_plan_reviewer_absent_or_without_a_model(mt, git_repo, profile_factory):
    phases = profile_config()["phases"]
    phases["plan"] = {"companion": "planning", "entry": "commands/make.md"}
    info = init(mt, git_repo, profile=profile_factory("p", config={"phases": phases}))
    assert info["plan_reviewer"] is None and info["plan_reviewer_model"] is None
    assert info["plan_entry"] and info["warnings"] == []

    d = profile_factory("q")
    reviewer = d / "companions" / "planning" / "agents" / "plan-review.md"
    reviewer.write_text("---\nname: plan-review\ntools: Read\n---\n\n# review\n")
    info = init(mt, git_repo, profile=d)
    assert info["plan_reviewer"] == str(reviewer) and info["plan_reviewer_model"] is None


def test_init_review_warning_when_revmux_is_missing(mt, git_repo, isolate, monkeypatch, run_state):
    which = MT.shutil.which
    monkeypatch.setattr(MT.shutil, "which", lambda name, *a, **kw: None if name == "revmux" else which(name, *a, **kw))
    info = init(mt, git_repo)
    assert info["warnings"] == [
        "profile default: phases.review requires 'revmux', which is not on PATH; the review phase cannot run"
    ]
    assert info["review_entry"] == run_profile_values(isolate.profile_dir)["review_entry"], "the keys stay set"
    assert run_state(Path(info["state_dir"]))["warnings"] == info["warnings"]


def test_init_passes_other_check_warnings_on(mt, git_repo, profile_factory):
    info = init(mt, git_repo, profile=profile_factory("p", config={"color": "blue"}))
    assert len(info["warnings"]) == 1 and info["warnings"][0].startswith("profile p: unknown key 'color'")


def test_init_skips_update_lookups(mt, git_repo, isolate, profile_factory):
    """A source check cannot read is a `profile check` warning; init does no update lookups at all."""
    d = profile_factory("p")
    data, body = MT.read_frontmatter((d / "UPDATE.md").read_text())
    data["companions"]["planning"]["source"] = "plugin:planning@umputun-cc-thingz"
    (d / "UPDATE.md").write_text(MT.write_frontmatter(data, body))
    isolate.plugins_json.write_text("{not json")
    check = mt("profile", "check", "--dir", str(d))
    assert check.code == 0 and any("cannot check plugin:planning" in w for w in check.json()["warnings"])
    assert init(mt, git_repo, profile=d)["warnings"] == []


@pytest.mark.parametrize("plugins", ["{}", "{not json", None], ids=["empty", "unreadable", "missing"])
def test_init_needs_no_installed_plugins(mt, git_repo, isolate, plugins):
    if plugins is None:
        isolate.plugins_json.unlink()
    else:
        isolate.plugins_json.write_text(plugins)
    info = init(mt, git_repo)
    assert info["exec_scripts"] == str(exec_scripts_dir(isolate.profile_dir)) and info["warnings"] == []


@pytest.mark.parametrize(
    "gone",
    [("planning@umputun-cc-thingz",), ("planning@umputun-cc-thingz", "revmux@revmux")],
    ids=["planning", "planning-and-revmux"],
)
def test_init_needs_no_planning_plugin(mt, git_repo, isolate, run_state, gone):
    """The fixture installed_plugins.json without the planning plugin, its cache tree deleted: init runs on the
    profile's copies alone, and default_branch comes from the profile's detect-branch.sh, not the plugin's."""
    plugins = json.loads(isolate.plugins_json.read_text())["plugins"]
    for key in gone:
        del plugins[key]
    isolate.write_plugins(plugins)
    shutil.rmtree(isolate.planning_root)
    if "revmux@revmux" in gone:
        shutil.rmtree(isolate.revmux_root)
    (exec_scripts_dir(isolate.profile_dir) / "detect-branch.sh").write_text("#!/bin/bash\necho from-the-profile\n")

    info = init(mt, git_repo)
    assert {k: info[k] for k in MT.PROFILE_STATE_KEYS} == run_profile_values(isolate.profile_dir)
    assert info["default_branch"] == "from-the-profile"
    assert info["warnings"] == [] and run_state(Path(info["state_dir"]))["warnings"] == []
    st = mt("status")
    assert st.code == 0 and st.json()["warnings"] == []
    assert {k: st.json()["values"][k] for k in MT.PROFILE_STATE_KEYS} == run_profile_values(isolate.profile_dir)


def break_contract(d: Path) -> None:
    (exec_scripts_dir(d) / "move-plan.sh").unlink()


def leave_a_token(d: Path) -> None:
    (d / "companions" / "revmux" / "SKILL.md").write_text("run ${CLAUDE_PLUGIN_ROOT}/x\n")


def bad_autonomy(d: Path) -> None:
    (d / "profile.md").write_text(MT.write_frontmatter({**profile_config(), "autonomy": "yolo"}, "\n"))


def as_template(d: Path) -> None:
    (d / "profile.md").write_text(MT.write_frontmatter({**profile_config(), "template": True}, "\n"))


def no_profile_md(d: Path) -> None:
    (d / "profile.md").unlink()


def no_dir(d: Path) -> None:
    shutil.rmtree(d)


@pytest.mark.parametrize(
    ("damage", "message"),
    [
        (break_contract, "lacks move-plan.sh"),
        (leave_a_token, "unrewritten token at companions/revmux/SKILL.md:1: ${CLAUDE_PLUGIN_ROOT}"),
        (bad_autonomy, "autonomy: expected one of"),
        (as_template, "is a template (template: true"),
        (no_profile_md, "no profile at"),
        (no_dir, "no profile at"),
    ],
    ids=lambda v: v.__name__ if callable(v) else None,
)
def test_init_fails_on_an_invalid_profile_and_creates_no_run(mt, git_repo, isolate, damage, message):
    first = Path(init(mt, git_repo, "first")["state_dir"])
    pointer = isolate.state_home / "current-default"
    damage(isolate.profile_dir)
    r = mt(*init_args("second"), cwd=git_repo)
    assert r.code == 1 and message in r.err, r.err
    assert r.out == ""
    assert sorted(p.name for p in isolate.state_home.iterdir()) == sorted([first.name, pointer.name])
    assert pointer.read_text().strip() == str(first), "the pointer still names the earlier run"


def test_init_profile_errors_name_the_check_command(mt, git_repo, isolate):
    break_contract(isolate.profile_dir)
    r = mt(*init_args("x"), cwd=git_repo)
    assert r.code == 1 and f"mt profile check --dir {isolate.profile_dir}" in r.err
    assert not isolate.state_home.exists()


def test_init_needs_profile_dir(mt, git_repo, isolate):
    r = mt("init", "--title", "x", cwd=git_repo)
    assert r.code == 2 and "--profile-dir" in r.err
    assert not isolate.state_home.exists()


@pytest.mark.parametrize("value", ["", "  "])
def test_init_refuses_an_empty_profile_dir(mt, git_repo, isolate, value):
    """An empty --profile-dir names no profile, as an empty --dir does; it never stands for the current directory."""
    write_run_profile(git_repo)  # a profile there would otherwise run
    r = mt("init", "--title", "x", "--profile-dir", value, cwd=git_repo)
    assert r.code == 1 and "--profile-dir: an empty path names no profile directory" in r.err, r.err
    assert not isolate.state_home.exists()


def test_plugin_entry_prefers_user_scope_and_skips_stale_paths(tmp_path):
    live, project = tmp_path / "live", tmp_path / "project"
    live.mkdir()
    project.mkdir()
    gone = str(tmp_path / "gone")
    plugins = {
        "x@m": [
            {"scope": "project", "projectPath": "/p", "installPath": str(project)},
            {"scope": "user", "installPath": gone},
            {"scope": "user", "installPath": str(live)},
        ]
    }
    assert MT.plugin_entry(plugins, "x@m")["installPath"] == str(live)
    assert MT.plugin_entry({"x@m": plugins["x@m"][:2]}, "x@m")["installPath"] == str(project), "else any live one"
    assert MT.plugin_entry({"x@m": [{"scope": "user", "installPath": gone}, "junk"]}, "x@m") is None
    assert MT.plugin_entry({}, "x@m") is None


def test_no_renderer(mt, git_repo, isolate, monkeypatch):
    monkeypatch.setenv("MT_VIEW", str(isolate.tmp / "missing-view"))
    assert init(mt, git_repo)["renderer"] is None


# ---------------------------------------------------------------- state io


def test_state_js_parses_and_escapes_script_close(mt, git_repo, run_state):
    info = init(mt, git_repo, "Fix </script><b> in titles")
    run = Path(info["state_dir"])
    assert mt("event", "</script> again").code == 0
    js = (run / "state.js").read_text()
    assert "</" not in js
    assert parse_state_js(js) == run_state(run)


def test_writes_leave_no_tmp_files(mt, git_repo):
    run = Path(init(mt, git_repo)["state_dir"])
    mt("event", "one")
    mt("assume", "x", "--why", "y")
    assert sorted(p.name for p in run.iterdir()) == ["dashboard.html", "state.js", "state.json"]


def test_command_loads_fresh_state(mt, git_repo, run_state):
    run = Path(init(mt, git_repo)["state_dir"])
    state = run_state(run)
    state["title"] = "edited outside"
    (run / "state.json").write_text(json.dumps(state))
    assert mt("event", "after edit").code == 0
    assert run_state(run)["title"] == "edited outside"


# ---------------------------------------------------------------- resolution


def test_resolution_run_flag_beats_pointer(mt, git_repo, run_state):
    first = Path(init(mt, git_repo, "first")["state_dir"])
    second = Path(init(mt, git_repo, "second")["state_dir"])
    assert mt("--run", str(first), "event", "to first").code == 0
    assert mt("event", "to pointer").code == 0
    assert run_state(first)["events"][-1]["text"] == "to first"
    assert run_state(second)["events"][-1]["text"] == "to pointer"


def test_resolution_uses_pointer_of_this_session(mt, git_repo, monkeypatch, isolate):
    init(mt, git_repo)
    monkeypatch.setenv("AGTERM_SESSION_ID", "other")
    r = mt("status")
    assert r.code == 1
    assert "current-other" in r.err


# ---------------------------------------------------------------- phase / activity


def test_phase_transitions_stamp_started_and_finished(mt, git_repo, run_state):
    run = Path(init(mt, git_repo)["state_dir"])
    assert mt("phase", "brainstorm", "--activity", "Reading the codebase").code == 0
    s = run_state(run)
    b = s["phases"][0]
    assert b["status"] == "run" and b["started"] and b["finished"] is None
    assert s["activity"] == "Reading the codebase"

    assert mt("phase", "plan", "--note", "planning:make").code == 0
    s = run_state(run)
    b, p = s["phases"][0], s["phases"][1]
    assert b["status"] == "done" and b["finished"] >= b["started"]
    assert p["status"] == "run" and p["started"] and p["note"] == "planning:make"

    assert mt("phase", "review", "--skipped", "--note", "revmux missing").code == 0
    s = run_state(run)
    assert s["phases"][1]["status"] == "done"
    assert s["phases"][3]["status"] == "skipped" and s["phases"][3]["note"] == "revmux missing"
    assert [e["text"] for e in s["events"]][-3:] == ["Brainstorm started", "Plan started", "Review skipped"]


def test_phase_same_id_keeps_start(mt, git_repo, run_state):
    run = Path(init(mt, git_repo)["state_dir"])
    mt("phase", "exec")
    started = run_state(run)["phases"][2]["started"]
    mt("phase", "exec", "--activity", "Task 2")
    s = run_state(run)
    assert s["phases"][2]["status"] == "run" and s["phases"][2]["started"] == started
    assert s["activity"] == "Task 2"


def test_phase_finishes_an_error_phase_and_clears_error(mt, git_repo, run_state):
    run = Path(init(mt, git_repo)["state_dir"])
    mt("phase", "exec")
    s = run_state(run)
    s["phases"][2]["status"], s["error"], s["now_kind"] = "error", "task 3 failed", "error"
    (run / "state.json").write_text(json.dumps(s))
    assert mt("phase", "review").code == 0
    s = run_state(run)
    assert s["phases"][2]["status"] == "done" and s["phases"][3]["status"] == "run"
    assert s["error"] is None and s["now_kind"] == ""


def test_activity_wait_and_error_recovery(mt, git_repo, run_state):
    run = Path(init(mt, git_repo)["state_dir"])
    mt("phase", "brainstorm")
    assert mt("activity", "Waiting for your answer", "--wait").code == 0
    s = run_state(run)
    assert s["activity"] == "Waiting for your answer" and s["now_kind"] == "wait"

    s["phases"][0]["status"], s["error"], s["now_kind"] = "error", "boom", "error"
    (run / "state.json").write_text(json.dumps(s))
    assert mt("activity", "Retrying").code == 0
    s = run_state(run)
    assert s["error"] is None and s["now_kind"] == "" and s["phases"][0]["status"] == "run"


def test_assume_decide_event(mt, git_repo, run_state):
    run = Path(init(mt, git_repo)["state_dir"])
    assert mt("assume", "SQLite storage", "--why", "already in go.mod").code == 0
    assert mt("decide", "How long should a session live?", "30 days, sliding").code == 0
    assert mt("event", "Design saved").code == 0
    s = run_state(run)
    assert s["assumptions"] == [{"text": "SQLite storage", "why": "already in go.mod"}]
    assert s["decisions"] == [{"q": "How long should a session live?", "a": "30 days, sliding"}]
    assert s["events"][-1]["text"] == "Design saved"
    assert re.fullmatch(r"\d\d:\d\d:\d\d", s["events"][-1]["t"])


# ---------------------------------------------------------------- set / status


def test_set_allowed_keys_and_clear(mt, git_repo, run_state):
    run = Path(init(mt, git_repo)["state_dir"])
    assert mt("set", "review_base=abc", "loop_start_sha=def", "revmux_tasks_dir=/x/.revmux/tasks").code == 0
    s = run_state(run)
    assert (s["review_base"], s["loop_start_sha"], s["revmux_tasks_dir"]) == ("abc", "def", "/x/.revmux/tasks")
    assert mt("set", "stash_ref=abc").code == 0
    assert mt("set", "stash_ref=").code == 0
    assert run_state(run)["stash_ref"] is None


@pytest.mark.parametrize(
    "key", ["review_base", "loop_start_sha", "reviewed_sha", "revmux_task", "stop_reason", "mode", "stash_restored"]
)
def test_set_refuses_an_empty_value(mt, git_repo, run_state, key):
    """`mt set review_base=$(git merge-base ...)` with a failing merge-base must stop the run, not clear the key."""
    run = Path(init(mt, git_repo)["state_dir"])
    before = run_state(run)
    r = mt("set", "loop_start_sha=abc", f"{key}=")
    assert r.code == 1 and f"empty value for {key}" in r.err
    assert run_state(run) == before


@pytest.mark.parametrize(
    ("key", "good", "bad"),
    [("stop_reason", "round-cap", "round_cap"), ("mode", "inplace", "here"), ("stash_restored", "gone", "kept")],
)
def test_set_checks_enumerated_values(mt, git_repo, run_state, key, good, bad):
    run = Path(init(mt, git_repo)["state_dir"])
    r = mt("set", f"{key}={bad}")
    assert r.code == 1 and "must be one of" in r.err
    assert run_state(run)[key] is None
    assert mt("set", f"{key}={good}").code == 0
    assert run_state(run)[key] == good


def test_set_reviewed_sha_is_written_once(mt, git_repo, run_state):
    """A rerun of Finalize step 1 after the finalizer committed must not move reviewed_sha onto unreviewed code."""
    run = Path(init(mt, git_repo)["state_dir"])
    assert mt("set", "reviewed_sha=abc").code == 0
    assert mt("set", "reviewed_sha=abc").code == 0, "the same value again is harmless"
    before = run_state(run)
    r = mt("set", "stop_reason=clean", "reviewed_sha=def")
    assert r.code == 1 and "reviewed_sha is already abc" in r.err
    assert run_state(run) == before, "a refused set changes nothing, not even the other pairs"


def test_set_worktree_path_turns_on_worktree_mode(mt, git_repo, run_state):
    run = Path(init(mt, git_repo)["state_dir"])
    assert mt("set", "worktree_path=/abs/wt").code == 0
    s = run_state(run)
    assert s["worktree"] is True and s["worktree_path"] == "/abs/wt"


def test_status_prints_phase_values_and_begun(mt, git_repo, run_state):
    run = Path(init(mt, git_repo)["state_dir"])
    mt("phase", "exec", "--activity", "Task 1")
    mt("set", "stash_ref=0123")
    s = run_state(run)
    s["checkpoints"] = [
        {"key": "exec.task.1", "state": "ended", "result": "ok", "at": 1},
        {"key": "exec.task.2", "state": "begun", "result": None, "at": 2},
    ]
    s["review"] = [{"round": 1, "run": "01-initial", "round_dir": "/rd", "report": "/r.json", "verdict": None,
                    "started": 5, "finished": None}]  # fmt: skip
    s["assumptions"] = [{"text": "a", "why": "w"}]
    s["decisions"] = [{"q": "Run the task in a worktree or in place?", "a": "In place"}]
    (run / "state.json").write_text(json.dumps(s))
    r = mt("status")
    assert r.code == 0
    out = r.json()
    assert out["run"] == str(run)
    assert out["phase"] == "exec" and out["activity"] == "Task 1" and out["error"] is None
    assert out["values"]["stash_ref"] == "0123"
    assert out["values"]["main_checkout"] == str(git_repo)
    assert out["begun"] == [{"key": "exec.task.2", "at": 2}]
    assert out["ended"] == [{"key": "exec.task.1", "result": "ok"}]
    assert out["rounds"][0]["run"] == "01-initial" and out["rounds"][0]["finished"] is None
    assert out["assumptions"] == [{"text": "a", "why": "w"}]
    assert out["decisions"][0]["a"] == "In place"


def test_status_prints_the_profile_keys(mt, git_repo):
    info = init(mt, git_repo)
    r = mt("status")
    assert r.code == 0 and r.err == ""
    out = r.json()
    assert {k: out["values"][k] for k in MT.PROFILE_STATE_KEYS} == {k: info[k] for k in MT.PROFILE_STATE_KEYS}
    assert out["warnings"] == []
    for gone in ("planning_root", "planning_data", "revmux_root"):
        assert gone not in out["values"]


def test_status_warns_on_a_run_from_before_profiles(mt, git_repo, run_state):
    run = Path(init(mt, git_repo)["state_dir"])
    s = run_state(run)
    for key in MT.PROFILE_STATE_KEYS:
        del s[key]
    s.update(planning_root="/old/planning", planning_data="/old/data", revmux_root=None)
    (run / "state.json").write_text(json.dumps(s))
    r = mt("status")
    assert r.code == 0
    out = r.json()
    assert len(out["warnings"]) == 1
    text = out["warnings"][0]
    assert "started before make-task profiles" in text and "start a new run" in text
    assert "finish it with the make-task version that started it" in text
    assert f"mt: warning: {text}" in r.err
    assert all(out["values"][k] is None for k in MT.PROFILE_STATE_KEYS)


# ---------------------------------------------------------------- errors


@pytest.mark.parametrize(
    "args",
    [
        ("status",),
        ("set", "review_base=x"),
        ("phase", "plan"),
        ("activity", "x"),
        ("assume", "x", "--why", "y"),
        ("decide", "q", "a"),
        ("event", "x"),
    ],
)
def test_command_without_active_run(mt, args):
    r = mt(*args)
    assert r.code == 1
    assert "no active run" in r.err


def test_pointer_to_deleted_run(mt, git_repo):
    run = Path(init(mt, git_repo)["state_dir"])
    for p in run.iterdir():
        p.unlink()
    run.rmdir()
    r = mt("status")
    assert r.code == 1 and "no state.json" in r.err


def test_run_flag_without_state(mt, tmp_path):
    r = mt("--run", str(tmp_path), "status")
    assert r.code == 1 and "no state.json" in r.err


def test_unknown_phase_id(mt, git_repo, run_state):
    run = Path(init(mt, git_repo)["state_dir"])
    before = run_state(run)
    r = mt("phase", "deploy")
    assert r.code == 1 and "unknown id 'deploy'" in r.err
    assert run_state(run) == before


def test_unknown_set_key_changes_nothing(mt, git_repo, run_state):
    run = Path(init(mt, git_repo)["state_dir"])
    before = run_state(run)
    r = mt("set", "review_base=abc", "title=hacked")
    assert r.code == 1 and "unknown key 'title'" in r.err
    assert run_state(run) == before


def test_set_without_equals(mt, git_repo):
    init(mt, git_repo)
    r = mt("set", "review_base")
    assert r.code == 1 and "KEY=VALUE" in r.err


def test_init_outside_git(mt, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    r = mt(*init_args("x"), cwd=plain)
    assert r.code == 1 and "not inside a git work tree" in r.err


def test_isolation_holds(isolate):
    assert not any(k.startswith("AGTERM_") for k in os.environ)
    assert not {"XDG_STATE_HOME", "XDG_CONFIG_HOME"} & set(os.environ)
    assert os.environ["MT_STATE_HOME"] == str(isolate.state_home)
    assert MT.pointer_path().parent == isolate.state_home
    assert os.environ["PATH"].split(os.pathsep)[0] == str(isolate.tmp / "bin")
    # nothing a test runs reaches the user's make-task state or profiles, the shipped templates, the installed
    # plugins, the real renderer or the live agterm
    tmp = isolate.tmp.resolve()
    for name, path in {
        "state root": MT.state_root(),
        "config root": MT.config_root(),
        "profile templates": MT.PROFILE_TEMPLATES,
        "installed plugins": MT.plugins_json_path(),
        "renderer": Path(MT.renderer_path() or "/"),
        "run profile": isolate.profile_dir,
    }.items():
        assert path.resolve().is_relative_to(tmp), name
    assert Path(shutil.which("agtermctl") or "/").resolve() == (isolate.tmp / "bin" / "agtermctl").resolve()


# ---------------------------------------------------------------- fake agtermctl


def _agtermctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["agtermctl", *args], capture_output=True, text=True, check=False)


def test_fake_agtermctl_logs_and_replies(isolate):
    isolate.agterm_replies.write_text(
        json.dumps(
            {
                "tree": {"stdout": '{"windows": []}'},
                "session overlay": {"stdout": "generic"},
                "session overlay open": {"stdout": '{"outcome":"submitted"}', "exit": 0},
            }
        )
    )
    assert _agtermctl("tree", "--json").stdout == '{"windows": []}'
    assert _agtermctl("session", "overlay", "open", "--html", "x").stdout == '{"outcome":"submitted"}'
    assert _agtermctl("session", "overlay", "close").stdout == "generic"
    r = _agtermctl("session", "status", "active")
    assert r.returncode == 0 and r.stdout == ""
    assert isolate.agterm_calls() == [
        ["tree", "--json"],
        ["session", "overlay", "open", "--html", "x"],
        ["session", "overlay", "close"],
        ["session", "status", "active"],
    ]


def test_fake_agtermctl_fail_switches(isolate, monkeypatch):
    monkeypatch.setenv("FAKE_AGTERMCTL_FAIL", "session status")
    assert _agtermctl("session", "status", "active").returncode == 1
    assert _agtermctl("tree").returncode == 0
    monkeypatch.setenv("FAKE_AGTERMCTL_FAIL", "1")
    assert _agtermctl("tree").returncode == 1


def test_fake_agtermctl_sleep(isolate, monkeypatch):
    monkeypatch.setenv("FAKE_AGTERMCTL_SLEEP", "0.3")
    t = time.monotonic()
    _agtermctl("tree")
    assert time.monotonic() - t >= 0.3
