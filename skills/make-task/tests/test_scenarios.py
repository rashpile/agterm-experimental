"""Edge cases of a whole run, end to end.

The review/completion cases go through `mt` commands only (no state edits), so they check the commands
together the way the orchestrator chains them. The stash cases run the exact shell recipes from SKILL.md
(Exec step 1 and Completion step 3) against a real repo. The profile cases install the default template
make-task ships and run it, and update a profile with a user's edit from a newer copy of that template.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
from conftest import FIXTURES, MT, SHIPPED, SHIPPED_TEMPLATES, TESTS, commit, git, init, profile

SKILL_TEXT = (TESTS.parent / "SKILL.md").read_text(encoding="utf-8")
FINDINGS = FIXTURES / "revmux-findings.json"
DEGRADED = FIXTURES / "revmux-degraded.json"
TASK = "session-store"
# every critical/major id of the findings fixture
GATING = ("bugs+impl-1", "bugs+impl-2", "adversarial-1", "adversarial-2")


def to_review(mt, repo: Path) -> Path:
    run = Path(init(mt, repo)["state_dir"])
    for pid in ("brainstorm", "plan", "exec", "review"):
        assert mt("phase", pid).code == 0
    assert mt("set", f"revmux_task={TASK}").code == 0
    return run


def round_(mt, tmp: Path, n: int, report: Path, *flags: str) -> None:
    name = f"{n:02d}-round"
    round_dir = tmp / "tasks" / TASK / name
    round_dir.mkdir(parents=True)
    r = mt("review", "start", "--round", str(n), "--run", name, "--round-dir", str(round_dir))
    assert r.code == 0, r.err
    r = mt("review", "result", str(report), "--round", str(n), *flags)
    assert r.code == 0, r.err


def finalize_ok(mt, repo: Path) -> None:
    """Finalize as the orchestrator does it: reviewed_sha, the checkpoint ended ok, verify-final."""
    assert mt("phase", "finalize").code == 0
    assert mt("set", f"reviewed_sha={git(repo, 'rev-parse', 'HEAD')}").code == 0
    assert mt("checkpoint", "begin", "finalize").code == 0
    assert mt("checkpoint", "end", "finalize", "--result", "ok").code == 0
    r = mt("review", "verify-final", cwd=repo)
    assert (r.code, r.out) == (0, "unchanged\n"), r.err


def refuse_clean(mt, *why: str) -> None:
    r = mt("done", "--status", "clean")
    assert r.code == 1
    for w in why:
        assert w in r.err, r.err


# ---------------------------------------------------------------- review endings


def test_round_cap_with_majors_left_ends_not_clean(mt, git_repo, tmp_path, run_state):
    run = to_review(mt, git_repo)
    for n in range(1, 6):
        round_(mt, tmp_path, n, FINDINGS, "--deferred", "adversarial-1=prose", "--not-gating", "adversarial-2=vendor")
    assert mt("set", "stop_reason=round-cap").code == 0
    st = run_state(run)
    assert len(st["review"]) == 5 and st["stop_reason"] == "round-cap"
    assert [u["id"] for u in st["unresolved"]] == ["bugs+impl-1", "bugs+impl-2"]
    assert [d["id"] for d in st["deferred"]] == ["adversarial-1"]
    assert mt("phase", "finalize").code == 0
    refuse_clean(mt, "2 unresolved gating", "1 deferred")
    assert mt("done", "--status", "not clean").code == 0
    assert run_state(run)["final_status"] == "not clean"


def test_not_gating_majors_let_the_run_end_clean(mt, git_repo, tmp_path, run_state):
    run = to_review(mt, git_repo)
    flags = [x for i in GATING for x in ("--not-gating", f"{i}=outside scope per goal.md")]
    round_(mt, tmp_path, 1, FINDINGS, *flags)
    assert mt("set", "stop_reason=clean").code == 0
    st = run_state(run)
    assert st["unresolved"] == [] and st["deferred"] == []
    assert {g["id"] for g in st["not_gating"]} == set(GATING)
    assert st["review"][0]["verdict"] == "clean" and st["review"][0]["major"] == 3
    review_md = (run / "review.md").read_text()
    assert review_md.count("not gating: outside scope per goal.md") == len(GATING)
    finalize_ok(mt, git_repo)
    assert mt("done", "--status", "clean").code == 0
    assert run_state(run)["final_status"] == "clean"


def test_degraded_round_blocks_clean_until_a_rerun_is_clean(mt, git_repo, tmp_path, run_state):
    run = to_review(mt, git_repo)
    round_(mt, tmp_path, 1, DEGRADED)
    assert run_state(run)["review"][0]["verdict"] == "degraded"
    assert mt("fail", "round 1 degraded").code == 0
    refuse_clean(mt, "last review round was degraded", "error state")
    # the error card's rerun: mt activity resumes, the re-run is a new round
    assert mt("activity", "Review · re-running round 1 as round 2").code == 0
    flags = [x for i in GATING for x in ("--not-gating", f"{i}=x")]
    round_(mt, tmp_path, 2, FINDINGS, *flags)
    finalize_ok(mt, git_repo)
    assert mt("done", "--status", "clean").code == 0


def test_clean_needs_a_finished_round_verify_final_and_finalize_ok(mt, git_repo, tmp_path):
    to_review(mt, git_repo)
    assert mt("phase", "finalize").code == 0
    refuse_clean(mt, "no review round has a result", "reviewed_sha is not set", "finalize checkpoint did not end ok")
    flags = [x for i in GATING for x in ("--not-gating", f"{i}=x")]
    round_(mt, tmp_path, 1, FINDINGS, *flags)
    assert mt("set", f"reviewed_sha={git(git_repo, 'rev-parse', 'HEAD')}").code == 0
    assert mt("checkpoint", "begin", "finalize").code == 0
    assert mt("checkpoint", "end", "finalize", "--result", "tests failed").code == 0
    refuse_clean(mt, "verify-final has not checked reviewed_sha", "result 'tests failed'")
    assert mt("done", "--status", "not clean").code == 0


def test_crashed_confirming_round_blocks_clean(mt, git_repo, tmp_path):
    """Round 1 is clean, the leftovers fix runs and its confirming round 2 never reports: the fix commits
    were not reviewed, so the run cannot end clean."""
    to_review(mt, git_repo)
    round_(mt, tmp_path, 1, FINDINGS, *[x for i in GATING for x in ("--not-gating", f"{i}=x")])
    rd = tmp_path / "tasks" / TASK / "02-confirm"
    assert mt("review", "start", "--round", "2", "--run", "02-confirm", "--round-dir", str(rd)).code == 0
    finalize_ok(mt, git_repo)
    refuse_clean(mt, "review round 2 has no result")
    assert mt("done", "--status", "not clean").code == 0


def leftovers_fix(mt, repo: Path) -> str:
    """The leftovers card's `fix`: one fixer between the checkpoint pair, committing a fix."""
    assert mt("checkpoint", "begin", "review.fix.leftovers").code == 0
    (repo / "src" / "a.txt").write_text("a\nfixed minor\n")
    sha = commit(repo, "fix: leftovers")
    assert mt("checkpoint", "end", "review.fix.leftovers").code == 0
    return sha


def test_leftovers_fix_without_its_confirming_round_cannot_end_clean(mt, git_repo, tmp_path, run_state):
    """A compact after the leftovers fix, before its confirming round: findings lists are empty and verify-final
    is unchanged, but the fix commit is not what any round reviewed."""
    run = to_review(mt, git_repo)
    round_(mt, tmp_path, 1, FINDINGS, *[x for i in GATING for x in ("--not-gating", f"{i}=x")])
    reviewed = run_state(run)["review"][0]["head"]
    fixed = leftovers_fix(mt, git_repo)
    status = mt("status").json()
    assert {"key": "review.fix.leftovers", "result": None} in status["ended"]
    assert status["rounds"][-1]["head"] == reviewed != fixed, "Recovery reads the gap from status"
    finalize_ok(mt, git_repo)
    refuse_clean(mt, f"reviewed_sha {fixed[:12]} is not the HEAD review round 1 reviewed ({reviewed[:12]})")
    assert mt("done", "--status", "not clean").code == 0


def test_leftovers_fix_with_its_confirming_round_ends_clean(mt, git_repo, tmp_path, run_state):
    run = to_review(mt, git_repo)
    round_(mt, tmp_path, 1, FINDINGS, *[x for i in GATING for x in ("--not-gating", f"{i}=x")])
    fixed = leftovers_fix(mt, git_repo)
    round_(mt, tmp_path, 2, FINDINGS, "--confirming", *[x for i in GATING for x in ("--not-gating", f"{i}=x")])
    assert run_state(run)["review"][1]["head"] == fixed
    finalize_ok(mt, git_repo)
    assert mt("done", "--status", "clean").code == 0


def test_rerun_of_finalize_step_1_cannot_vouch_for_the_finalizer_commit(mt, git_repo, tmp_path, run_state):
    """A compact after `checkpoint end finalize`: re-setting reviewed_sha to the post-finalize HEAD is refused,
    so verify-final still compares the finalizer's commit with what the round reviewed."""
    run = to_review(mt, git_repo)
    git(git_repo, "checkout", "-q", "-b", "feature")
    (git_repo / "src" / "a.txt").write_text("a\nfeature\n")
    reviewed = commit(git_repo, "feature")
    round_(mt, tmp_path, 1, FINDINGS, *[x for i in GATING for x in ("--not-gating", f"{i}=x")])
    assert mt("phase", "finalize").code == 0
    assert mt("set", f"reviewed_sha={reviewed}").code == 0
    assert mt("checkpoint", "begin", "finalize").code == 0
    (git_repo / "src" / "late.txt").write_text("the finalizer fixed a test\n")
    after = commit(git_repo, "fix tests")
    assert mt("checkpoint", "end", "finalize", "--result", "ok").code == 0
    r = mt("set", f"reviewed_sha={after}")
    assert r.code == 1 and "written once" in r.err
    assert run_state(run)["reviewed_sha"] == reviewed
    r = mt("review", "verify-final", cwd=git_repo)
    assert (r.code, r.out) == (0, "changed\n"), r.err
    refuse_clean(mt, "review is stale")


def test_deferred_major_stays_open_when_a_later_round_does_not_repeat_it(mt, git_repo, tmp_path, run_state):
    run = to_review(mt, git_repo)
    round_(mt, tmp_path, 1, FINDINGS, "--deferred", "adversarial-1=prose", *[
        x for i in GATING if i != "adversarial-1" for x in ("--not-gating", f"{i}=x")
    ])  # fmt: skip
    clean = json.loads(FINDINGS.read_text())
    clean["findings"] = [f for f in clean["findings"] if f["severity"] == "minor"]
    report = tmp_path / "clean.json"
    report.write_text(json.dumps(clean))
    round_(mt, tmp_path, 2, report)
    assert mt("set", "stop_reason=clean").code == 0
    assert [d["id"] for d in run_state(run)["deferred"]] == ["adversarial-1"]
    finalize_ok(mt, git_repo)
    refuse_clean(mt, "1 deferred")
    assert mt("done", "--status", "not clean").code == 0


def test_degraded_confirming_round_ends_not_clean(mt, git_repo, tmp_path, run_state):
    run = to_review(mt, git_repo)
    round_(mt, tmp_path, 1, FINDINGS, "--deferred", "adversarial-1=prose", "--not-gating", "adversarial-2=vendor")
    assert mt("set", "stop_reason=not-converging").code == 0
    assert [d["id"] for d in run_state(run)["deferred"]] == ["adversarial-1"]
    # leftovers card → fix → one confirming round, which comes back degraded
    round_(mt, tmp_path, 2, DEGRADED, "--confirming")
    assert mt("set", "stop_reason=degraded").code == 0
    st = run_state(run)
    assert st["unresolved"] == [] and st["deferred"] == [], "the confirming round settles the carried list"
    finalize_ok(mt, git_repo)
    refuse_clean(mt, "last review round was degraded")
    assert mt("done", "--status", "not clean").code == 0


def test_stale_review_after_finalize_without_origin(mt, git_repo, run_state):
    run = to_review(mt, git_repo)
    assert run_state(run)["has_origin"] is False
    git(git_repo, "checkout", "-q", "-b", "feature")
    (git_repo / "src" / "a.txt").write_text("a\nfeature\n")
    reviewed = commit(git_repo, "feature")
    assert mt("phase", "finalize").code == 0
    assert mt("set", f"reviewed_sha={reviewed}").code == 0
    # the finalizer adds content after the review
    (git_repo / "src" / "late.txt").write_text("late\n")
    commit(git_repo, "late change")
    r = mt("review", "verify-final", cwd=git_repo)
    assert (r.code, r.out) == (0, "changed\n"), r.err
    assert run_state(run)["review_stale"] is True
    refuse_clean(mt, "review is stale")
    assert mt("done", "--status", "review stale").code == 0


# ---------------------------------------------------------------- outside agterm


def test_outside_agterm_two_runs_share_the_pointer_and_run_flag_keeps_them_apart(
    mt, git_repo, tmp_path, isolate, run_state
):
    first = Path(init(mt, git_repo)["state_dir"])
    assert init(mt, git_repo)["agterm"] is False
    second = Path((isolate.state_home / "current-default").read_text().strip())
    assert second != first, "the second run took over current-default"
    for args in (("phase", "brainstorm"), ("assume", "x", "--why", "y"), ("phase", "plan")):
        assert mt("--run", str(first), *args).code == 0
    card = tmp_path / "ask.json"
    card.write_text(json.dumps({"phase": "brainstorm", "title": "Q?", "options": [{"id": "a", "label": "A",
                                "recommended": True}, {"id": "b", "label": "B"}]}))  # fmt: skip
    r = mt("--run", str(first), "ask", str(card))
    assert r.code == 3 and json.loads(r.out) == {"choice": None}
    assert mt("--run", str(first), "decide", "Q?", "b").code == 0
    assert mt("--run", str(first), "done", "--status", "stopped").code == 0
    one, two = run_state(first), run_state(second)
    assert one["final_status"] == "stopped" and one["decisions"] == [{"q": "Q?", "a": "b"}]
    assert two["final_status"] is None and two["decisions"] == [] and two["assumptions"] == []
    assert isolate.agterm_calls() == []
    assert not (first / "cards").exists() or not any((first / "cards").iterdir()), "exit 3 writes no card"


