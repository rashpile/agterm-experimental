"""Shared fixtures for the mt tests.

Tests run inside agterm (exec subagents included), so one autouse fixture cuts
every link to the live terminal and to the user's state: no AGTERM_* variables,
state and profiles (MT_CONFIG_HOME) under tmp_path, the profile templates
(MT.PROFILE_TEMPLATES) under tmp_path, the profile every `mt init` runs
(isolate.profile_dir, outside MT_CONFIG_HOME) under tmp_path, a fixture
installed_plugins.json (for plugin: sources), a fake agtermctl and a stub revmux
first on PATH, and git with no global or system config.
"""

from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import json
import os
import stat
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

TESTS = Path(__file__).resolve().parent
FIXTURES = TESTS / "fixtures"
MT_PATH = TESTS.parent / "scripts" / "mt"


def _load_mt() -> ModuleType:
    loader = importlib.machinery.SourceFileLoader("mt", str(MT_PATH))
    spec = importlib.util.spec_from_loader("mt", loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


MT = _load_mt()
# the templates make-task ships; isolate points MT.PROFILE_TEMPLATES at tmp_path, tests that read the real ones
# (never write them) use these
SHIPPED_TEMPLATES = MT.ASSETS / "profiles"
SHIPPED = SHIPPED_TEMPLATES / MT.DEFAULT_TEMPLATE
# what `mt init` leaves in a new run dir: the state, the dashboard and one viewer entry page per tab
RUN_FILES = sorted(["dashboard.html", "state.js", "state.json", *(f"viewer-{t}.html" for t in MT.VIEWER_TABS)])

# the profile dir the `init` helper passes; the autouse isolate fixture sets it for each test
_RUN_PROFILE: dict[str, Path] = {}

# same local steps as planning's detect-branch.sh (no network)
DETECT_BRANCH_STUB = """#!/bin/bash
set -e
branch=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@')
if [ -z "$branch" ]; then
  for c in main master trunk develop; do
    if git show-ref --verify --quiet "refs/heads/$c" 2>/dev/null; then branch="$c"; break; fi
  done
fi
echo "${branch:-main}"
"""


def write_exec(path: Path, text: str) -> Path:
    """Write an executable file, creating its parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


@dataclass
class Env:
    tmp: Path
    state_home: Path
    config_home: Path
    templates: Path
    profile_dir: Path
    plugins_dir: Path
    plugins_json: Path
    planning_root: Path
    revmux_root: Path
    agterm_log: Path
    agterm_replies: Path

    def write_plugins(self, plugins: dict[str, Any]) -> None:
        self.plugins_json.write_text(json.dumps({"version": 2, "plugins": plugins}))

    def agterm_calls(self) -> list[list[str]]:
        if not self.agterm_log.exists():
            return []
        return [json.loads(line)["argv"] for line in self.agterm_log.read_text().splitlines() if line]


@pytest.fixture(autouse=True)
def isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    prefixes = ("AGTERM_", "GIT_", "MT_", "FAKE_AGTERMCTL_")
    for key in list(os.environ):
        if key.startswith(prefixes) or key in ("XDG_STATE_HOME", "XDG_CONFIG_HOME"):
            monkeypatch.delenv(key, raising=False)

    state_home = tmp_path / "state"
    monkeypatch.setenv("MT_STATE_HOME", str(state_home))
    config_home = tmp_path / "config"
    monkeypatch.setenv("MT_CONFIG_HOME", str(config_home))
    # make-task:<profile>/<id> sources and install-default read the templates here, never the shipped ones
    # (profile_factory writes a `default` template matching its profiles; a test may point it elsewhere)
    templates = tmp_path / "mt-templates"
    monkeypatch.setattr(MT, "PROFILE_TEMPLATES", templates)

    # the profile `init` (the helper below) runs: the PROFILE_FILES stubs; outside MT_CONFIG_HOME, so a profile
    # test starts with no profile there
    profile_dir = (tmp_path / "run-profile").resolve() / "default"
    write_run_profile(profile_dir)
    monkeypatch.setitem(_RUN_PROFILE, "dir", profile_dir)

    # installed plugins, read only by plugin: profile sources
    plugins_dir = tmp_path / "plugins"
    plugins_json = plugins_dir / "installed_plugins.json"
    plugins_dir.mkdir()
    plugins_json.write_text((FIXTURES / "installed_plugins.json").read_text().replace("@PLUGINS@", str(plugins_dir)))
    planning_root = plugins_dir / "cache" / "umputun-cc-thingz" / "planning" / "3.8.4"
    write_exec(planning_root / "skills" / "exec" / "scripts" / "detect-branch.sh", DETECT_BRANCH_STUB)
    revmux_root = plugins_dir / "cache" / "revmux" / "revmux" / "0.5.5"
    revmux_root.mkdir(parents=True)
    monkeypatch.setenv("MT_PLUGINS_JSON", str(plugins_json))

    bindir = tmp_path / "bin"
    # a profile's review phase `requires: [revmux]`; the stub keeps that check quiet (it never runs a review)
    write_exec(bindir / "revmux", "#!/bin/sh\nexit 0\n")

    write_exec(
        bindir / "agtermctl",
        f'#!/bin/sh\nexec "{sys.executable}" "{TESTS / "fake_agtermctl.py"}" "$@"\n',
    )
    agterm_log = tmp_path / "agtermctl.log"
    agterm_replies = tmp_path / "agtermctl-replies.json"
    monkeypatch.setenv("FAKE_AGTERMCTL_LOG", str(agterm_log))
    monkeypatch.setenv("FAKE_AGTERMCTL_REPLIES", str(agterm_replies))
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")

    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "mt test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "mt@example.invalid")
        monkeypatch.setenv(f"GIT_{role}_DATE", "2026-01-01T00:00:00Z")

    return Env(
        tmp=tmp_path,
        state_home=state_home,
        config_home=config_home,
        templates=templates,
        profile_dir=profile_dir,
        plugins_dir=plugins_dir,
        plugins_json=plugins_json,
        planning_root=planning_root,
        revmux_root=revmux_root,
        agterm_log=agterm_log,
        agterm_replies=agterm_replies,
    )


def git(cwd: Path, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return r.stdout.strip()


def commit(repo: Path, msg: str) -> str:
    """Stage everything, commit, return the new HEAD."""
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """A real repo on `main` with one commit, at a resolved path (macOS /var → /private/var)."""
    repo = (tmp_path / "repo").resolve()
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("hello\n")
    (repo / "src").mkdir()
    (repo / "src" / "a.txt").write_text("a\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "init")
    return repo


@dataclass
class Result:
    code: int
    out: str
    err: str

    def json(self) -> Any:
        return json.loads(self.out)


@pytest.fixture
def mt(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> Callable[..., Result]:
    """Run mt in-process: mt("status", cwd=repo) → Result(code, out, err)."""

    def run(*args: str, cwd: Path | None = None) -> Result:
        capsys.readouterr()
        if cwd is not None:
            monkeypatch.chdir(cwd)
        try:
            code = MT.main(list(args))
        except SystemExit as e:  # argparse errors
            code = int(e.code or 0)
        captured = capsys.readouterr()
        return Result(code, captured.out, captured.err)

    return run


def profile(mt: Callable[..., Result], *args: str) -> tuple[Result, Any]:
    """Run `mt profile ARGS`; (result, its one JSON line or None)."""
    r = mt("profile", *args)
    lines = r.out.strip().splitlines()
    if not lines:
        return r, None
    assert len(lines) == 1, f"profile commands print one JSON line, got {r.out!r}"
    return r, json.loads(lines[0])


def subparsers(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    """The subcommand parsers of parser by name ({} when it has none)."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    return {}


def init_args(title: str = "Session store", profile: Path | None = None) -> list[str]:
    """`init --title T --profile-dir D`, D being isolate.profile_dir unless `profile` is given."""
    return ["init", "--title", title, "--profile-dir", str(profile or _RUN_PROFILE["dir"])]


def init(
    mt: Callable[..., Result], cwd: Path, title: str = "Session store", profile: Path | None = None
) -> dict[str, Any]:
    """`mt init` in cwd with the run profile (or `profile`); asserts it printed exactly one JSON line, returns it."""
    r = mt(*init_args(title, profile), cwd=cwd)
    assert r.code == 0, r.err
    lines = r.out.strip().splitlines()
    assert len(lines) == 1, "init prints exactly one JSON line"
    return json.loads(lines[0])


def init_run(mt: Callable[..., Result], cwd: Path, title: str = "Session store") -> Path:
    """`mt init` in cwd; returns the run directory."""
    return Path(init(mt, cwd, title)["state_dir"])


# a minimal profile: the companions hold one-line stub files where the phases map and the contracts need them
PROFILE_FILES: dict[str, dict[str, str]] = {
    "brainstorm": {"skills/brainstorm/SKILL.md": "# brainstorm\n"},
    "planning": {
        "commands/make.md": "# make\n",
        "agents/plan-review.md": "---\nname: plan-review\nmodel: opus\ntools: Read, Glob, Grep, Bash\n---\n\n# review\n",
        "scripts/resolve-rules.sh": "#!/bin/bash\n",
        **{f"skills/exec/scripts/{s}": "#!/bin/bash\n" for s in MT.EXEC_CONTRACT_SCRIPTS},
        "skills/exec/scripts/detect-branch.sh": DETECT_BRANCH_STUB,
        **{f"skills/exec/references/prompts/{p}": f"# {p}\n" for p in MT.EXEC_CONTRACT_PROMPTS},
    },
    "revmux": {
        "SKILL.md": "# revmux\n",
        **{f"scripts/{s}": "#!/bin/bash\n" for s in MT.REVIEW_CONTRACT_SCRIPTS},
    },
}


def profile_config() -> dict[str, Any]:
    """profile.md's frontmatter for PROFILE_FILES (the shape the template ships)."""
    return {
        "schema": MT.PROFILE_SCHEMA,
        "template": False,
        "autonomy": "default",
        "defaults": {"mode": "worktree", "leftovers": "leave"},
        "phases": {
            "brainstorm": {"companion": "brainstorm", "entry": "skills/brainstorm/SKILL.md"},
            "plan": {"companion": "planning", "entry": "commands/make.md", "reviewer": "agents/plan-review.md"},
            "exec": {"companion": "planning", "scripts": "skills/exec/scripts", "rules": "scripts/resolve-rules.sh"},
            "review": {"companion": "revmux", "entry": "SKILL.md", "scripts": "scripts", "requires": ["revmux"]},
        },
    }


def write_profile_files(d: Path, tree: str) -> None:
    """The PROFILE_FILES stubs under d/<tree>/<id>/ (scripts executable)."""
    for cid, files in PROFILE_FILES.items():
        for rel, text in files.items():
            path = d / tree / cid / rel
            if rel.endswith(".sh"):
                write_exec(path, text)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)


