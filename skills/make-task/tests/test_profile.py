"""`mt profile` commands and the profile helpers: config root, frontmatter, resolve, check, status, import,
install-default, outdated, fetch, accept; and the default template make-task ships."""

from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import (
    MT,
    MT_PATH,
    PROFILE_FILES,
    SHIPPED,
    Env,
    Result,
    git,
    init_args,
    profile,
    profile_config,
    subparsers,
    write_exec,
)

Factory = Callable[..., Path]


def reconfig(d: Path, change: Callable[[dict[str, Any]], None]) -> None:
    """Change profile.md's frontmatter in place; the body stays as it is."""
    path = d / "profile.md"
    data, body = MT.read_frontmatter(path.read_text())
    assert data is not None
    change(data)
    path.write_text(MT.write_frontmatter(data, body))


def check(mt: Callable[..., Result], d: Path) -> tuple[Result, dict[str, Any]]:
    r, out = profile(mt, "check", "--dir", str(d))
    assert out is not None, r.err
    assert r.code == (0 if out["ok"] else 1)
    return r, out


# ---------------------------------------------------------------- config root


def test_config_root_order(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MT_CONFIG_HOME", str(tmp_path / "mt"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert MT.config_root() == tmp_path / "mt"
    monkeypatch.delenv("MT_CONFIG_HOME")
    assert MT.config_root() == tmp_path / "xdg" / "make-task"
    monkeypatch.delenv("XDG_CONFIG_HOME")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert MT.config_root() == tmp_path / "home" / ".config" / "make-task"


def test_isolation_points_the_config_root_at_tmp(isolate: Env) -> None:
    assert MT.config_root() == isolate.config_home
    assert str(isolate.config_home).startswith(str(isolate.tmp))


# ---------------------------------------------------------------- frontmatter


def test_frontmatter_round_trip_keeps_the_body() -> None:
    body = "\n# Title\n\n---\nnot: frontmatter\n---\n\ttabs and trailing spaces  \nno final newline"
    text = MT.write_frontmatter({"b": 1, "a": {"x": [1, 2]}, "c": "skip"}, body)
    data, rest = MT.read_frontmatter(text)
    assert data == {"b": 1, "a": {"x": [1, 2]}, "c": "skip"}
    assert list(data) == ["b", "a", "c"], "key order is kept"
    assert rest == body


def test_frontmatter_edit_changes_only_the_frontmatter() -> None:
    text = "---\nschema: 1\nautonomy: default\n---\nbody line\n\n## plan\nkeep me\n"
    data, body = MT.read_frontmatter(text)
    assert data is not None
    data["autonomy"] = "interactive"
    data2, body2 = MT.read_frontmatter(MT.write_frontmatter(data, body))
    assert data2 == {"schema": 1, "autonomy": "interactive"}
    assert body2 == "body line\n\n## plan\nkeep me\n"


@pytest.mark.parametrize(
    ("text", "data", "body"),
    [
        ("no frontmatter\n", None, "no frontmatter\n"),
        ("---\n---\nbody\n", {}, "body\n"),
        ("---\na: 1\n---", {"a": 1}, ""),
        ("---\r\na: 1\r\n---\r\nbody\r\n", {"a": 1}, "body\r\n"),
        ("text\n---\na: 1\n---\n", None, "text\n---\na: 1\n---\n"),
    ],
)
def test_frontmatter_shapes(text: str, data: dict[str, Any] | None, body: str) -> None:
    assert MT.read_frontmatter(text) == (data, body)


@pytest.mark.parametrize("text", ["---\na: [1\n---\n", "---\n- a\n- b\n---\n", "---\njust text\n---\n"])
def test_frontmatter_invalid(text: str) -> None:
    with pytest.raises(MT.MtError):
        MT.read_frontmatter(text)


# ---------------------------------------------------------------- resolve


def test_resolve_missing_default(mt: Callable[..., Result], isolate: Env) -> None:
    r, out = profile(mt, "resolve")
    assert r.code == 0
    assert out == {"name": "default", "dir": str(isolate.config_home / "profiles" / "default"), "status": "missing"}


def test_resolve_ok(mt: Callable[..., Result], isolate: Env, profile_factory: Factory) -> None:
    d = profile_factory()
    r, out = profile(mt, "resolve")
    assert r.code == 0
    assert out == {"name": "default", "dir": str(d), "status": "ok"}


def test_resolve_named_profile(mt: Callable[..., Result], isolate: Env, profile_factory: Factory) -> None:
    profile_factory("fast")
    _, out = profile(mt, "resolve", "--profile", "fast")
    assert out["name"] == "fast"
    assert out["status"] == "ok"
    _, out = profile(mt, "resolve", "--profile", "slow")
    assert out == {"name": "slow", "dir": str(isolate.config_home / "profiles" / "slow"), "status": "missing"}


def test_resolve_dir(mt: Callable[..., Result], tmp_path: Path, profile_factory: Factory) -> None:
    d = profile_factory("tpl", root=tmp_path / "elsewhere")
    _, out = profile(mt, "resolve", "--dir", str(d))
    assert out == {"name": "tpl", "dir": str(d), "status": "ok"}


def test_dir_is_made_absolute_and_keeps_symlinks(
    mt: Callable[..., Result], tmp_path: Path, profile_factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--dir names a profile the way --profile and init's --profile-dir do: `~` expanded, made absolute,
    symlinks kept (the path the copies are rewritten for and a run stores)."""
    d = profile_factory("tpl", root=tmp_path / "elsewhere")
    link = tmp_path / "link"
    link.symlink_to(d)
    _, out = profile(mt, "resolve", "--dir", str(link))
    assert out == {"name": "link", "dir": str(link), "status": "ok"}
    assert mt("profile", "resolve", "--dir", "elsewhere/tpl", cwd=tmp_path).json()["dir"] == str(d)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert mt("profile", "resolve", "--dir", "~/elsewhere/tpl").json()["dir"] == str(d)


def test_resolve_dir_without_profile_md_is_missing(mt: Callable[..., Result], tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    _, out = profile(mt, "resolve", "--dir", str(tmp_path / "empty"))
    assert out["status"] == "missing"


@pytest.mark.parametrize("name", ["../x", "a/b", ".hidden", ""])
def test_resolve_refuses_a_bad_name(mt: Callable[..., Result], name: str) -> None:
    r, out = profile(mt, "resolve", "--profile", name)
    assert r.code == 1
    assert out is None
    assert "--profile" in r.err


def test_profile_and_dir_are_mutually_exclusive(mt: Callable[..., Result], tmp_path: Path) -> None:
    r = mt("profile", "resolve", "--profile", "x", "--dir", str(tmp_path))
    assert r.code == 2
    assert "not allowed with" in r.err


# one valid argument list per `mt profile` command
PROFILE_COMMAND_ARGS = {
    "resolve": [],
    "check": [],
    "status": [],
    "install-default": [],
    "import": ["extra", "--source", "local"],
    "outdated": [],
    "fetch": ["planning"],
    "accept": ["planning", "--expect", "sha256:0"],
    "raise-schema": [],
}


def test_profile_command_args_cover_every_profile_command() -> None:
    assert set(subparsers(subparsers(MT.build_parser())["profile"])) == set(PROFILE_COMMAND_ARGS)


@pytest.mark.parametrize("command", PROFILE_COMMAND_ARGS)
def test_profile_commands_refuse_run(mt: Callable[..., Result], tmp_path: Path, isolate: Env, command: str) -> None:
    """Every `mt profile` command refuses the global --run before it reads or writes anything."""
    r = mt("--run", str(tmp_path), "profile", command, *PROFILE_COMMAND_ARGS[command])
    assert (r.code, r.out) == (1, "")
    assert "never --run" in r.err
    assert not isolate.config_home.exists(), "nothing is installed or written"


# ---------------------------------------------------------------- check


def test_check_passes_on_a_valid_profile(mt: Callable[..., Result], profile_factory: Factory) -> None:
    d = profile_factory()
    r, out = profile(mt, "check")
    assert r.code == 0, r.err
    assert out == {"ok": True, "errors": [], "warnings": [], "updates": []}
    _, out = check(mt, d)
    assert out["ok"] is True


def test_check_missing_profile_fails(mt: Callable[..., Result]) -> None:
    r, out = profile(mt, "check", "--profile", "nope")
    assert r.code == 1
    assert out is None
    assert "no profile at" in r.err


@pytest.mark.parametrize(
    ("config", "ok"),
    [
        ({"brainstorm": "skip"}, True),
        ({"review": "skip"}, True),
        ({"brainstorm": "skip", "review": "skip"}, True),
    ],
)
def test_check_brainstorm_and_review_may_skip(
    mt: Callable[..., Result], profile_factory: Factory, config: dict[str, Any], ok: bool
) -> None:
    d = profile_factory()
    reconfig(d, lambda c: c["phases"].update(config))
    _, out = check(mt, d)
    assert out["ok"] is ok, out


def rm(rel: str) -> Callable[[Path], None]:
    return lambda d: (d / rel).unlink()


def phase(pid: str, **fields: Any) -> Callable[[Path], None]:
    return lambda d: reconfig(d, lambda c: c["phases"][pid].update(fields))


def config(**keys: Any) -> Callable[[Path], None]:
    return lambda d: reconfig(d, lambda c: c.update(keys))


def without(*path: str) -> Callable[[Path], None]:
    def change(c: dict[str, Any]) -> None:
        node = c
        for key in path[:-1]:
            node = node[key]
        del node[path[-1]]

    return lambda d: reconfig(d, change)


def write(rel: str, text: str) -> Callable[[Path], None]:
    def go(d: Path) -> None:
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(text)

    return go


PLANNING = "companions/planning"
FAILURES: list[tuple[str, Callable[[Path], None], str]] = [
    ("unknown phase", lambda d: reconfig(d, lambda c: c["phases"].update(deploy="skip")), "unknown phase 'deploy'"),
    ("finalize in phases", lambda d: reconfig(d, lambda c: c["phases"].update(finalize="skip")), "phases.finalize"),
    ("skip on exec", lambda d: reconfig(d, lambda c: c["phases"].update(exec="skip")), "exec cannot be skip"),
    ("skip on plan", lambda d: reconfig(d, lambda c: c["phases"].update(plan="skip")), "plan cannot be skip"),
    ("phase neither skip nor mapping", lambda d: reconfig(d, lambda c: c["phases"].update(plan="off")), "phases.plan"),
    ("missing phase", without("phases", "review"), "phases.review: missing"),
    ("phases not a mapping", config(phases=["plan"]), "phases: expected a mapping"),
    ("missing entry file", phase("brainstorm", entry="skills/brainstorm/GONE.md"), "is not a file"),
    ("missing entry field", without("phases", "plan", "entry"), "phases.plan: missing entry"),
    ("entry outside the companion", phase("brainstorm", entry="../planning/commands/make.md"), "inside companions/"),
    ("absolute entry", phase("brainstorm", entry="/etc/hosts"), "inside companions/"),
    ("unknown companion", phase("review", companion="nope"), "companions/nope/ does not exist"),
    ("bad companion id", phase("review", companion="../x"), "is not a companion id"),
    ("missing contract script", rm(f"{PLANNING}/skills/exec/scripts/create-branch.sh"), "lacks create-branch.sh"),
    ("missing contract prompt", rm(f"{PLANNING}/skills/exec/references/prompts/stats.md"), "lacks stats.md"),
    ("missing exec scripts dir", phase("exec", scripts="skills/exec/nope"), "is not a dir"),
    ("missing rules", rm(f"{PLANNING}/scripts/resolve-rules.sh"), "phases.exec.rules"),
    ("missing review contract script", rm("companions/revmux/scripts/task-state.sh"), "lacks task-state.sh"),
    ("reviewer without frontmatter", write(f"{PLANNING}/agents/plan-review.md", "# review\n"), "has no frontmatter"),
    ("reviewer with a bad model", write(f"{PLANNING}/agents/plan-review.md", "---\nmodel: 3\n---\n"), "`model`"),
    ("missing reviewer file", phase("plan", reviewer="agents/gone.md"), "phases.plan.reviewer"),
    ("bad requires", phase("review", requires="revmux"), "requires: expected a list"),
    ("bad autonomy", config(autonomy="yolo"), "autonomy: expected one of"),
    ("missing autonomy", without("autonomy"), "autonomy: expected one of"),
    ("unknown gate", config(gates=["design", "deploy"]), "unknown gate 'deploy'"),
    ("gates not a list", config(gates="design"), "gates: expected a list"),
    ("duplicate gate", config(gates=["plan", "plan"]), "listed twice"),
    ("bad defaults.mode", config(defaults={"mode": "branch", "leftovers": "leave"}), "defaults.mode"),
    ("bad defaults.leftovers", config(defaults={"mode": "inplace", "leftovers": "ask"}), "defaults.leftovers"),
    ("defaults not a mapping", config(defaults="worktree"), "defaults: expected a mapping"),
    ("autonomous without defaults", config(autonomy="autonomous", defaults=None), "autonomous needs it"),
    ("template not a bool", config(template="no"), "template: expected"),
    ("leftover braced token", write(f"{PLANNING}/commands/make.md", "a\nrun ${CLAUDE_PLUGIN_ROOT}/x\n"), "make.md:2"),
    ("leftover unbraced token", write(f"{PLANNING}/notes.txt", "$CLAUDE_PLUGIN_DATA/p"), "$CLAUDE_PLUGIN_DATA"),
    ("leftover skill dir token", write("companions/revmux/SKILL.md", "${CLAUDE_SKILL_DIR}"), "${CLAUDE_SKILL_DIR}"),
    ("leftover user_config token", write("companions/revmux/SKILL.md", "${user_config.key}"), "${user_config.key}"),
    (
        "leftover token in profile.md",
        lambda d: (d / "profile.md").write_text((d / "profile.md").read_text() + "${CLAUDE_PLUGIN_ROOT}\n"),
        "profile.md:",
    ),
    ("leftover token with a modifier", write("companions/revmux/SKILL.md", "${CLAUDE_PLUGIN_DATA:-/tmp}/x"), ":-/tmp}"),
    ("autonomy a list", config(autonomy=["default"]), "autonomy: expected one of"),
    ("autonomy a mapping", config(autonomy={"a": 1}), "autonomy: expected one of"),
    (
        "reviewer with invalid yaml",
        write(f"{PLANNING}/agents/plan-review.md", "---\nmodel: [x\n---\n"),
        "not valid YAML",
    ),
    ("requires with an empty name", phase("review", requires=["revmux", " "]), "requires: expected a list"),
    ("requires with a non-string", phase("review", requires=["revmux", 3]), "requires: expected a list"),
    ("schema too new", config(schema=2), "newer than this make-task"),
    ("schema too old", config(schema=0), "older than this make-task"),
    ("schema missing", without("schema"), "schema: expected 1"),
    ("schema not an int", config(schema="1"), "schema: expected 1"),
    ("invalid yaml", lambda d: (d / "profile.md").write_text("---\nschema: [1\n---\n"), "not valid YAML"),
    ("no frontmatter", lambda d: (d / "profile.md").write_text("# just a body\n"), "has no frontmatter"),
]


@pytest.mark.parametrize(("break_it", "error"), [f[1:] for f in FAILURES], ids=[f[0] for f in FAILURES])
def test_check_fails(
    mt: Callable[..., Result], profile_factory: Factory, break_it: Callable[[Path], None], error: str
) -> None:
    d = profile_factory()
    break_it(d)
    r, out = check(mt, d)
    assert r.code == 1
    assert out["ok"] is False
    assert any(error in e for e in out["errors"]), out["errors"]


def test_check_tells_how_to_raise_an_older_schema(mt: Callable[..., Result], profile_factory: Factory) -> None:
    """A user's profile: `/make-task --update` (install-default refuses an existing profile, so never reinstall);
    the template: its maintainer sets the schema."""
    older = f"schema 0 is older than this make-task reads ({MT.PROFILE_SCHEMA}); "
    d = profile_factory(config={"schema": 0})
    _, out = check(mt, d)
    fix = "run `/make-task --update`: it takes make-task's updates to the copies, then raises the schema"
    assert out["errors"] == [older + fix]
    tpl = profile_factory("tpl", config={"schema": 0, "template": True})
    _, out = check(mt, tpl)
    assert out["errors"] == [older + f"a template's schema follows PROFILE_SCHEMA: set it to {MT.PROFILE_SCHEMA}"]


def test_check_schema_error_stops_further_checks(mt: Callable[..., Result], profile_factory: Factory) -> None:
    d = profile_factory()
    reconfig(d, lambda c: c.update(schema=2, autonomy="yolo"))
    _, out = check(mt, d)
    assert len(out["errors"]) == 1
    assert "newer" in out["errors"][0]


UNREWRITTEN_HINT = " (nothing fills it in when a run reads the copy: write what it stands for)"


def test_check_reports_each_leftover_file(mt: Callable[..., Result], profile_factory: Factory) -> None:
    d = profile_factory()
    (d / PLANNING / "a.md").write_text("${CLAUDE_PLUGIN_ROOT} ${CLAUDE_PLUGIN_DATA}\n")
    (d / PLANNING / "b.md").write_text("ok\n$CLAUDE_SKILL_DIR\n")
    _, out = check(mt, d)
    hits = [e for e in out["errors"] if "unrewritten token" in e]
    assert hits == [
        "unrewritten token at companions/planning/a.md:1: ${CLAUDE_PLUGIN_ROOT}" + UNREWRITTEN_HINT,
        "unrewritten token at companions/planning/b.md:2: $CLAUDE_SKILL_DIR" + UNREWRITTEN_HINT,
    ]


def test_check_other_claude_variables_are_warnings(mt: Callable[..., Result], profile_factory: Factory) -> None:
    """A Claude Code variable make-task does not rewrite is not something a profile can fix: a warning, never an
    error, so an update that brings one in does not block every run."""
    d = profile_factory()
    (d / PLANNING / "a.md").write_text("cd $CLAUDE_PROJECT_DIR\nid ${CLAUDE_SESSION_ID}\n")
    (d / PLANNING / "b.md").write_text("$CLAUDE_PLUGIN_DATAX ${CLAUDE_PLUGIN_ROOT_DIR}\n")
    _, out = check(mt, d)
    assert out["ok"] is True, out
    assert out["warnings"] == [
        "companions/planning/a.md:1: $CLAUDE_PROJECT_DIR: make-task sets no such variable; a run reads it as written",
        "companions/planning/b.md:1: $CLAUDE_PLUGIN_DATAX: make-task sets no such variable; a run reads it as written",
    ]


def test_check_skips_binary_files_for_tokens(mt: Callable[..., Result], profile_factory: Factory) -> None:
    d = profile_factory()
    (d / PLANNING / "blob.bin").write_bytes(b"\xff\xfe${CLAUDE_PLUGIN_ROOT}\x00")
    _, out = check(mt, d)
    assert out["ok"] is True, out


def test_check_template_keeps_tokens(mt: Callable[..., Result], profile_factory: Factory) -> None:
    d = profile_factory(config={"template": True})
    (d / PLANNING / "commands" / "make.md").write_text("bash ${CLAUDE_PLUGIN_ROOT}/x $CLAUDE_PLUGIN_DATA\n")
    _, out = check(mt, d)
    assert out["ok"] is True, out


@pytest.mark.parametrize("token", ["${CLAUDE_PLUGIN_DATA:-x}", "${CLAUDE_SKILL_DIR%/}", "${user_config.key}"])
def test_check_template_fails_on_tokens_an_install_keeps(
    mt: Callable[..., Result], profile_factory: Factory, token: str
) -> None:
    """The template is checked as an install would leave it: a token the rewrite does not take fails there, not
    in every user's profile after the install."""
    d = profile_factory(config={"template": True})
    (d / PLANNING / "commands" / "make.md").write_text(f"bash ${{CLAUDE_PLUGIN_ROOT}}/x\nuse {token}\n")
    _, out = check(mt, d)
    assert out["ok"] is False
    assert out["errors"] == [f"unrewritten token at companions/planning/commands/make.md:2: {token}" + UNREWRITTEN_HINT]


def test_check_requires_missing_is_a_warning(mt: Callable[..., Result], profile_factory: Factory) -> None:
    d = profile_factory()
    reconfig(d, lambda c: c["phases"]["review"].update(requires=["revmux", "mt-test-no-such-command"]))
    r, out = check(mt, d)
    assert r.code == 0
    assert out["ok"] is True
    assert out["errors"] == []
    assert len(out["warnings"]) == 1
    assert "mt-test-no-such-command" in out["warnings"][0]
    assert "not on PATH" in out["warnings"][0]


def test_check_requires_revmux_found_on_the_stub(mt: Callable[..., Result], profile_factory: Factory) -> None:
    d = profile_factory()
    _, out = check(mt, d)
    assert out["warnings"] == []


def test_check_unknown_keys_are_warnings(mt: Callable[..., Result], profile_factory: Factory) -> None:
    d = profile_factory()
    reconfig(d, lambda c: (c.update(gate=["plan"]), c["phases"]["plan"].update(extra=1)))
    _, out = check(mt, d)
    assert out["ok"] is True
    assert any("unknown key 'gate'" in w for w in out["warnings"])
    assert any("unknown field 'extra'" in w for w in out["warnings"])


@pytest.mark.parametrize("autonomy", ["autonomous", "default", "interactive"])
def test_check_accepts_every_autonomy(mt: Callable[..., Result], profile_factory: Factory, autonomy: str) -> None:
    d = profile_factory(config={"autonomy": autonomy})
    _, out = check(mt, d)
    assert out["ok"] is True, out


def test_check_defaults_optional_unless_autonomous(mt: Callable[..., Result], profile_factory: Factory) -> None:
    d = profile_factory()
    reconfig(d, lambda c: c.pop("defaults"))
    _, out = check(mt, d)
    assert out["ok"] is True, out


def test_check_reviewer_model_is_optional(mt: Callable[..., Result], profile_factory: Factory) -> None:
    d = profile_factory()
    (d / PLANNING / "agents" / "plan-review.md").write_text("---\nname: plan-review\n---\nbody\n")
    _, out = check(mt, d)
    assert out["ok"] is True, out


def test_check_plan_reviewer_is_optional(mt: Callable[..., Result], profile_factory: Factory) -> None:
    d = profile_factory()
    reconfig(d, lambda c: c["phases"]["plan"].pop("reviewer"))
    _, out = check(mt, d)
    assert out["ok"] is True, out


def test_check_empty_gates_turn_the_preset_off(mt: Callable[..., Result], profile_factory: Factory) -> None:
    d = profile_factory(config={"autonomy": "interactive", "gates": []})
    _, out = check(mt, d)
    assert out["ok"] is True, out


def test_check_defaults_unknown_key_is_a_warning_and_init_leaves_it_out(
    mt: Callable[..., Result], profile_factory: Factory
) -> None:
    d = profile_factory(config={"defaults": {"mode": "inplace", "leftovers": "fix", "when": datetime.date(2026, 1, 1)}})
    _, out = check(mt, d)
    assert out["ok"] is True, out
    assert out["warnings"] == ["defaults: unknown key 'when' (keys: mode, leftovers)"]
    values, _ = MT.run_profile(d)
    assert values["defaults"] == {"mode": "inplace", "leftovers": "fix"}, "the run state holds only the known keys"


def test_check_requires_only_on_review(mt: Callable[..., Result], profile_factory: Factory) -> None:
    """No step acts on `requires` outside review (where a missing one counts as a failed preflight): elsewhere it is
    an unknown field, and its commands are not looked up."""
    d = profile_factory()
    reconfig(d, lambda c: c["phases"]["plan"].update(requires=["mt-test-no-such-command"]))
    _, out = check(mt, d)
    assert out["ok"] is True, out
    assert out["warnings"] == ["phases.plan: unknown field 'requires' (fields: companion, entry, reviewer)"]


def test_non_string_yaml_values_are_errors_not_tracebacks(mt: Callable[..., Result], profile_factory: Factory) -> None:
    """YAML reads `1:` as an int and `2026-01-01` as a date: status, outdated and check report them, and print them
    as text."""
    d = profile_factory()
    data, body = update_md(d)
    data["companions"][1] = {"source": "local", "version": None}
    (d / "UPDATE.md").write_text(MT.write_frontmatter(data, body))
    reconfig(d, lambda c: c.update(autonomy=datetime.date(2026, 1, 1)))
    r, st = profile(mt, "status", "--dir", str(d))
    assert r.code == 0, r.err
    assert st["autonomy"] == "2026-01-01"
    assert st["companions"]["1"]["source"] == "local"
    skipped = outdated(mt, "--dir", str(d))["skipped"]
    assert [s["id"] for s in skipped] == ["1"]
    r, chk = check(mt, d)
    assert "Traceback" not in r.err
    assert any("autonomy: expected one of" in e for e in chk["errors"]), chk


def test_empty_dir_names_no_profile(mt: Callable[..., Result], profile_factory: Factory) -> None:
    profile_factory()
    for value in ("", "  "):
        r, out = profile(mt, "check", "--dir", value)
        assert (r.code, out) == (1, None)
        assert "--dir: an empty path" in r.err


# ---------------------------------------------------------------- status


def test_status_fresh_profile(mt: Callable[..., Result], profile_factory: Factory) -> None:
    d = profile_factory()
    r, out = profile(mt, "status")
    assert r.code == 0, r.err
    assert out["name"] == "default"
    assert out["dir"] == str(d)
    assert out["template"] is False
    assert out["autonomy"] == "default"
    assert out["gates"] == []
    assert out["defaults"] == {"mode": "worktree", "leftovers": "leave"}
    assert set(out["phases"]) == {"brainstorm", "plan", "exec", "review"}
    assert set(out["companions"]) == {"brainstorm", "planning", "revmux"}
    for cid, comp in out["companions"].items():
        assert comp["source"] == f"make-task:default/{cid}"
        assert comp["version"] == MT.tree_hash(d / "upstream" / cid)
        assert comp["version"].startswith("sha256:")
        assert comp["present"] is True
        assert comp["modified"] is False


def test_status_modified_after_an_edit(mt: Callable[..., Result], profile_factory: Factory) -> None:
    d = profile_factory()
    (d / PLANNING / "commands" / "make.md").write_text("# make, my way\n")
    _, out = profile(mt, "status")
    assert out["companions"]["planning"]["modified"] is True
    assert out["companions"]["brainstorm"]["modified"] is False
    assert out["companions"]["revmux"]["modified"] is False


@pytest.mark.parametrize(
    "edit",
    [
        lambda d: (d / "companions/revmux/scripts/preflight.sh").chmod(0o644),
        lambda d: (d / "companions/revmux/extra.md").write_text("new\n"),
        lambda d: (d / "companions/revmux/SKILL.md").unlink(),
        lambda d: (d / "companions/revmux/link").symlink_to("SKILL.md"),
    ],
    ids=["exec bit", "added file", "removed file", "added symlink"],
)
def test_status_modified_kinds(
    mt: Callable[..., Result], profile_factory: Factory, edit: Callable[[Path], Any]
) -> None:
    d = profile_factory()
    edit(d)
    _, out = profile(mt, "status")
    assert out["companions"]["revmux"]["modified"] is True


def test_status_ignores_ds_store(mt: Callable[..., Result], profile_factory: Factory) -> None:
    d = profile_factory()
    (d / "companions/revmux/.DS_Store").write_bytes(b"\x00\x01")
    _, out = profile(mt, "status")
    assert out["companions"]["revmux"]["modified"] is False


def test_status_without_upstream_or_update(mt: Callable[..., Result], profile_factory: Factory) -> None:
    d = profile_factory()
    (d / "companions" / "mine").mkdir()
    (d / "companions" / "mine" / "SKILL.md").write_text("# mine\n")
    (d / "UPDATE.md").unlink()
    _, out = profile(mt, "status")
    assert out["companions"]["mine"] == {"source": None, "version": None, "present": True, "modified": None}
    assert out["companions"]["planning"]["source"] is None
    assert out["companions"]["planning"]["modified"] is False


def test_status_listed_companion_without_a_copy(mt: Callable[..., Result], profile_factory: Factory) -> None:
    d = profile_factory()
    MT.shutil.rmtree(d / "companions" / "revmux")
    _, out = profile(mt, "status")
    assert out["companions"]["revmux"]["present"] is False
    assert out["companions"]["revmux"]["modified"] is None


@pytest.mark.parametrize(
    ("config", "gates"),
    [
        ({"autonomy": "interactive"}, ["design", "plan", "review-round"]),
        ({"autonomy": "autonomous"}, []),
        ({"autonomy": "interactive", "gates": ["plan"]}, ["plan"]),
        ({"autonomy": "default", "gates": ["review-round"]}, ["review-round"]),
    ],
)
def test_status_resolves_gates(
    mt: Callable[..., Result], profile_factory: Factory, config: dict[str, Any], gates: list[str]
) -> None:
    profile_factory(config=config)
    _, out = profile(mt, "status")
    assert out["gates"] == gates


def test_status_missing_profile_fails(mt: Callable[..., Result]) -> None:
    r, out = profile(mt, "status", "--profile", "nope")
    assert r.code == 1
    assert out is None
    assert "no profile at" in r.err


def test_status_by_dir(mt: Callable[..., Result], tmp_path: Path, profile_factory: Factory) -> None:
    d = profile_factory("tpl", root=tmp_path / "templates", config={"template": True})
    _, out = profile(mt, "status", "--dir", str(d))
    assert out["name"] == "tpl"
    assert out["template"] is True


# ---------------------------------------------------------------- tree hash


def test_tree_hash_is_stable_and_content_sensitive(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    for root in (a, b):
        (root / "x" / "y").mkdir(parents=True)
        (root / "x" / "y" / "f.txt").write_text("f\n")
        (root / "g.sh").write_text("#!/bin/sh\n")
    assert MT.tree_hash(a) == MT.tree_hash(b)
    (b / "x" / "y" / "f.txt").write_text("F\n")
    assert MT.tree_hash(a) != MT.tree_hash(b)


def test_tree_hash_sees_renames(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "one").write_text("same\n")
    (b / "two").write_text("same\n")
    assert MT.tree_hash(a) != MT.tree_hash(b)


# ---------------------------------------------------------------- import: sources

TOKENS = (
    "braced root ${CLAUDE_PLUGIN_ROOT}/x\n"
    "unbraced root $CLAUDE_PLUGIN_ROOT/x\n"
    "braced data ${CLAUDE_PLUGIN_DATA}/p\n"
    "unbraced data $CLAUDE_PLUGIN_DATA\n"
    "braced skill ${CLAUDE_SKILL_DIR}/s\n"
    "unbraced skill $CLAUDE_SKILL_DIR.\n"
)
# other variables: word boundary and unknown names stay as they are
OTHER_VARS = "keep $CLAUDE_PLUGIN_DATAX $CLAUDE_PLUGIN_ROOT_DIR ${CLAUDE_PLUGIN_DATA_DIR} $CLAUDE_SKILL_DIRS\n"
BLOB = b"\x89PNG\r\n\x1a\n\xff\xfe${CLAUDE_PLUGIN_ROOT}\x00$CLAUDE_PLUGIN_DATA\n"
SOURCE_FILES = [
    ".claude-plugin/plugin.json",
    "blob.bin",
    "commands/make.md",
    "crlf.md",
    "notes.txt",
    "skills/foo/SKILL.md",
    "skills/foo/hooks/keep.md",
    "skills/foo/link.md",
    "skills/foo/references/r.md",
    "skills/foo/scripts/run.sh",
    "skills/foo/sub/SKILL.md",
]


def rewritten(root: Path, data: Path, skill: Path) -> str:
    """TOKENS after the rewrite."""
    return (
        f"braced root {root}/x\n"
        f"unbraced root {root}/x\n"
        f"braced data {data}/p\n"
        f"unbraced data {data}\n"
        f"braced skill {skill}/s\n"
        f"unbraced skill {skill}.\n"
    )


def make_source(root: Path, *, plugin_root_token: bool = True, git_dir: bool = True) -> Path:
    """A plugin-shaped tree: every token form, an executable, a binary, a CRLF file, a symlink, excluded paths."""
    tokens = TOKENS if plugin_root_token else TOKENS.replace("ROOT}", "DATA}").replace("ROOT/", "DATA/")
    files = {
        ".claude-plugin/plugin.json": '{"name": "foo"}\n',
        "commands/make.md": tokens + OTHER_VARS,  # no SKILL.md above it: the skill dir is the companion root
        "notes.txt": OTHER_VARS,
        "skills/foo/SKILL.md": tokens,
        "skills/foo/references/r.md": "${CLAUDE_SKILL_DIR}/r\n",  # the nearest SKILL.md is in skills/foo
        "skills/foo/sub/SKILL.md": "${CLAUDE_SKILL_DIR}\n",  # its own dir
        "skills/foo/hooks/keep.md": "a hooks dir below the root is content\n",
        # excluded
        ".in_use/4242": "",
        "hooks/hooks.json": '{"hooks": {"x": "/Users/someone/abs"}}\n',
    }
    if git_dir:
        files[".git/HEAD"] = "ref: refs/heads/main\n"
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    (root / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1")
    (root / "skills" / "foo" / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1")
    (root / "crlf.md").write_bytes(b"line ${CLAUDE_PLUGIN_DATA}\r\nnext\r\n")
    write_exec(
        root / "skills" / "foo" / "scripts" / "run.sh", "#!/bin/sh\necho ${CLAUDE_SKILL_DIR} $CLAUDE_PLUGIN_DATA\n"
    )
    (root / "blob.bin").write_bytes(BLOB)
    (root / "skills" / "foo" / "link.md").symlink_to("../../notes.txt")
    return root


def assert_copy(d: Path, cid: str, *, plugin_root_token: bool = True) -> None:
    """companions/<cid> is make_source() rewritten for d, and upstream/<cid> equals it."""
    comp, up, data = d / "companions" / cid, d / "upstream" / cid, d / "data" / cid
    assert sorted(rel for rel, _ in MT.tree_files(comp)) == SOURCE_FILES
    for gone in (".in_use", "hooks", ".git", ".DS_Store", "skills/foo/.DS_Store"):
        assert not (comp / gone).exists(), gone
        assert not (up / gone).exists(), gone
    assert MT.tree_hash(comp) == MT.tree_hash(up)
    for rel in SOURCE_FILES:
        assert (comp / rel).read_bytes() == (up / rel).read_bytes(), rel
    expected = rewritten(comp, data, comp)
    skill_expected = rewritten(comp, data, comp / "skills" / "foo")
    if not plugin_root_token:
        expected = expected.replace(f"root {comp}/x", f"root {data}/x")
        skill_expected = skill_expected.replace(f"root {comp}/x", f"root {data}/x")
    assert (comp / "commands/make.md").read_text() == expected + OTHER_VARS
    assert (comp / "skills/foo/SKILL.md").read_text() == skill_expected
    assert (comp / "skills/foo/references/r.md").read_text() == f"{comp}/skills/foo/r\n"
    assert (comp / "skills/foo/sub/SKILL.md").read_text() == f"{comp}/skills/foo/sub\n"
    assert (comp / "skills/foo/scripts/run.sh").read_text() == f"#!/bin/sh\necho {comp}/skills/foo {data}\n"
    assert (comp / "crlf.md").read_bytes() == f"line {data}\r\nnext\r\n".encode()
    assert (comp / "blob.bin").read_bytes() == BLOB, "a binary file is copied byte for byte"
    link = comp / "skills/foo/link.md"
    assert not link.is_symlink() and link.read_text() == OTHER_VARS, "a symlink is copied as its file"
    for tree in (comp, up):
        assert MT.os.access(tree / "skills/foo/scripts/run.sh", MT.os.X_OK), "the exec bit is kept"
        assert not MT.os.access(tree / "commands/make.md", MT.os.X_OK)
    assert data.is_dir() and not any(data.iterdir()), "data/<id> starts empty"


def bare_profile(isolate: Env, name: str = "p", *, template: bool = False) -> Path:
    """A profile dir holding only profile.md (import needs no more)."""
    d = isolate.config_home / "profiles" / name
    d.mkdir(parents=True)
    config = {"schema": MT.PROFILE_SCHEMA, "template": template, "autonomy": "default", "phases": {}}
    (d / "profile.md").write_text(MT.write_frontmatter(config, "\n"))
    return d


def add_plugin(isolate: Env, key: str, path: Path, **fields: Any) -> None:
    """Add an installed_plugins.json entry for key at path (user scope)."""
    data = json.loads(isolate.plugins_json.read_text())
    data["plugins"].setdefault(key, []).append({"scope": "user", "installPath": str(path), **fields})
    isolate.plugins_json.write_text(json.dumps(data))


def update_md(d: Path) -> tuple[dict[str, Any], str]:
    data, body = MT.read_frontmatter((d / "UPDATE.md").read_text())
    assert data is not None
    return data, body


def no_leftovers(d: Path, cid: str) -> None:
    """A failed import leaves no companion, no upstream and no work dir."""
    assert not (d / "companions" / cid).exists()
    assert not (d / "upstream" / cid).exists()
    assert not [p for p in d.iterdir() if p.name.startswith(".import-")]


SHA = "dd01fa7f181528b564d86ce2f48c8e47a4b99011"


def test_import_plugin_source(mt: Callable[..., Result], isolate: Env) -> None:
    d = bare_profile(isolate)
    src = make_source(isolate.plugins_dir / "cache" / "market" / "foo" / "1.2.0")
    add_plugin(isolate, "foo@market", src, version="1.2.0", gitCommitSha=SHA)
    r, out = profile(mt, "import", "foo", "--source", "plugin:foo@market", "--profile", "p")
    assert r.code == 0, r.err
    comp = d / "companions" / "foo"
    assert out == {
        "id": "foo",
        "source": "plugin:foo@market",
        "version": "1.2.0+dd01fa7f1815",
        "dir": str(comp),
        "replaced": False,
    }
    assert_copy(d, "foo")
    data, body = update_md(d)
    assert data == {
        "rewritten_for": str(d),
        "companions": {"foo": {"source": "plugin:foo@market", "version": "1.2.0+dd01fa7f1815"}},
    }
    assert body == MT.UPDATE_BODY, "a new UPDATE.md gets the empty sections"
    assert "## Preferences" in body and "## Changes" in body and "## Wanted" in body
    _, st = profile(mt, "status", "--profile", "p")
    assert st["companions"]["foo"] == {
        "source": "plugin:foo@market",
        "version": "1.2.0+dd01fa7f1815",
        "present": True,
        "modified": False,
    }


def test_import_plugin_from_the_fixture_plugins_json(mt: Callable[..., Result], isolate: Env) -> None:
    d = bare_profile(isolate)
    make_source(isolate.planning_root)
    r, out = profile(mt, "import", "planning", "--source", "plugin:planning@umputun-cc-thingz", "--profile", "p")
    assert r.code == 0, r.err
    assert out["version"] == "3.8.4", "no gitCommitSha recorded: the version alone"
    comp = d / "companions" / "planning"
    assert (comp / "skills/exec/scripts/detect-branch.sh").is_file()
    assert MT.os.access(comp / "skills/exec/scripts/detect-branch.sh", MT.os.X_OK)
    assert MT.tree_hash(comp) == MT.tree_hash(d / "upstream" / "planning")


@pytest.mark.parametrize(
    ("entry", "version"),
    [
        ({"version": "1.0.0", "gitCommitSha": SHA}, "1.0.0+dd01fa7f1815"),
        ({"version": "1.0.0"}, "1.0.0"),
        ({"gitCommitSha": SHA}, "dd01fa7f1815"),
        ({"version": 2}, "2"),
    ],
)
def test_plugin_version(tmp_path: Path, entry: dict[str, Any], version: str) -> None:
    assert MT.plugin_version(entry, tmp_path) == version


def test_plugin_version_without_version_or_sha_is_the_tree_hash(tmp_path: Path) -> None:
    (tmp_path / "f").write_text("f\n")
    assert MT.plugin_version({}, tmp_path) == MT.tree_hash(tmp_path)


def test_import_plugin_subtree(mt: Callable[..., Result], isolate: Env) -> None:
    d = bare_profile(isolate)
    plugin = isolate.plugins_dir / "cache" / "rx" / "rx" / "0.5.5"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text("{}\n")
    (plugin / "main.go").write_text("package main\n")
    make_source(plugin / ".claude-plugin" / "skills" / "rx", plugin_root_token=False, git_dir=False)
    add_plugin(isolate, "rx@rx", plugin, version="0.5.5", gitCommitSha=SHA)
    r, out = profile(mt, "import", "rx", "--source", "plugin:rx@rx:.claude-plugin/skills/rx", "--profile", "p")
    assert r.code == 0, r.err
    assert out["version"] == "0.5.5+dd01fa7f1815"
    assert_copy(d, "rx", plugin_root_token=False)
    comp = d / "companions" / "rx"
    assert not (comp / "main.go").exists() and not (comp / "skills" / "rx").exists(), "only the subtree is copied"
    assert update_md(d)[0]["companions"]["rx"]["source"] == "plugin:rx@rx:.claude-plugin/skills/rx"


@pytest.mark.parametrize("token", ["${CLAUDE_PLUGIN_ROOT}", "$CLAUDE_PLUGIN_ROOT"])
def test_import_plugin_subtree_with_plugin_root_fails(mt: Callable[..., Result], isolate: Env, token: str) -> None:
    d = bare_profile(isolate)
    plugin = isolate.plugins_dir / "cache" / "rx" / "rx" / "0.5.5"
    skill = plugin / "skills" / "rx"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(f"# rx\n\nrun {token}/bin/x\n")
    add_plugin(isolate, "rx@rx", plugin, version="0.5.5")
    r, out = profile(mt, "import", "rx", "--source", "plugin:rx@rx:skills/rx", "--profile", "p")
    assert r.code == 1
    assert out is None
    assert f"SKILL.md:3: {token}" in r.err
    assert "plugin root" in r.err
    no_leftovers(d, "rx")
    assert not (d / "UPDATE.md").exists()


def test_import_plugin_subtree_check_applies_to_a_template(mt: Callable[..., Result], isolate: Env) -> None:
    d = bare_profile(isolate, template=True)
    plugin = isolate.plugins_dir / "cache" / "rx" / "rx" / "0.5.5"
    (plugin / "skills" / "rx").mkdir(parents=True)
    (plugin / "skills" / "rx" / "SKILL.md").write_text("${CLAUDE_PLUGIN_ROOT}\n")
    add_plugin(isolate, "rx@rx", plugin, version="0.5.5")
    r, _ = profile(mt, "import", "rx", "--source", "plugin:rx@rx:skills/rx", "--profile", "p")
    assert r.code == 1
    assert "plugin root" in r.err
    no_leftovers(d, "rx")


def test_import_plugin_root_token_in_the_whole_plugin_is_fine(mt: Callable[..., Result], isolate: Env) -> None:
    d = bare_profile(isolate)
    plugin = isolate.plugins_dir / "cache" / "m" / "x" / "1.0.0"
    (plugin / "skills" / "x").mkdir(parents=True)
    (plugin / "skills" / "x" / "SKILL.md").write_text("${CLAUDE_PLUGIN_ROOT}\n")
    add_plugin(isolate, "x@m", plugin, version="1.0.0")
    for spec in ("plugin:x@m", "plugin:x@m:", "plugin:x@m:."):
        r, _ = profile(mt, "import", "x", "--source", spec, "--profile", "p", "--replace")
        assert r.code == 0, (spec, r.err)
        assert (d / "companions/x/skills/x/SKILL.md").read_text() == f"{d / 'companions' / 'x'}\n"


@pytest.mark.parametrize(
    ("spec", "error"),
    [
        ("plugin:other@market", "is not installed"),  # its installPath does not exist
        ("plugin:nope@market", "is not installed"),
        ("plugin:planning@umputun-cc-thingz:no/such/dir", "is not a directory"),
    ],
)
def test_import_plugin_failures(mt: Callable[..., Result], isolate: Env, spec: str, error: str) -> None:
    d = bare_profile(isolate)
    r, out = profile(mt, "import", "x", "--source", spec, "--profile", "p")
    assert r.code == 1
    assert out is None
    assert error in r.err
    no_leftovers(d, "x")


def git_source(tmp_path: Path, sub: str = "plugins/x") -> tuple[Path, str]:
    """A local repo with make_source() under sub, committed on main; (repo, HEAD)."""
    repo = tmp_path / "src-repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("outside the subpath\n")
    make_source(repo / sub, git_dir=False)
    git(repo, "add", "-A", "-f")
    git(repo, "commit", "-q", "-m", "plugin")
    return repo, git(repo, "rev-parse", "HEAD")


def test_import_git_source(
    mt: Callable[..., Result], isolate: Env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d = bare_profile(isolate)
    repo, head = git_source(tmp_path)
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    monkeypatch.setattr(MT.tempfile, "tempdir", str(tmp))
    spec = f"git:file://{repo}#main:plugins/x"
    r, out = profile(mt, "import", "x", "--source", spec, "--profile", "p")
    assert r.code == 0, r.err
    assert out["version"] == head
    assert_copy(d, "x")  # a git subpath is the plugin root: ${CLAUDE_PLUGIN_ROOT} is rewritten, not refused
    assert update_md(d)[0]["companions"]["x"] == {"source": spec, "version": head}
    assert not (d / "companions" / "x" / "README.md").exists()
    assert list(tmp.iterdir()) == [], "the clone is removed"


def test_import_git_whole_repo_leaves_out_the_git_dir(mt: Callable[..., Result], isolate: Env, tmp_path: Path) -> None:
    d = bare_profile(isolate)
    repo, head = git_source(tmp_path, sub=".")
    r, out = profile(mt, "import", "x", "--source", f"git:file://{repo}#main", "--profile", "p")
    assert r.code == 0, r.err
    assert out["version"] == head
    comp = d / "companions" / "x"
    assert not (comp / ".git").exists()
    assert (comp / "README.md").is_file()
    assert sorted(rel for rel, _ in MT.tree_files(comp)) == sorted([*SOURCE_FILES, "README.md"])


def test_import_git_tag_records_the_commit(mt: Callable[..., Result], isolate: Env, tmp_path: Path) -> None:
    d = bare_profile(isolate)
    repo, head = git_source(tmp_path)
    git(repo, "tag", "-a", "v1", "-m", "release")
    (repo / "plugins" / "x" / "notes.txt").write_text("after the tag\n")
    git(repo, "commit", "-q", "-am", "later")
    r, out = profile(mt, "import", "x", "--source", f"git:file://{repo}#v1:plugins/x", "--profile", "p")
    assert r.code == 0, r.err
    assert out["version"] == head, "an annotated tag records the commit it points to"
    assert (d / "companions/x/notes.txt").read_text() == OTHER_VARS


@pytest.mark.parametrize(("ref", "error"), [("nope", "git fetch"), ("main:no/such", "is not a directory")])
def test_import_git_failures(mt: Callable[..., Result], isolate: Env, tmp_path: Path, ref: str, error: str) -> None:
    d = bare_profile(isolate)
    repo, _ = git_source(tmp_path)
    r, out = profile(mt, "import", "x", "--source", f"git:file://{repo}#{ref}", "--profile", "p")
    assert r.code == 1
    assert out is None
    assert error in r.err
    no_leftovers(d, "x")


def template_foo(isolate: Env) -> Path:
    """A fixture template <MT.PROFILE_TEMPLATES>/default whose companions/foo is make_source(); that dir."""
    return make_source(isolate.templates / "default" / "companions" / "foo", git_dir=False)


def test_import_make_task_source(mt: Callable[..., Result], isolate: Env) -> None:
    foo = template_foo(isolate)
    d = bare_profile(isolate)
    r, out = profile(mt, "import", "foo", "--source", "make-task:default/foo", "--profile", "p")
    assert r.code == 0, r.err
    version = MT.tree_hash(foo)
    assert out["version"] == version and version.startswith("sha256:")
    assert_copy(d, "foo")
    assert update_md(d)[0]["companions"]["foo"] == {"source": "make-task:default/foo", "version": version}


def test_import_make_task_missing_companion_fails(mt: Callable[..., Result], isolate: Env) -> None:
    template_foo(isolate)
    d = bare_profile(isolate)
    r, _ = profile(mt, "import", "bar", "--source", "make-task:default/bar", "--profile", "p")
    assert r.code == 1
    assert "does not exist" in r.err
    no_leftovers(d, "bar")


# ---------------------------------------------------------------- import: copy rules


def test_import_into_a_template_keeps_tokens_raw(mt: Callable[..., Result], isolate: Env, tmp_path: Path) -> None:
    d = bare_profile(isolate, template=True)
    src = make_source(tmp_path / "foo")
    add_plugin(isolate, "foo@market", src, version="1.0.0")
    r, _ = profile(mt, "import", "foo", "--source", "plugin:foo@market", "--dir", str(d))
    assert r.code == 0, r.err
    comp = d / "companions" / "foo"
    assert (comp / "commands/make.md").read_text() == TOKENS + OTHER_VARS
    assert (comp / "skills/foo/scripts/run.sh").read_text() == (src / "skills/foo/scripts/run.sh").read_text()
    assert (comp / "crlf.md").read_bytes() == (src / "crlf.md").read_bytes()
    assert MT.tree_hash(comp) == MT.tree_hash(d / "upstream" / "foo")
    assert not (comp / "hooks").exists(), "the exclusions still apply"
    assert not (d / "data").exists(), "a template has no data dir"


def test_import_binary_file_is_copied_byte_for_byte(mt: Callable[..., Result], isolate: Env, tmp_path: Path) -> None:
    d = bare_profile(isolate)
    src = tmp_path / "bin-src"
    src.mkdir()
    blob = bytes(range(256)) + b"${CLAUDE_PLUGIN_DATA}"
    (src / "image.png").write_bytes(blob)
    add_plugin(isolate, "b@m", src, version="1")
    r, _ = profile(mt, "import", "b", "--source", "plugin:b@m", "--profile", "p")
    assert r.code == 0, r.err
    assert (d / "companions/b/image.png").read_bytes() == blob
    assert (d / "upstream/b/image.png").read_bytes() == blob


@pytest.mark.parametrize(
    ("make_link", "error"),
    [
        (lambda src, out: (src / "leak.md").symlink_to(out / "secret.md"), "outside the source"),
        (lambda src, out: (src / "sub" / "up.md").symlink_to("../../secret.md"), "outside the source"),
        (lambda src, out: (src / "dangling.md").symlink_to("nothing.md"), "does not exist"),
        (lambda src, out: (src / "sub" / "loop").symlink_to(".."), "loops"),
    ],
    ids=["absolute outside", "relative outside", "dangling", "loop"],
)
def test_import_bad_symlink_fails(
    mt: Callable[..., Result], isolate: Env, tmp_path: Path, make_link: Callable[[Path, Path], Any], error: str
) -> None:
    d = bare_profile(isolate)
    out = tmp_path / "out"
    out.mkdir()
    (out / "secret.md").write_text("secret\n")
    (tmp_path / "secret.md").write_text("secret\n")
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "SKILL.md").write_text("# s\n")
    make_link(src, out)
    add_plugin(isolate, "s@m", src, version="1")
    r, _ = profile(mt, "import", "s", "--source", "plugin:s@m", "--profile", "p")
    assert r.code == 1
    assert error in r.err
    no_leftovers(d, "s")


def test_import_symlinked_dir_inside_is_copied_as_a_dir(
    mt: Callable[..., Result], isolate: Env, tmp_path: Path
) -> None:
    d = bare_profile(isolate)
    src = tmp_path / "src"
    (src / "real").mkdir(parents=True)
    (src / "real" / "SKILL.md").write_text("${CLAUDE_SKILL_DIR}\n")
    write_exec(src / "real" / "x.sh", "#!/bin/sh\n")
    (src / "alias").symlink_to("real")
    add_plugin(isolate, "s@m", src, version="1")
    r, _ = profile(mt, "import", "s", "--source", "plugin:s@m", "--profile", "p")
    assert r.code == 0, r.err
    comp = d / "companions" / "s"
    assert (comp / "alias").is_dir() and not (comp / "alias").is_symlink()
    assert (comp / "alias" / "SKILL.md").read_text() == f"{comp / 'alias'}\n", "rewritten for the copy's location"
    assert (comp / "real" / "SKILL.md").read_text() == f"{comp / 'real'}\n"
    assert MT.os.access(comp / "alias" / "x.sh", MT.os.X_OK)


def test_nearest_skill_dir() -> None:
    dirs = {"", "skills/a", "skills/a/b"}
    assert MT.nearest_skill_dir("skills/a/b/c/x.md", dirs) == "skills/a/b"
    assert MT.nearest_skill_dir("skills/a/x.md", dirs) == "skills/a"
    assert MT.nearest_skill_dir("skills/a/SKILL.md", dirs) == "skills/a"
    assert MT.nearest_skill_dir("skills/z/x.md", dirs) == ""
    assert MT.nearest_skill_dir("top.md", {"skills/a"}) == ""


# ---------------------------------------------------------------- import: records and replace


def test_import_refuses_an_existing_companion(mt: Callable[..., Result], isolate: Env, tmp_path: Path) -> None:
    d = bare_profile(isolate)
    src = make_source(tmp_path / "foo")
    add_plugin(isolate, "foo@market", src, version="1.0.0")
    assert profile(mt, "import", "foo", "--source", "plugin:foo@market", "--profile", "p")[0].code == 0
    (d / "companions/foo/commands/make.md").write_text("mine\n")
    r, out = profile(mt, "import", "foo", "--source", "plugin:foo@market", "--profile", "p")
    assert r.code == 1
    assert out is None
    assert "--replace" in r.err
    assert (d / "companions/foo/commands/make.md").read_text() == "mine\n", "the copy is untouched"


@pytest.mark.parametrize("tree", ["companions", "upstream"])
def test_import_refuses_when_either_tree_exists(
    mt: Callable[..., Result], isolate: Env, tmp_path: Path, tree: str
) -> None:
    d = bare_profile(isolate)
    (d / tree / "foo").mkdir(parents=True)
    add_plugin(isolate, "foo@market", make_source(tmp_path / "foo"), version="1.0.0")
    r, _ = profile(mt, "import", "foo", "--source", "plugin:foo@market", "--profile", "p")
    assert r.code == 1
    assert "--replace" in r.err


def test_import_replace(mt: Callable[..., Result], isolate: Env, profile_factory: Factory, tmp_path: Path) -> None:
    d = profile_factory()
    (d / "UPDATE.md").write_text((d / "UPDATE.md").read_text() + "- my change\n")
    before, body = update_md(d)
    src = tmp_path / "rx"
    write_exec(src / "scripts" / "preflight.sh", "#!/bin/sh\necho ${CLAUDE_SKILL_DIR}\n")
    write_exec(src / "scripts" / "task-state.sh", "#!/bin/sh\n")
    (src / "SKILL.md").write_text("# rx 2\n$CLAUDE_PLUGIN_DATA\n")
    add_plugin(isolate, "rx@rx", src, version="0.6.0", gitCommitSha=SHA)
    (d / "companions" / "revmux" / "stale.md").write_text("old\n")
    r, out = profile(mt, "import", "revmux", "--source", "plugin:rx@rx", "--replace")
    assert r.code == 0, r.err
    assert out["replaced"] is True
    comp = d / "companions" / "revmux"
    assert not (comp / "stale.md").exists(), "the old copy is gone"
    assert (comp / "SKILL.md").read_text() == f"# rx 2\n{d / 'data' / 'revmux'}\n"
    assert (comp / "scripts/preflight.sh").read_text() == f"#!/bin/sh\necho {comp}\n"
    after, body2 = update_md(d)
    assert body2 == body, "UPDATE.md's body is kept"
    assert after["companions"]["revmux"] == {"source": "plugin:rx@rx", "version": "0.6.0+dd01fa7f1815"}
    assert after["companions"]["planning"] == before["companions"]["planning"], "other records are kept"
    _, chk = check(mt, d)
    assert chk["ok"] is True, chk
    _, st = profile(mt, "status")
    assert st["companions"]["revmux"]["modified"] is False
    assert not [p for p in d.iterdir() if p.name.startswith(".import-")]


def test_import_adds_frontmatter_to_an_update_md_without_one(
    mt: Callable[..., Result], isolate: Env, tmp_path: Path
) -> None:
    d = bare_profile(isolate)
    (d / "UPDATE.md").write_text("## Preferences\n\nterse\n")
    add_plugin(isolate, "foo@market", make_source(tmp_path / "foo"), version="1.0.0")
    assert profile(mt, "import", "foo", "--source", "plugin:foo@market", "--profile", "p")[0].code == 0
    data, body = update_md(d)
    assert data == {"rewritten_for": str(d), "companions": {"foo": {"source": "plugin:foo@market", "version": "1.0.0"}}}
    assert body == "## Preferences\n\nterse\n"


def test_import_local(mt: Callable[..., Result], isolate: Env) -> None:
    d = bare_profile(isolate)
    r, _ = profile(mt, "import", "mine", "--source", "local", "--profile", "p")
    assert r.code == 1
    assert "does not exist" in r.err
    (d / "companions" / "mine").mkdir(parents=True)
    (d / "companions" / "mine" / "SKILL.md").write_text("# mine ${CLAUDE_SKILL_DIR}\n")
    r, out = profile(mt, "import", "mine", "--source", "local", "--profile", "p")
    assert r.code == 0, r.err
    assert out == {
        "id": "mine",
        "source": "local",
        "version": None,
        "dir": str(d / "companions/mine"),
        "replaced": False,
    }
    assert update_md(d)[0]["companions"]["mine"] == {"source": "local", "version": None}
    assert (d / "companions/mine/SKILL.md").read_text() == "# mine ${CLAUDE_SKILL_DIR}\n", "local files are not touched"
    assert not (d / "upstream" / "mine").exists()
    r, _ = profile(mt, "import", "mine", "--source", "local", "--profile", "p")
    assert r.code == 0, "recording local again is fine"


def test_import_local_over_a_sourced_companion_needs_replace(
    mt: Callable[..., Result], profile_factory: Factory
) -> None:
    d = profile_factory()
    r, _ = profile(mt, "import", "revmux", "--source", "local")
    assert r.code == 1
    assert "--replace" in r.err
    assert (d / "upstream" / "revmux").is_dir()
    r, out = profile(mt, "import", "revmux", "--source", "local", "--replace")
    assert r.code == 0, r.err
    assert out["replaced"] is True
    assert not (d / "upstream" / "revmux").exists()
    assert (d / "companions" / "revmux" / "SKILL.md").is_file()
    _, st = profile(mt, "status")
    assert st["companions"]["revmux"] == {"source": "local", "version": None, "present": True, "modified": None}


@pytest.mark.parametrize("cid", ["../x", "a/b", ".hidden"])
def test_import_refuses_a_bad_companion_id(mt: Callable[..., Result], isolate: Env, cid: str) -> None:
    d = bare_profile(isolate)
    r, _ = profile(mt, "import", cid, "--source", "plugin:planning@umputun-cc-thingz", "--profile", "p")
    assert r.code == 1
    assert "companion id" in r.err
    assert not (d / "companions").exists()


def test_import_needs_a_profile(mt: Callable[..., Result]) -> None:
    r, _ = profile(mt, "import", "x", "--source", "plugin:planning@umputun-cc-thingz", "--profile", "nope")
    assert r.code == 1
    assert "no profile at" in r.err


def test_import_needs_a_source(mt: Callable[..., Result], isolate: Env) -> None:
    bare_profile(isolate)
    r = mt("profile", "import", "x", "--profile", "p")
    assert r.code == 2
    assert "--source" in r.err


def test_import_then_check(mt: Callable[..., Result], isolate: Env, profile_factory: Factory, tmp_path: Path) -> None:
    """What import leaves, check accepts: the path variables are rewritten and other $CLAUDE_ variables are
    warnings; a path variable in a form the rewrite does not take is an error that names it."""
    d = profile_factory()
    src = make_source(tmp_path / "foo")
    (src / "commands" / "extra.md").write_text("cd $CLAUDE_PROJECT_DIR\n")
    add_plugin(isolate, "foo@market", src, version="1.0.0")
    r, _ = profile(mt, "import", "foo", "--source", "plugin:foo@market")
    assert r.code == 0, r.err
    assert update_md(d)[0]["rewritten_for"] == str(d), "the first rewritten import records the dir"
    _, out = check(mt, d)
    assert out["ok"] is True, out
    warned = {w.split(":")[0] for w in out["warnings"]}
    others = {"commands/extra.md", "commands/make.md", "notes.txt", "skills/foo/link.md"}  # link.md: notes.txt's copy
    assert warned == {f"companions/foo/{rel}" for rel in others}
    (src / "commands" / "extra.md").write_text("cat ${CLAUDE_PLUGIN_DATA:-/tmp}/x\n")
    r, _ = profile(mt, "import", "foo", "--source", "plugin:foo@market", "--replace")
    assert r.code == 0, r.err
    _, out = check(mt, d)
    assert out["errors"] == [
        "unrewritten token at companions/foo/commands/extra.md:1: ${CLAUDE_PLUGIN_DATA:-/tmp}" + UNREWRITTEN_HINT
    ]


@pytest.mark.parametrize("update", [b"---\na: [1\n---\n", b"---\ncompanions: {}\n---\n\xff\xfe"], ids=["yaml", "utf-8"])
def test_import_with_an_unreadable_update_md_changes_nothing(
    mt: Callable[..., Result], isolate: Env, tmp_path: Path, update: bytes
) -> None:
    """The record is written last: an UPDATE.md it cannot read stops the import before any tree is copied or
    replaced, so no companion is left without a record."""
    d = bare_profile(isolate)
    src = make_source(tmp_path / "foo")
    add_plugin(isolate, "foo@market", src, version="1.0.0")
    assert profile(mt, "import", "foo", "--source", "plugin:foo@market", "--profile", "p")[0].code == 0
    before = MT.tree_hash(d / "companions"), MT.tree_hash(d / "upstream")
    (d / "UPDATE.md").write_bytes(update)
    r, _ = profile(mt, "import", "bar", "--source", "plugin:foo@market", "--profile", "p")
    assert r.code == 1 and "UPDATE.md" in r.err
    assert "Traceback" not in r.err
    no_leftovers(d, "bar")
    (src / "notes.txt").write_text("a newer notes.txt a replace would copy\n")
    r, _ = profile(mt, "import", "foo", "--source", "plugin:foo@market", "--replace", "--profile", "p")
    assert r.code == 1 and "UPDATE.md" in r.err
    assert (MT.tree_hash(d / "companions"), MT.tree_hash(d / "upstream")) == before, "the old trees stay"
    assert not [p for p in d.iterdir() if p.name.startswith(".import-")]


def test_import_subpath_symlink_out_of_the_source_fails(
    mt: Callable[..., Result], isolate: Env, tmp_path: Path
) -> None:
    """A subpath that is itself a symlink out of the plugin or the clone is refused: the copy's own symlink check
    only sees what is under the subpath."""
    d = bare_profile(isolate)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("not the plugin's\n")
    plug = tmp_path / "plug"
    plug.mkdir()
    (plug / "sub").symlink_to(outside)
    add_plugin(isolate, "foo@market", plug, version="1.0.0")
    r, _ = profile(mt, "import", "foo", "--source", "plugin:foo@market:sub", "--profile", "p")
    assert r.code == 1 and "outside the source" in r.err
    no_leftovers(d, "foo")

    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("x\n")
    (repo / "plug").symlink_to(outside)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "a symlinked subpath")
    r, _ = profile(mt, "import", "bar", "--source", f"git:file://{repo}#main:plug", "--profile", "p")
    assert r.code == 1 and "outside the source" in r.err
    no_leftovers(d, "bar")


def test_import_refuses_a_fifo(mt: Callable[..., Result], isolate: Env, tmp_path: Path) -> None:
    d = bare_profile(isolate)
    src = make_source(tmp_path / "foo")
    os.mkfifo(src / "pipe")
    add_plugin(isolate, "foo@market", src, version="1.0.0")
    r, _ = profile(mt, "import", "foo", "--source", "plugin:foo@market", "--profile", "p")
    assert r.code == 1 and "pipe: not a regular file, directory or symlink" in r.err
    no_leftovers(d, "foo")


def test_git_fetch_timeout_is_an_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def hang(argv: list[str], **kwargs: Any) -> Any:
        raise MT.subprocess.TimeoutExpired(argv, kwargs.get("timeout") or 0)

    monkeypatch.setattr(MT.subprocess, "run", hang)
    with pytest.raises(MT.MtError, match=r"git init file:///nowhere#main timed out after 300 s"):
        MT.git_fetch("file:///nowhere", "main", tmp_path)


# ---------------------------------------------------------------- source specs


@pytest.mark.parametrize(
    ("text", "kind", "where", "ref", "subpath"),
    [
        ("local", "local", "", "", ""),
        ("make-task:default/planning", "make-task", "default/planning", "", ""),
        ("plugin:planning@umputun-cc-thingz", "plugin", "planning@umputun-cc-thingz", "", ""),
        (
            "plugin:revmux@revmux:.claude-plugin/skills/revmux",
            "plugin",
            "revmux@revmux",
            "",
            ".claude-plugin/skills/revmux",
        ),
        ("plugin:x@m:./a//b/", "plugin", "x@m", "", "a/b"),
        ("plugin:x@m:.", "plugin", "x@m", "", ""),
        ("git:https://github.com/o/r.git#main", "git", "https://github.com/o/r.git", "main", ""),
        ("git:https://github.com/o/r.git#v1.2:plugins/x", "git", "https://github.com/o/r.git", "v1.2", "plugins/x"),
        ("git:git@github.com:org/repo.git#main:plugins/x", "git", "git@github.com:org/repo.git", "main", "plugins/x"),
        ("git:git@github.com:org/repo.git#main", "git", "git@github.com:org/repo.git", "main", ""),
        ("git:ssh://git@host:2222/org/repo.git#dev:a/b", "git", "ssh://git@host:2222/org/repo.git", "dev", "a/b"),
        ("git:file:///tmp/r#feature/x:sub", "git", "file:///tmp/r", "feature/x", "sub"),
    ],
)
def test_parse_source(text: str, kind: str, where: str, ref: str, subpath: str) -> None:
    assert MT.parse_source(text) == MT.Source(kind, text, where, ref, subpath)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "local:x",
        "foo:bar",
        "make-task:default",
        "make-task:../x/y",
        "make-task:default/a/b",
        "plugin:noat",
        "plugin:@m",
        "plugin:x@",
        "plugin:x@m:../up",
        "plugin:x@m:/abs",
        "git:https://h/r.git",
        "git:#main",
        "git:https://h/r.git#",
        "git:https://h/r.git#:sub",
        "git:-oProxyCommand=x#main",
        "git:https://h/r.git#-x",
        "git:https://h/r.git#main:../up",
    ],
)
def test_parse_source_rejects(text: str) -> None:
    with pytest.raises(MT.MtError, match="source"):
        MT.parse_source(text)


def test_import_rejects_a_bad_source(mt: Callable[..., Result], isolate: Env) -> None:
    d = bare_profile(isolate)
    r, out = profile(mt, "import", "x", "--source", "svn:somewhere", "--profile", "p")
    assert r.code == 1
    assert out is None
    assert "expected make-task:" in r.err
    no_leftovers(d, "x")


# ---------------------------------------------------------------- install-default

LICENSE_TEXT = "MIT License\n\nCopyright (c) a companion author\n"
# one file per companion with raw tokens, as the shipped template keeps them
TEMPLATE_TOKENS = {
    "planning/commands/make.md": (
        "# make\nrun ${CLAUDE_PLUGIN_ROOT}/skills/exec/scripts/init-progress.sh\nprompts $CLAUDE_PLUGIN_DATA/prompts\n"
    ),
    "brainstorm/skills/brainstorm/SKILL.md": "# brainstorm\nbash ${CLAUDE_SKILL_DIR}/notes.md\n",
    "revmux/SKILL.md": "# revmux\n$CLAUDE_SKILL_DIR/scripts/preflight.sh\n",
}
TEMPLATE_SCRIPT = "planning/skills/exec/scripts/init-progress.sh"
TEMPLATE_BODY = "\n# default profile\n\n## plan\n\nKeep plans short.\n"
TEMPLATE_CHANGE = "- planning: make.md asks no questions"
INSTALLED = {"profile.md", "UPDATE.md", "LICENSES", "companions", "upstream", "data"}


@pytest.fixture
def template(isolate: Env, profile_factory: Factory) -> Path:
    """A fixture template <MT.PROFILE_TEMPLATES>/default (what install-default and make-task: sources read).

    The conftest profile with `template: true`, raw tokens in one file per companion and in an executable
    script, a body with a `## plan` section, LICENSES/<id>, Finder files; its own upstream/ differs from
    companions/ and its UPDATE.md lists plugin sources and a make-task change: neither may reach an install.
    """
    tpl = profile_factory("default", root=isolate.templates, config={"template": True})
    for rel, text in TEMPLATE_TOKENS.items():
        (tpl / "companions" / rel).write_text(text)
    write_exec(tpl / "companions" / TEMPLATE_SCRIPT, "#!/bin/bash\necho ${CLAUDE_PLUGIN_DATA}\n")
    (tpl / "upstream/planning/commands/make.md").write_text("# make, as the plugin ships it\n")
    (tpl / "companions/.DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1")
    (tpl / "companions/revmux/.DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1")
    config, _ = MT.read_frontmatter((tpl / "profile.md").read_text())
    assert config is not None
    (tpl / "profile.md").write_text(MT.write_frontmatter(config, TEMPLATE_BODY))
    plugins = {
        "brainstorm": "plugin:brainstorm@umputun-cc-thingz",
        "planning": "plugin:planning@umputun-cc-thingz",
        "revmux": "plugin:revmux@revmux:.claude-plugin/skills/revmux",
    }
    sources = {cid: {"source": spec, "version": "1.0.0"} for cid, spec in plugins.items()}
    body = f"\n## Preferences\n\n## Changes\n\n{TEMPLATE_CHANGE}\n\n## Wanted\n"
    (tpl / "UPDATE.md").write_text(MT.write_frontmatter({"companions": sources}, body))
    (tpl / "LICENSES").mkdir()
    for cid in PROFILE_FILES:
        (tpl / "LICENSES" / cid).write_text(f"{cid}\n{LICENSE_TEXT}")
    chk = MT.check_profile(tpl)
    assert chk["ok"] is True, chk
    return tpl


def template_sources(tpl: Path) -> dict[str, Any]:
    return {
        cid: {"source": f"make-task:default/{cid}", "version": MT.tree_hash(tpl / "companions" / cid)}
        for cid in PROFILE_FILES
    }


def assert_installed(d: Path, tpl: Path) -> None:
    """d holds the fixture template installed for d: both trees from its companions/, rewritten for d."""
    assert set(MT.os.listdir(d)) == INSTALLED, "the template's upstream/ and UPDATE.md are not the profile's"
    comps, data = d / "companions", d / "data"
    assert (comps / "planning/commands/make.md").read_text() == (
        f"# make\nrun {comps}/planning/skills/exec/scripts/init-progress.sh\nprompts {data}/planning/prompts\n"
    )
    assert (comps / "brainstorm/skills/brainstorm/SKILL.md").read_text() == (
        f"# brainstorm\nbash {comps}/brainstorm/skills/brainstorm/notes.md\n"
    )
    assert (comps / "revmux/SKILL.md").read_text() == f"# revmux\n{comps}/revmux/scripts/preflight.sh\n"
    assert (comps / TEMPLATE_SCRIPT).read_text() == f"#!/bin/bash\necho {data}/planning\n"
    for cid in PROFILE_FILES:
        comp, up = comps / cid, d / "upstream" / cid
        src_files = [rel for rel, _ in MT.tree_files(tpl / "companions" / cid)]
        assert [rel for rel, _ in MT.tree_files(comp)] == src_files, cid
        assert MT.tree_hash(comp) == MT.tree_hash(up), f"upstream/{cid} equals companions/{cid}"
        assert (data / cid).is_dir() and not any((data / cid).iterdir()), f"data/{cid} starts empty"
        assert (d / "LICENSES" / cid).read_text() == f"{cid}\n{LICENSE_TEXT}"
    for tree in ("companions", "upstream"):
        assert MT.os.access(d / tree / TEMPLATE_SCRIPT, MT.os.X_OK), "the exec bit is kept"
        assert not (d / tree / "revmux" / ".DS_Store").exists()
    assert not (d / "companions" / ".DS_Store").exists()

    config, body = MT.read_frontmatter((d / "profile.md").read_text())
    tpl_config, tpl_body = MT.read_frontmatter((tpl / "profile.md").read_text())
    assert config == {**tpl_config, "template": False}
    assert list(config) == list(tpl_config), "key order kept"
    assert body == tpl_body == TEMPLATE_BODY, "the body (## <phase> sections) is kept"

    update, update_body = update_md(d)
    assert update == {"rewritten_for": str(d), "companions": template_sources(tpl)}
    assert update_body == MT.UPDATE_BODY, "empty ## Preferences, ## Changes and ## Wanted"
    assert TEMPLATE_CHANGE not in (d / "UPDATE.md").read_text(), "make-task's changes stay in the template"

    for rel, text in TEMPLATE_TOKENS.items():
        assert (tpl / "companions" / rel).read_text() == text, "the template keeps its raw tokens"
    assert not [p for p in d.parent.iterdir() if p.name.startswith(".install-")], "no work dir is left"


def test_install_default(mt: Callable[..., Result], isolate: Env, template: Path) -> None:
    r, out = profile(mt, "install-default")
    assert r.code == 0, r.err
    d = isolate.config_home / "profiles" / "default"
    assert out == {
        "name": "default",
        "dir": str(d),
        "template": str(template),
        "companions": template_sources(template),
    }
    for comp in out["companions"].values():
        assert comp["version"].startswith("sha256:")
    assert_installed(d, template)

    _, chk = check(mt, d)
    assert chk == {"ok": True, "errors": [], "warnings": [], "updates": []}
    _, st = profile(mt, "status")
    assert st["template"] is False
    assert st["autonomy"] == "default"
    for cid, comp in st["companions"].items():
        assert comp == {**template_sources(template)[cid], "present": True, "modified": False}, cid
    _, res = profile(mt, "resolve")
    assert res["status"] == "ok"


def test_install_default_under_another_name(mt: Callable[..., Result], isolate: Env, template: Path) -> None:
    profiles = isolate.config_home / "profiles"
    r, out = profile(mt, "install-default", "--profile", "other")
    assert r.code == 0, r.err
    assert out["name"] == "other"
    assert out["dir"] == str(profiles / "other")
    assert not (profiles / "default").exists()
    assert_installed(profiles / "other", template)
    assert profile(mt, "install-default")[0].code == 0
    assert_installed(profiles / "default", template)
    assert (
        f"{profiles / 'other'}/companions/planning/"
        in (profiles / "other/companions/planning/commands/make.md").read_text()
    )
    for name in ("default", "other"):
        _, chk = check(mt, profiles / name)
        assert chk["ok"] is True, (name, chk)


def test_install_default_into_a_dir(mt: Callable[..., Result], tmp_path: Path, template: Path) -> None:
    d = tmp_path / "elsewhere" / "mine"
    r, out = profile(mt, "install-default", "--dir", str(d))
    assert r.code == 0, r.err
    assert out["name"] == "mine"
    assert out["dir"] == str(d.resolve())
    assert_installed(d.resolve(), template)


@pytest.mark.parametrize("kind", ["profile", "empty dir", "file"])
def test_install_default_refuses_an_existing_target(
    mt: Callable[..., Result], isolate: Env, template: Path, profile_factory: Factory, kind: str
) -> None:
    d = isolate.config_home / "profiles" / "default"
    if kind == "profile":
        profile_factory()
    elif kind == "empty dir":
        d.mkdir(parents=True)
    else:
        d.parent.mkdir(parents=True)
        d.write_text("not a profile\n")
    before = MT.tree_hash(d) if d.is_dir() else d.read_text()
    r, out = profile(mt, "install-default")
    assert r.code == 1
    assert out is None
    assert "exists" in r.err
    assert (MT.tree_hash(d) if d.is_dir() else d.read_text()) == before, "the target is untouched"
    assert not [p for p in d.parent.iterdir() if p.name.startswith(".install-")]


def break_by_symlink(tpl: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # revmux is the last companion: brainstorm and planning are copied before it fails
    (tmp_path / "outside.md").write_text("outside\n")
    (tpl / "companions" / "revmux" / "leak.md").symlink_to(tmp_path / "outside.md")


def break_by_disk(tpl: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # after every companion and LICENSES/ are in the work dir: profile.md cannot be written
    def full(*args: Any, **kwargs: Any) -> str:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(MT, "write_frontmatter", full)


@pytest.mark.parametrize(
    ("break_it", "error"),
    [(break_by_symlink, "outside the source"), (break_by_disk, "No space left on device")],
    ids=["bad symlink in the last companion", "profile.md write fails"],
)
def test_install_default_failure_leaves_no_target(
    mt: Callable[..., Result],
    isolate: Env,
    template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    break_it: Callable[[Path, Path, pytest.MonkeyPatch], None],
    error: str,
) -> None:
    break_it(template, tmp_path, monkeypatch)
    r, out = profile(mt, "install-default")
    assert r.code == 1
    assert out is None
    assert error in r.err
    profiles = isolate.config_home / "profiles"
    assert not (profiles / "default").exists()
    assert list(profiles.iterdir()) == [], "no work dir is left either"


@pytest.mark.parametrize(
    ("break_it", "error"),
    [
        (lambda tpl: MT.shutil.rmtree(tpl), "no make-task template"),
        (lambda tpl: reconfig(tpl, lambda c: c.update(template=False)), "template: true"),
        (lambda tpl: reconfig(tpl, lambda c: c.pop("template")), "template: true"),
        (lambda tpl: (tpl / "profile.md").write_text("# no frontmatter\n"), "template: true"),
        (lambda tpl: MT.shutil.rmtree(tpl / "companions"), "no companions"),
        (lambda tpl: (tpl / "companions" / ".hidden").mkdir(), "not ids: .hidden"),
        (lambda tpl: (tpl / "profile.md").write_text("---\ntemplate: [true\n---\n"), "not valid YAML"),
    ],
    ids=["no template", "template false", "template missing", "no frontmatter", "no companions", "bad id", "bad yaml"],
)
def test_install_default_needs_a_template(
    mt: Callable[..., Result], isolate: Env, template: Path, break_it: Callable[[Path], Any], error: str
) -> None:
    break_it(template)
    r, out = profile(mt, "install-default")
    assert r.code == 1
    assert out is None
    assert error in r.err
    assert not (isolate.config_home / "profiles").exists(), "nothing is written"


def test_install_default_refuses_a_bad_name(mt: Callable[..., Result], isolate: Env, template: Path) -> None:
    r, _ = profile(mt, "install-default", "--profile", "../x")
    assert r.code == 1
    assert "--profile" in r.err
    assert not isolate.config_home.exists()


def test_install_default_dir_help_says_the_dir_must_not_exist() -> None:
    commands = subparsers(subparsers(MT.build_parser())["profile"])
    assert "install into this directory (it must not exist)" in commands["install-default"].format_help()
    assert "must not exist" not in commands["check"].format_help()


def test_check_fails_on_a_copied_or_moved_profile(
    mt: Callable[..., Result], isolate: Env, template: Path, tmp_path: Path
) -> None:
    """The copies hold the absolute paths they were installed for: a copy of the profile would run the original's
    files, so check (and init) fail on it and say how to fix it. The same dir through a symlink is fine."""
    assert profile(mt, "install-default")[0].code == 0
    profiles = isolate.config_home / "profiles"
    MT.shutil.copytree(profiles / "default", profiles / "work", symlinks=True)
    _, out = check(mt, profiles / "work")
    assert out["ok"] is False
    [err] = out["errors"]
    assert err.startswith(f"UPDATE.md: the copies' paths were written for {str(profiles / 'default')!r}")
    assert "copied or moved" in err and "mt profile install-default" in err
    with pytest.raises(MT.MtError, match="copied or moved"):
        MT.run_profile(profiles / "work")
    MT.os.rename(profiles / "default", profiles / "moved")
    assert check(mt, profiles / "moved")[1]["ok"] is False
    MT.os.rename(profiles / "moved", profiles / "default")
    (tmp_path / "link").symlink_to(profiles / "default")
    assert MT.check_profile(tmp_path / "link")["ok"] is True


# ---------------------------------------------------------------- profile paths go into shell commands unquoted


def test_install_default_refuses_a_config_root_with_a_space(
    mt: Callable[..., Result], isolate: Env, template: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under `config with spaces` the copies' `bash <path>/scripts/x.sh` would run `<...>/config` (exit 127):
    install-default refuses before it creates anything, the config root included, and says how to move it."""
    root = isolate.tmp / "config with spaces"
    monkeypatch.setenv("MT_CONFIG_HOME", str(root))
    r, out = profile(mt, "install-default")
    assert (r.code, out) == (1, None)
    assert f"the profile path {str(root / 'profiles' / 'default')!r} holds ' '" in r.err
    assert "MT_CONFIG_HOME" in r.err and "--dir" in r.err
    assert not root.exists()


@pytest.mark.parametrize("char", [" ", "\t", "\n", *MT.SHELL_SPECIAL])
def test_install_default_refuses_a_dir_that_is_not_a_shell_word(
    mt: Callable[..., Result], tmp_path: Path, template: Path, char: str
) -> None:
    parent = tmp_path / f"a{char}b"
    r, _ = profile(mt, "install-default", "--dir", str(parent / "p"))
    assert r.code == 1
    assert f"holds {char!r}:" in r.err
    assert not parent.exists()


def test_a_non_ascii_profile_path_is_a_shell_word(mt: Callable[..., Result], tmp_path: Path, template: Path) -> None:
    for name in ("café-профиль", "café", "a@b%c+d=e:f,g.h_i-j"):  # NFC, NFD (a combining mark), punctuation
        d = tmp_path / name / "p"
        r, _ = profile(mt, "install-default", "--dir", str(d))
        assert r.code == 0, (name, r.err)
        assert check(mt, d.resolve())[1]["ok"] is True, name


def test_check_and_init_refuse_a_profile_path_that_is_not_a_shell_word(
    mt: Callable[..., Result], isolate: Env, profile_factory: Factory, git_repo: Path
) -> None:
    d = profile_factory(root=isolate.tmp / "my profiles")
    _, out = check(mt, d)
    assert out["ok"] is False
    assert out["errors"] == [MT.unsafe_profile_path(str(d))]
    r = mt(*init_args(profile=d), cwd=git_repo)
    assert r.code == 1
    assert "holds ' '" in r.err
    # a template keeps raw tokens and never runs: its path is never written into a command
    tpl = profile_factory(root=isolate.tmp / "my templates", config={"template": True})
    assert check(mt, tpl)[1]["ok"] is True


def test_import_and_fetch_refuse_a_profile_path_that_is_not_a_shell_word(
    mt: Callable[..., Result], isolate: Env
) -> None:
    d = foo_profile(mt, isolate)
    moved = isolate.tmp / "a;b" / "p"
    moved.parent.mkdir()
    MT.os.rename(d, moved)
    before = MT.tree_hash(moved)
    upgrade_foo(isolate)
    for args in (("import", "bar", "--source", "plugin:foo@market"), ("fetch", "foo")):
        r, out = profile(mt, *args, "--dir", str(moved))
        assert (r.code, out) == (1, None), args
        assert "holds ';'" in r.err
    assert MT.tree_hash(moved) == before, "nothing copied, no .staging/ or work dir left"
    assert sorted(p.name for p in moved.iterdir()) == ["UPDATE.md", "companions", "data", "profile.md", "upstream"]
    # in a template the tokens stay raw: nothing is written for the path, so the import runs
    tpl = bare_profile(isolate, "t", template=True)
    MT.os.rename(tpl, isolate.tmp / "a;b" / "t")
    r, _ = profile(mt, "import", "foo", "--source", "plugin:foo@market", "--dir", str(isolate.tmp / "a;b" / "t"))
    assert r.code == 0, r.err


@pytest.mark.parametrize(
    ("pid", "key", "rel"),
    [
        ("exec", "scripts", "skills/exec/scripts extra"),
        ("exec", "rules", "scripts/resolve;rules.sh"),
        ("review", "scripts", "scripts$x"),
        ("plan", "entry", "commands/make (new).md"),
        ("plan", "reviewer", "agents/plan\treview.md"),
        ("brainstorm", "entry", "skills/brain*storm/SKILL.md"),
    ],
)
def test_check_refuses_a_phase_path_that_is_not_a_shell_word(
    mt: Callable[..., Result], isolate: Env, profile_factory: Factory, pid: str, key: str, rel: str
) -> None:
    """A run's phase path is <profile>/companions/<id>/<phases path>, used unquoted like the profile dir: an
    existing file or dir whose name is not one plain word is a check error. A template is checked too: every
    install takes its phases map."""
    spec = profile_config()["phases"][pid]
    for d in (profile_factory(), profile_factory("t", root=isolate.tmp, config={"template": True})):
        comp = d / "companions" / spec["companion"]
        old, new = comp / spec[key], comp / rel
        new.parent.mkdir(parents=True, exist_ok=True)
        MT.shutil.copytree(old, new) if old.is_dir() else MT.shutil.copy2(old, new)
        reconfig(d, lambda c: c["phases"][pid].update({key: rel}))
        _, out = check(mt, d)
        [err] = out["errors"]
        path = f"companions/{spec['companion']}/{rel}"
        assert err.startswith(f"phases.{pid}.{key}: the path {path!r} holds "), err
        assert f"Rename it, in companions/{spec['companion']}/ and in profile.md" in err


def test_init_refuses_an_exec_scripts_path_that_is_not_a_shell_word(
    mt: Callable[..., Result], profile_factory: Factory, git_repo: Path
) -> None:
    """The case `bash <exec_scripts>/create-branch.sh` would run as `bash .../scripts` (exit 127): no run starts."""
    d = profile_factory()
    planning = d / "companions" / "planning"
    MT.shutil.copytree(planning / "skills/exec/scripts", planning / "skills/exec/scripts extra")
    reconfig(d, lambda c: c["phases"]["exec"].update(scripts="skills/exec/scripts extra"))
    r = mt(*init_args(profile=d), cwd=git_repo)
    assert r.code == 1
    assert "phases.exec.scripts: the path 'companions/planning/skills/exec/scripts extra' holds ' '" in r.err


def test_a_rewrite_refuses_a_skill_dir_that_is_not_a_shell_word(mt: Callable[..., Result], isolate: Env) -> None:
    """${CLAUDE_SKILL_DIR} becomes <profile>/companions/<id>/<skill dir>, unquoted like the rest: a source whose
    skill dir is not one plain word is refused where a token names it, with nothing written. A template keeps
    the token raw, and a skill dir no token names is never written into a command."""
    d = bare_profile(isolate)
    run = "skills/my skill/run.md"
    files = {"skills/my skill/SKILL.md": "# s\n", run: "a\nbash ${CLAUDE_SKILL_DIR}/x.sh\n"}
    root = plugin_release(isolate, files, "1.0.0")
    install_plugin(isolate, "foo@market", root, version="1.0.0")
    r, out = profile(mt, "import", "foo", "--source", "plugin:foo@market", "--profile", "p")
    assert (r.code, out) == (1, None)
    where = f"{run}:2: ${{CLAUDE_SKILL_DIR}} names its skill dir"
    assert f"{where}, whose path 'companions/foo/skills/my skill' holds ' '" in r.err
    assert sorted(p.name for p in d.iterdir()) == ["profile.md"], "nothing written"
    bare_profile(isolate, "t", template=True)
    assert profile(mt, "import", "foo", "--source", "plugin:foo@market", "--profile", "t")[0].code == 0
    (root / run).write_text("no token\n")
    r, _ = profile(mt, "import", "foo", "--source", "plugin:foo@market", "--profile", "p")
    assert r.code == 0, r.err


# ---------------------------------------------------------------- outdated, fetch, accept

# a plugin in two releases: v2 changes SKILL.md, drops old.md, adds new.md (with a token) and makes notes.md
# executable
PLUGIN_V1 = {
    "SKILL.md": "# foo v1\nrun ${CLAUDE_PLUGIN_ROOT}/scripts/run.sh\n",
    "scripts/run.sh": "#!/bin/sh\necho ${CLAUDE_PLUGIN_DATA}\n",
    "notes.md": "keep\n",
    "old.md": "goes away in v2\n",
}
PLUGIN_V2 = {
    "SKILL.md": "# foo v2\nrun ${CLAUDE_PLUGIN_ROOT}/scripts/run.sh\n",
    "scripts/run.sh": "#!/bin/sh\necho ${CLAUDE_PLUGIN_DATA}\n",
    "notes.md": "keep\n",
    "new.md": "added in v2 at ${CLAUDE_SKILL_DIR}\n",
}
SHA2 = "0123456789abcdef0123456789abcdef01234567"


def plugin_release(isolate: Env, files: dict[str, str], version: str) -> Path:
    """Write a release of foo@market under the plugin cache; its dir."""
    root = isolate.plugins_dir / "cache" / "market" / "foo" / version
    for rel, text in files.items():
        if rel.endswith(".sh") or (version != "1.0.0" and rel == "notes.md"):
            write_exec(root / rel, text)
        else:
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text)
    return root


def install_plugin(isolate: Env, key: str, path: Path, **fields: Any) -> None:
    """Make path the only installed_plugins.json entry for key (an upgrade replaces the entry)."""
    data = json.loads(isolate.plugins_json.read_text())
    data["plugins"][key] = [{"scope": "user", "installPath": str(path), **fields}]
    isolate.plugins_json.write_text(json.dumps(data))


def foo_profile(mt: Callable[..., Result], isolate: Env) -> Path:
    """Profile p with companion foo imported from foo@market 1.0.0."""
    d = bare_profile(isolate)
    install_plugin(
        isolate, "foo@market", plugin_release(isolate, PLUGIN_V1, "1.0.0"), version="1.0.0", gitCommitSha=SHA
    )
    r, _ = profile(mt, "import", "foo", "--source", "plugin:foo@market", "--profile", "p")
    assert r.code == 0, r.err
    return d


def upgrade_foo(isolate: Env) -> None:
    install_plugin(
        isolate, "foo@market", plugin_release(isolate, PLUGIN_V2, "2.0.0"), version="2.0.0", gitCommitSha=SHA2
    )


def outdated(mt: Callable[..., Result], *args: str) -> dict[str, Any]:
    r, out = profile(mt, "outdated", *args)
    assert r.code == 0, r.err
    assert out is not None
    return out


def staged_paths(d: Path) -> list[str]:
    """Every file in companions/ that names a .staging or upstream/ path."""
    hits = []
    for rel, path in MT.tree_files(d / "companions"):
        text = path.read_text()
        if f"{d}/{MT.STAGING}" in text or f"{d}/upstream" in text or ".staging" in text:
            hits.append(rel)
    return hits


def test_outdated_no_updates_after_install(mt: Callable[..., Result], isolate: Env, template: Path) -> None:
    assert profile(mt, "install-default")[0].code == 0
    out = outdated(mt)
    assert out == {"updates": [], "current": ["brainstorm", "planning", "revmux"], "skipped": [], "errors": []}
    assert outdated(mt, "--network") == out, "no git: source: --network changes nothing"
    _, chk = check(mt, isolate.config_home / "profiles" / "default")
    assert chk == {"ok": True, "errors": [], "warnings": [], "updates": []}


def test_outdated_factory_profile_matches_its_template(mt: Callable[..., Result], profile_factory: Factory) -> None:
    profile_factory()
    assert outdated(mt)["current"] == ["brainstorm", "planning", "revmux"]


def test_outdated_template_change(mt: Callable[..., Result], isolate: Env, template: Path) -> None:
    assert profile(mt, "install-default")[0].code == 0
    d = isolate.config_home / "profiles" / "default"
    recorded = update_md(d)[0]["companions"]["planning"]["version"]
    (template / "companions" / "planning" / "commands" / "make.md").write_text("# make, make-task's next version\n")
    available = MT.tree_hash(template / "companions" / "planning")
    out = outdated(mt)
    assert out["updates"] == [
        {
            "id": "planning",
            "source": "make-task:default/planning",
            "version": recorded,
            "available": available,
            "modified": False,
        }
    ]
    assert out["current"] == ["brainstorm", "revmux"]
    _, chk = check(mt, d)
    assert chk["ok"] is True
    assert chk["updates"] == out["updates"], "check lists the make-task: update"
    assert chk["warnings"] == []


def test_outdated_plugin_version_bump(mt: Callable[..., Result], isolate: Env) -> None:
    d = foo_profile(mt, isolate)
    assert outdated(mt, "--profile", "p")["current"] == ["foo"]
    upgrade_foo(isolate)
    out = outdated(mt, "--profile", "p")
    assert out["updates"] == [
        {
            "id": "foo",
            "source": "plugin:foo@market",
            "version": "1.0.0+dd01fa7f1815",
            "available": "2.0.0+0123456789ab",
            "modified": False,
        }
    ]
    assert out["current"] == [] and out["errors"] == []
    _, chk = check(mt, d)
    assert chk["updates"] == out["updates"]


def test_outdated_reports_a_modified_copy(mt: Callable[..., Result], isolate: Env) -> None:
    d = foo_profile(mt, isolate)
    (d / "companions" / "foo" / "notes.md").write_text("mine\n")
    upgrade_foo(isolate)
    assert outdated(mt, "--profile", "p")["updates"][0]["modified"] is True


def test_outdated_git_only_with_network(mt: Callable[..., Result], isolate: Env, tmp_path: Path) -> None:
    d = bare_profile(isolate)
    repo, head = git_source(tmp_path)
    spec = f"git:file://{repo}#main:plugins/x"
    assert profile(mt, "import", "x", "--source", spec, "--profile", "p")[0].code == 0
    skipped = [{"id": "x", "source": spec, "reason": "git: looked up only with --network"}]
    assert outdated(mt, "--profile", "p") == {"updates": [], "current": [], "skipped": skipped, "errors": []}
    assert outdated(mt, "--network", "--profile", "p") == {"updates": [], "current": ["x"], "skipped": [], "errors": []}

    (repo / "plugins" / "x" / "notes.txt").write_text("a new commit\n")
    git(repo, "commit", "-q", "-am", "next")
    new = git(repo, "rev-parse", "HEAD")
    assert outdated(mt, "--profile", "p")["updates"] == [], "not seen without --network"
    _, chk = check(mt, d)
    assert chk["updates"] == [], "check never goes to the network"
    out = outdated(mt, "--network", "--profile", "p")
    assert out["updates"] == [{"id": "x", "source": spec, "version": head, "available": new, "modified": False}]

    r, fetched = profile(mt, "fetch", "x", "--profile", "p")
    assert r.code == 0, r.err
    assert fetched["available"] == new
    assert "+a new commit" in fetched["diff"]
    assert profile(mt, "accept", "x", "--expect", fetched["tree"], "--profile", "p")[0].code == 0
    assert (d / "companions" / "x" / "notes.txt").read_text() == "a new commit\n"
    assert outdated(mt, "--network", "--profile", "p")["current"] == ["x"]


def test_outdated_git_annotated_tag_compares_the_commit(
    mt: Callable[..., Result], isolate: Env, tmp_path: Path
) -> None:
    bare_profile(isolate)
    repo, head = git_source(tmp_path)
    git(repo, "tag", "-a", "v1", "-m", "release")
    assert git(repo, "rev-parse", "v1") != head, "the tag is an object of its own"
    spec = f"git:file://{repo}#v1:plugins/x"
    assert profile(mt, "import", "x", "--source", spec, "--profile", "p")[1]["version"] == head
    assert outdated(mt, "--network", "--profile", "p")["current"] == ["x"], "the peeled tag is the recorded commit"
    (repo / "plugins" / "x" / "notes.txt").write_text("v1 moved\n")
    git(repo, "commit", "-q", "-am", "next")
    git(repo, "tag", "-f", "-a", "v1", "-m", "release again")
    new = git(repo, "rev-parse", "HEAD")
    assert outdated(mt, "--network", "--profile", "p")["updates"][0]["available"] == new


def test_outdated_skips_local_and_unrecorded_copies(mt: Callable[..., Result], isolate: Env) -> None:
    d = bare_profile(isolate)
    for cid in ("mine", "stray"):
        (d / "companions" / cid).mkdir(parents=True)
        (d / "companions" / cid / "SKILL.md").write_text(f"# {cid}\n")
    assert profile(mt, "import", "mine", "--source", "local", "--profile", "p")[0].code == 0
    out = outdated(mt, "--network", "--profile", "p")
    assert out == {
        "updates": [],
        "current": [],
        "skipped": [
            {"id": "mine", "source": "local", "reason": "local: written by you, never updated"},
            {"id": "stray", "source": None, "reason": "no source recorded in UPDATE.md"},
        ],
        "errors": [],
    }


def test_outdated_errors_are_listed_and_check_warns(mt: Callable[..., Result], isolate: Env) -> None:
    d = foo_profile(mt, isolate)
    data, body = update_md(d)
    data["companions"]["bad"] = {"source": "svn:somewhere", "version": "1"}
    data["companions"]["gone"] = {"source": "make-task:default/gone", "version": "sha256:x"}
    (d / "UPDATE.md").write_text(MT.write_frontmatter(data, body))
    install_plugin(isolate, "foo@market", isolate.tmp / "uninstalled", version="1.0.0")
    out = outdated(mt, "--profile", "p")
    assert out["updates"] == [] and out["current"] == []
    errors = {e["id"]: e for e in out["errors"]}
    assert set(errors) == {"bad", "foo", "gone"}
    assert "expected make-task:" in errors["bad"]["error"]
    assert "is not installed" in errors["foo"]["error"]
    assert errors["foo"]["source"] == "plugin:foo@market"
    assert "does not exist" in errors["gone"]["error"]
    _, chk = check(mt, d)
    assert chk["updates"] == []
    assert sum("cannot check" in w for w in chk["warnings"]) == 3, chk["warnings"]
    assert any("companion foo: cannot check plugin:foo@market" in w for w in chk["warnings"])


def test_check_warns_on_an_unreadable_update_md(mt: Callable[..., Result], profile_factory: Factory) -> None:
    d = profile_factory()
    (d / "UPDATE.md").write_text("---\ncompanions: [1\n---\n")
    _, chk = check(mt, d)
    assert chk["ok"] is True
    assert chk["updates"] == []
    assert any(w.startswith("updates not checked:") for w in chk["warnings"]), chk["warnings"]


def test_outdated_needs_a_profile(mt: Callable[..., Result]) -> None:
    r, out = profile(mt, "outdated", "--profile", "nope")
    assert r.code == 1
    assert out is None
    assert "no profile at" in r.err


def test_fetch_stages_the_new_source_and_prints_the_diff(mt: Callable[..., Result], isolate: Env) -> None:
    d = foo_profile(mt, isolate)
    before = MT.tree_hash(d / "companions" / "foo")
    upgrade_foo(isolate)
    r, out = profile(mt, "fetch", "foo", "--profile", "p")
    assert r.code == 0, r.err
    staged = d / ".staging" / "foo" / "tree"
    comp, data = d / "companions" / "foo", d / "data" / "foo"
    diff = out.pop("diff")
    assert out == {
        "id": "foo",
        "source": "plugin:foo@market",
        "version": "1.0.0+dd01fa7f1815",
        "available": "2.0.0+0123456789ab",
        "staging": str(staged),
        "tree": MT.tree_hash(staged),
        "changed": True,
        "modified": False,
    }
    # the staged tree is the new release, rewritten for companions/foo
    assert sorted(rel for rel, _ in MT.tree_files(staged)) == ["SKILL.md", "new.md", "notes.md", "scripts/run.sh"]
    assert (staged / "SKILL.md").read_text() == f"# foo v2\nrun {comp}/scripts/run.sh\n"
    assert (staged / "new.md").read_text() == f"added in v2 at {comp}\n"
    assert (staged / "scripts/run.sh").read_text() == f"#!/bin/sh\necho {data}\n"
    assert MT.os.access(staged / "notes.md", MT.os.X_OK)
    assert json.loads((d / ".staging" / "foo" / "fetch.json").read_text()) == {
        "source": "plugin:foo@market",
        "version": "2.0.0+0123456789ab",
    }
    # the unified diff upstream/foo -> .staging/foo/tree
    assert "diff --git a/upstream/foo/SKILL.md b/.staging/foo/tree/SKILL.md" in diff
    assert "-# foo v1\n+# foo v2\n" in diff
    assert "--- a/upstream/foo/old.md\n+++ /dev/null\n" in diff and "-goes away in v2\n" in diff
    assert "new file mode 100644" in diff and f"+added in v2 at {comp}\n" in diff
    assert "old mode 100644\nnew mode 100755" in diff, "notes.md became executable"
    assert "b/.staging/foo/tree/scripts/run.sh" not in diff, "an unchanged file is not in the diff"
    # nothing else moved
    assert MT.tree_hash(comp) == before == MT.tree_hash(d / "upstream" / "foo")
    assert update_md(d)[0]["companions"]["foo"]["version"] == "1.0.0+dd01fa7f1815"
    assert outdated(mt, "--profile", "p")["updates"][0]["available"] == "2.0.0+0123456789ab", "still outdated"
    assert sorted(p.name for p in (d / ".staging").iterdir()) == ["foo"]
    assert not [p for p in d.iterdir() if p.name.startswith(".fetch-")], "no work dir is left"


def test_fetch_with_no_change(mt: Callable[..., Result], isolate: Env) -> None:
    d = foo_profile(mt, isolate)
    r, out = profile(mt, "fetch", "foo", "--profile", "p")
    assert r.code == 0, r.err
    assert out["changed"] is False
    assert out["diff"] == ""
    assert out["available"] == out["version"]
    assert MT.tree_hash(d / ".staging" / "foo" / "tree") == MT.tree_hash(d / "upstream" / "foo")


def test_fetch_again_replaces_the_staging(mt: Callable[..., Result], isolate: Env) -> None:
    d = foo_profile(mt, isolate)
    upgrade_foo(isolate)
    assert profile(mt, "fetch", "foo", "--profile", "p")[0].code == 0
    (d / ".staging" / "foo" / "tree" / "leftover.md").write_text("from the first fetch\n")
    root3 = plugin_release(isolate, {**PLUGIN_V2, "SKILL.md": "# foo v3\n"}, "3.0.0")
    install_plugin(isolate, "foo@market", root3, version="3.0.0")
    r, out = profile(mt, "fetch", "foo", "--profile", "p")
    assert r.code == 0, r.err
    assert out["available"] == "3.0.0"
    assert out["tree"] == MT.tree_hash(d / ".staging" / "foo" / "tree")
    assert not (d / ".staging" / "foo" / "tree" / "leftover.md").exists()
    assert (d / ".staging" / "foo" / "tree" / "SKILL.md").read_text() == "# foo v3\n"
    assert json.loads((d / ".staging" / "foo" / "fetch.json").read_text())["version"] == "3.0.0"
    assert sorted(p.name for p in (d / ".staging").iterdir()) == ["foo"]
    assert sorted(p.name for p in (d / ".staging" / "foo").iterdir()) == ["fetch.json", "tree"]


def test_fetch_prints_its_own_tree_and_diff_when_another_fetch_lands(
    mt: Callable[..., Result], isolate: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Session B's fetch of v3 runs start to end while A's fetch of v2 copies (no lock held yet): A prints v2's
    tree with v2's diff (never B's), stages them with v2's record, and accepting A's tree records v2."""
    d = foo_profile(mt, isolate)
    upgrade_foo(isolate)
    root3 = plugin_release(isolate, {**PLUGIN_V2, "SKILL.md": "# foo v3\n"}, "3.0.0")
    real = MT.copy_source
    b: dict[str, Any] = {}

    def copy_source(*args: Any, **kwargs: Any) -> str:
        monkeypatch.setattr(MT, "copy_source", real)
        version = real(*args, **kwargs)
        install_plugin(isolate, "foo@market", root3, version="3.0.0")
        b.update(MT.fetch_companion(d, "foo"))  # session B
        return version

    monkeypatch.setattr(MT, "copy_source", copy_source)
    r, a = profile(mt, "fetch", "foo", "--profile", "p")
    assert r.code == 0, r.err
    assert (a["available"], b["available"]) == ("2.0.0+0123456789ab", "3.0.0")
    assert a["tree"] != b["tree"]
    assert "-# foo v1\n+# foo v2\n" in a["diff"] and "v3" not in a["diff"]
    staged = d / ".staging" / "foo"
    assert MT.tree_hash(staged / "tree") == a["tree"], "A landed last: its tree is staged"
    assert json.loads((staged / "fetch.json").read_text())["version"] == a["available"]
    assert sorted(p.name for p in (d / ".staging").iterdir()) == ["foo"]
    assert not [p for p in d.iterdir() if p.name.startswith(".fetch-")], "no work dir left"
    r, out = profile(mt, "accept", "foo", "--expect", a["tree"], "--profile", "p")
    assert r.code == 0, r.err
    assert out["version"] == "2.0.0+0123456789ab"
    assert (d / "companions" / "foo" / "SKILL.md").read_text().startswith("# foo v2\n")


def test_fetch_that_another_fetch_beats_to_the_staging_says_so(
    mt: Callable[..., Result], isolate: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fetch staged between this fetch's two renames (by a writer that takes no lock: an older mt) stays; this
    one prints nothing and leaves no work dir."""
    d = foo_profile(mt, isolate)
    upgrade_foo(isolate)
    target, real = d / ".staging" / "foo", MT.os.rename

    def rename(src: Path, dst: Path) -> None:
        if dst == target and not target.exists():
            (target / "tree").mkdir(parents=True)  # another session's fetch lands now
            (target / "fetch.json").write_text("{}\n")
        real(src, dst)

    monkeypatch.setattr(MT.os, "rename", rename)
    r, out = profile(mt, "fetch", "foo", "--profile", "p")
    monkeypatch.setattr(MT.os, "rename", real)
    assert (r.code, out) == (1, None)
    assert "another fetch of 'foo' was staged" in r.err and "run `mt profile fetch foo` again" in r.err
    assert (target / "fetch.json").read_text() == "{}\n", "the other fetch stays"
    assert sorted(p.name for p in (d / ".staging").iterdir()) == ["foo"]


def test_fetch_without_an_upstream_diffs_against_nothing(mt: Callable[..., Result], isolate: Env) -> None:
    d = foo_profile(mt, isolate)
    MT.shutil.rmtree(d / "upstream" / "foo")
    r, out = profile(mt, "fetch", "foo", "--profile", "p")
    assert r.code == 0, r.err
    assert out["changed"] is True
    assert out["modified"] is None
    assert out["diff"].count("new file mode") == 4
    assert "+++ b/.staging/foo/tree/SKILL.md" in out["diff"]


@pytest.mark.parametrize(
    ("cid", "error"),
    [
        ("nope", "has no source"),
        ("mine", "is local"),
        ("../x", "companion id"),
    ],
)
def test_fetch_refuses(mt: Callable[..., Result], isolate: Env, cid: str, error: str) -> None:
    d = bare_profile(isolate)
    (d / "companions" / "mine").mkdir(parents=True)
    assert profile(mt, "import", "mine", "--source", "local", "--profile", "p")[0].code == 0
    r, out = profile(mt, "fetch", cid, "--profile", "p")
    assert r.code == 1
    assert out is None
    assert error in r.err
    assert not (d / ".staging").exists()


def test_fetch_failure_leaves_no_staging(mt: Callable[..., Result], isolate: Env) -> None:
    d = foo_profile(mt, isolate)
    install_plugin(isolate, "foo@market", isolate.tmp / "uninstalled", version="2.0.0")
    r, _ = profile(mt, "fetch", "foo", "--profile", "p")
    assert r.code == 1
    assert "is not installed" in r.err
    assert not (d / ".staging").exists()


def test_accept_on_an_unmodified_copy_replaces_it(mt: Callable[..., Result], isolate: Env) -> None:
    d = foo_profile(mt, isolate)
    upgrade_foo(isolate)
    _, fetched = profile(mt, "fetch", "foo", "--profile", "p")
    staged = MT.tree_hash(d / ".staging" / "foo" / "tree")
    assert fetched["tree"] == staged
    r, out = profile(mt, "accept", "foo", "--expect", staged, "--profile", "p")
    assert r.code == 0, r.err
    assert out == {
        "id": "foo",
        "source": "plugin:foo@market",
        "version": "2.0.0+0123456789ab",
        "previous": "1.0.0+dd01fa7f1815",
        "copy": "updated",
        "modified": False,
    }
    comp = d / "companions" / "foo"
    assert MT.tree_hash(comp) == MT.tree_hash(d / "upstream" / "foo") == staged
    assert (comp / "SKILL.md").read_text() == f"# foo v2\nrun {comp}/scripts/run.sh\n"
    assert not (comp / "old.md").exists()
    assert MT.os.access(comp / "notes.md", MT.os.X_OK)
    assert update_md(d)[0]["companions"]["foo"] == {"source": "plugin:foo@market", "version": "2.0.0+0123456789ab"}
    assert not (d / ".staging").exists(), "the staging is gone"
    assert not [p for p in d.iterdir() if p.name.startswith(".accept-")]
    assert staged_paths(d) == [], "no .staging or upstream/ path in companions/"
    assert outdated(mt, "--profile", "p")["current"] == ["foo"]
    _, st = profile(mt, "status", "--profile", "p")
    assert st["companions"]["foo"]["modified"] is False


def test_accept_on_a_modified_copy_keeps_it_and_moves_the_base(mt: Callable[..., Result], isolate: Env) -> None:
    d = foo_profile(mt, isolate)
    comp = d / "companions" / "foo"
    (comp / "notes.md").write_text("my notes\n")
    mine = MT.tree_hash(comp)
    upgrade_foo(isolate)
    r, fetched = profile(mt, "fetch", "foo", "--profile", "p")
    assert r.code == 0, r.err
    assert fetched["modified"] is True
    assert "my notes" not in fetched["diff"], "the diff is upstream -> staging, not the copy"
    staged = MT.tree_hash(d / ".staging" / "foo" / "tree")
    r, out = profile(mt, "accept", "foo", "--expect", fetched["tree"], "--profile", "p")
    assert r.code == 0, r.err
    assert out["copy"] == "kept"
    assert out["modified"] is True
    assert MT.tree_hash(comp) == mine, "the copy is untouched"
    assert MT.tree_hash(d / "upstream" / "foo") == staged, "the base moved to the fetched tree"
    assert update_md(d)[0]["companions"]["foo"]["version"] == "2.0.0+0123456789ab"
    assert not (d / ".staging").exists()
    assert staged_paths(d) == []
    assert outdated(mt, "--profile", "p")["current"] == ["foo"]


def test_accept_after_a_merge_into_the_copy(mt: Callable[..., Result], isolate: Env) -> None:
    # the --update flow for a modified copy: merge the diff into companions/<id>, then accept
    d = foo_profile(mt, isolate)
    comp = d / "companions" / "foo"
    (comp / "notes.md").write_text("my notes\n")
    upgrade_foo(isolate)
    _, fetched = profile(mt, "fetch", "foo", "--profile", "p")
    staged = d / ".staging" / "foo" / "tree"
    for rel in ("SKILL.md", "new.md"):
        (comp / rel).write_text((staged / rel).read_text())
    (comp / "old.md").unlink()
    (comp / "notes.md").chmod(0o755)
    r, out = profile(mt, "accept", "foo", "--expect", fetched["tree"], "--profile", "p")
    assert r.code == 0, r.err
    assert out["copy"] == "kept"
    assert out["modified"] is True, "my notes are still a change of mine"
    assert (comp / "notes.md").read_text() == "my notes\n"
    assert (comp / "new.md").read_text() == f"added in v2 at {comp}\n"
    (comp / "notes.md").write_text("keep\n")
    _, st = profile(mt, "status", "--profile", "p")
    assert st["companions"]["foo"]["modified"] is False, "the merge equals the new base once my change is undone"


def test_accept_without_fetch_fails(mt: Callable[..., Result], isolate: Env) -> None:
    d = foo_profile(mt, isolate)
    before = MT.tree_hash(d)
    r, out = profile(mt, "accept", "foo", "--expect", before, "--profile", "p")
    assert r.code == 1
    assert out is None
    assert "nothing fetched" in r.err and "mt profile fetch foo" in r.err
    assert MT.tree_hash(d) == before


def test_accept_needs_the_fetch_record(mt: Callable[..., Result], isolate: Env) -> None:
    d = foo_profile(mt, isolate)
    upgrade_foo(isolate)
    _, fetched = profile(mt, "fetch", "foo", "--profile", "p")
    (d / ".staging" / "foo" / "fetch.json").unlink()
    r, _ = profile(mt, "accept", "foo", "--expect", fetched["tree"], "--profile", "p")
    assert r.code == 1
    assert "nothing fetched" in r.err
    assert update_md(d)[0]["companions"]["foo"]["version"] == "1.0.0+dd01fa7f1815"


@pytest.mark.parametrize(
    ("record", "error"),
    [
        ("{", "cannot read"),
        ('{"source": "plugin:foo@market", "version": 2}', "records no version"),
        ("[]", "records no"),
    ],
)
def test_accept_refuses_a_broken_fetch_record(mt: Callable[..., Result], isolate: Env, record: str, error: str) -> None:
    d = foo_profile(mt, isolate)
    upgrade_foo(isolate)
    _, fetched = profile(mt, "fetch", "foo", "--profile", "p")
    (d / ".staging" / "foo" / "fetch.json").write_text(record)
    r, _ = profile(mt, "accept", "foo", "--expect", fetched["tree"], "--profile", "p")
    assert r.code == 1
    assert error in r.err and "mt profile fetch foo" in r.err
    assert update_md(d)[0]["companions"]["foo"]["version"] == "1.0.0+dd01fa7f1815"
    assert (d / ".staging" / "foo" / "tree").is_dir(), "nothing moved"


def test_accept_refuses_when_the_source_changed_since_fetch(mt: Callable[..., Result], isolate: Env) -> None:
    d = foo_profile(mt, isolate)
    upgrade_foo(isolate)
    _, fetched = profile(mt, "fetch", "foo", "--profile", "p")
    data, body = update_md(d)
    data["companions"]["foo"]["source"] = "plugin:foo@elsewhere"
    (d / "UPDATE.md").write_text(MT.write_frontmatter(data, body))
    r, _ = profile(mt, "accept", "foo", "--expect", fetched["tree"], "--profile", "p")
    assert r.code == 1
    assert "fetch foo" in r.err and "plugin:foo@elsewhere" in r.err
    assert (d / ".staging" / "foo" / "tree").is_dir(), "nothing moved"


def test_accept_refuses_a_staging_a_later_fetch_replaced(mt: Callable[..., Result], isolate: Env) -> None:
    """Session A fetches v2 and shows its diff; session B fetches v3 into the same staging before A accepts. A's
    accept names the tree it showed and is refused, nothing moves, and B's own accept installs v3."""
    d = foo_profile(mt, isolate)
    comp, up = d / "companions" / "foo", d / "upstream" / "foo"
    before = MT.tree_hash(comp)
    upgrade_foo(isolate)
    _, a = profile(mt, "fetch", "foo", "--profile", "p")
    root3 = plugin_release(isolate, {**PLUGIN_V2, "SKILL.md": "# foo v3\n"}, "3.0.0")
    install_plugin(isolate, "foo@market", root3, version="3.0.0")
    _, b = profile(mt, "fetch", "foo", "--profile", "p")
    assert a["tree"] != b["tree"]
    r, out = profile(mt, "accept", "foo", "--expect", a["tree"], "--profile", "p")
    assert (r.code, out) == (1, None)
    assert f"holds the tree {b['tree']}, not --expect {a['tree']}" in r.err
    assert "run `mt profile fetch foo` again" in r.err
    assert MT.tree_hash(d / ".staging" / "foo" / "tree") == b["tree"], "B's fetch stays staged"
    assert json.loads((d / ".staging" / "foo" / "fetch.json").read_text())["version"] == "3.0.0"
    assert MT.tree_hash(comp) == MT.tree_hash(up) == before
    assert update_md(d)[0]["companions"]["foo"]["version"] == "1.0.0+dd01fa7f1815"
    assert not [p for p in d.iterdir() if p.name.startswith(".accept-")]

    r, out = profile(mt, "accept", "foo", "--expect", b["tree"], "--profile", "p")
    assert r.code == 0, r.err
    assert out["version"] == "3.0.0"
    assert (comp / "SKILL.md").read_text() == "# foo v3\n"


def test_accept_refuses_a_staging_edited_since_the_fetch(mt: Callable[..., Result], isolate: Env) -> None:
    d = foo_profile(mt, isolate)
    upgrade_foo(isolate)
    _, fetched = profile(mt, "fetch", "foo", "--profile", "p")
    (d / ".staging" / "foo" / "tree" / "notes.md").write_text("not what the diff showed\n")
    r, _ = profile(mt, "accept", "foo", "--expect", fetched["tree"], "--profile", "p")
    assert r.code == 1
    assert "not the fetch that was reviewed" in r.err
    assert (d / ".staging" / "foo" / "tree" / "notes.md").read_text() == "not what the diff showed\n"
    assert update_md(d)[0]["companions"]["foo"]["version"] == "1.0.0+dd01fa7f1815"


def test_accept_refusal_keeps_a_fetch_that_lands_while_it_compares(
    mt: Callable[..., Result], isolate: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """accept claims the staging before hashing it: a fetch that lands meanwhile (a writer that takes no lock: an
    older mt) stages beside it and stays; the refused tree is dropped, not put back over the newer one."""
    d = foo_profile(mt, isolate)
    upgrade_foo(isolate)
    assert profile(mt, "fetch", "foo", "--profile", "p")[0].code == 0
    newer = d / "upstream" / "foo"  # any other tree
    real = MT.tree_hash

    def tree_hash(root: Path) -> str:
        if root.parent.name == "staged" and root.parent.parent.name.startswith(".accept-"):
            landed = d / ".staging" / "foo"
            assert not landed.exists(), "claimed: no staging left to fetch into"
            MT.shutil.copytree(newer, landed / "tree")  # another session's fetch lands now
            (landed / "fetch.json").write_text('{"source": "plugin:foo@market", "version": "9"}\n')
        return real(root)

    monkeypatch.setattr(MT, "tree_hash", tree_hash)
    r, _ = profile(mt, "accept", "foo", "--expect", "sha256:stale", "--profile", "p")
    monkeypatch.setattr(MT, "tree_hash", real)
    assert r.code == 1
    assert MT.tree_hash(d / ".staging" / "foo" / "tree") == MT.tree_hash(newer), "the newer fetch stays staged"
    assert not [p for p in d.iterdir() if p.name.startswith(".accept-")]


def test_accept_records_the_version_of_the_tree_it_installs(
    mt: Callable[..., Result], isolate: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A's fetch of v2 is staged when accept --expect <v3's tree> starts; B's fetch of v3 lands before the claim
    (a writer that takes no lock: an older mt). accept reads the record from the fetch it claimed, so it installs
    v3 and records v3, never v2."""
    d = foo_profile(mt, isolate)
    staged = d / ".staging" / "foo"
    root3 = plugin_release(isolate, {**PLUGIN_V2, "SKILL.md": "# foo v3\n"}, "3.0.0")
    install_plugin(isolate, "foo@market", root3, version="3.0.0")
    v3 = profile(mt, "fetch", "foo", "--profile", "p")[1]["tree"]
    b = isolate.tmp / "b-fetch"
    MT.os.rename(staged, b)  # B's fetch of v3, ready to land
    upgrade_foo(isolate)
    assert profile(mt, "fetch", "foo", "--profile", "p")[1]["available"] == "2.0.0+0123456789ab"
    real = MT.companion_modified

    def companion_modified(d: Path, cid: str) -> bool | None:
        monkeypatch.setattr(MT, "companion_modified", real)
        MT.shutil.rmtree(staged)
        MT.os.rename(b, staged)  # B's fetch lands
        return real(d, cid)

    monkeypatch.setattr(MT, "companion_modified", companion_modified)
    r, out = profile(mt, "accept", "foo", "--expect", v3, "--profile", "p")
    assert r.code == 0, r.err
    assert out["version"] == "3.0.0"
    assert update_md(d)[0]["companions"]["foo"]["version"] == "3.0.0"
    assert (d / "companions" / "foo" / "SKILL.md").read_text() == "# foo v3\n"


V3 = {**PLUGIN_V2, "SKILL.md": "# foo v3\n"}


def test_an_accept_waits_for_another_sessions_accept(
    mt: Callable[..., Result], isolate: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reported interleaving: A's accept of v2 pauses right after it installs upstream/foo, and session B
    fetches and accepts v3 meanwhile. B waits for the profile's lock instead of running in between: A installs
    companions/foo and records v2, then B's fetch diffs against that v2 base and its accept installs and records
    v3. upstream, the copy and the recorded version always come from one fetch."""
    monkeypatch.setenv("MT_PROFILE_LOCK_WAIT", "30")
    d = foo_profile(mt, isolate)
    comp, up = d / "companions" / "foo", d / "upstream" / "foo"
    upgrade_foo(isolate)
    a_tree = profile(mt, "fetch", "foo", "--profile", "p")[1]["tree"]
    root3 = plugin_release(isolate, V3, "3.0.0")
    real_rename, real_lock = MT.os.rename, MT.profile_lock
    at_lock = threading.Event()
    b: dict[str, Any] = {}

    def session_b() -> None:
        try:
            b["fetch"] = MT.fetch_companion(d, "foo")
            b["accept"] = MT.accept_companion(d, "foo", b["fetch"]["tree"])
        except (MT.MtError, OSError) as e:  # the main thread reports it
            b["error"] = e

    def profile_lock(path: Path) -> Any:
        if threading.current_thread() is not threading.main_thread():
            at_lock.set()
        return real_lock(path)

    def rename(src: Any, dst: Any) -> None:
        real_rename(src, dst)
        if threading.current_thread() is threading.main_thread() and Path(dst) == up and "thread" not in b:
            # A has installed upstream v2 and holds the lock: B starts now
            install_plugin(isolate, "foo@market", root3, version="3.0.0")
            b["thread"] = threading.Thread(target=session_b, daemon=True)
            b["thread"].start()
            assert at_lock.wait(10), "B reached the lock"
            b["thread"].join(0.5)
            assert b["thread"].is_alive(), "B waits for A's lock"
            assert "fetch" not in b and not (d / ".staging" / "foo").exists(), "B's fetch has not landed"
            assert update_md(d)[0]["companions"]["foo"]["version"] == "1.0.0+dd01fa7f1815"

    monkeypatch.setattr(MT.os, "rename", rename)
    monkeypatch.setattr(MT, "profile_lock", profile_lock)
    r, a = profile(mt, "accept", "foo", "--expect", a_tree, "--profile", "p")
    b["thread"].join(30)
    monkeypatch.setattr(MT.os, "rename", real_rename)
    assert r.code == 0, r.err
    assert not b["thread"].is_alive()
    assert "error" not in b, b.get("error")
    assert a["version"] == "2.0.0+0123456789ab"
    # B's fetch landed after A's accept: its base is A's v2
    assert (b["fetch"]["version"], b["fetch"]["available"]) == ("2.0.0+0123456789ab", "3.0.0")
    assert "-# foo v2\n" in b["fetch"]["diff"] and "+# foo v3\n" in b["fetch"]["diff"]
    assert b["fetch"]["modified"] is False
    assert (b["accept"]["version"], b["accept"]["previous"], b["accept"]["copy"]) == (
        "3.0.0",
        "2.0.0+0123456789ab",
        "updated",
    )
    assert MT.tree_hash(up) == MT.tree_hash(comp) == b["fetch"]["tree"]
    assert update_md(d)[0]["companions"]["foo"] == {"source": "plugin:foo@market", "version": "3.0.0"}
    assert (comp / "SKILL.md").read_text() == "# foo v3\n"
    assert not (d / ".staging").exists()
    assert not [p for p in d.iterdir() if p.name.startswith((".accept-", ".fetch-"))]


def test_a_profile_command_that_cannot_get_the_lock_changes_nothing(
    mt: Callable[..., Result], isolate: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same interleaving with a short MT_PROFILE_LOCK_WAIT: B's fetch, accept and import, run while A's
    accept holds the lock after installing upstream v2, each fail with the lock error and change nothing. A ends
    with upstream, copy and record all v2; B's retry afterwards installs v3."""
    monkeypatch.setenv("MT_PROFILE_LOCK_WAIT", "0.2")
    d = foo_profile(mt, isolate)
    comp, up = d / "companions" / "foo", d / "upstream" / "foo"
    upgrade_foo(isolate)
    a_tree = profile(mt, "fetch", "foo", "--profile", "p")[1]["tree"]
    root3 = plugin_release(isolate, V3, "3.0.0")
    real = MT.os.rename
    errors: list[str] = []

    def rename(src: Any, dst: Any) -> None:
        real(src, dst)
        if Path(dst) == up and not errors:
            install_plugin(isolate, "foo@market", root3, version="3.0.0")
            calls: list[Callable[[], Any]] = [
                lambda: MT.fetch_companion(d, "foo"),
                lambda: MT.accept_companion(d, "foo", a_tree),
                lambda: MT.import_companion(d, "foo", MT.parse_source("plugin:foo@market"), True),
            ]
            for call in calls:
                with pytest.raises(MT.MtError) as e:
                    call()
                errors.append(str(e.value))
            assert not [p for p in d.iterdir() if p.name.startswith((".fetch-", ".import-"))], "B left nothing"

    monkeypatch.setattr(MT.os, "rename", rename)
    r, a = profile(mt, "accept", "foo", "--expect", a_tree, "--profile", "p")
    monkeypatch.setattr(MT.os, "rename", real)
    assert r.code == 0, r.err
    assert len(errors) == 3
    for err in errors:
        assert err.startswith("another make-task command is changing profile p; try again once it is done"), err
        assert "MT_PROFILE_LOCK_WAIT" in err
    assert a["version"] == "2.0.0+0123456789ab"
    assert MT.tree_hash(up) == MT.tree_hash(comp) == a_tree
    assert update_md(d)[0]["companions"]["foo"]["version"] == "2.0.0+0123456789ab"
    assert not (d / ".staging").exists()

    r, b = profile(mt, "fetch", "foo", "--profile", "p")  # B again, once A is done
    assert r.code == 0, r.err
    assert (b["version"], b["available"]) == ("2.0.0+0123456789ab", "3.0.0")
    assert profile(mt, "accept", "foo", "--expect", b["tree"], "--profile", "p")[0].code == 0
    assert MT.tree_hash(up) == MT.tree_hash(comp) == b["tree"]
    assert update_md(d)[0]["companions"]["foo"]["version"] == "3.0.0"


def test_the_profile_lock_holds_off_another_process_and_reads_take_none(
    mt: Callable[..., Result], isolate: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another process's accept waits for the lock this one holds, then fails and changes nothing; status,
    check and outdated read the profile meanwhile. Once the lock is dropped, the same accept succeeds."""
    monkeypatch.setenv("MT_PROFILE_LOCK_WAIT", "0.3")
    d = foo_profile(mt, isolate)
    upgrade_foo(isolate)
    _, fetched = profile(mt, "fetch", "foo", "--profile", "p")
    reads = [("status", "--profile", "p"), ("check", "--profile", "p"), ("outdated", "--profile", "p")]
    expected = [profile(mt, *args) for args in reads]
    before = MT.tree_hash(d)
    accept = [sys.executable, str(MT_PATH), "profile", "accept", "foo", "--expect", fetched["tree"], "--profile", "p"]
    with MT.profile_lock(d):
        r = subprocess.run(accept, capture_output=True, text=True, check=False)
        assert r.returncode == 1, r.stderr
        assert "another make-task command is changing profile p; try again" in r.stderr
        assert [profile(mt, *args) for args in reads] == expected
    assert MT.tree_hash(d) == before
    r = subprocess.run(accept, capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["version"] == "2.0.0+0123456789ab"


def test_install_default_waits_for_the_dir_it_installs_into(
    mt: Callable[..., Result], isolate: Env, template: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MT_PROFILE_LOCK_WAIT", "0.2")
    profiles = isolate.config_home / "profiles"
    profiles.mkdir(parents=True)
    with MT.dir_lock(profiles, "a test"):
        r, out = profile(mt, "install-default")
    assert (r.code, out) == (1, None)
    assert f"another make-task command is changing the profiles in {profiles}; try again" in r.err
    assert list(profiles.iterdir()) == [], "no profile, no work dir"
    assert profile(mt, "install-default")[0].code == 0


@pytest.mark.parametrize("wait", ["0", "-1", "soon", "nan", "inf"])
def test_a_bad_profile_lock_wait_is_refused(
    mt: Callable[..., Result], isolate: Env, monkeypatch: pytest.MonkeyPatch, wait: str
) -> None:
    d = foo_profile(mt, isolate)
    monkeypatch.setenv("MT_PROFILE_LOCK_WAIT", wait)
    before = MT.tree_hash(d)
    r, _ = profile(mt, "fetch", "foo", "--profile", "p")
    assert r.code == 1
    assert f"MT_PROFILE_LOCK_WAIT must be a positive number of seconds, got {wait!r}" in r.err
    assert MT.tree_hash(d) == before


def test_accept_needs_the_reviewed_tree(mt: Callable[..., Result], isolate: Env) -> None:
    d = foo_profile(mt, isolate)
    upgrade_foo(isolate)
    assert profile(mt, "fetch", "foo", "--profile", "p")[0].code == 0
    r = mt("profile", "accept", "foo", "--profile", "p")
    assert r.code == 2
    assert "--expect" in r.err
    assert (d / ".staging" / "foo" / "tree").is_dir()


def test_import_drops_a_pending_fetch(mt: Callable[..., Result], isolate: Env) -> None:
    d = foo_profile(mt, isolate)
    upgrade_foo(isolate)
    _, fetched = profile(mt, "fetch", "foo", "--profile", "p")
    r, _ = profile(mt, "import", "foo", "--source", "plugin:foo@market", "--replace", "--profile", "p")
    assert r.code == 0, r.err
    assert not (d / ".staging").exists()
    r, _ = profile(mt, "accept", "foo", "--expect", fetched["tree"], "--profile", "p")
    assert r.code == 1
    assert "nothing fetched" in r.err


def test_accept_recreates_a_missing_copy(mt: Callable[..., Result], isolate: Env) -> None:
    d = foo_profile(mt, isolate)
    MT.shutil.rmtree(d / "companions" / "foo")
    upgrade_foo(isolate)
    _, fetched = profile(mt, "fetch", "foo", "--profile", "p")
    r, out = profile(mt, "accept", "foo", "--expect", fetched["tree"], "--profile", "p")
    assert r.code == 0, r.err
    assert out["copy"] == "updated"
    assert MT.tree_hash(d / "companions" / "foo") == MT.tree_hash(d / "upstream" / "foo")


def test_accept_without_an_upstream_keeps_the_copy(mt: Callable[..., Result], isolate: Env) -> None:
    d = foo_profile(mt, isolate)
    MT.shutil.rmtree(d / "upstream" / "foo")
    mine = MT.tree_hash(d / "companions" / "foo")
    upgrade_foo(isolate)
    _, fetched = profile(mt, "fetch", "foo", "--profile", "p")
    r, out = profile(mt, "accept", "foo", "--expect", fetched["tree"], "--profile", "p")
    assert r.code == 0, r.err
    assert out["copy"] == "kept", "with no base to compare with, the copy may hold changes"
    assert MT.tree_hash(d / "companions" / "foo") == mine
    assert (d / "upstream" / "foo" / "new.md").is_file()


def test_update_an_installed_profile_from_a_template_change(
    mt: Callable[..., Result], isolate: Env, template: Path
) -> None:
    assert profile(mt, "install-default")[0].code == 0
    d = isolate.config_home / "profiles" / "default"
    (d / "companions" / "revmux" / "SKILL.md").write_text("# revmux, my way\n")
    for cid in ("planning", "revmux"):
        (template / "companions" / cid / "NEW.md").write_text(f"{cid}: ${{CLAUDE_PLUGIN_ROOT}}/x\n")
    out = outdated(mt)
    assert [(u["id"], u["modified"]) for u in out["updates"]] == [("planning", False), ("revmux", True)]
    for cid in ("planning", "revmux"):
        r, fetched = profile(mt, "fetch", cid)
        assert r.code == 0, r.err
        assert f"+{cid}: {d}/companions/{cid}/x\n" in fetched["diff"]
        assert profile(mt, "accept", cid, "--expect", fetched["tree"])[0].code == 0
    assert (d / "companions/planning/NEW.md").read_text() == f"planning: {d}/companions/planning/x\n"
    assert not (d / "companions/revmux/NEW.md").exists(), "a modified copy waits for its merge"
    assert (d / "upstream/revmux/NEW.md").is_file()
    assert staged_paths(d) == []
    _, chk = check(mt, d)
    assert chk == {"ok": True, "errors": [], "warnings": [], "updates": []}


def test_template_fetch_and_accept_keep_tokens_raw(mt: Callable[..., Result], isolate: Env) -> None:
    d = bare_profile(isolate, template=True)
    install_plugin(isolate, "foo@market", plugin_release(isolate, PLUGIN_V1, "1.0.0"), version="1.0.0")
    assert profile(mt, "import", "foo", "--source", "plugin:foo@market", "--dir", str(d))[0].code == 0
    upgrade_foo(isolate)
    r, out = profile(mt, "fetch", "foo", "--dir", str(d))
    assert r.code == 0, r.err
    assert "+added in v2 at ${CLAUDE_SKILL_DIR}\n" in out["diff"]
    assert profile(mt, "accept", "foo", "--expect", out["tree"], "--dir", str(d))[0].code == 0
    assert (d / "companions/foo/new.md").read_text() == PLUGIN_V2["new.md"]
    assert (d / "companions/foo/SKILL.md").read_text() == PLUGIN_V2["SKILL.md"]
    assert not (d / "data").exists(), "a template has no data dir"


# ---------------------------------------------------------------- raise-schema: past a PROFILE_SCHEMA bump


def bump_schema(monkeypatch: pytest.MonkeyPatch, template: Path) -> None:
    """A make-task release with the next PROFILE_SCHEMA: the template says it, and its planning copy carries the
    text the new SKILL.md relies on."""
    monkeypatch.setattr(MT, "PROFILE_SCHEMA", MT.PROFILE_SCHEMA + 1)
    reconfig(template, lambda c: c.update(schema=MT.PROFILE_SCHEMA))
    make = template / "companions/planning/commands/make.md"
    make.write_text(make.read_text() + "the rule the new SKILL.md relies on\n")


def test_raise_schema_after_a_schema_bump_keeps_the_users_profile(
    mt: Callable[..., Result], isolate: Env, template: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a bump, check (and so init) stops an older profile and names `/make-task --update`; raise-schema
    refuses while a make-task: companion is behind, and once the update flow took it raises the schema line
    and nothing else: the user's sections, `## Changes` and edited copies stay."""
    assert profile(mt, "install-default")[0].code == 0
    d = isolate.config_home / "profiles" / "default"
    old = MT.PROFILE_SCHEMA
    pm, update = d / "profile.md", d / "UPDATE.md"
    text = pm.read_text()
    assert f"\nschema: {old}\n" in text
    pm.write_text(text.replace(f"schema: {old}\n", f"schema: {old}  # as installed\n", 1) + "\n## exec\n\nLint.\n")
    mine = "## Changes\n\n### revmux\n- `SKILL.md` top: my way. Mine.\n"
    update.write_text(update.read_text().replace("## Changes\n", mine, 1))
    (d / "companions/revmux/SKILL.md").write_text("# revmux, my way\n")
    bump_schema(monkeypatch, template)

    _, chk = check(mt, d)
    assert chk["ok"] is False
    assert len(chk["errors"]) == 1 and "run `/make-task --update`" in chk["errors"][0]
    with pytest.raises(MT.MtError, match="make-task --update"):
        MT.run_profile(d)
    before = pm.read_text()
    r, out = profile(mt, "raise-schema")
    assert (r.code, out) == (1, None)
    assert "these companions are not at make-task's current version yet: planning; take their updates" in r.err
    assert "`/make-task --update`" in r.err
    assert pm.read_text() == before

    r, fetched = profile(mt, "fetch", "planning")
    assert r.code == 0, r.err
    assert profile(mt, "accept", "planning", "--expect", fetched["tree"])[0].code == 0
    r, out = profile(mt, "raise-schema")
    assert r.code == 0, r.err
    assert out == {"dir": str(d), "schema": old + 1, "previous": old, "raised": True}
    assert pm.read_text() == before.replace(f"schema: {old}  #", f"schema: {old + 1}  #", 1)
    assert mine in update.read_text()
    assert (d / "companions/revmux/SKILL.md").read_text() == "# revmux, my way\n"
    _, chk = check(mt, d)
    assert chk == {"ok": True, "errors": [], "warnings": [], "updates": []}
    assert MT.run_profile(d)[0]["profile_name"] == "default"
    r, again = profile(mt, "raise-schema")
    assert r.code == 0, r.err
    assert again == {**out, "previous": old + 1, "raised": False}
    assert pm.read_text() == before.replace(f"schema: {old}  #", f"schema: {old + 1}  #", 1)


def test_raise_schema_counts_only_make_task_companions(
    mt: Callable[..., Result], isolate: Env, profile_factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plugin: companion's pending update does not hold the schema back; a make-task: one whose template copy
    cannot be read does, until it is made local."""
    d = profile_factory()
    install_plugin(
        isolate, "foo@market", plugin_release(isolate, PLUGIN_V1, "1.0.0"), version="1.0.0", gitCommitSha=SHA
    )
    assert profile(mt, "import", "foo", "--source", "plugin:foo@market")[0].code == 0
    upgrade_foo(isolate)
    assert [u["id"] for u in outdated(mt)["updates"]] == ["foo"]
    MT.shutil.rmtree(isolate.templates / "default" / "companions" / "brainstorm")
    monkeypatch.setattr(MT, "PROFILE_SCHEMA", MT.PROFILE_SCHEMA + 1)
    r, _ = profile(mt, "raise-schema")
    assert r.code == 1
    listed = r.err.split("current version yet: ")[1].split("; take their updates")[0]
    assert listed.startswith("brainstorm (cannot check: source 'make-task:default/brainstorm': "), listed
    assert "foo" not in listed
    assert profile(mt, "import", "brainstorm", "--source", "local", "--replace")[0].code == 0
    r, out = profile(mt, "raise-schema")
    assert r.code == 0, r.err
    assert out["raised"] is True
    assert MT.load_profile(d)["schema"] == MT.PROFILE_SCHEMA


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"schema": 99}, "schema 99 is newer than this make-task reads"),
        ({"schema": "1"}, "schema: expected an integer, got '1'"),
        ({"template": True}, "is a template: its schema follows PROFILE_SCHEMA; set it to"),
    ],
    ids=["newer", "not an integer", "template"],
)
def test_raise_schema_refuses(
    mt: Callable[..., Result], profile_factory: Factory, change: dict[str, Any], error: str
) -> None:
    d = profile_factory()
    reconfig(d, lambda c: c.update(change))
    before = MT.tree_hash(d)
    r, out = profile(mt, "raise-schema", "--dir", str(d))
    assert (r.code, out) == (1, None)
    assert error in r.err
    assert MT.tree_hash(d) == before


def test_raise_schema_needs_a_profile(mt: Callable[..., Result]) -> None:
    r, out = profile(mt, "raise-schema", "--profile", "nope")
    assert (r.code, out) == (1, None)
    assert "no profile at" in r.err


@pytest.mark.parametrize("line", ["schema:\n  {old}", "schema: !!int {old}"], ids=["value on the next line", "a tag"])
def test_raise_schema_changes_only_a_plain_schema_line(
    mt: Callable[..., Result], profile_factory: Factory, monkeypatch: pytest.MonkeyPatch, line: str
) -> None:
    """A `schema:` line it cannot set without touching anything else is refused, and profile.md stays."""
    d = profile_factory()
    old = MT.PROFILE_SCHEMA
    pm = d / "profile.md"
    pm.write_text(pm.read_text().replace(f"schema: {old}\n", line.format(old=old) + "\n", 1))
    assert MT.load_profile(d)["schema"] == old
    monkeypatch.setattr(MT, "PROFILE_SCHEMA", old + 1)
    before = pm.read_text()
    r, _ = profile(mt, "raise-schema", "--dir", str(d))
    assert r.code == 1
    assert f"found no one `schema: {old}` line to raise; set it to {old + 1} by hand" in r.err
    assert pm.read_text() == before


def test_raise_schema_holds_the_profile_lock(
    mt: Callable[..., Result], profile_factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    d = profile_factory()
    monkeypatch.setattr(MT, "PROFILE_SCHEMA", MT.PROFILE_SCHEMA + 1)
    monkeypatch.setenv("MT_PROFILE_LOCK_WAIT", "0.2")
    before = (d / "profile.md").read_text()
    with MT.profile_lock(d):
        r, out = profile(mt, "raise-schema", "--dir", str(d))
    assert (r.code, out) == (1, None)
    assert f"another make-task command is changing profile {d.name}; try again" in r.err
    assert (d / "profile.md").read_text() == before
    r, out = profile(mt, "raise-schema", "--dir", str(d))
    assert r.code == 0, r.err
    assert out["raised"] is True


# ---------------------------------------------------------------- the shipped default template

SHIPPED_SOURCES = {
    "brainstorm": "plugin:brainstorm@umputun-cc-thingz",
    "planning": "plugin:planning@umputun-cc-thingz",
    "revmux": "plugin:revmux@revmux:.claude-plugin/skills/revmux",
}


def shipped_files() -> list[Path]:
    """Every file in the shipped template, dotfiles included."""
    return sorted(Path(root) / name for root, _, names in MT.os.walk(SHIPPED) for name in names)


def names(d: Path) -> list[str]:
    """The entries of d that mt counts, sorted: Finder's files (MT.TREE_IGNORE) are not part of any tree."""
    return sorted(p.name for p in d.iterdir() if p.name not in MT.TREE_IGNORE)


def test_shipped_template_profile_md() -> None:
    config, _ = MT.read_frontmatter((SHIPPED / "profile.md").read_text())
    assert config == {**profile_config(), "template": True}, "the conftest stubs mirror the shipped phases map"
    assert "gates" not in config, "the autonomy preset decides the gates"


def test_shipped_template_records_its_sources() -> None:
    comps, _ = MT.read_frontmatter((SHIPPED / "UPDATE.md").read_text())
    assert comps is not None
    assert {cid: rec["source"] for cid, rec in comps["companions"].items()} == SHIPPED_SOURCES
    assert all(rec["version"] for rec in comps["companions"].values())
    for tree in ("companions", "upstream"):
        assert names(SHIPPED / tree) == sorted(SHIPPED_SOURCES), tree
    assert not (SHIPPED / "data").exists(), "a template has no data dir"


def test_shipped_template_check_passes(mt: Callable[..., Result], isolate: Env) -> None:
    """check --dir on the shipped template, with its plugins installed at the recorded versions: nothing to report."""
    comps, _ = MT.read_frontmatter((SHIPPED / "UPDATE.md").read_text())
    assert comps is not None
    for cid, rec in comps["companions"].items():
        src = MT.parse_source(rec["source"])
        version, _, sha = rec["version"].partition("+")
        root = isolate.tmp / "installed" / cid
        (root / src.subpath).mkdir(parents=True)
        install_plugin(isolate, src.where, root, version=version, gitCommitSha=sha)
    _, out = check(mt, SHIPPED)
    assert out == {"ok": True, "errors": [], "warnings": [], "updates": []}


def test_shipped_template_has_a_license_per_companion() -> None:
    assert names(SHIPPED / "LICENSES") == sorted(SHIPPED_SOURCES)
    for cid in SHIPPED_SOURCES:
        text = (SHIPPED / "LICENSES" / cid).read_text()
        assert "License" in text.splitlines()[0] and "Permission is hereby granted" in text, cid


def test_shipped_template_has_no_absolute_home_path() -> None:
    hits = [
        str(p.relative_to(SHIPPED))
        for p in shipped_files()
        if any(home in p.read_bytes() for home in (b"/Users/", b"/home/"))
    ]
    assert hits == []


def test_shipped_review_preflight_fails_without_revmux(tmp_path: Path) -> None:
    """SKILL.md Start step 3 counts a missing review `requires` command as a failed preflight; for the shipped
    `requires: [revmux]` the copy's preflight.sh agrees on its own."""
    empty = tmp_path / "empty-path"
    empty.mkdir()
    r = MT.subprocess.run(
        ["/bin/bash", str(SHIPPED / "companions" / "revmux" / "scripts" / "preflight.sh")],
        env={"PATH": str(empty)},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert r.returncode == 1
    assert "revmux: MISSING" in r.stdout and "ok: false" in r.stdout


# a relative path in a companion's text: not part of a longer path or a ${...} token
RELATIVE_PATH_RE = re.compile(r"(?<![\w/}.$-])((?:references|scripts)/[\w.-]+(?:/[\w.-]+)*)")


def test_shipped_entries_relative_paths_resolve() -> None:
    """SKILL.md Running a companion: a Read announces no base directory, so a relative `references/…` or
    `scripts/…` path in a companion's files resolves against the directory of the phase's entry file. Every such
    path in the brainstorm, plan and review companions must exist there."""
    config, _ = MT.read_frontmatter((SHIPPED / "profile.md").read_text())
    assert config is not None
    checked = 0
    for pid in ("brainstorm", "plan", "review"):
        spec = config["phases"][pid]
        comp = SHIPPED / "companions" / spec["companion"]
        entry_dir = (comp / spec["entry"]).parent
        for rel, path in MT.tree_files(comp):
            if not rel.endswith(".md"):
                continue
            for ref in RELATIVE_PATH_RE.findall(path.read_text(encoding="utf-8")):
                ref = ref.rstrip(".")
                assert (entry_dir / ref).exists(), f"{pid}: companions/{spec['companion']}/{rel} names {ref}"
                checked += 1
    assert checked >= 8, "the revmux entry points at its references/ and scripts/"


def test_shipped_template_holds_no_claude_dir() -> None:
    """Claude Code registers <plugin>/skills/<name>/SKILL.md, <plugin>/commands/, <plugin>/agents/,
    ~/.claude/skills/<name>/SKILL.md and nested .claude/{skills,commands,agents}/ in a project. The template's
    copies sit below skills/make-task/assets/, deeper than any of those; a .claude/ dir would be found."""
    assert [p for p in SHIPPED.rglob("*") if p.name == ".claude"] == []


# ---------------------------------------------------------------- make-task's edits in the shipped copies

# (rule, companion file under companions/, phrase, kept): kept=True -> make-task added the phrase (in the copy, not
# in upstream/); kept=False -> make-task removed it (in upstream/, gone from the copy)
TEMPLATE_EDITS = [
    # brainstorm: questions go to the orchestrator's question handling, one at a time
    (
        "brainstorm questions to the orchestrator",
        "brainstorm/skills/brainstorm/SKILL.md",
        "Every question goes to the orchestrator's question handling, never straight to AskUserQuestion",
        True,
    ),
    ("brainstorm questions on cards", "brainstorm/skills/brainstorm/SKILL.md", "make-task's question cards", True),
    # brainstorm Phase 2: the approach is asked only when it needs the user's knowledge or taste
    (
        "brainstorm approach asked on knowledge or taste only",
        "brainstorm/skills/brainstorm/SKILL.md",
        "ask which approach the user wants only when the choice needs their knowledge or taste",
        True,
    ),
    # brainstorm Phase 3: the whole design once
    (
        "brainstorm design written once",
        "brainstorm/skills/brainstorm/SKILL.md",
        "**Write the whole design once**",
        True,
    ),
    (
        "brainstorm no section validation",
        "brainstorm/skills/brainstorm/SKILL.md",
        "**No section-by-section validation**",
        True,
    ),
    ("brainstorm design at once", "brainstorm/skills/brainstorm/SKILL.md", "Present the entire design at once.", True),
    ("brainstorm no ask per section", "brainstorm/skills/brainstorm/SKILL.md", "**Ask after each section**", False),
    (
        "brainstorm no incremental closing",
        "brainstorm/skills/brainstorm/SKILL.md",
        "Do not present entire design at once.",
        False,
    ),
    # brainstorm Phase 4: no next-step menu
    ("brainstorm no menu", "brainstorm/skills/brainstorm/SKILL.md", "No next-step menu.", True),
    (
        "brainstorm menu not asked",
        "brainstorm/skills/brainstorm/SKILL.md",
        "After design is validated, use AskUserQuestion tool:",
        False,
    ),
    # brainstorm Key Principles
    ("brainstorm principle design at once", "brainstorm/skills/brainstorm/SKILL.md", "**Whole design at once**", True),
    (
        "brainstorm principle no incremental",
        "brainstorm/skills/brainstorm/SKILL.md",
        "present design in sections, validate each",
        False,
    ),
    ("brainstorm principle no let user decide", "brainstorm/skills/brainstorm/SKILL.md", "but let user decide", False),
    (
        "brainstorm principle duplication decided",
        "brainstorm/skills/brainstorm/SKILL.md",
        "against abstraction (DRY but adds complexity) and decide",
        True,
    ),
    ("brainstorm principle no ask user", "brainstorm/skills/brainstorm/SKILL.md", "when code repeats, ask user", False),
    # plan step 0/1: no questions, inputs from the design file, testing approach decided and recorded
    ("plan step 0 no asking", "planning/commands/make.md", "take it from the design file in step 1", True),
    ("plan step 0 asked before", "planning/commands/make.md", "ask the user in step 1", False),
    ("plan step 1 no questions", "planning/commands/make.md", "**no questions under make-task.**", True),
    (
        "plan step 1 testing approach",
        "planning/commands/make.md",
        "decide the testing approach yourself, Regular unless the design or the project's rules call for TDD",
        True,
    ),
    (
        "plan step 1 testing approach recorded",
        "planning/commands/make.md",
        "call for TDD, and record it as an assumption with the reason",
        True,
    ),
    # plan step 1.5 skipped, step 3 no menu
    ("plan step 1.5 skipped", "planning/commands/make.md", "**skipped under make-task.**", True),
    ("plan step 3 no menu", "planning/commands/make.md", "no menu under make-task.", True),
    ("plan step 3 menu not asked", "planning/commands/make.md", "then use AskUserQuestion:", False),
    # plan key principles
    ("plan principle no questions", "planning/commands/make.md", "- **no questions** -", True),
    ("plan principle one question gone", "planning/commands/make.md", "**one question at a time**", False),
    ("plan principle approach from design", "planning/commands/make.md", "**approach from the design**", True),
    (
        "plan principle no alternatives",
        "planning/commands/make.md",
        "always propose 2-3 approaches before settling",
        False,
    ),
    ("plan principle no let user decide", "planning/commands/make.md", "but let user decide", False),
    ("plan principle no ask user", "planning/commands/make.md", "when code repeats, ask user", False),
    # revmux non-negotiable 0: the user's answer is make-task's own card, not a question per round
    ("revmux answer is a card", "revmux/SKILL.md", "**Under make-task that answer is make-task's own card**", True),
    # revmux Step 0: preflight already ran at make-task's start; once more with round 1's profile
    ("revmux preflight already ran", "revmux/SKILL.md", "**Under make-task preflight already ran**", True),
    ("revmux preflight round 1 profile", "revmux/SKILL.md", "with the profile Step 3 picks for round 1", True),
    # revmux Step 0.5: never create or offer the project profile; generic calibration recorded as an assumption
    ("revmux no project profile", "revmux/SKILL.md", "**Under make-task never create or offer it.**", True),
    ("revmux no profile assumption", "revmux/SKILL.md", "record as an assumption that the", True),
    ("revmux no round-local profile", "revmux/SKILL.md", "write no round-local `input/profile.md`", True),
    # revmux Step 1: the scope is fixed to review_base..HEAD with the carried exclusions
    ("revmux scope fixed", "revmux/SKILL.md", "**Under make-task the scope is fixed and never asked**", True),
    ("revmux scope range", "revmux/SKILL.md", "`git diff <review_base>..HEAD`", True),
    ("revmux review_base input", "revmux/SKILL.md", "`review_base` the commit the orchestrator hands over", True),
    ("revmux scope exclusions", "revmux/SKILL.md", "with every exclusion earlier rounds carried", True),
    # revmux Step 3: the profile is chosen without asking, later rounds by loop.md
    ("revmux profile not asked", "revmux/SKILL.md", "**Under make-task the profile is chosen without asking**", True),
    ("revmux later rounds by loop.md", "revmux/SKILL.md", "later rounds by `references/loop.md`", True),
    (
        "revmux profile questions not asked",
        "revmux/SKILL.md",
        "The questions in this step and in Step 7 are the standalone skill's: do not ask them.",
        True,
    ),
    # revmux Step 4: headless only
    ("revmux headless only", "revmux/SKILL.md", "**Under make-task: headless only, never the overlay**", True),
    # revmux Step 6: no question, no present.md turn, one line per round
    ("revmux no present turn", "revmux/SKILL.md", "**Under make-task: no question and no `present.md` turn.**", True),
    ("revmux one-line round report", "revmux/SKILL.md", "round in one plain line with the combined tags below", True),
    # revmux loop.md: the rounds review from review_base, not the starting commit
    (
        "revmux loop scope from review_base",
        "revmux/references/loop.md",
        "**Under make-task the rounds review the diff from `review_base`**",
        True,
    ),
    (
        "revmux loop iteration scope",
        "revmux/references/loop.md",
        "Under make-task that cumulative diff starts at",
        True,
    ),
    # revmux loop.md: the closing question is the leftovers card, whose fix gets one confirming round
    (
        "revmux loop no closing question",
        "revmux/references/loop.md",
        "**Under make-task the closing question below is not asked.**",
        True,
    ),
    ("revmux loop leftovers card", "revmux/references/loop.md", "make-task's leftovers card replaces it", True),
    (
        "revmux loop confirming round",
        "revmux/references/loop.md",
        "**Under make-task one review round does run after the leftovers card.**",
        True,
    ),
    (
        "revmux loop confirming final",
        "revmux/references/loop.md",
        "confirming round under `final` follows the fix",
        True,
    ),
    # revmux loop.md: archive housekeeping skipped
    (
        "revmux loop no housekeeping",
        "revmux/references/loop.md",
        "**Archive housekeeping is skipped under make-task.**",
        True,
    ),
]


@pytest.mark.parametrize(("rule", "rel", "phrase", "kept"), TEMPLATE_EDITS, ids=[e[0] for e in TEMPLATE_EDITS])
def test_template_copy_carries_make_task_rule(rule: str, rel: str, phrase: str, kept: bool) -> None:
    copy = (SHIPPED / "companions" / rel).read_text(encoding="utf-8")
    base = (SHIPPED / "upstream" / rel).read_text(encoding="utf-8")
    assert (phrase in copy) is kept, f"{rule}: copy {rel}"
    assert (phrase in base) is not kept, f"{rule}: the phrase must be make-task's edit, not upstream text"


def mt_command_re() -> re.Pattern[str]:
    """An `mt` call: the word mt followed by one of its commands or --run, or the script path itself."""
    commands = subparsers(MT.build_parser())
    names = "|".join(sorted(map(re.escape, commands), key=len, reverse=True))
    return re.compile(rf"(?<![\w/.-])mt\s+(?:--run|(?:{names}))(?![\w-])|scripts/mt(?![\w.-])")


@pytest.mark.parametrize(
    ("text", "hit"),
    [
        ('`mt assume "testing approach: x"`', True),
        ("mt --run <state_dir> status", True),
        ("then mt phase plan", True),
        ("uv run -q --script <skill dir>/scripts/mt", True),
        ("record it as an assumption", False),
        ("smt phase", False),
        ("format status", False),
        ("the mt CLI", False),
        ("mt assumed", False),
    ],
)
def test_mt_command_pattern(text: str, hit: bool) -> None:
    assert bool(mt_command_re().search(text)) is hit


def test_template_companions_name_no_mt_command() -> None:
    """Copies say "the orchestrator", "make-task's question cards", "record it as an assumption"; the `mt` calls
    stay in SKILL.md."""
    pattern = mt_command_re()
    hits = []
    for p in sorted((SHIPPED / "companions").rglob("*")):
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                hits.append(f"{p.relative_to(SHIPPED)}:{n}: {line.strip()}")
    assert hits == []


def changes_by_companion(update_md: str) -> dict[str, set[str]]:
    """The files each `### <id>` sub-heading of `## Changes` names, one `- `<path>` ...` line per edit."""
    _, body = MT.read_frontmatter(update_md)
    section = body.split("\n## Changes\n", 1)[1].split("\n## ", 1)[0]
    named: dict[str, set[str]] = {}
    current = None
    for line in section.splitlines():
        if line.startswith("### "):
            current = line[4:].strip()
            assert current not in named, f"one sub-heading per companion: {current}"
            named[current] = set()
        elif line.startswith("- "):
            m = re.match(r"- `([^`]+)` \S", line)
            assert current is not None and m, f"a Changes line names its companion and file: {line!r}"
            named[current].add(m.group(1))
        else:
            assert not line.strip(), f"unexpected line in ## Changes: {line!r}"
    return named


def edited_files(cid: str) -> set[str]:
    """Files that differ between the template's companions/<id> and upstream/<id> (added, removed, or changed in
    content or kind: an exec bit counts, as in MT.tree_hash; Finder's files do not)."""
    copy, base = (MT.tree_entries(SHIPPED / tree / cid) for tree in ("companions", "upstream"))
    return {rel for rel in copy.keys() | base.keys() if copy.get(rel) != base.get(rel)}


def test_template_changes_name_every_edited_file() -> None:
    """UPDATE.md `## Changes` has a line for each make-task edit: the files it names under `### <id>` are exactly
    the files where companions/<id> differs from upstream/<id>."""
    named = changes_by_companion((SHIPPED / "UPDATE.md").read_text(encoding="utf-8"))
    assert set(named) <= set(SHIPPED_SOURCES)
    assert {cid: named.get(cid, set()) for cid in SHIPPED_SOURCES} == {
        cid: edited_files(cid) for cid in SHIPPED_SOURCES
    }


def test_template_revmux_copy_keeps_the_safety_non_negotiables() -> None:
    """make-task's overrides never relax a safety rule: revmux non-negotiables 1, 3, 4, 5 and 6 read as upstream's
    (0 gives way to make-task's cards, 2 holds through make-task's own wait)."""

    def rules(root: Path) -> dict[str, str]:
        text = (root / "revmux" / "SKILL.md").read_text(encoding="utf-8")
        section = text.split("\n## Non-negotiables\n", 1)[1].split("\n## ", 1)[0]
        parts = re.split(r"^\*\*(\d+)\. ", section, flags=re.MULTILINE)
        return dict(zip(parts[1::2], parts[2::2], strict=True))

    copy, base = rules(SHIPPED / "companions"), rules(SHIPPED / "upstream")
    assert sorted(base) == [str(n) for n in range(7)]
    assert sorted(copy) == sorted(base)
    for n in "13456":
        assert copy[n] == base[n], f"non-negotiable {n}"


def test_changes_by_companion_parses_lines() -> None:
    text = (
        "---\na: 1\n---\n\n## Preferences\n\n## Changes\n\n### x\n\n- `a/b.md` step 1: why.\n- `c.md` y.\n\n## Wanted\n"
    )
    assert changes_by_companion(text) == {"x": {"a/b.md", "c.md"}}
    with pytest.raises(AssertionError, match="names its companion"):
        changes_by_companion("---\na: 1\n---\n\n## Changes\n\n- `a.md` no heading.\n")