# ---------------------------------------------------------------- dirty tree, worktree mode


def test_dirty_main_checkout_in_worktree_mode(mt, git_repo, tmp_path, run_state):
    (git_repo / "README.md").write_text("user edit\n")
    (git_repo / "scratch.txt").write_text("untracked\n")
    info = init(mt, git_repo)
    run = Path(info["state_dir"])
    assert sorted(info["dirty_paths"]) == ["README.md", "scratch.txt"]
    wt = (git_repo / ".claude" / "worktrees" / "store").resolve()
    git(git_repo, "worktree", "add", "-q", "-b", "store", str(wt))
    assert mt("set", f"worktree_path={wt}").code == 0
    (wt / "docs" / "plans").mkdir(parents=True)
    shutil.copy(FIXTURES / "plan.md", wt / "docs" / "plans" / "p.md")
    commit(wt, "docs: add plan")
    assert mt("plan", "docs/plans/p.md", cwd=wt).code == 0
    assert mt("refresh", cwd=wt).code == 0
    # the user's edits stay in the main checkout and are absent from the worktree
    assert git(wt, "status", "--porcelain") == "", "the before-round-1 clean check passes in the worktree"
    assert (wt / "README.md").read_text() == "hello\n" and not (wt / "scratch.txt").exists()
    assert git(git_repo, "branch", "--show-current") == "main"
    assert run_state(run)["plan_path"] == str(wt / "docs" / "plans" / "p.md")
    assert mt("refresh", cwd=git_repo).code == 1, "the main checkout is not the run's tree"


# ---------------------------------------------------------------- dirty tree in place: SKILL.md stash recipes


def recipe(marker: str) -> str:
    blocks = re.findall(r"^[ \t]*```bash\n(.*?)^[ \t]*```", SKILL_TEXT, re.DOTALL | re.MULTILINE)
    hits = [textwrap.dedent(b) for b in blocks if marker in b]
    assert len(hits) == 1, f"SKILL.md has {len(hits)} bash blocks with {marker!r}"
    return hits[0]


PUSH = recipe('stash push -u -m "make-task <run_id>"')
LOOKUP = recipe('rev-parse -q --verify "$r") ||')
RESTORE = recipe("stash list --format='%gd %H'")
REVMUX_SAVE = recipe("revmux_saved=yes")
REVMUX_RESTAGE = recipe("revmux_index=ok")
REVMUX_ASIDE = recipe("mt revmux aside")
REVMUX_PUT_BACK = recipe("mt revmux put-back")


def run_recipe(
    text: str, cwd: Path, expect: int = 0, env: dict[str, str] | None = None, **values: str
) -> dict[str, str]:
    for key, value in values.items():
        assert f"<{key}>" in text
        text = text.replace(f"<{key}>", value)
    assert not re.search(r"<\w+>", text), text
    r = subprocess.run(["bash", "-c", text], cwd=cwd, capture_output=True, text=True, check=False, env=env)
    assert r.returncode == expect, r.stderr
    return dict(re.findall(r"(\w+)=(\S*)", r.stdout))


RUN_ID = "repo-task-20260101-0000"


def dirty(mt, repo: Path, *exclude: str) -> dict:
    """SKILL.md Exec step 1: `mt dirty --exclude <plan>` gives `<paths>`."""
    r = mt("dirty", *[x for e in exclude for x in ("--exclude", e)], cwd=repo)
    assert r.code == 0, r.err
    return r.json()


def push(repo: Path, paths: list[str] | str, cwd: Path, run_id: str = RUN_ID, unstage: str = "") -> dict[str, str]:
    """The push recipe; `cwd` doubles as `<state_dir>` (where the unstaged deletions are recorded)."""
    quoted = paths if isinstance(paths, str) else " ".join(shlex.quote(p) for p in paths)
    return run_recipe(
        PUSH,
        cwd,
        toplevel=shlex.quote(str(repo)),
        paths=quoted,
        run_id=run_id,
        unstage=unstage,
        state_dir=shlex.quote(str(cwd)),
    )


def push_dirty(mt, repo: Path, cwd: Path, *exclude: str) -> dict[str, str]:
    """`mt dirty` and the push recipe with both of its lists, as Exec step 1 chains them."""
    found = dirty(mt, repo, *exclude)
    return push(repo, found["quoted"], cwd, unstage=found["unstage_quoted"])


def porcelain(repo: Path) -> list[str]:
    r = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True)
    return sorted(r.stdout.splitlines())


def staged(repo: Path, path: str) -> str:
    return git(repo, "show", f":{path}")


def restore(repo: Path, ref: str, cwd: Path, env: dict[str, str] | None = None, run_id: str = RUN_ID) -> dict[str, str]:
    """The restore recipe; `ref` is `""` when nothing was stashed (the recipe gets `''`)."""
    return run_recipe(
        RESTORE,
        cwd,
        env=env,
        toplevel=shlex.quote(str(repo)),
        stash_ref=shlex.quote(ref),
        state_dir=shlex.quote(str(cwd)),
        run_id=run_id,
    )


def stash_list(repo: Path) -> list[str]:
    return git(repo, "stash", "list", "--format=%H %s").splitlines()


def stash_shas(repo: Path) -> list[str]:
    return [line.split()[0] for line in stash_list(repo)]