def write_run_profile(d: Path) -> Path:
    """profile.md (profile_config()) and companions/<id>/ with the PROFILE_FILES stubs: what `mt init` reads."""
    write_profile_files(d, "companions")
    (d / "profile.md").write_text(MT.write_frontmatter(profile_config(), "\n# run profile\n"))
    return d


@pytest.fixture
def profile_factory(isolate: Env) -> Callable[..., Path]:
    """profile_factory(name="default", *, config=None, root=None) → a valid profile dir.

    Written under <MT_CONFIG_HOME>/profiles/<name> (or <root>/<name>): profile.md (profile_config() with
    `config` merged in at the top level), companions/<id>/ with the PROFILE_FILES stubs (scripts executable),
    upstream/<id>/ equal to them, and UPDATE.md recording each as `make-task:default/<id>` with its tree hash.
    The first call also writes that source, the template <isolate.templates>/default with the same companions,
    unless the call writes that very dir.
    """

    def make(name: str = "default", *, config: dict[str, Any] | None = None, root: Path | None = None) -> Path:
        d = (root or isolate.config_home / "profiles") / name
        tpl = isolate.templates / "default"
        if d != tpl and not tpl.exists():
            # the make-task:default/<id> the profiles record: the same files, so `check` finds no updates
            write_profile_files(tpl, "companions")
            config_tpl = {**profile_config(), "template": True}
            (tpl / "profile.md").write_text(MT.write_frontmatter(config_tpl, "\n# template\n"))
        sources: dict[str, Any] = {}
        for tree in ("companions", "upstream"):
            write_profile_files(d, tree)
        for cid in PROFILE_FILES:
            version = MT.tree_hash(d / "companions" / cid)
            sources[cid] = {"source": f"make-task:default/{cid}", "version": version}
        (d / "profile.md").write_text(MT.write_frontmatter({**profile_config(), **(config or {})}, "\n# profile\n"))
        (d / "UPDATE.md").write_text(MT.write_frontmatter({"companions": sources}, MT.UPDATE_BODY))
        return d

    return make


@pytest.fixture
def run_state() -> Callable[[Path], dict[str, Any]]:
    def read(run: Path) -> dict[str, Any]:
        return json.loads((Path(run) / "state.json").read_text())

    return read