def private_ref(repo: Path, run_id: str = RUN_ID) -> str:
    """The sha `refs/make-task/<run_id>` holds, or "" when the ref does not exist."""
    r = subprocess.run(
        ["git", "rev-parse", "-q", "--verify", f"refs/make-task/{run_id}"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    return r.stdout.strip()


@pytest.fixture
def dirty_repo(git_repo: Path) -> tuple[Path, str]:
    """git_repo with an older stash of the user's and every kind of dirt; returns (repo, user stash sha)."""
    (git_repo / "old.txt").write_text("old\n")
    (git_repo / "del.txt").write_text("del\n")
    commit(git_repo, "more files")
    (git_repo / "old.txt").write_text("user's older edit\n")
    git(git_repo, "stash", "push", "-q", "-m", "user-old")
    user = git(git_repo, "rev-parse", "refs/stash")
    (git_repo / "README.md").write_text("changed\n")
    (git_repo / "new file.txt").write_text("untracked\n")
    (git_repo / "newdir").mkdir()
    (git_repo / "newdir" / "n.txt").write_text("n\n")
    git(git_repo, "mv", "src/a.txt", "src/b.txt")
    git(git_repo, "rm", "-q", "del.txt")
    (git_repo / "added.txt").write_text("staged new\n")
    git(git_repo, "add", "added.txt")
    (git_repo / ".revmux").mkdir()
    (git_repo / ".revmux" / "profile.md").write_text("profile\n")
    return git_repo, user


def test_stash_recipe_round_trip_keeps_the_user_stash_and_revmux(mt, dirty_repo, tmp_path):
    repo, user = dirty_repo
    info = init(mt, repo)
    assert ".revmux/profile.md" in info["dirty_paths"]
    found = dirty(mt, repo)
    assert sorted(found["paths"]) == sorted(["README.md", "new file.txt", "newdir/n.txt", "src/a.txt", "src/b.txt",
                                             "del.txt", "added.txt"])  # fmt: skip
    assert sorted(found["unstage"]) == ["del.txt", "src/a.txt"]
    out = push(repo, found["quoted"], cwd=tmp_path, unstage=found["unstage_quoted"])
    assert out["push"] == "0" and out["before"] == "" and out["after"] not in ("", user) and out["left"] == "0"
    assert private_ref(repo) == out["after"], "the private ref keeps the stash commit"
    assert git(repo, "status", "--porcelain") == "?? .revmux/", "only .revmux/ stays in the tree"
    assert mt("set", f"stash_ref={out['after']}").code == 0
    # Exec step 1 goes on: feature branch, plan commit, tasks
    git(repo, "switch", "-q", "-c", "store")
    (repo / "feature.txt").write_text("feature\n")
    git(repo, "add", "--", "feature.txt")
    git(repo, "commit", "-q", "-m", "feature", "--", "feature.txt")

    assert restore(repo, out["after"], cwd=tmp_path) == {"restore": "ok", "entry": "stash@{0}"}
    assert stash_list(repo) == [f"{out['after']} On main: make-task {RUN_ID}", f"{user} On main: user-old"], (
        "no stash entry is dropped: make-task's is a spare copy the final report names"
    )
    assert private_ref(repo) == "", "the private ref is deleted once the edits are back"
    assert (repo / "README.md").read_text() == "changed\n"
    assert (repo / "new file.txt").is_file() and (repo / "newdir" / "n.txt").is_file()
    assert (repo / "added.txt").read_text() == "staged new\n"
    assert (repo / "src" / "b.txt").is_file() and not (repo / "src" / "a.txt").exists()
    assert not (repo / "del.txt").exists()
    assert (repo / ".revmux" / "profile.md").read_text() == "profile\n"
    assert (repo / "old.txt").read_text() == "old\n", "the user's older stash was not applied"
    # staging comes back too, the staged deletion and the staged rename included
    want = sorted([" M README.md", "D  del.txt", "R  src/a.txt -> src/b.txt", "?? .revmux/", '?? "new file.txt"',
                   "?? newdir/", "A  added.txt"])  # fmt: skip
    assert porcelain(repo) == want

    # a rerun after an auto-compact must not apply the stash a second time
    assert restore(repo, out["after"], cwd=tmp_path) == {"restore": "ok", "entry": "stash@{0}"}
    assert stash_shas(repo) == [out["after"], user]
    assert porcelain(repo) == want


def other_terminal_pushes(tmp: Path, repo: Path, when: str, after: bool) -> tuple[dict[str, str], str]:
    """A `git` on PATH that stores another terminal's stash on top of the stash list once, right before (or
    after) the recipe's first command matching `when` (a shell `case` pattern over the arguments); returns
    its env and that stash's sha. The tree and index stay as they are."""
    real = shutil.which("git")
    assert real
    readme = (repo / "README.md").read_bytes()
    (repo / "README.md").write_text("another terminal's edit\n")
    other = git(repo, "stash", "create")
    (repo / "README.md").write_bytes(readme)
    bin_dir = tmp / "other-terminal-bin"
    bin_dir.mkdir()
    flag = tmp / "other-terminal-pushed"
    wrapper = bin_dir / "git"
    wrapper.write_text(
        textwrap.dedent(f"""\
            #!/bin/bash
            other() {{
              case " $* " in
                {when}) if [ ! -e {shlex.quote(str(flag))} ]; then
                    : > {shlex.quote(str(flag))}
                    {shlex.quote(real)} -C {shlex.quote(str(repo))} stash store -m "other terminal" {other}
                  fi ;;
              esac
            }}
            [ {int(after)} = 1 ] || other "$@"
            {shlex.quote(real)} "$@"; rc=$?
            [ {int(after)} = 0 ] || other "$@"
            exit $rc
            """)
    )
    wrapper.chmod(0o755)
    return {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}, other


RESTORE_STEPS = [
    ('*" cat-file "*', False),
    ('*" stash apply "*', False),
    ('*" stash apply "*', True),
    ('*" rev-parse "*', False),
    ('*" rev-parse "*', True),
    ('*" update-ref "*', False),
    ('*" update-ref "*', True),
    ('*" stash list "*', False),
    ('*" stash list "*', True),
]


@pytest.mark.parametrize(("when", "after"), RESTORE_STEPS)
def test_restore_never_drops_a_stash_another_terminal_pushes(mt, dirty_repo, tmp_path, when, after):
    """Another terminal stores a stash at any point of the restore. `git stash drop` takes only a position,
    which that push shifts between any check and the drop (the earlier recipe dropped the other terminal's
    stash after its identity check passed). The recipe applies the sha and drops nothing."""
    repo, user = dirty_repo
    init(mt, repo)
    ref = push_dirty(mt, repo, tmp_path)["after"]
    env, other = other_terminal_pushes(tmp_path, repo, when, after=after)
    out = restore(repo, ref, cwd=tmp_path, env=env)
    assert out["restore"] == "ok" and out["entry"] in ("stash@{0}", "stash@{1}"), out
    assert (tmp_path / "other-terminal-pushed").exists(), "the other terminal's push ran"
    assert stash_shas(repo) == [other, ref, user], "no stash entry was dropped"
    assert (repo / "README.md").read_text() == "changed\n" and "D  del.txt" in porcelain(repo)
    assert private_ref(repo) == ""


@pytest.mark.parametrize(
    ("when", "after"), [('*" stash push "*', False), ('*" stash push "*', True), ('*" update-ref "*', False)]
)
def test_push_finds_its_own_stash_when_another_terminal_stores_one(mt, dirty_repo, tmp_path, when, after):
    """Another terminal stores a stash between the push and the lookup: the tip of `refs/stash` is then its
    stash (the earlier recipe took it as stash_ref, and the restore applied the wrong one). The recipe
    resolves this run's entry by its message and keeps that sha in the private ref."""
    repo, user = dirty_repo
    init(mt, repo)
    found = dirty(mt, repo)
    env, other = other_terminal_pushes(tmp_path, repo, when, after=after)
    out = run_recipe(
        PUSH,
        tmp_path,
        env=env,
        toplevel=shlex.quote(str(repo)),
        paths=found["quoted"],
        run_id=RUN_ID,
        unstage=found["unstage_quoted"],
        state_dir=shlex.quote(str(tmp_path)),
    )
    assert (tmp_path / "other-terminal-pushed").exists(), "the other terminal's store ran"
    ours = out["after"]
    assert out["push"] == "0" and out["before"] == "" and ours not in ("", other, user) and out["left"] == "0", out
    assert f"{ours} On main: make-task {RUN_ID}" in stash_list(repo)
    assert private_ref(repo) == ours
    assert restore(repo, ours, cwd=tmp_path)["restore"] == "ok"
    assert (repo / "README.md").read_text() == "changed\n", "the user's edits are back, not the other terminal's"
    assert set(stash_shas(repo)) == {other, ours, user}


def test_restore_applies_from_the_private_ref_after_the_entry_is_dropped(mt, dirty_repo, tmp_path):
    """Someone else drops make-task's entry (a `git stash pop` in another terminal takes the top one): the
    private ref keeps the commit, the restore still puts the edits back and reports `entry=none`."""
    repo, user = dirty_repo
    init(mt, repo)
    ref = push_dirty(mt, repo, tmp_path)["after"]
    git(repo, "stash", "drop", "-q", "stash@{0}")
    git(repo, "reflog", "expire", "--expire-unreachable=now", "--all")
    git(repo, "gc", "-q", "--prune=now")
    assert restore(repo, ref, cwd=tmp_path) == {"restore": "ok", "entry": "none"}
    assert stash_shas(repo) == [user]
    assert (repo / "README.md").read_text() == "changed\n" and "D  del.txt" in porcelain(repo)
    assert private_ref(repo) == ""


def test_restore_after_a_compact_between_apply_and_restage_stages_the_deletions(mt, dirty_repo, tmp_path):
    """The apply ran and wrote its marker, the re-staging did not: the rerun applies nothing again and stages
    the recorded deletions."""
    repo, _ = dirty_repo
    init(mt, repo)
    ref = push_dirty(mt, repo, tmp_path)["after"]
    git(repo, "stash", "apply", "-q", "--index", ref)
    (tmp_path / "stash-applied").write_text(f"{ref}\n")
    assert " D del.txt" in porcelain(repo)
    assert restore(repo, ref, cwd=tmp_path) == {"restore": "ok", "entry": "stash@{0}"}
    assert "D  del.txt" in porcelain(repo) and "R  src/a.txt -> src/b.txt" in porcelain(repo)


def test_restage_leaves_a_deleted_path_the_tree_has_again(mt, git_repo, tmp_path):
    """The stash commit is gone (its entry and the private ref deleted outside make-task, then pruned): HEAD's
    copy of the staged deletion is back in the tree, and the loop leaves a file the tree holds in the index."""
    git(git_repo, "rm", "-q", "src/a.txt")
    init(mt, git_repo)
    found = dirty(mt, git_repo)
    assert found["unstage"] == ["src/a.txt"]
    ref = push(git_repo, found["quoted"], tmp_path, unstage=found["unstage_quoted"])["after"]
    git(git_repo, "stash", "drop", "-q", "stash@{0}")
    git(git_repo, "update-ref", "-d", f"refs/make-task/{RUN_ID}")
    git(git_repo, "reflog", "expire", "--expire-unreachable=now", "--all")
    git(git_repo, "gc", "-q", "--prune=now")
    assert (git_repo / "src" / "a.txt").is_file()
    assert restore(git_repo, ref, cwd=tmp_path) == {"restore": "gone", "entry": "none"}
    assert porcelain(git_repo) == [], "src/a.txt stays tracked"


def test_stash_recipe_on_clean_paths_creates_no_stash(dirty_repo, tmp_path):
    repo, user = dirty_repo
    out = push(repo, ["old.txt"], cwd=tmp_path)
    assert out == {"push": "0", "before": "", "after": "", "left": "0"}, "nothing stashed: stash_ref stays unset"
    assert private_ref(repo) == "" and stash_shas(repo) == [user]


def restore_text(repo: Path, ref: str, cwd: Path, env: dict[str, str] | None = None) -> str:
    """The restore recipe's whole stdout (the `exists:` / `conflicts:` lines carry no `key=value`)."""
    text = RESTORE
    for key, value in {
        "toplevel": shlex.quote(str(repo)),
        "stash_ref": shlex.quote(ref),
        "state_dir": shlex.quote(str(cwd)),
        "run_id": RUN_ID,
    }.items():
        text = text.replace(f"<{key}>", value)
    r = subprocess.run(["bash", "-c", text], cwd=cwd, capture_output=True, text=True, check=False, env=env)
    assert r.returncode == 0, r.stderr
    return r.stdout


def assert_untouched(repo: Path, ref: str, user: str, state_dir: Path) -> None:
    """A refused restore: the tree as the push left it, nothing applied, nothing restaged, the stash kept."""
    assert porcelain(repo) == ["?? .revmux/"], "the tree is as it was"
    assert git(repo, "ls-files", "-u") == "", "no conflict markers"
    assert (repo / "README.md").read_text() == "hello\n" and not (repo / "added.txt").exists()
    assert git(repo, "ls-files", "--", "del.txt") == "del.txt", "the recorded deletions are not staged again"
    assert not (state_dir / "stash-applied").exists() and not (state_dir / "stash-partial").exists()
    assert stash_shas(repo) == [ref, user], "the stash is kept for the user"
    assert private_ref(repo) == ref, "and so is the private ref"


def test_stash_restore_conflict_keeps_the_stash(mt, dirty_repo, tmp_path):
    """The branch commits a file the user had untracked: `stash apply` would put the tracked changes in and
    then stop on that file. The recipe checks first and applies nothing."""
    repo, user = dirty_repo
    init(mt, repo)
    ref = push_dirty(mt, repo, tmp_path)["after"]
    git(repo, "switch", "-q", "-c", "store")
    (repo / "new file.txt").write_text("the branch made this file too\n")
    git(repo, "add", "--", "new file.txt")
    git(repo, "commit", "-q", "-m", "collide", "--", "new file.txt")
    assert restore(repo, ref, cwd=tmp_path) == {"restore": "conflict"}
    assert_untouched(repo, ref, user, tmp_path)
    assert "exists: new file.txt\n" in restore_text(repo, ref, tmp_path), "a rerun names the path"
    assert_untouched(repo, ref, user, tmp_path)


def test_stash_restore_parent_path_collision_applies_nothing(mt, dirty_repo, tmp_path):
    """The branch commits a file where the user's untracked file needs a directory."""
    repo, user = dirty_repo
    init(mt, repo)
    ref = push_dirty(mt, repo, tmp_path)["after"]
    git(repo, "switch", "-q", "-c", "store")
    assert not (repo / "newdir").exists()
    (repo / "newdir").write_text("a file now\n")
    git(repo, "add", "--", "newdir")
    git(repo, "commit", "-q", "-m", "file", "--", "newdir")
    out = restore_text(repo, ref, tmp_path)
    assert "exists: newdir\n" in out and out.endswith("restore=conflict\n"), out
    assert_untouched(repo, ref, user, tmp_path)


def test_stash_restore_merge_conflict_applies_nothing(mt, dirty_repo, tmp_path):
    """The branch changes the line the user edited: `stash apply` would leave conflict markers and the rest
    of the stash in the tree. The trial merge refuses first."""
    repo, user = dirty_repo
    init(mt, repo)
    ref = push_dirty(mt, repo, tmp_path)["after"]
    git(repo, "switch", "-q", "-c", "store")
    (repo / "README.md").write_text("the branch's edit\n")
    git(repo, "commit", "-q", "-m", "edit readme", "--", "README.md")
    out = restore_text(repo, ref, tmp_path)
    assert "conflicts: README.md\n" in out and out.endswith("restore=conflict\n"), out
    assert porcelain(repo) == ["?? .revmux/"] and git(repo, "ls-files", "-u") == ""
    assert (repo / "README.md").read_text() == "the branch's edit\n" and not (repo / "added.txt").exists()
    assert not (tmp_path / "stash-applied").exists() and not (tmp_path / "stash-partial").exists()
    assert stash_shas(repo) == [ref, user] and private_ref(repo) == ref


def test_stash_restore_reports_a_partial_apply(mt, dirty_repo, tmp_path):
    """An apply that fails after changing the tree (past the checks) is `partial`: no plain-apply fallback on
    the half-restored tree, no restage, the stash kept, and a rerun never applies again."""
    repo, user = dirty_repo
    init(mt, repo)
    ref = push_dirty(mt, repo, tmp_path)["after"]
    git(repo, "switch", "-q", "-c", "store")
    real = shutil.which("git")
    assert real
    bin_dir = tmp_path / "half-apply-bin"
    bin_dir.mkdir()
    calls = tmp_path / "apply-calls"
    (bin_dir / "git").write_text(
        textwrap.dedent(f"""\
            #!/bin/bash
            case " $* " in
              *" stash apply "*) echo x >> {shlex.quote(str(calls))}
                echo "half" > {shlex.quote(str(repo / "README.md"))}; exit 1 ;;
            esac
            exec {shlex.quote(real)} "$@"
            """)
    )
    (bin_dir / "git").chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    assert restore(repo, ref, cwd=tmp_path, env=env) == {"restore": "partial"}
    assert calls.read_text() == "x\n", "no plain apply on top of the failed one"
    assert (tmp_path / "stash-partial").read_text() == f"{ref}\n" and not (tmp_path / "stash-applied").exists()
    assert (repo / "README.md").read_text() == "half\n", "the partial state is left for the user"
    assert git(repo, "ls-files", "--", "del.txt") == "del.txt", "the recorded deletions are not staged again"
    assert stash_shas(repo) == [ref, user] and private_ref(repo) == ref
    assert restore(repo, ref, cwd=tmp_path) == {"restore": "partial"}, "a rerun applies nothing"
    assert calls.read_text() == "x\n" and (repo / "README.md").read_text() == "half\n"


def lookup(repo: Path, run_id: str, cwd: Path) -> list[str]:
    """SKILL.md Recovery: this run's stash sha, found by its message."""
    text = LOOKUP.replace("<toplevel>", shlex.quote(str(repo))).replace("<run_id>", run_id)
    assert not re.search(r"<\w+>", text), text
    r = subprocess.run(["bash", "-c", text], cwd=cwd, capture_output=True, text=True, check=True)
    return r.stdout.split()


def test_recovery_finds_only_this_runs_stash(dirty_repo, tmp_path):
    """Earlier runs' stashes stay (push failed, restore conflicted); recovery must not adopt one of them,
    not even one whose run id extends this run's id."""
    repo, user = dirty_repo
    ours = "repo-task-20260101-0000"
    first = push(repo, ["README.md"], cwd=tmp_path, run_id=f"{ours}-2")["after"]
    mine = push(repo, ["added.txt"], cwd=tmp_path, run_id=ours)["after"]
    later = push(repo, ["new file.txt"], cwd=tmp_path, run_id="repo-other-20260101-0001")["after"]
    assert len({user, first, mine, later}) == 4
    assert f"{mine} On main: make-task {ours}" in stash_list(repo)
    assert lookup(repo, ours, cwd=tmp_path) == [mine]
    assert lookup(repo, f"{ours}-2", cwd=tmp_path) == [first]
    assert lookup(repo, "repo-none-20260101-0002", cwd=tmp_path) == []
    # a compact between the push and the private ref: the entry's message still names the run
    for run_id in (ours, f"{ours}-2"):
        git(repo, "update-ref", "-d", f"refs/make-task/{run_id}")
    assert lookup(repo, ours, cwd=tmp_path) == [mine]
    assert private_ref(repo, ours) == mine, "the lookup writes the private ref again"
    assert lookup(repo, f"{ours}-2", cwd=tmp_path) == [first]


def test_private_ref_name_survives_a_run_id_git_refuses(mt, git_repo, tmp_path):
    """The run id starts with the checkout's directory name, which may hold spaces or `..`; the private ref
    replaces what a ref name cannot hold, and push, lookup and restore agree on it."""
    run_id = "my repo..x-task-20260101-0000"
    (git_repo / "README.md").write_text("changed\n")
    out = push(git_repo, ["README.md"], tmp_path, run_id=run_id)
    assert out["push"] == "0" and out["after"] and "ref" not in out, out
    assert private_ref(git_repo, "my-repo--x-task-20260101-0000") == out["after"]
    assert lookup(git_repo, run_id, cwd=tmp_path) == [out["after"]]
    assert restore(git_repo, out["after"], cwd=tmp_path, run_id=run_id)["restore"] == "ok"
    assert private_ref(git_repo, "my-repo--x-task-20260101-0000") == ""
    assert (git_repo / "README.md").read_text() == "changed\n"


AWKWARD_CHECKOUT = "my\\repo \"q\" $HOME `x` 'y' \\t"


def test_run_id_from_an_awkward_checkout_name_round_trips(mt, tmp_path):
    """The run id starts with the checkout's directory name. A backslash in it once made `awk -v` read an
    escape, so the push stashed the edits, the lookup found nothing and the run took it for "nothing
    stashed"; a quote or `$` would break the shell strings the recipes write it into. `mt init` keeps the
    run id to `[A-Za-z0-9_-]`, and push, lookup and restore agree on it."""
    repo = (tmp_path / AWKWARD_CHECKOUT).resolve()
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("hello\n")
    commit(repo, "init")
    run_id = Path(init(mt, repo)["state_dir"]).name
    assert re.fullmatch(r"[A-Za-z0-9_-]+", run_id), run_id
    assert run_id.startswith("my-repo-q-HOME-x-y-t-session-store-"), run_id
    state = tmp_path / "recipe-state"
    state.mkdir()
    (repo / "README.md").write_text("changed\n")
    out = push(repo, dirty(mt, repo)["quoted"], state, run_id=run_id)
    assert out["push"] == "0" and out["after"] and out["left"] == "0" and "ref" not in out, out
    assert private_ref(repo, run_id) == out["after"]
    git(repo, "update-ref", "-d", f"refs/make-task/{run_id}")
    assert lookup(repo, run_id, cwd=state) == [out["after"]], "the message lookup finds this run's entry"
    assert restore(repo, out["after"], cwd=state, run_id=run_id)["restore"] == "ok"
    assert (repo / "README.md").read_text() == "changed\n"


def test_message_lookup_reads_a_backslash_in_the_run_id_literally(git_repo, tmp_path):
    """A run dir named before `mt init` cleaned the checkout name may still hold a backslash: `ours` and the
    Recovery lookup compare the message as a plain string, never an awk escape."""
    run_id = "repo\\test-task-20260101-0000"
    (git_repo / "README.md").write_text("changed\n")
    out = push(git_repo, ["README.md"], tmp_path, run_id=run_id)
    assert out["push"] == "0" and out["after"] and out["before"] == "", out
    assert f"{out['after']} On main: make-task {run_id}" in stash_list(git_repo)
    assert private_ref(git_repo, "repo-test-task-20260101-0000") == out["after"]
    git(git_repo, "update-ref", "-d", "refs/make-task/repo-test-task-20260101-0000")
    assert lookup(git_repo, run_id, cwd=tmp_path) == [out["after"]]
    # the lookup wrote the ref again; a tab maps to the same ref name, so drop it to test the message alone
    git(git_repo, "update-ref", "-d", "refs/make-task/repo-test-task-20260101-0000")
    assert lookup(git_repo, "repo\ttest-task-20260101-0000", cwd=tmp_path) == [], "no escape is expanded"
    assert restore(git_repo, out["after"], cwd=tmp_path, run_id=run_id)["restore"] == "ok"
    assert (git_repo / "README.md").read_text() == "changed\n"


def test_stash_leaves_the_plan_in_an_untracked_dir_and_survives_a_vanished_path(mt, git_repo, tmp_path):
    """The init snapshot would hold `docs/` (sweeping the plan into the stash) and `gone.txt` (deleted since,
    so `stash push` exits 128 half done). `mt dirty` at Exec step 1 holds neither."""
    (git_repo / "docs").mkdir()
    (git_repo / "docs" / "notes.md").write_text("user notes\n")
    (git_repo / "gone.txt").write_text("deleted before exec\n")
    init(mt, git_repo)
    (git_repo / "gone.txt").unlink()
    (git_repo / "docs" / "plans").mkdir()
    (git_repo / "docs" / "plans" / "p.md").write_text("# plan\n")
    found = dirty(mt, git_repo, str(git_repo / "docs" / "plans" / "p.md"))
    assert found["paths"] == ["docs/notes.md"] and found["unstage"] == []
    out = push(git_repo, found["quoted"], cwd=tmp_path)
    assert out["push"] == "0" and out["after"] and out["left"] == "0"
    assert (git_repo / "docs" / "plans" / "p.md").read_text() == "# plan\n", "the plan stays in the tree"
    assert git(git_repo, "status", "--porcelain", "--untracked-files=all") == "?? docs/plans/p.md"


def three_line(repo: Path, name: str, first: str = "1", third: str = "3") -> None:
    (repo / name).write_text(f"{first}\n2\n{third}\n")


@pytest.fixture
def staged_repo(git_repo: Path) -> Path:
    """Files whose staged content differs from both HEAD and the tree: partially staged edits (MM), a new file
    edited after staging (AM), a staged rename edited after staging (RM), a new file staged then deleted (AD)."""
    for name in ("mm.txt", "ren.txt"):
        three_line(git_repo, name)
    commit(git_repo, "base")
    three_line(git_repo, "mm.txt", first="staged")
    git(git_repo, "add", "mm.txt")
    three_line(git_repo, "mm.txt", first="tree")
    (git_repo / "am.txt").write_text("staged\n")
    git(git_repo, "add", "am.txt")
    (git_repo / "am.txt").write_text("tree\n")
    git(git_repo, "mv", "ren.txt", "moved.txt")
    three_line(git_repo, "moved.txt", first="staged")
    git(git_repo, "add", "moved.txt")
    three_line(git_repo, "moved.txt", first="tree")
    (git_repo / "ad.txt").write_text("staged only\n")
    git(git_repo, "add", "ad.txt")
    (git_repo / "ad.txt").unlink()
    return git_repo


def test_stash_keeps_staged_content_the_tree_no_longer_has(mt, staged_repo, tmp_path):
    """Resetting every path before the push threw the staged versions away; only HEAD-only paths are reset now."""
    repo = staged_repo
    init(mt, repo)
    assert dirty(mt, repo)["unstage"] == ["ren.txt"]
    out = push_dirty(mt, repo, tmp_path)
    assert out["push"] == "0" and out["after"] and out["left"] == "0", out
    assert porcelain(repo) == [], "the tree is clean for the task"
    git(repo, "switch", "-q", "-c", "store")
    (repo / "feature.txt").write_text("feature\n")
    commit(repo, "feature")
    assert restore(repo, out["after"], cwd=tmp_path)["restore"] == "ok"
    assert staged(repo, "mm.txt") == "staged\n2\n3" and (repo / "mm.txt").read_text() == "tree\n2\n3\n"
    assert staged(repo, "am.txt") == "staged" and (repo / "am.txt").read_text() == "tree\n"
    assert staged(repo, "moved.txt") == "staged\n2\n3" and (repo / "moved.txt").read_text() == "tree\n2\n3\n"
    assert staged(repo, "ad.txt") == "staged only", "the staged-only new file is back in the index"
    assert not (repo / "ren.txt").exists()


def test_stash_of_index_only_changes_exits_128_but_is_complete(mt, git_repo, tmp_path):
    """Every listed path's working copy equals HEAD: git saves the stash, then `git apply` gets an empty patch
    and exits 128. `after` differs and `left=0`: the recipe takes the stash."""
    three_line(git_repo, "f.txt")
    commit(git_repo, "base")
    three_line(git_repo, "f.txt", first="staged")
    git(git_repo, "add", "f.txt")
    three_line(git_repo, "f.txt")  # reverted in the tree, the staged hunk stays
    (git_repo / "ad.txt").write_text("staged only\n")
    git(git_repo, "add", "ad.txt")
    (git_repo / "ad.txt").unlink()
    init(mt, git_repo)
    out = push_dirty(mt, git_repo, tmp_path)
    assert out["push"] == "128" and out["after"] and out["after"] != out["before"] and out["left"] == "0", out
    assert porcelain(git_repo) == []
    assert restore(git_repo, out["after"], cwd=tmp_path)["restore"] == "ok"
    assert staged(git_repo, "f.txt") == "staged\n2\n3" and (git_repo / "f.txt").read_text() == "1\n2\n3\n"
    assert staged(git_repo, "ad.txt") == "staged only"


def test_stash_push_failure_is_reported_when_paths_stay_dirty(git_repo, tmp_path):
    """A staged deletion pushed without its reset (the `unstage` list ignored) fails half done: `left` is not 0."""
    git(git_repo, "rm", "-q", "src/a.txt")
    (git_repo / "README.md").write_text("changed\n")
    out = push(git_repo, ["src/a.txt", "README.md"], cwd=tmp_path)
    assert out["push"] != "0" and out["left"] != "0", out


def test_stash_reads_paths_as_file_names_not_globs(mt, git_repo, tmp_path):
    """An untracked file named `*.md` must not pull the excluded plan into the stash, and resetting the staged
    deletion of `[ab].txt` must not reset `b.txt` (its staged content would be lost)."""
    (git_repo / "*.md").write_text("star\n")
    (git_repo / "[ab].txt").write_text("glob\n")
    three_line(git_repo, "b.txt")
    commit(git_repo, "glob names")
    plan = git_repo / "plan.md"
    plan.write_text("# plan\n")
    (git_repo / "*.md").write_text("star edited\n")
    git(git_repo, "--literal-pathspecs", "rm", "-q", "--", "[ab].txt")
    three_line(git_repo, "b.txt", first="staged")
    git(git_repo, "add", "b.txt")
    three_line(git_repo, "b.txt", first="tree")
    init(mt, git_repo)
    found = dirty(mt, git_repo, str(plan))
    assert sorted(found["paths"]) == ["*.md", "[ab].txt", "b.txt"] and found["unstage"] == ["[ab].txt"]
    out = push(git_repo, found["quoted"], tmp_path, unstage=found["unstage_quoted"])
    assert out["push"] == "0" and out["after"] and out["left"] == "0", out
    assert porcelain(git_repo) == ["?? plan.md"] and plan.read_text() == "# plan\n", "the plan stays in the tree"
    stashed = git(git_repo, "stash", "show", "--include-untracked", "--name-only", out["after"]).split()
    assert sorted(stashed) == ["*.md", "[ab].txt", "b.txt"]
    assert restore(git_repo, out["after"], cwd=tmp_path)["restore"] == "ok"
    assert porcelain(git_repo) == sorted([" M *.md", "D  [ab].txt", "MM b.txt", "?? plan.md"])
    assert staged(git_repo, "b.txt") == "staged\n2\n3"


def test_index_only_deletion_comes_back_when_nothing_was_stashed(mt, git_repo, tmp_path):
    """`git rm --cached` keeps the file: the reset leaves the path clean, no stash is made, and the restore
    (run with an empty stash_ref) untracks it again."""
    git(git_repo, "rm", "-q", "--cached", "src/a.txt")
    init(mt, git_repo)
    found = dirty(mt, git_repo)
    assert found["unstage"] == ["src/a.txt"]
    out = push(git_repo, found["quoted"], tmp_path, unstage=found["unstage_quoted"])
    assert out["push"] == "0" and out["after"] == out["before"] == "" and out["left"] == "0", out
    assert porcelain(git_repo) == []
    assert restore(git_repo, "", cwd=tmp_path) == {"restore": "none"}
    assert porcelain(git_repo) == ["?? src/", "D  src/a.txt"]
    assert (git_repo / "src" / "a.txt").read_text() == "a\n"
    assert restore(git_repo, "", cwd=tmp_path) == {"restore": "none"}, "a rerun changes nothing"
    assert porcelain(git_repo) == ["?? src/", "D  src/a.txt"]


def test_index_only_deletion_of_an_edited_file_comes_back_untracked(mt, git_repo, tmp_path):
    """`git rm --cached`, then the file edited: the reset turns it into an unstaged edit, which the stash
    holds; the restore puts the edit back and untracks the path again instead of leaving ` M`."""
    git(git_repo, "rm", "-q", "--cached", "src/a.txt")
    (git_repo / "src" / "a.txt").write_text("edited\n")
    init(mt, git_repo)
    out = push_dirty(mt, git_repo, tmp_path)
    assert out["push"] == "0" and out["after"] and out["left"] == "0", out
    assert porcelain(git_repo) == []
    git(git_repo, "switch", "-q", "-c", "store")
    (git_repo / "feature.txt").write_text("feature\n")
    commit(git_repo, "feature")
    assert restore(git_repo, out["after"], cwd=tmp_path)["restore"] == "ok"
    assert porcelain(git_repo) == ["?? src/", "D  src/a.txt"]
    assert (git_repo / "src" / "a.txt").read_text() == "edited\n"


def test_restore_falls_back_to_plain_apply_only_when_nothing_staged_is_lost(mt, git_repo, tmp_path):
    """The branch changed line 3 of a file the user staged line 1 of: `apply --index` fails, the plain merge works.
    For a path whose staged content is in neither HEAD nor the tree, a plain apply would drop it: the stash stays."""
    three_line(git_repo, "same.txt")
    three_line(git_repo, "split.txt")
    commit(git_repo, "base")
    init(mt, git_repo)
    three_line(git_repo, "same.txt", first="user")
    git(git_repo, "add", "same.txt")
    plain = push_dirty(mt, git_repo, tmp_path)["after"]
    three_line(git_repo, "split.txt", first="staged")
    git(git_repo, "add", "split.txt")
    three_line(git_repo, "split.txt", first="tree")
    kept = push_dirty(mt, git_repo, tmp_path)["after"]
    git(git_repo, "switch", "-q", "-c", "store")
    three_line(git_repo, "same.txt", third="branch")
    three_line(git_repo, "split.txt", third="branch")
    commit(git_repo, "branch edits line 3")

    assert restore(git_repo, kept, cwd=tmp_path) == {"restore": "conflict"}
    assert porcelain(git_repo) == [], "a refused restore leaves the tree as it was"
    assert kept in stash_shas(git_repo)

    assert restore(git_repo, plain, cwd=tmp_path)["restore"] == "ok"
    assert (git_repo / "same.txt").read_text() == "user\n2\nbranch\n"
    assert stash_shas(git_repo) == [kept, plain], "no entry is dropped"


def revmux_save(
    repo: Path, state_dir: Path, cwd: Path, expect: int = 0, env: dict[str, str] | None = None
) -> dict[str, str]:
    return run_recipe(
        REVMUX_SAVE, cwd, expect, env, state_dir=shlex.quote(str(state_dir)), toplevel=shlex.quote(str(repo))
    )


def revmux_restage(repo: Path, state_dir: Path, cwd: Path) -> dict[str, str]:
    return run_recipe(REVMUX_RESTAGE, cwd, state_dir=shlex.quote(str(state_dir)), toplevel=shlex.quote(str(repo)))


@pytest.fixture
def revmux_staged_repo(git_repo: Path) -> Path:
    """A committed .revmux/profile.md with an edit staged (p1) and a later one in the tree (p2), a staged new
    .revmux file, and a partly staged file (MM): the shape that made `stash apply --index` fail and whose
    .revmux part a bare `git commit` swept into the first task commit."""
    (git_repo / ".revmux").mkdir()
    (git_repo / ".revmux" / "profile.md").write_text("p0\n")
    three_line(git_repo, "mm.txt")
    commit(git_repo, "base")
    (git_repo / ".revmux" / "profile.md").write_text("p1\n")
    (git_repo / ".revmux" / "lens.md").write_text("lens\n")
    git(git_repo, "add", ".revmux")
    (git_repo / ".revmux" / "profile.md").write_text("p2\n")
    three_line(git_repo, "mm.txt", first="staged")
    git(git_repo, "add", "mm.txt")
    three_line(git_repo, "mm.txt", first="tree")
    return git_repo


def test_staged_revmux_changes_stay_out_of_task_commits_and_come_back_staged(mt, revmux_staged_repo, tmp_path):
    repo, state_dir = revmux_staged_repo, tmp_path / "state"
    state_dir.mkdir()
    init(mt, repo)
    git(repo, "checkout", "-q", "-b", "store")
    assert revmux_save(repo, state_dir, tmp_path) == {"revmux_saved": "yes"}
    assert git(repo, "diff", "--cached", "--name-only") == "mm.txt", "nothing under .revmux/ is staged"
    assert (repo / ".revmux" / "profile.md").read_text() == "p2\n", "the working copies are untouched"
    # a rerun after a compact keeps the first save (the index no longer holds the staged content)
    assert revmux_save(repo, state_dir, tmp_path) == {"revmux_saved": "yes"}
    out = push_dirty(mt, repo, tmp_path)
    assert out["push"] == "0" and out["after"] and out["left"] == "0", out
    # a task commit the way planning's stage-and-commit.sh makes it: git add, then a bare git commit
    (repo / "feature.txt").write_text("feature\n")
    git(repo, "add", "--", "feature.txt")
    git(repo, "commit", "-q", "-m", "task")
    assert git(repo, "show", "--name-only", "--format=", "HEAD") == "feature.txt", "no .revmux path rode along"

    assert restore(repo, out["after"], cwd=tmp_path)["restore"] == "ok", "apply --index works again"
    assert staged(repo, "mm.txt") == "staged\n2\n3" and (repo / "mm.txt").read_text() == "tree\n2\n3\n"
    assert revmux_restage(repo, state_dir, tmp_path) == {"revmux_index": "ok"}
    assert staged(repo, ".revmux/profile.md") == "p1" and staged(repo, ".revmux/lens.md") == "lens"
    assert (repo / ".revmux" / "profile.md").read_text() == "p2\n"
    assert git(repo, "show", "HEAD:.revmux/profile.md") == "p0"
    # a rerun after a compact does not apply the patch twice
    assert revmux_restage(repo, state_dir, tmp_path) == {"revmux_index": "none"}


def test_revmux_save_with_nothing_staged_resets_nothing(mt, dirty_repo, tmp_path):
    """The untracked profile of dirty_repo: nothing to save, the index is left alone, the restage is a no-op."""
    repo, _ = dirty_repo
    before = git(repo, "diff", "--cached", "--name-status")
    assert revmux_save(repo, tmp_path, tmp_path) == {"revmux_saved": "no"}
    assert git(repo, "diff", "--cached", "--name-status") == before
    assert revmux_restage(repo, tmp_path, tmp_path) == {"revmux_index": "none"}


def test_revmux_restage_conflict_keeps_the_patch(mt, revmux_staged_repo, tmp_path):
    repo = revmux_staged_repo
    assert revmux_save(repo, tmp_path, tmp_path) == {"revmux_saved": "yes"}
    (repo / ".revmux" / "profile.md").write_text("upstream\n")
    git(repo, "add", "--", ".revmux/profile.md")
    git(repo, "commit", "-q", "-m", "upstream profile", "--", ".revmux/profile.md")
    assert revmux_restage(repo, tmp_path, tmp_path) == {"revmux_index": "conflict"}
    assert (tmp_path / "revmux-index.patch").is_file(), "the patch stays for the user"
    assert git(repo, "diff", "--cached", "--name-only", "--", ".revmux") == "", "git apply changed nothing"


def mt_recipe(mt, text: str, expect: int, **values: str) -> dict[str, str]:
    """Run a one-line `mt ...` block from SKILL.md in-process, its placeholders filled in."""
    (line,) = text.strip().splitlines()
    argv = shlex.split(line)
    assert argv[0] == "mt", line
    for key, value in values.items():
        assert f"<{key}>" in argv, line
        argv = [value if a == f"<{key}>" else a for a in argv]
    assert not any(re.fullmatch(r"<\w+>", a) for a in argv), argv
    r = mt(*argv[1:])
    assert r.code == expect, r.err
    return dict(re.findall(r"(\w+)=(\S*)", r.out))


def revmux_aside(mt, repo: Path, state_dir: Path, expect: int = 0) -> dict[str, str]:
    return mt_recipe(mt, REVMUX_ASIDE, expect, state_dir=str(state_dir), toplevel=str(repo))


def revmux_put_back(mt, repo: Path, state_dir: Path) -> dict[str, str]:
    return mt_recipe(mt, REVMUX_PUT_BACK, 0, state_dir=str(state_dir), toplevel=str(repo))


def rebase(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """A real `git rebase`, the way the finalizer runs it (`-i` with a no-op todo editor for the squash step)."""
    env = {**os.environ, "GIT_SEQUENCE_EDITOR": "true", "GIT_EDITOR": "true"}
    return subprocess.run(["git", "rebase", *args], cwd=repo, capture_output=True, text=True, check=False, env=env)


def in_place_to_finalize(mt, repo: Path, state_dir: Path, cwd: Path) -> str:
    """Exec step 1 in place (branch, .revmux save, stash push), one task commit with a pathspec, and a new commit
    on main, so the finalizer's rebase has work to do. Returns the stash sha."""
    init(mt, repo)
    git(repo, "checkout", "-q", "-b", "store")
    assert revmux_save(repo, state_dir, cwd) == {"revmux_saved": "yes"}
    out = push_dirty(mt, repo, cwd)
    assert out["push"] == "0" and out["after"] and out["left"] == "0", out
    (repo / "feature.txt").write_text("feature\n")
    git(repo, "add", "--", "feature.txt")
    git(repo, "commit", "-q", "-m", "task", "--", "feature.txt")
    git(repo, "switch", "-q", "main")
    (repo / "upstream.txt").write_text("upstream\n")
    git(repo, "add", "--", "upstream.txt")
    git(repo, "commit", "-q", "-m", "upstream", "--", "upstream.txt")
    git(repo, "switch", "-q", "store")
    return out["after"]


def test_finalizer_rebase_runs_with_a_modified_tracked_revmux_profile(mt, revmux_staged_repo, tmp_path):
    """In place the edited tracked .revmux/profile.md (p2 in the tree, its staged p1 unstaged at Exec step 1)
    is the only dirt left at Finalize, and `git rebase` refuses an unstaged change. Finalize step 2 sets it
    aside, the real rebases (onto main, then -i for the squash) run, and Completion puts tree and index back."""
    repo, state_dir = revmux_staged_repo, tmp_path / "state"
    state_dir.mkdir()
    stash = in_place_to_finalize(mt, repo, state_dir, tmp_path)
    assert (repo / ".revmux" / "profile.md").read_text() == "p2\n"
    blocked = rebase(repo, "main")
    assert blocked.returncode != 0 and "unstaged changes" in blocked.stderr, "the shape that failed every rebase"

    assert revmux_aside(mt, repo, state_dir) == {"revmux_tree": "aside"}
    assert (repo / ".revmux" / "profile.md").read_text() == "p0\n", "HEAD's version for the finalizer"
    assert (repo / ".revmux" / "lens.md").read_text() == "lens\n", "untracked files stay"
    # a rerun after a compact keeps the first save and does not stop on the clean tree
    assert revmux_aside(mt, repo, state_dir) == {"revmux_tree": "aside"}
    r = rebase(repo, "main")
    assert r.returncode == 0, r.stderr
    r = rebase(repo, "-i", "main")
    assert r.returncode == 0, r.stderr
    assert git(repo, "log", "--format=%s", "main..HEAD") == "task"
    assert git(repo, "merge-base", "--is-ancestor", "main", "HEAD") == ""

    assert restore(repo, stash, cwd=tmp_path)["restore"] == "ok"
    assert revmux_put_back(mt, repo, state_dir) == {"revmux_tree": "ok"}
    assert revmux_restage(repo, state_dir, tmp_path) == {"revmux_index": "ok"}
    assert (repo / ".revmux" / "profile.md").read_text() == "p2\n", "the user's working copy is back"
    assert staged(repo, ".revmux/profile.md") == "p1" and staged(repo, ".revmux/lens.md") == "lens"
    assert git(repo, "show", "HEAD:.revmux/profile.md") == "p0"
    assert staged(repo, "mm.txt") == "staged\n2\n3" and (repo / "mm.txt").read_text() == "tree\n2\n3\n"
    # a rerun after a compact puts nothing back twice
    assert revmux_put_back(mt, repo, state_dir) == {"revmux_tree": "none"}
    assert (repo / ".revmux" / "profile.md").read_text() == "p2\n"


def test_revmux_put_back_after_a_copy_that_was_not_recorded(mt, revmux_staged_repo, tmp_path):
    """A compact between the copy back and the rename: the saved copy is in the tree already, so the rerun
    records it as put back instead of a conflict."""
    repo, state_dir = revmux_staged_repo, tmp_path / "state"
    state_dir.mkdir()
    assert revmux_save(repo, state_dir, tmp_path) == {"revmux_saved": "yes"}
    assert revmux_aside(mt, repo, state_dir) == {"revmux_tree": "aside"}
    saved = state_dir / "revmux-tree" / "files" / ".revmux" / "profile.md"
    assert saved.read_bytes() == b"p2\n"
    shutil.copy2(saved, repo / ".revmux" / "profile.md")
    assert revmux_put_back(mt, repo, state_dir) == {"revmux_tree": "ok"}
    assert (repo / ".revmux" / "profile.md").read_text() == "p2\n"
    assert (state_dir / "revmux-tree.restored" / "list").is_file() and not (state_dir / "revmux-tree").exists()


def test_revmux_aside_refuses_edits_other_than_the_saved_ones(mt, revmux_staged_repo, tmp_path):
    """A rerun that finds .revmux/ edits the first save does not hold stops instead of discarding them."""
    repo, state_dir = revmux_staged_repo, tmp_path / "state"
    state_dir.mkdir()
    assert revmux_save(repo, state_dir, tmp_path) == {"revmux_saved": "yes"}
    assert revmux_aside(mt, repo, state_dir) == {"revmux_tree": "aside"}
    (repo / ".revmux" / "profile.md").write_text("p3\n")
    assert revmux_aside(mt, repo, state_dir, expect=1) == {"revmux_tree": "differs"}
    assert (repo / ".revmux" / "profile.md").read_text() == "p3\n", "the new edit is not checked out"


@pytest.mark.parametrize("index_change", ["rm-cached", "restaged"])
def test_revmux_aside_rerun_refuses_a_saved_path_whose_index_record_changed(mt, git_repo, tmp_path, index_change):
    """After the aside, a saved path leaves the index (`git rm --cached`) or is restaged, and its working copy
    is edited again. The rerun unlinks every saved path, so it must check them all: it reports differs and
    leaves the new contents and the first save as they were."""
    repo, state_dir = git_repo, tmp_path / "state"
    state_dir.mkdir()
    (repo / ".revmux").mkdir()
    (repo / ".revmux" / "a").write_text("v0\n")
    (repo / ".revmux" / "b").write_text("b0\n")
    commit(repo, "base")
    (repo / ".revmux" / "a").write_text("first edit\n")
    assert revmux_aside(mt, repo, state_dir) == {"revmux_tree": "aside"}
    assert (repo / ".revmux" / "a").read_text() == "v0\n"
    if index_change == "rm-cached":
        git(repo, "rm", "-q", "--cached", "--", ".revmux/a")
        (repo / ".revmux" / "a").write_text("new contents\n")
    else:
        (repo / ".revmux" / "a").write_text("new contents\n")
        git(repo, "add", "--", ".revmux/a")
    assert revmux_aside(mt, repo, state_dir, expect=1) == {"revmux_tree": "differs"}
    assert (repo / ".revmux" / "a").read_text() == "new contents\n", "the new contents are not removed"
    assert (repo / ".revmux" / "b").read_text() == "b0\n"
    saved = state_dir / "revmux-tree" / "files" / ".revmux" / "a"
    assert saved.read_bytes() == b"first edit\n", "the first save is kept"


def test_revmux_put_back_conflict_keeps_the_saved_copies(mt, revmux_staged_repo, tmp_path):
    """The rebase brings a new .revmux/profile.md from main: the saved edit no longer applies. git apply changes
    nothing, the saved copies stay for the user, and it is not a stop."""
    repo, state_dir = revmux_staged_repo, tmp_path / "state"
    state_dir.mkdir()
    in_place_to_finalize(mt, repo, state_dir, tmp_path)
    git(repo, "switch", "-q", "main")
    assert revmux_aside(mt, repo, state_dir) == {"revmux_tree": "aside"}
    (repo / ".revmux" / "profile.md").write_text("upstream\n")
    git(repo, "commit", "-q", "-m", "upstream profile", "--", ".revmux/profile.md")
    git(repo, "switch", "-q", "store")
    r = rebase(repo, "main")
    assert r.returncode == 0, r.stderr
    assert revmux_put_back(mt, repo, state_dir) == {"revmux_tree": "conflict"}
    saved = state_dir / "revmux-tree" / "files" / ".revmux" / "profile.md"
    assert saved.read_bytes() == b"p2\n", "the saved copy stays for the user"
    assert (repo / ".revmux" / "profile.md").read_text() == "upstream\n", "nothing was copied back"
    assert revmux_put_back(mt, repo, state_dir) == {"revmux_tree": "conflict"}, "a rerun refuses again"


def test_revmux_aside_is_a_no_op_without_tracked_revmux_edits(mt, dirty_repo, tmp_path):
    """An untracked profile (dirty_repo) and worktree mode: nothing to set aside, nothing to put back."""
    repo, _ = dirty_repo
    before = porcelain(repo)
    assert revmux_aside(mt, repo, tmp_path) == {"revmux_tree": "none"}
    assert porcelain(repo) == before
    assert revmux_put_back(mt, repo, tmp_path) == {"revmux_tree": "none"}


# The user's git config must not reach the .revmux/ backups: each case alone used to lose or garble them (a
# redacting textconv driver, no a/ b/ prefixes, zero context, color codes, an external diff that prints nothing,
# `apply.whitespace=fix` stripping trailing blanks on the way back, a clean filter that rewrote the saved
# working copy, line-ending conversion).
HOSTILE_CONFIG = {
    "textconv": [("diff.redact.textconv", "sed s/SECRET/XXXXXX/")],
    "noprefix": [("diff.noprefix", "true")],
    "mnemonicprefix": [("diff.mnemonicPrefix", "true")],
    "context0": [("diff.context", "0")],
    "color": [("color.ui", "always"), ("color.diff", "always")],
    "external": [("diff.external", "true")],
    "whitespace-fix": [("apply.whitespace", "fix")],
    "clean-smudge": [("filter.redact.clean", "sed s/SECRET/XXXXXX/g"), ("filter.redact.smudge", "cat")],
    "autocrlf": [("core.autocrlf", "true")],
    "eol-crlf": [],
}
HOSTILE_ATTRIBUTES = {
    "clean-smudge": "* diff=redact filter=redact\n",
    "eol-crlf": "* diff=redact text eol=crlf\n",
}


@pytest.fixture
def revmux_secret_repo(git_repo: Path) -> Path:
    """A tracked .revmux/profile.md with an edit staged and a later one in the tree, both holding a word a
    textconv driver (or clean filter) redacts and a line with trailing blanks; a tracked redacted.md whose tree
    copy differs from HEAD only by that word (so a redacting clean filter makes git see it as clean), and an
    untouched tracked keep.md. Every path is routed to that diff driver."""
    (git_repo / ".revmux").mkdir()
    (git_repo / ".revmux" / "profile.md").write_text("p0\nkeep\n")
    (git_repo / ".revmux" / "redacted.md").write_text("token XXXXXX\n")
    (git_repo / ".revmux" / "keep.md").write_text("keep\n")
    commit(git_repo, "base")
    (git_repo / ".git" / "info" / "attributes").write_text("* diff=redact\n")
    (git_repo / ".revmux" / "profile.md").write_text("p1 SECRET  \nkeep\n")
    git(git_repo, "add", ".revmux")
    (git_repo / ".revmux" / "profile.md").write_text("p2 SECRET tree  \nkeep\n")
    (git_repo / ".revmux" / "redacted.md").write_text("token SECRET\n")
    return git_repo


def revmux_bytes(repo: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted((repo / ".revmux").iterdir())}


@pytest.mark.parametrize("case", list(HOSTILE_CONFIG))
def test_revmux_backups_round_trip_under_hostile_git_config(mt, revmux_secret_repo, tmp_path, case):
    repo, state_dir = revmux_secret_repo, tmp_path / "state"
    state_dir.mkdir()
    for key, value in HOSTILE_CONFIG[case]:
        git(repo, "config", key, value)
    if case in HOSTILE_ATTRIBUTES:
        (repo / ".git" / "info" / "attributes").write_text(HOSTILE_ATTRIBUTES[case])
    tree = revmux_bytes(repo)
    index = git(repo, "ls-files", "-s", "--", ".revmux")
    assert revmux_save(repo, state_dir, tmp_path) == {"revmux_saved": "yes"}
    assert git(repo, "diff", "--cached", "--name-only") == ""
    assert revmux_bytes(repo) == tree, "the save touches no working copy"
    patch = (state_dir / "revmux-index.patch").read_bytes()
    assert b"SECRET" in patch and b"XXXXXX" not in patch and b"\x1b[" not in patch
    assert b"--- a/.revmux/profile.md" in patch

    assert revmux_aside(mt, repo, state_dir) == {"revmux_tree": "aside"}
    assert porcelain(repo) == [], "HEAD's version of every tracked .revmux file for the finalizer"
    assert b"SECRET" not in b"".join(revmux_bytes(repo).values())
    saved = state_dir / "revmux-tree" / "files" / ".revmux"
    assert (saved / "profile.md").read_bytes() == tree["profile.md"]
    assert (saved / "redacted.md").read_bytes() == tree["redacted.md"], "raw bytes, not the cleaned content"

    assert revmux_put_back(mt, repo, state_dir) == {"revmux_tree": "ok"}
    assert revmux_restage(repo, state_dir, tmp_path) == {"revmux_index": "ok"}
    assert revmux_bytes(repo) == tree, "byte for byte"
    assert git(repo, "ls-files", "-s", "--", ".revmux") == index, "the same staged blobs"
    r = subprocess.run(["git", "show", ":.revmux/profile.md"], cwd=repo, capture_output=True, check=True)
    assert r.stdout == b"p1 SECRET  \nkeep\n"


def test_revmux_aside_keeps_mode_symlinks_and_deletions(mt, revmux_secret_repo, tmp_path):
    """A chmod +x, a tracked file replaced by a symlink and a deleted tracked file come back as they were."""
    repo, state_dir = revmux_secret_repo, tmp_path / "state"
    state_dir.mkdir()
    git(repo, "reset", "-q", "--", ".revmux")
    (repo / ".revmux" / "keep.md").chmod(0o755)
    (repo / ".revmux" / "redacted.md").unlink()
    (repo / ".revmux" / "redacted.md").symlink_to("profile.md")
    (repo / ".revmux" / "gone.md").write_text("gone\n")
    git(repo, "add", "--", ".revmux/gone.md")
    git(repo, "commit", "-q", "-m", "gone", "--", ".revmux/gone.md")
    (repo / ".revmux" / "gone.md").unlink()
    before = porcelain(repo)
    assert revmux_aside(mt, repo, state_dir) == {"revmux_tree": "aside"}
    assert porcelain(repo) == []
    assert not os.access(repo / ".revmux" / "keep.md", os.X_OK) and (repo / ".revmux" / "gone.md").is_file()
    assert revmux_put_back(mt, repo, state_dir) == {"revmux_tree": "ok"}
    assert porcelain(repo) == before
    assert os.access(repo / ".revmux" / "keep.md", os.X_OK)
    assert os.readlink(repo / ".revmux" / "redacted.md") == "profile.md"
    assert not (repo / ".revmux" / "gone.md").exists()


def lossy_diff_git(tmp: Path) -> dict[str, str]:
    """A `git` on PATH whose `diff` output drops what a redacting driver would, whatever the flags: a patch
    that does not round-trip, the case the pinned options cannot rule out."""
    real = shutil.which("git")
    assert real
    bin_dir = tmp / "lossy-bin"
    bin_dir.mkdir()
    wrapper = bin_dir / "git"
    wrapper.write_text(
        textwrap.dedent(f"""\
            #!/bin/bash
            case " $* " in
              *" diff "*" --quiet "*) exec {shlex.quote(real)} "$@" ;;
              *" diff "*) set -o pipefail; {shlex.quote(real)} "$@" | sed s/SECRET/XXXXXX/ ;;
              *) exec {shlex.quote(real)} "$@" ;;
            esac
            """)
    )
    wrapper.chmod(0o755)
    return {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}


def test_revmux_save_refuses_a_patch_that_does_not_round_trip(mt, revmux_secret_repo, tmp_path):
    """The check runs before the reset: a lossy patch stops the recipe with the index untouched, and is renamed
    so no restore recipe applies it."""
    repo, state_dir = revmux_secret_repo, tmp_path / "state"
    state_dir.mkdir()
    env = lossy_diff_git(tmp_path)
    before = porcelain(repo)
    assert revmux_save(repo, state_dir, tmp_path, expect=1, env=env) == {"revmux_saved": "unsafe"}
    assert porcelain(repo) == before and staged(repo, ".revmux/profile.md") == "p1 SECRET  \nkeep"
    assert not (state_dir / "revmux-index.patch").exists() and (state_dir / "revmux-index.patch.unsafe").is_file()
    assert revmux_restage(repo, state_dir, tmp_path) == {"revmux_index": "none"}
    # a rerun saves again and refuses again
    assert revmux_save(repo, state_dir, tmp_path, expect=1, env=env) == {"revmux_saved": "unsafe"}


def test_revmux_aside_refuses_a_copy_that_does_not_match(mt, revmux_secret_repo, tmp_path, monkeypatch):
    """The byte-for-byte check runs before anything is checked out: a copy that ends with a byte the source does
    not have stops the aside with the tree untouched, and the copies move to revmux-tree.unsafe/ so no put-back
    uses them."""
    repo, state_dir = revmux_secret_repo, tmp_path / "state"
    state_dir.mkdir()
    git(repo, "reset", "-q", "--", ".revmux")
    tree, before = revmux_bytes(repo), porcelain(repo)
    real_copy = MT.copy_entry

    def corrupting_copy(src: bytes, dst: bytes) -> None:
        real_copy(src, dst)
        if not os.path.islink(dst):
            with open(dst, "ab") as f:
                f.write(b"x")

    with monkeypatch.context() as m:
        m.setattr(MT, "copy_entry", corrupting_copy)
        assert revmux_aside(mt, repo, state_dir, expect=1) == {"revmux_tree": "unsafe"}
    assert revmux_bytes(repo) == tree and porcelain(repo) == before, "not checked out"
    assert not (state_dir / "revmux-tree").exists() and (state_dir / "revmux-tree.unsafe").is_dir()
    assert revmux_put_back(mt, repo, state_dir) == {"revmux_tree": "none"}
    # with a working copy the same tree is set aside and put back
    assert revmux_aside(mt, repo, state_dir) == {"revmux_tree": "aside"}
    assert revmux_put_back(mt, repo, state_dir) == {"revmux_tree": "ok"}
    assert revmux_bytes(repo) == tree


@pytest.fixture
def revmux_symlink_repo(git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Tracked .revmux/dash and .revmux/newline replaced by symlinks to `-f` and to `new<LF>`: targets that
    `ln -s "$(readlink ...)"` read as an option or cut short. The cwd holds a bystander named like the copy, the
    file an `ln -s -f <copy>` used to overwrite."""
    (git_repo / ".revmux").mkdir()
    for name in ("dash", "newline"):
        (git_repo / ".revmux" / name).write_text(f"{name}\n")
    commit(git_repo, "base")
    for name, target in (("dash", "-f"), ("newline", "new\n")):
        (git_repo / ".revmux" / name).unlink()
        (git_repo / ".revmux" / name).symlink_to(target)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / "dash").write_text("bystander\n")
    monkeypatch.chdir(cwd)
    return git_repo


def test_revmux_aside_keeps_symlink_targets_byte_for_byte(mt, revmux_symlink_repo, tmp_path):
    repo, state_dir = revmux_symlink_repo, tmp_path / "run"
    state_dir.mkdir()
    before = porcelain(repo)
    assert revmux_aside(mt, repo, state_dir) == {"revmux_tree": "aside"}
    assert (tmp_path / "cwd" / "dash").read_text() == "bystander\n", "nothing is written outside the run dir"
    assert sorted(p.name for p in (tmp_path / "cwd").iterdir()) == ["dash"]
    saved = state_dir / "revmux-tree" / "files" / ".revmux"
    assert os.readlink(saved / "dash") == "-f" and os.readlink(saved / "newline") == "new\n"
    assert porcelain(repo) == [] and (repo / ".revmux" / "newline").read_text() == "newline\n"
    assert revmux_put_back(mt, repo, state_dir) == {"revmux_tree": "ok"}
    assert os.readlink(repo / ".revmux" / "dash") == "-f"
    assert os.readlink(repo / ".revmux" / "newline") == "new\n", "the trailing newline is part of the target"
    assert porcelain(repo) == before
    assert (tmp_path / "cwd" / "dash").read_text() == "bystander\n"


def test_revmux_symlink_target_without_its_trailing_newline_is_another_edit(mt, revmux_symlink_repo, tmp_path):
    """`new` is not `new<LF>`: a rerun of the aside reports differs, and the put-back reports conflict."""
    repo, state_dir = revmux_symlink_repo, tmp_path / "run"
    state_dir.mkdir()
    assert revmux_aside(mt, repo, state_dir) == {"revmux_tree": "aside"}
    link = repo / ".revmux" / "newline"
    link.unlink()
    link.symlink_to("new")
    assert revmux_aside(mt, repo, state_dir, expect=1) == {"revmux_tree": "differs"}
    assert revmux_put_back(mt, repo, state_dir) == {"revmux_tree": "conflict"}
    assert os.readlink(link) == "new", "nothing was copied back"
    assert os.readlink(state_dir / "revmux-tree" / "files" / ".revmux" / "newline") == "new\n"


ODD_NAMES = ("-f", "a\nb.md", "-n\n")


@pytest.fixture
def revmux_odd_names_repo(git_repo: Path) -> Path:
    """Tracked .revmux files named with a leading `-` and with newlines, each edited in the tree."""
    (git_repo / ".revmux").mkdir()
    for name in ODD_NAMES:
        (git_repo / ".revmux" / name).write_text(f"v0 {name!r}\n")
    commit(git_repo, "base")
    for name in ODD_NAMES:
        (git_repo / ".revmux" / name).write_text(f"user {name!r}\n")
    return git_repo


def test_revmux_aside_and_put_back_keep_odd_file_names(mt, revmux_odd_names_repo, tmp_path):
    repo, state_dir = revmux_odd_names_repo, tmp_path / "run"
    state_dir.mkdir()
    tree, before = revmux_bytes(repo), porcelain(repo)
    assert revmux_aside(mt, repo, state_dir) == {"revmux_tree": "aside"}
    assert porcelain(repo) == []
    assert revmux_aside(mt, repo, state_dir) == {"revmux_tree": "aside"}, "a rerun keeps the first save"
    assert revmux_put_back(mt, repo, state_dir) == {"revmux_tree": "ok"}
    assert revmux_bytes(repo) == tree and porcelain(repo) == before


def test_revmux_put_back_compares_whole_records_for_names_with_newlines(mt, revmux_odd_names_repo, tmp_path):
    """A new index entry for `a<LF>b.md` (the rebase brought another version) leaves the record's `b.md` tail
    unchanged: the put-back compares whole records, so it is a conflict and the upstream content stays."""
    repo, state_dir = revmux_odd_names_repo, tmp_path / "run"
    state_dir.mkdir()
    assert revmux_aside(mt, repo, state_dir) == {"revmux_tree": "aside"}
    path = repo / ".revmux" / "a\nb.md"
    path.write_text("upstream\n")
    git(repo, "commit", "-q", "-m", "upstream", "--", ".revmux/a\nb.md")
    assert revmux_put_back(mt, repo, state_dir) == {"revmux_tree": "conflict"}
    assert path.read_text() == "upstream\n", "the upstream content is not overwritten"
    assert (state_dir / "revmux-tree" / "files" / ".revmux" / "a\nb.md").read_text() == "user 'a\\nb.md'\n"


def test_revmux_aside_refuses_unmerged_entries(mt, revmux_secret_repo, tmp_path):
    repo, state_dir = revmux_secret_repo, tmp_path / "state"
    state_dir.mkdir()
    git(repo, "reset", "-q", "--", ".revmux")
    tree = revmux_bytes(repo)
    blob = git(repo, "rev-parse", "HEAD:.revmux/keep.md")
    entries = "".join(f"100644 {blob} {n}\t.revmux/keep.md\n" for n in (1, 2, 3))
    subprocess.run(
        ["git", "update-index", "--index-info"],
        cwd=repo,
        input=f"0 {'0' * 40}\t.revmux/keep.md\n" + entries,
        text=True,
        check=True,
    )
    assert revmux_aside(mt, repo, state_dir, expect=1) == {"revmux_tree": "unsafe"}
    assert revmux_bytes(repo) == tree and not (state_dir / "revmux-tree").exists()


@pytest.fixture
def revmux_nested_repo(git_repo: Path, tmp_path: Path) -> tuple[Path, Path]:
    """Tracked .revmux/sub/a (v0 at HEAD, `user` in the tree), and an external directory holding its own `a`."""
    (git_repo / ".revmux" / "sub").mkdir(parents=True)
    (git_repo / ".revmux" / "sub" / "a").write_text("v0\n")
    commit(git_repo, "base")
    (git_repo / ".revmux" / "sub" / "a").write_text("user\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    return git_repo, outside


def link_sub_to(repo: Path, target: Path) -> Path:
    """Replace the directory .revmux/sub by a symlink to target."""
    sub = repo / ".revmux" / "sub"
    shutil.rmtree(sub)
    sub.symlink_to(target, target_is_directory=True)
    return sub


def test_revmux_aside_refuses_a_symlinked_parent_directory(mt, revmux_nested_repo, tmp_path):
    """.revmux/sub is a symlink to a directory outside the checkout: removing .revmux/sub/a would delete the
    external file. The aside refuses before touching anything."""
    repo, outside = revmux_nested_repo
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (outside / "a").write_text("external\n")
    sub = link_sub_to(repo, outside)
    assert revmux_aside(mt, repo, state_dir, expect=1) == {"revmux_tree": "unsafe"}
    assert (outside / "a").read_text() == "external\n", "the external file is untouched"
    assert sorted(p.name for p in outside.iterdir()) == ["a"]
    assert sub.is_symlink() and os.readlink(sub) == str(outside)
    assert not any(state_dir.iterdir()), "nothing saved"


def test_revmux_aside_rerun_refuses_a_symlinked_parent_of_an_earlier_saved_path(mt, revmux_nested_repo, tmp_path):
    repo, outside = revmux_nested_repo
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    assert revmux_aside(mt, repo, state_dir) == {"revmux_tree": "aside"}
    (outside / "a").write_text("v0\n")
    link_sub_to(repo, outside)
    assert revmux_aside(mt, repo, state_dir, expect=1) == {"revmux_tree": "unsafe"}
    assert (outside / "a").read_text() == "v0\n"
    assert (state_dir / "revmux-tree" / "files" / ".revmux" / "sub" / "a").read_text() == "user\n"


def test_revmux_put_back_refuses_a_symlinked_parent_directory(mt, revmux_nested_repo, tmp_path):
    """After the aside .revmux/sub becomes a symlink to an external directory whose `a` holds HEAD's bytes, so
    the HEAD-version check alone passes. The put-back is a conflict and the external file keeps its bytes."""
    repo, outside = revmux_nested_repo
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    assert revmux_aside(mt, repo, state_dir) == {"revmux_tree": "aside"}
    (outside / "a").write_text("v0\n")
    sub = link_sub_to(repo, outside)
    assert revmux_put_back(mt, repo, state_dir) == {"revmux_tree": "conflict"}
    assert (outside / "a").read_text() == "v0\n", "the external file is not overwritten"
    assert sorted(p.name for p in outside.iterdir()) == ["a"]
    assert sub.is_symlink()
    saved = state_dir / "revmux-tree" / "files" / ".revmux" / "sub" / "a"
    assert saved.read_text() == "user\n", "the saved copy stays for a later put-back"
    # with the directory back the put-back goes through
    sub.unlink()
    git(repo, "checkout", "-q", "--", ".revmux")
    assert revmux_put_back(mt, repo, state_dir) == {"revmux_tree": "ok"}
    assert (repo / ".revmux" / "sub" / "a").read_text() == "user\n"


@pytest.mark.parametrize("component", ["revmux-tree", "files", ".revmux", "sub"])
def test_revmux_put_back_refuses_a_symlinked_parent_in_the_state_dir(mt, revmux_nested_repo, tmp_path, component):
    """A saved copy reached through a symlinked directory under the state dir is not copied into the tree."""
    repo, outside = revmux_nested_repo
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    assert revmux_aside(mt, repo, state_dir) == {"revmux_tree": "aside"}
    chain = ["revmux-tree", "files", ".revmux", "sub"]
    real = state_dir.joinpath(*chain[: chain.index(component) + 1])
    moved = outside / "moved"
    shutil.move(real, moved)
    (moved.joinpath(*chain[chain.index(component) + 1 :], "a")).write_text("evil\n")
    real.symlink_to(moved, target_is_directory=True)
    assert revmux_put_back(mt, repo, state_dir) == {"revmux_tree": "conflict"}
    assert (repo / ".revmux" / "sub" / "a").read_text() == "v0\n", "nothing copied into the tree"
    assert real.is_symlink()


def test_revmux_save_keeps_an_earlier_save_when_the_rerun_check_fails(mt, revmux_staged_repo, tmp_path):
    """A rerun whose index no longer matches the first save stops without touching it: the saved patch is the
    user's staged content, not this call's."""
    repo, state_dir = revmux_staged_repo, tmp_path / "state"
    state_dir.mkdir()
    assert revmux_save(repo, state_dir, tmp_path) == {"revmux_saved": "yes"}
    saved = (state_dir / "revmux-index.patch").read_bytes()
    (repo / ".revmux" / "other.md").write_text("other\n")
    git(repo, "add", "--", ".revmux/other.md")
    assert revmux_save(repo, state_dir, tmp_path, expect=1) == {"revmux_saved": "unsafe"}
    assert (state_dir / "revmux-index.patch").read_bytes() == saved
    assert staged(repo, ".revmux/other.md") == "other"


# ---------------------------------------------------------------- a free branch name, both modes

NAME = recipe("create-branch.sh --print-name")
PRINT_NAME_STUB = """#!/bin/bash
[ "$1" = --print-name ] || exit 1
basename "$2" .md | sed 's/^[0-9]\\{8\\}-//'
"""


def free_name(repo: Path, tmp: Path) -> dict[str, str]:
    exec_scripts = tmp / "exec-scripts"
    exec_scripts.mkdir(exist_ok=True)
    (exec_scripts / "create-branch.sh").write_text(PRINT_NAME_STUB)
    plan = shlex.quote(str(repo / "docs" / "plans" / "20261029-session-store.md"))
    return run_recipe(
        NAME, tmp, exec_scripts=shlex.quote(str(exec_scripts)), plan=plan, main_checkout=shlex.quote(str(repo))
    )


def test_worktree_name_skips_branches_and_worktrees_that_exist(git_repo, tmp_path):
    assert free_name(git_repo, tmp_path) == {"base": "session-store", "name": "session-store"}
    git(git_repo, "branch", "session-store")
    assert free_name(git_repo, tmp_path)["name"] == "session-store-2"
    git(git_repo, "branch", "worktree-session-store-2")
    (git_repo / ".claude" / "worktrees" / "session-store-3").mkdir(parents=True)
    assert free_name(git_repo, tmp_path) == {"base": "session-store", "name": "session-store-4"}


INPLACE_BRANCH = re.search(r"`(git -C <toplevel> checkout -b <name>)`", SKILL_TEXT)


def test_in_place_branch_never_reuses_an_old_branch_of_the_same_name(git_repo, tmp_path):
    """An earlier task left `session-store` behind (stale here); create-branch.sh would check it out. In place
    the run takes the free name and branches from the current HEAD, keeping the user's dirty tree."""
    assert INPLACE_BRANCH, "SKILL.md lost the in-place branch command"
    old = git(git_repo, "rev-parse", "HEAD")
    git(git_repo, "branch", "session-store")
    (git_repo / "later.txt").write_text("later\n")
    head = commit(git_repo, "later on main")
    (git_repo / "README.md").write_text("user edit\n")
    name = free_name(git_repo, tmp_path)["name"]
    assert name == "session-store-2"
    cmd = INPLACE_BRANCH.group(1).replace("<toplevel>", shlex.quote(str(git_repo))).replace("<name>", name)
    subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, check=True)
    assert git(git_repo, "branch", "--show-current") == "session-store-2"
    assert git(git_repo, "rev-parse", "HEAD") == head != old
    assert git(git_repo, "rev-parse", "session-store") == old, "the old branch is left alone"
    assert porcelain(git_repo) == [" M README.md"]


def test_dirty_check_before_review_ignores_revmux(git_repo):
    """SKILL.md Before round 1 step 3: an untracked or edited .revmux/ profile is not dirt."""
    before = SKILL_TEXT[SKILL_TEXT.index("### Before round 1") : SKILL_TEXT.index("### Each round")]
    m = re.search(r"`(git -C <toplevel> status --porcelain [^`]*)`", before)
    assert m, "SKILL.md lost the before-review clean check"
    cmd = m.group(1).replace("<toplevel>", shlex.quote(str(git_repo)))
    (git_repo / ".revmux").mkdir()
    (git_repo / ".revmux" / "profile.md").write_text("profile\n")
    r = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, check=True)
    assert r.stdout == ""
    (git_repo / "README.md").write_text("dirty\n")
    r = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, check=True)
    assert r.stdout.strip() == "M README.md"


# ---------------------------------------------------------------- the default profile, from the shipped template

SHIPPED_COMPANIONS = ("brainstorm", "planning", "revmux")
PROMPT_RECIPE = re.search(
    r"`(\(cd <main_checkout> && bash <exec_scripts>/resolve-file\.sh prompts/<name>\.md <exec_data>\))`", SKILL_TEXT
)
RULES_RECIPE = re.search(r"`(\(cd <main_checkout> && bash <exec_rules> planning-rules\.md <exec_data>\))`", SKILL_TEXT)


def profile_cmd(mt, *args: str) -> dict:
    """`mt profile ARGS`: exit 0 and its JSON line."""
    r, out = profile(mt, *args)
    assert r.code == 0 and out is not None, r.err
    return out


def shell_out(text: str, cwd: Path, **values: str) -> str:
    """A SKILL.md command with its <key>s filled in (quoted), run in bash; its stdout."""
    for key, value in values.items():
        assert f"<{key}>" in text
        text = text.replace(f"<{key}>", shlex.quote(value))
    assert not re.search(r"<\w+>", text), text
    r = subprocess.run(["bash", "-c", text], cwd=cwd, capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr
    return r.stdout


def repo_with_origin(tmp: Path) -> Path:
    """A repo on `work` whose origin, a local bare repo, has its HEAD on `stable`.

    No origin/HEAD is cached and no main, master, trunk or develop branch exists, so only planning's
    detect-branch.sh finds `stable` (its last step asks origin); mt's own fallback stops at `main`.
    """
    origin = (tmp / "origin.git").resolve()
    subprocess.run(["git", "init", "-q", "--bare", "-b", "stable", str(origin)], check=True)
    repo = (tmp / "project").resolve()
    repo.mkdir()
    git(repo, "init", "-q", "-b", "work")
    (repo / "README.md").write_text("hello\n")
    commit(repo, "init")
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "-q", "origin", "work:stable")
    return repo


def test_default_profile_from_the_shipped_template_runs_every_phase(mt, isolate, tmp_path, monkeypatch):
    """SKILL.md Start on first use with the template make-task ships: no profile, install-default, check, then
    init runs the profile's copies and the phases go from brainstorm to finalize."""
    monkeypatch.setattr(MT, "PROFILE_TEMPLATES", SHIPPED_TEMPLATES)
    monkeypatch.setenv("LC_ALL", "C")  # detect-branch.sh reads git's untranslated `HEAD branch:` line
    tpl = SHIPPED
    repo = repo_with_origin(tmp_path)
    d = isolate.config_home / "profiles" / "default"

    # Start step 0: resolve, install-default on first use, check
    assert profile_cmd(mt, "resolve") == {"name": "default", "dir": str(d), "status": "missing"}
    installed = profile_cmd(mt, "install-default")
    assert (installed["dir"], installed["template"]) == (str(d), str(tpl))
    assert installed["companions"] == {
        cid: {"source": f"make-task:default/{cid}", "version": MT.tree_hash(tpl / "companions" / cid)}
        for cid in SHIPPED_COMPANIONS
    }
    assert profile_cmd(mt, "resolve")["status"] == "ok"
    r = mt("profile", "check")
    assert (r.code, r.json()) == (0, {"ok": True, "errors": [], "warnings": [], "updates": []}), r.err

    # Start step 2: the init line points into the installed copies
    info = init(mt, repo, profile=d)
    assert info["warnings"] == []
    comps = d / "companions"
    files = {
        "brainstorm_entry": comps / "brainstorm/skills/brainstorm/SKILL.md",
        "plan_entry": comps / "planning/commands/make.md",
        "plan_reviewer": comps / "planning/agents/plan-review.md",
        "exec_rules": comps / "planning/scripts/resolve-rules.sh",
        "review_entry": comps / "revmux/SKILL.md",
    }
    dirs = {
        "exec_root": comps / "planning",
        "exec_scripts": comps / "planning/skills/exec/scripts",
        "exec_data": d / "data/planning",
        "review_scripts": comps / "revmux/scripts",
        "review_data": d / "data/revmux",
    }
    assert {k: info[k] for k in [*files, *dirs]} == {k: str(p) for k, p in {**files, **dirs}.items()}
    assert [k for k, p in files.items() if not p.is_file()] == []
    assert [k for k, p in dirs.items() if not p.is_dir()] == []
    scripts = Path(info["exec_scripts"])
    assert [s for s in MT.EXEC_CONTRACT_SCRIPTS if not (scripts / s).is_file()] == []
    assert [s for s in MT.REVIEW_CONTRACT_SCRIPTS if not (Path(info["review_scripts"]) / s).is_file()] == []
    assert {k: info[k] for k in ("profile_name", "profile_dir", "autonomy", "gates", "defaults")} == {
        "profile_name": "default",
        "profile_dir": str(d),
        "autonomy": "default",
        "gates": [],
        "defaults": {"mode": "worktree", "leftovers": "leave"},
    }
    assert info["plan_reviewer_model"] == "opus", "from the plan-review copy's frontmatter"
    assert info["default_branch"] == "stable", "the planning copy's detect-branch.sh asked origin"
    assert MT.default_branch(str(repo), str(tmp_path / "no-scripts")) == "main", "mt's own fallback would not"

    # Subagent prompts: the SKILL.md commands read the copy's prompts (<exec_data> starts empty) and rules
    assert PROMPT_RECIPE and RULES_RECIPE, "SKILL.md lost the prompt or rules command"
    filled = {"main_checkout": info["main_checkout"], "exec_data": info["exec_data"]}
    prompts = scripts.parent / "references" / "prompts"
    for p in MT.EXEC_CONTRACT_PROMPTS:
        cmd = PROMPT_RECIPE.group(1).replace("<name>", p.removesuffix(".md"))
        assert shell_out(cmd, tmp_path, exec_scripts=info["exec_scripts"], **filled) == (prompts / p).read_text(), p
    task = (prompts / "task.md").read_text()
    named = {n.rstrip(".") for n in re.findall(re.escape(info["exec_root"]) + r"/[\w./-]+", task)}
    assert named and [n for n in named if not Path(n).is_file()] == [], "the task prompt names the copy's scripts"
    assert "${CLAUDE_" not in task
    assert shell_out(RULES_RECIPE.group(1), tmp_path, exec_rules=info["exec_rules"], **filled) == ""
    # Exec step 1's branch name, by the copy's create-branch.sh
    plan = repo / "docs" / "plans" / "20261029-session-store.md"
    name = run_recipe(
        NAME,
        tmp_path,
        exec_scripts=shlex.quote(info["exec_scripts"]),
        plan=shlex.quote(str(plan)),
        main_checkout=shlex.quote(info["main_checkout"]),
    )
    assert name == {"base": "session-store", "name": "session-store"}

    # Recovery reads the same keys from mt status
    st = mt("status").json()
    assert st["warnings"] == []
    assert {k: st["values"][k] for k in MT.PROFILE_STATE_KEYS} == {k: info[k] for k in MT.PROFILE_STATE_KEYS}

    for i, pid in enumerate(MT.PHASE_IDS):
        assert mt("phase", pid).code == 0, pid
        st = mt("status").json()
        assert (st["phase"], st["phase_status"]) == (pid, "run")
        assert list(st["phases"].values()) == ["done"] * i + ["run"] + ["pending"] * (len(MT.PHASE_IDS) - 1 - i)
    assert mt("done", "--status", "not clean").code == 0
    st = mt("status").json()
    assert list(st["phases"]) == MT.PHASE_IDS
    assert set(st["phases"].values()) == {"done"}
    assert st["values"]["final_status"] == "not clean"


def test_prompt_overrides_with_the_shipped_template(mt, isolate, git_repo, tmp_path, monkeypatch):
    """The Overview's prompt-source change, run through SKILL.md's prompt and rules commands: the profile copy's
    prompts by default (its data/planning starts empty), <exec_data> over the copy, the project's
    .claude/exec-plan/prompts/ and .claude/planning-rules.md over both; the planning plugin's data dir, where the
    prompts resolved before profiles, is never read."""
    assert PROMPT_RECIPE and RULES_RECIPE, "SKILL.md lost the prompt or rules command"
    monkeypatch.setattr(MT, "PROFILE_TEMPLATES", SHIPPED_TEMPLATES)
    d = Path(profile_cmd(mt, "install-default")["dir"])
    info = init(mt, git_repo, profile=d)
    copy = Path(info["exec_scripts"]).parent / "references" / "prompts"
    data = Path(info["exec_data"])
    assert data == d / "data" / "planning" and data.is_dir() and not any(data.iterdir()), "it starts empty"
    filled = {"main_checkout": info["main_checkout"], "exec_data": info["exec_data"]}
    names = [p.removesuffix(".md") for p in MT.EXEC_CONTRACT_PROMPTS]

    def prompts() -> dict[str, str]:
        return {
            n: shell_out(
                PROMPT_RECIPE.group(1).replace("<name>", n), tmp_path, exec_scripts=info["exec_scripts"], **filled
            )
            for n in names
        }

    def rules() -> str:
        return shell_out(RULES_RECIPE.group(1), tmp_path, exec_rules=info["exec_rules"], **filled)

    ours = {n: (copy / f"{n}.md").read_text() for n in names}
    # the planning plugin's own data dir (installed_plugins.json's dir/data/<plugin>-<marketplace>) holds stale
    # copies, as the user's machine did before profiles
    old = isolate.plugins_dir / "data" / "planning-umputun-cc-thingz"
    (old / "prompts").mkdir(parents=True)
    for n in names:
        (old / "prompts" / f"{n}.md").write_text(f"stale {n}\n")
    (old / "planning-rules.md").write_text("stale rules\n")
    assert prompts() == ours and rules() == "", "the copy's prompts, no rules"

    # user level: the profile's data dir
    (data / "prompts").mkdir()
    (data / "prompts" / "fixer.md").write_text("user fixer\n")
    (data / "prompts" / "stats.md").write_text("user stats\n")
    (data / "planning-rules.md").write_text("user rules\n")
    assert prompts() == {**ours, "fixer": "user fixer\n", "stats": "user stats\n"}
    assert rules() == "user rules\n"

    # project level: the main checkout's .claude/
    project = git_repo / ".claude"
    (project / "exec-plan" / "prompts").mkdir(parents=True)
    (project / "exec-plan" / "prompts" / "task.md").write_text("project task\n")
    (project / "exec-plan" / "prompts" / "fixer.md").write_text("project fixer\n")
    (project / "planning-rules.md").write_text("project rules\n")
    assert prompts() == {**ours, "task": "project task\n", "fixer": "project fixer\n", "stats": "user stats\n"}
    assert rules() == "project rules\n"


TEMPLATE_RULE = "Template rule: run ${CLAUDE_SKILL_DIR}/scripts/preflight.sh first.\n"
TEMPLATE_FILE = "added by the template\n"
PROMPT_RULE = "Template: keep each commit small.\n"
USER_RULE = "My rule: always run the security lens.\n"


def test_modified_copy_survives_an_update_from_a_newer_template(mt, isolate, git_repo):
    """references/profiles.md Update flow against a newer copy of the shipped template: the user's edit to the
    revmux copy stays through fetch, the merge and accept, and the template's change joins it; the unmodified
    planning copy is replaced."""
    tpl = isolate.templates / "default"
    shutil.copytree(SHIPPED, tpl, symlinks=True)
    installed = profile_cmd(mt, "install-default")
    d = Path(installed["dir"])
    comp, up = (d / tree / "revmux" for tree in ("companions", "upstream"))
    entry = comp / "SKILL.md"
    assert entry.read_text().endswith("\n")
    with entry.open("a") as f:
        f.write(USER_RULE)
    mine = MT.tree_hash(comp)

    # make-task's next release: a rule with a raw token and a new file in revmux, a prompt line in planning
    skill = tpl / "companions/revmux/SKILL.md"
    lines = skill.read_text().splitlines(keepends=True)
    end = lines.index("---\n", 1) + 1  # right after the frontmatter, far from the user's line at the end
    skill.write_text("".join([*lines[:end], "\n", TEMPLATE_RULE, *lines[end:]]))
    (tpl / "companions/revmux/references/new-rule.md").write_text(TEMPLATE_FILE)
    with (tpl / "companions/planning/skills/exec/references/prompts/task.md").open("a") as f:
        f.write(PROMPT_RULE)
    new = {cid: MT.tree_hash(tpl / "companions" / cid) for cid in ("planning", "revmux")}

    # Start step 0's check lists both updates; the update flow's outdated finds the same
    r = mt("profile", "check")
    out = r.json()
    assert (r.code, out["ok"], out["errors"], out["warnings"]) == (0, True, [], []), r.err
    assert [(u["id"], u["available"], u["modified"]) for u in out["updates"]] == [
        ("planning", new["planning"], False),
        ("revmux", new["revmux"], True),
    ]
    assert profile_cmd(mt, "outdated", "--network")["updates"] == out["updates"]

    # step 3.1: fetch stages the new version, rewritten; the copy is untouched
    fetched = profile_cmd(mt, "fetch", "revmux")
    assert (fetched["changed"], fetched["modified"]) == (True, True)
    staged = Path(fetched["staging"])
    assert staged == d / ".staging" / "revmux" / "tree"
    rule = TEMPLATE_RULE.replace("${CLAUDE_SKILL_DIR}", str(comp))
    assert f"+{rule}" in fetched["diff"] and f"+{TEMPLATE_FILE}" in fetched["diff"]
    assert USER_RULE not in fetched["diff"]
    assert MT.tree_hash(comp) == mine

    # step 3.4: merge into the copy file by file; the result differs from the new version by the user's edit only
    r = subprocess.run(
        ["git", "merge-file", str(entry), str(up / "SKILL.md"), str(staged / "SKILL.md")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, f"conflicts: {r.returncode} {r.stderr}"
    shutil.copy2(staged / "references/new-rule.md", comp / "references/new-rule.md")
    shown = MT.tree_diff(d, staged, comp)
    assert [ln for ln in shown.splitlines() if ln[:1] in "+-" and not ln.startswith(("+++", "---"))] == [
        f"+{USER_RULE.rstrip()}"
    ]
    new_base = MT.tree_hash(staged)
    assert new_base == fetched["tree"], "the merge left the staging as fetch printed it"

    accepted = profile_cmd(mt, "accept", "revmux", "--expect", fetched["tree"])
    assert accepted == {
        "id": "revmux",
        "source": "make-task:default/revmux",
        "version": new["revmux"],
        "previous": installed["companions"]["revmux"]["version"],
        "copy": "kept",
        "modified": True,
    }
    text = entry.read_text()
    assert text.endswith(USER_RULE), "the user's edit survived"
    assert rule in text and (comp / "references/new-rule.md").read_text() == TEMPLATE_FILE, "the update joined it"
    assert MT.tree_hash(up) == new_base, "upstream/revmux is the new version, the next merge's base"

    # an unmodified copy takes the new version as it is
    fetched = profile_cmd(mt, "fetch", "planning")
    assert (fetched["changed"], fetched["modified"]) == (True, False)
    assert profile_cmd(mt, "accept", "planning", "--expect", fetched["tree"])["copy"] == "updated"
    assert (d / "companions/planning/skills/exec/references/prompts/task.md").read_text().endswith(PROMPT_RULE)

    assert not (d / ".staging").exists()
    r = mt("profile", "check")
    assert (r.code, r.json()) == (0, {"ok": True, "errors": [], "warnings": [], "updates": []}), r.err
    st = profile_cmd(mt, "status")
    assert {cid: c["modified"] for cid, c in st["companions"].items()} == {
        "brainstorm": False,
        "planning": False,
        "revmux": True,
    }
    assert {cid: c["version"] for cid, c in st["companions"].items()} == {
        "brainstorm": installed["companions"]["brainstorm"]["version"],
        **new,
    }
    assert MT.tree_hash(tpl / "companions/revmux") == new["revmux"], "an update never writes into the template"
    assert init(mt, git_repo, profile=d)["warnings"] == [], "the updated profile runs"
