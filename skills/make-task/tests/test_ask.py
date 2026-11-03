"""Question cards: `mt ask` against the fake agtermctl, card validation, and the card page itself.

No test touches the live terminal: agterm calls go to the fake from conftest.
"""

from __future__ import annotations

import html as htmllib
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
from conftest import MT, TESTS, init_args
from test_agterm import PANE_ID, SID, tree

CARD_TEMPLATE = TESTS.parent / "assets" / "card.html"


def card(**kw: Any) -> dict[str, Any]:
    c: dict[str, Any] = {
        "phase": "brainstorm",
        "title": "How long should a session stay valid?",
        "context": "Sessions live in memory, so every deploy signs everyone out.",
        "options": [
            {"id": "sliding-30d", "label": "30 days, sliding", "pro": "stay signed in", "con": "stolen token lives",
             "recommended": True},
            {"id": "fixed-24h", "label": "24 hours, fixed", "pro": "short leak window", "con": "daily sign-in",
             "recommended": False},
            {"id": "per-client", "label": "Per client", "pro": "flexible", "con": "one more key"},
        ],
        "allow_other": True,
    }  # fmt: skip
    c.update(kw)
    return c


def write_card(tmp: Path, data: Any, name: str = "card.json") -> Path:
    p = tmp / name
    p.write_text(json.dumps(data) if not isinstance(data, str) else data)
    return p


@pytest.fixture
def agterm_run(mt, git_repo, isolate, monkeypatch):
    """A run initialized in (fake) agterm with the dashboard open; returns (run dir, set_block_reply)."""
    monkeypatch.setenv("AGTERM_ENABLED", "1")
    monkeypatch.setenv("AGTERM_SESSION_ID", SID)
    monkeypatch.setenv("AGTERM_PANE_ID", PANE_ID)
    base = {"tree": {"stdout": json.dumps(tree())}}
    isolate.agterm_replies.write_text(json.dumps(base))
    r = mt(*init_args("Ask test"), cwd=git_repo)
    assert r.code == 0, r.err
    run = Path(json.loads(r.out)["state_dir"])
    assert json.loads((run / "state.json").read_text())["agterm"] is True
    isolate.agterm_log.unlink()

    def block_reply(stdout: Any = None, exit: int = 0, sleep: float = 0) -> None:
        reply: dict[str, Any] = {"exit": exit, "sleep": sleep}
        if stdout is not None:
            reply["stdout"] = stdout if isinstance(stdout, str) else json.dumps(stdout)
        isolate.agterm_replies.write_text(json.dumps({**base, "session overlay open": reply}))

    return run, block_reply


def submitted(answer: Any) -> dict[str, Any]:
    """agtermctl --block output: the page's JSON is double-encoded in value."""
    return {"outcome": "submitted", "value": json.dumps(answer)}


def open_argv(run: Path, n: int = 1) -> list[str]:
    return [
        "session", "overlay", "open", "--html", str(run / "cards" / f"{n}.html"), "--js", "--chromeless",
        "--size-percent", "85", "--block", "--follow", "--target", SID,
    ]  # fmt: skip


def status_argv(state: str, sound: bool = False) -> list[str]:
    argv = ["session", "status", state, "--target", SID]
    if sound:
        argv += ["--sound", "default"]
    return argv + ["--pane-id", PANE_ID]


def embedded(page: str) -> dict[str, Any]:
    m = re.search(r'<script id="card" type="application/json">(.*?)</script>', page, re.DOTALL)
    assert m
    return json.loads(m.group(1))


# ---------------------------------------------------------------- answers


def test_submitted_choice_records_decision(mt, git_repo, isolate, agterm_run, run_state):
    run, block_reply = agterm_run
    block_reply(submitted({"choice": "fixed-24h"}))
    r = mt("ask", str(write_card(isolate.tmp, card())), cwd=git_repo)
    assert r.code == 0, r.err
    assert json.loads(r.out) == {"choice": "fixed-24h"}
    assert isolate.agterm_calls() == [status_argv("blocked", sound=True), open_argv(run), status_argv("active")]
    s = run_state(run)
    assert s["decisions"] == [{"q": "How long should a session stay valid?", "a": "24 hours, fixed"}]
    assert s["assumptions"] == []
    assert s["activity"] == "Starting" and s["now_kind"] == ""
    events = [e["text"] for e in s["events"]]
    assert "Question 1: How long should a session stay valid?" in events
    assert events[-1] == "Answer 1: 24 hours, fixed"
    assert json.loads((run / "cards" / "1.json").read_text())["options"][2]["recommended"] is False
    page = (run / "cards" / "1.html").read_text()
    assert "<title>make-task question 1</title>" in page
    assert "{{" not in page


def test_other_text_is_trimmed_and_recorded(mt, git_repo, isolate, agterm_run, run_state):
    run, block_reply = agterm_run
    block_reply(submitted({"choice": "other", "text": "  7 days, fixed  "}))
    r = mt("ask", str(write_card(isolate.tmp, card())), cwd=git_repo)
    assert r.code == 0, r.err
    assert json.loads(r.out) == {"choice": "other", "text": "7 days, fixed"}
    assert run_state(run)["decisions"][-1]["a"] == "7 days, fixed"


def test_delegate_takes_recommended_and_records_assumption(mt, git_repo, isolate, agterm_run, run_state):
    run, block_reply = agterm_run
    block_reply(submitted({"choice": "delegate"}))
    r = mt("ask", str(write_card(isolate.tmp, card())), cwd=git_repo)
    assert r.code == 0, r.err
    assert json.loads(r.out) == {"choice": "delegate", "id": "sliding-30d"}
    s = run_state(run)
    assert s["decisions"] == []
    assert len(s["assumptions"]) == 1
    assert s["assumptions"][0]["text"] == "How long should a session stay valid? → 30 days, sliding"
    assert "delegated" in s["assumptions"][0]["why"]


def test_reply_in_an_ok_result_envelope_is_unwrapped(mt, git_repo, isolate, agterm_run):
    """agtermctl's other replies use {"ok", "result"}; a --block reply in that shape must not exit 3."""
    _, block_reply = agterm_run
    block_reply({"ok": True, "result": submitted({"choice": "fixed-24h"})})
    r = mt("ask", str(write_card(isolate.tmp, card())), cwd=git_repo)
    assert (r.code, json.loads(r.out)) == (0, {"choice": "fixed-24h"}), r.err


def test_value_that_is_not_an_object_is_unusable(mt, git_repo, isolate, agterm_run):
    _, block_reply = agterm_run
    for value in ("5", '"fixed-24h"', "[1]", "null"):
        block_reply({"outcome": "submitted", "value": value})
        r = mt("ask", str(write_card(isolate.tmp, card())), cwd=git_repo)
        assert (r.code, json.loads(r.out)) == (2, {"choice": None}), value


def test_value_already_an_object_is_accepted(mt, git_repo, isolate, agterm_run):
    _, block_reply = agterm_run
    block_reply({"outcome": "submitted", "value": {"choice": "per-client"}})
    r = mt("ask", str(write_card(isolate.tmp, card())), cwd=git_repo)
    assert (r.code, json.loads(r.out)) == (0, {"choice": "per-client"})


def test_dismissed_card_exits_2(mt, git_repo, isolate, agterm_run, run_state):
    run, block_reply = agterm_run
    block_reply({"outcome": "dismissed"}, exit=2)
    r = mt("ask", str(write_card(isolate.tmp, card())), cwd=git_repo)
    assert r.code == 2
    assert json.loads(r.out) == {"choice": None}
    assert isolate.agterm_calls()[-1] == status_argv("active")
    s = run_state(run)
    assert s["decisions"] == [] and s["assumptions"] == []
    assert s["events"][-1]["text"] == "Question 1 dismissed"
    assert s["activity"] == "Starting" and s["now_kind"] == ""


def test_dismissed_outcome_with_exit_0_still_exits_2(mt, git_repo, isolate, agterm_run):
    _, block_reply = agterm_run
    block_reply({"outcome": "dismissed"}, exit=0)
    r = mt("ask", str(write_card(isolate.tmp, card())), cwd=git_repo)
    assert r.code == 2


def test_deadline_closes_card_and_exits_2(mt, git_repo, isolate, agterm_run, monkeypatch, run_state):
    run, block_reply = agterm_run
    block_reply(submitted({"choice": "fixed-24h"}), sleep=20)
    monkeypatch.setenv("MT_ASK_DEADLINE", "1")
    t0 = time.monotonic()
    r = mt("ask", str(write_card(isolate.tmp, card())), cwd=git_repo)
    assert time.monotonic() - t0 < 10
    assert r.code == 2
    assert json.loads(r.out) == {"choice": None}
    calls = isolate.agterm_calls()
    assert calls[1] == open_argv(run)
    assert ["session", "overlay", "close", "--target", SID] in calls
    assert calls.index(["session", "overlay", "close", "--target", SID]) > 1
    assert calls[-1] == status_argv("active")
    s = run_state(run)
    assert s["decisions"] == []
    assert s["events"][-1]["text"] == "Question 1 expired after 1s"


@pytest.mark.usefixtures("agterm_run")
def test_ask_stays_under_the_bash_limit_when_agtermctl_hangs(mt, git_repo, isolate, monkeypatch):
    """Status, close and status again are capped at ASK_CALL_TIMEOUT, so a hung agtermctl cannot push
    `mt ask` past the Bash tool's 600 s."""
    assert MT.ASK_DEADLINE + 3 * MT.ASK_CALL_TIMEOUT < MT.REVIEW_DEADLINE < MT.BASH_LIMIT
    hang = {"sleep": 30}
    replies = json.loads(isolate.agterm_replies.read_text())
    replies.update({"session status": hang, "session overlay open": hang, "session overlay close": hang})
    isolate.agterm_replies.write_text(json.dumps(replies))
    monkeypatch.setenv("MT_ASK_DEADLINE", "0.5")
    monkeypatch.setattr(MT, "ASK_CALL_TIMEOUT", 0.3)
    t0 = time.monotonic()
    r = mt("ask", str(write_card(isolate.tmp, card())), cwd=git_repo)
    assert time.monotonic() - t0 < 5
    assert r.code == 2 and json.loads(r.out) == {"choice": None}
    assert "session overlay close: timed out" in r.err and "session status active: timed out" in r.err


def test_failing_close_after_the_deadline_only_warns(mt, git_repo, isolate, agterm_run, monkeypatch, run_state):
    run, block_reply = agterm_run
    block_reply(submitted({"choice": "fixed-24h"}), sleep=20)
    monkeypatch.setenv("MT_ASK_DEADLINE", "0.5")
    monkeypatch.setenv("FAKE_AGTERMCTL_FAIL", "session overlay close")
    r = mt("ask", str(write_card(isolate.tmp, card())), cwd=git_repo)
    assert r.code == 2 and "agtermctl session overlay close" in r.err
    assert run_state(run)["events"][-1]["text"] == "Question 1 expired after 0.5s"


def test_unusable_answer_exits_2(mt, git_repo, isolate, agterm_run, run_state):
    run, block_reply = agterm_run
    for value in ({"choice": "nope"}, {"choice": "other", "text": "   "}, "not json", {"text": "x"}):
        block_reply({"outcome": "submitted", "value": value if isinstance(value, str) else json.dumps(value)})
        r = mt("ask", str(write_card(isolate.tmp, card())), cwd=git_repo)
        assert r.code == 2, value
        assert json.loads(r.out) == {"choice": None}
        assert "does not fit the card" in r.err
    assert run_state(run)["decisions"] == []


def test_other_refused_when_not_allowed(mt, git_repo, isolate, agterm_run):
    _, block_reply = agterm_run
    block_reply(submitted({"choice": "other", "text": "mine"}))
    r = mt("ask", str(write_card(isolate.tmp, card(allow_other=False))), cwd=git_repo)
    assert r.code == 2


def test_overlay_failure_exits_3(mt, git_repo, isolate, agterm_run, run_state):
    run, block_reply = agterm_run
    block_reply(exit=1)
    r = mt("ask", str(write_card(isolate.tmp, card())), cwd=git_repo)
    assert r.code == 3
    assert json.loads(r.out) == {"choice": None}
    assert "overlay open" in r.err
    assert isolate.agterm_calls()[-1] == status_argv("active")
    assert run_state(run)["now_kind"] == ""


def test_garbage_block_output_exits_3(mt, git_repo, isolate, agterm_run):
    _, block_reply = agterm_run
    block_reply("not json at all")
    r = mt("ask", str(write_card(isolate.tmp, card())), cwd=git_repo)
    assert r.code == 3


def test_cards_are_numbered_and_activity_restored(mt, git_repo, isolate, agterm_run, run_state):
    run, block_reply = agterm_run
    assert mt("activity", "Brainstorming", cwd=git_repo).code == 0
    block_reply(submitted({"choice": "fixed-24h"}))
    for _ in range(2):
        assert mt("ask", str(write_card(isolate.tmp, card())), cwd=git_repo).code == 0
    assert sorted(p.name for p in (run / "cards").iterdir()) == ["1.html", "1.json", "2.html", "2.json"]
    assert "<title>make-task question 2</title>" in (run / "cards" / "2.html").read_text()
    assert open_argv(run, 2) in isolate.agterm_calls()
    s = run_state(run)
    assert s["activity"] == "Brainstorming" and s["now_kind"] == ""
    assert len(s["decisions"]) == 2


def test_error_kind_is_kept_across_a_card(mt, git_repo, isolate, agterm_run, run_state):
    run, block_reply = agterm_run
    assert mt("phase", "exec", cwd=git_repo).code == 0
    assert mt("fail", "task 2 failed twice", cwd=git_repo).code == 0
    block_reply(submitted({"choice": "fixed-24h"}))
    assert mt("ask", str(write_card(isolate.tmp, card(phase="exec"))), cwd=git_repo).code == 0
    s = run_state(run)
    assert s["now_kind"] == "error" and s["error"] == "task 2 failed twice"
    assert embedded((run / "cards" / "1.html").read_text())["from"] == "Execute needs your call"


def test_not_in_agterm_exits_3(mt, git_repo, isolate, run_state):
    r = mt(*init_args("Plain"), cwd=git_repo)
    run = Path(json.loads(r.out)["state_dir"])
    r = mt("ask", str(write_card(isolate.tmp, card())), cwd=git_repo)
    assert r.code == 3
    assert json.loads(r.out) == {"choice": None}
    assert isolate.agterm_calls() == []
    assert not (run / "cards").exists()


def test_no_cards_exits_3_without_opening_a_card(mt, git_repo, isolate, agterm_run, monkeypatch):
    run, _ = agterm_run
    monkeypatch.setenv("MT_NO_CARDS", "1")
    r = mt("ask", str(write_card(isolate.tmp, card())), cwd=git_repo)
    assert r.code == 3
    assert json.loads(r.out) == {"choice": None}
    assert "MT_NO_CARDS" in r.err
    assert isolate.agterm_calls() == []
    assert not (run / "cards").exists()


@pytest.mark.parametrize(("value", "cards"), [("1", False), ("yes", False), ("0", True), ("", True)])
def test_init_reports_cards_and_keeps_the_dashboard(mt, git_repo, isolate, monkeypatch, value, cards):
    monkeypatch.setenv("AGTERM_ENABLED", "1")
    monkeypatch.setenv("AGTERM_SESSION_ID", SID)
    monkeypatch.setenv("AGTERM_PANE_ID", PANE_ID)
    monkeypatch.setenv("MT_NO_CARDS", value)
    isolate.agterm_replies.write_text(json.dumps({"tree": {"stdout": json.dumps(tree())}}))
    r = mt(*init_args("Cards"), cwd=git_repo)
    assert r.code == 0, r.err
    info = json.loads(r.out)
    assert info["agterm"] is True, "MT_NO_CARDS never turns the dashboard off"
    assert info["cards"] is cards
    assert json.loads((Path(info["state_dir"]) / "state.json").read_text())["cards"] is cards


def test_init_outside_agterm_reports_no_cards(mt, git_repo):
    info = json.loads(mt(*init_args("Plain"), cwd=git_repo).out)
    assert info["agterm"] is False and info["cards"] is False


def test_run_without_agterm_exits_3_even_in_agterm(mt, git_repo, isolate, monkeypatch):
    monkeypatch.setenv("AGTERM_ENABLED", "1")
    monkeypatch.setenv("AGTERM_SESSION_ID", SID)
    monkeypatch.setenv("FAKE_AGTERMCTL_FAIL", "1")
    assert mt(*init_args("Fell back"), cwd=git_repo).code == 0
    monkeypatch.delenv("FAKE_AGTERMCTL_FAIL")
    isolate.agterm_log.unlink()
    r = mt("ask", str(write_card(isolate.tmp, card())), cwd=git_repo)
    assert r.code == 3
    assert isolate.agterm_calls() == []


def test_ask_without_run_fails(mt, git_repo, isolate):
    r = mt("ask", str(write_card(isolate.tmp, card())), cwd=git_repo)
    assert r.code == 1
    assert "no active run" in r.err


# ---------------------------------------------------------------- validation


def options(n: int) -> list[dict[str, Any]]:
    return [{"id": f"o{i}", "label": f"Option {i}", "recommended": i == 0} for i in range(n)]


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (card(options=[]), "1 to 4 options, got 0"),
        (card(options=options(5)), "1 to 4 options, got 5"),
        (card(options="x"), "1 to 4 options"),
        (card(options=[*options(2), {"id": "o1", "label": "again"}]), "duplicate option id 'o1'"),
        (card(options=[{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]), "exactly one option must be recommended, got 0"),
        (card(options=[{"id": "a", "label": "A", "recommended": True}, {"id": "b", "label": "B", "recommended": True}]),
         "exactly one option must be recommended, got 2"),
        (card(options=[{"id": "other", "label": "A", "recommended": True}]), "reserved"),
        (card(options=[{"id": "delegate", "label": "A", "recommended": True}]), "reserved"),
        (card(options=[{"id": "", "label": "A", "recommended": True}]), "option 1: id"),
        (card(options=[{"id": "a", "label": " ", "recommended": True}]), "option 1: label"),
        (card(options=[{"id": "a", "label": "A", "recommended": "yes"}]), "recommended must be true or false"),
        (card(options=[{"id": "a", "label": "A", "recommended": True, "pro": 3}]), "option 1: pro"),
        (card(options=["a"]), "option 1: must be a JSON object"),
        (card(title=""), "title must be a non-empty string"),
        (card(title=None), "title must be a non-empty string"),
        (card(context=["x"]), "context must be a string"),
        (card(allow_other="no"), "allow_other must be true or false"),
        ([1, 2], "must be a JSON object"),
        ("{not json", "is not valid JSON"),
    ],
)  # fmt: skip
def test_invalid_cards_are_refused(mt, git_repo, isolate, agterm_run, data, message):
    run, block_reply = agterm_run
    block_reply(submitted({"choice": "o0"}))
    r = mt("ask", str(write_card(isolate.tmp, data)), cwd=git_repo)
    assert r.code == 1
    assert message in r.err
    assert isolate.agterm_calls() == []
    assert not (run / "cards").exists()


def test_four_options_and_one_option_are_valid():
    """Four is the cap: the AskUserQuestion fallback carries at most four."""
    assert len(MT.validate_card(card(options=options(4)))["options"]) == 4
    one = MT.validate_card({"title": "T", "options": [{"id": "a", "label": "A", "recommended": True}]})
    assert one == {
        "phase": "",
        "title": "T",
        "context": "",
        "allow_other": True,
        "options": [{"id": "a", "label": "A", "pro": "", "con": "", "recommended": True}],
    }


def test_missing_card_file_fails(mt, git_repo, isolate, agterm_run):
    r = mt("ask", str(isolate.tmp / "nope.json"), cwd=git_repo)
    assert r.code == 1
    assert "cannot read" in r.err


def test_bad_deadline_fails(mt, git_repo, isolate, agterm_run, monkeypatch):
    for bad in ("0", "-3", "soon"):
        monkeypatch.setenv("MT_ASK_DEADLINE", bad)
        r = mt("ask", str(write_card(isolate.tmp, card())), cwd=git_repo)
        assert r.code == 1
        assert "MT_ASK_DEADLINE" in r.err
    assert isolate.agterm_calls() == []


# ---------------------------------------------------------------- escaping and rendering

HOSTILE = '</script><script>alert(1)</script><!-- <img src=x onerror="alert(2)"> & "q"'


def test_script_close_in_context_and_labels_is_escaped(mt, git_repo, isolate, agterm_run):
    run, block_reply = agterm_run
    block_reply(submitted({"choice": "a"}))
    data = card(
        title=f"Title {HOSTILE}",
        context=f"Context {HOSTILE}",
        options=[
            {"id": "a", "label": f"Label {HOSTILE}", "pro": HOSTILE, "con": HOSTILE, "recommended": True},
            {"id": "b</script>", "label": "B"},
        ],
    )
    assert mt("ask", str(write_card(isolate.tmp, data)), cwd=git_repo).code == 0
    page = (run / "cards" / "1.html").read_text()
    template = CARD_TEMPLATE.read_text()
    assert page.count("</script>") == template.count("</script>") == 2
    assert page.count("<script") == template.count("<script")
    assert page.count("<!--") == template.count("<!--")
    raw = re.search(r'<script id="card" type="application/json">(.*?)</script>', page, re.DOTALL).group(1)
    assert "<" not in raw and ">" not in raw and "&" not in raw
    e = embedded(page)
    assert e["title"] == f"Title {HOSTILE}"
    assert e["options"][0]["label"] == f"Label {HOSTILE}"
    assert e["options"][1]["id"] == "b</script>"
    # raw HTML in the context is text, not markup
    assert "<script>" not in e["context_html"] and "<img" not in e["context_html"]
    assert "&lt;/script&gt;&lt;script&gt;alert(1)" in e["context_html"]


def test_context_markdown_is_rendered():
    page = MT.card_page(
        MT.validate_card(
            card(context="Uses **SQLite** and `middleware/auth.go`.\n\n- one\n- two\n\n[x](javascript:alert(1))")
        ),
        4,
        "Run <b>title</b>",
    )
    e = embedded(page)
    assert "<strong>SQLite</strong>" in e["context_html"]
    assert "<code>middleware/auth.go</code>" in e["context_html"]
    assert "<li>one</li>" in e["context_html"]
    assert "href=" not in e["context_html"], "markdown-it refuses javascript: links"
    assert e["n"] == 4 and e["run_title"] == "Run <b>title</b>"
    assert e["from"] == "Brainstorm needs your call"
    assert "<title>make-task question 4</title>" in page


def test_empty_context_and_unknown_phase():
    e = embedded(MT.card_page(MT.validate_card(card(context="", phase="leftovers")), 1, "R"))
    assert e["context_html"] == ""
    assert e["from"] == "leftovers needs your call"
    e = embedded(MT.card_page(MT.validate_card(card(phase="")), 1, "R"))
    assert e["from"] == "make-task needs your call"


def test_template_has_placeholders_once_and_no_debug_title_suffix():
    text = CARD_TEMPLATE.read_text()
    assert text.count("{{TITLE}}") == 1 and text.count("{{CARD}}") == 1
    assert "nozoom" not in text and "' · h='" not in text and "document.title +=" not in text
    assert not re.search(r"--agterm-[\w-]+\s*:", text), "agterm injects these; the page only reads them"
    assert "session.overlay.submit" in text and "session.overlay.resize" in text
    assert not re.search(r"<script[^>]*\bsrc=", text)


# ---------------------------------------------------------------- the page script (node)

NODE_HARNESS = r"""
const fs = require('fs');
const [script, cardJson, ops, tree] = [fs.readFileSync(process.argv[2], 'utf8'), fs.readFileSync(process.argv[3], 'utf8'),
                                       JSON.parse(process.argv[4]), JSON.parse(process.argv[5])];
const requests = [];
let rejectSubmits = 0;
let focused = null;
function el(extra) {
  return Object.assign({ innerHTML: '', textContent: '', className: '', value: '', checked: false, disabled: false,
    tagName: 'DIV', listeners: {}, offsetHeight: 100,
    addEventListener(t, f) { this.listeners[t] = f; },
    focus() { focused = this; if (this.listeners.focus) this.listeners.focus({}); },
    querySelectorAll: () => [] }, extra || {});
}
// a radio group: checking one unchecks the others, as in a browser
const data = JSON.parse(cardJson);
let picked = -1;
function radio(i, value) {
  const r = el({ tagName: 'INPUT', value });
  delete r.checked;
  Object.defineProperty(r, 'checked', { get() { return picked === i; },
    set(v) { if (v) picked = i; else if (picked === i) picked = -1; } });
  return r;
}
const radios = (data.options || []).map((o, i) => radio(i, o.id));
if (data.allow_other !== false) radios.push(radio(radios.length, 'other'));
picked = (data.options || []).findIndex(o => o.recommended);
const els = { card: el({ textContent: cardJson }), root: el(), f: el({ querySelectorAll: () => radios }),
  delegate: el({ tagName: 'BUTTON' }), answer: el({ tagName: 'BUTTON' }), status: el(),
  other: data.allow_other !== false ? el({ tagName: 'INPUT' }) : null };
const body = el({ tagName: 'BODY' });
const docListeners = {};
globalThis.window = globalThis;
globalThis.agterm = { request(cmd, opts) {
  requests.push([cmd, opts || null]);
  if (cmd === 'session.overlay.submit' && rejectSubmits > 0) { rejectSubmits--; return Promise.reject(new Error('gone')); }
  return Promise.resolve(cmd === 'tree' ? tree : {});
} };
globalThis.requestAnimationFrame = cb => setTimeout(cb, 0);
globalThis.document = {
  title: process.argv[6], fonts: { ready: Promise.resolve() },
  getElementById: id => els[id] || null, querySelector: () => null,
  addEventListener(t, f) { docListeners[t] = f; },
};
(0, eval)(script);
const tick = () => new Promise(r => setTimeout(r, 0));
(async () => {
  const results = [];
  for (const [fn, ...args] of ops) {
    if (fn === 'answerFor') results.push(window.mtCard.answerFor(...args));
    else if (fn === 'render') results.push(window.mtCard.render(args[0]));
    else if (fn === 'send') { await window.mtCard.send(args[0]); results.push(null); }
    else if (fn === 'rejectNext') { rejectSubmits = args[0]; results.push(null); }
    else if (fn === 'click') { els[args[0]].listeners.click({}); results.push(null); }
    else if (fn === 'submit') { els.f.listeners.submit({ preventDefault() {} }); results.push(null); }
    else if (fn === 'key') {
      const [key, target, mods] = args;
      const e = Object.assign({ key, target: target ? els[target] : body, prevented: false,
        preventDefault() { this.prevented = true; } }, mods || {});
      docListeners.keydown(e);
      results.push({ prevented: e.prevented, picked, focused: Object.keys(els).find(k => els[k] === focused) || null });
    }
    await tick(); await tick();
    results[results.length - 1] = { value: results[results.length - 1], status: els.status.textContent,
      statusClass: els.status.className, disabled: els.answer.disabled };
  }
  setTimeout(() => process.stdout.write(JSON.stringify({ html: els.root.innerHTML, requests, results })), 50);
})();
"""


def run_card_page(tmp_path: Path, page: str, ops: list[Any] | None = None, tree_reply: Any = None) -> dict[str, Any]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    scripts = re.findall(r"<script>(.*?)</script>", page, re.DOTALL)
    assert len(scripts) == 1
    raw = re.search(r'<script id="card" type="application/json">(.*?)</script>', page, re.DOTALL).group(1)
    title = htmllib.unescape(re.search(r"<title>(.*?)</title>", page).group(1))
    harness, script, data = tmp_path / "card-harness.js", tmp_path / "card.js", tmp_path / "card.json"
    harness.write_text(NODE_HARNESS)
    script.write_text(scripts[0])
    data.write_text(raw)
    r = subprocess.run(
        [node, str(harness), str(script), str(data), json.dumps(ops or []), json.dumps(tree_reply or {}), title],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def page_for(data: dict[str, Any], n: int = 1) -> str:
    return MT.card_page(MT.validate_card(data), n, "Persist sessions")


def test_page_builds_one_row_per_option_with_keys_and_recommended(tmp_path):
    out = run_card_page(tmp_path, page_for(card()))
    h = out["html"]
    assert h.count('class="opt"') == 3 and h.count('class="opt other"') == 1
    assert [m for m in re.findall(r'<span class="key">(\w)</span>', h)] == ["1", "2", "3", "4"]
    assert h.count("Recommended") == 1
    assert re.search(r'value="sliding-30d" checked>', h)
    assert '<span class="who">Brainstorm needs your call</span>' in h
    assert "question 1" in h and "Persist sessions" in h
    assert "<p>Sessions live in memory" in h
    assert "Decide for me" in h and "Esc asks in the terminal instead" in h


def test_page_without_other_and_with_four_options(tmp_path):
    h = run_card_page(tmp_path, page_for(card(allow_other=False)))["html"]
    assert 'class="opt other"' not in h and 'id="other"' not in h
    h = run_card_page(tmp_path, page_for(card(options=options(4))))["html"]
    assert re.findall(r'<span class="key">(\w)</span>', h) == [*"12345"]


def test_page_escapes_outside_text(tmp_path):
    data = card(
        title=HOSTILE,
        options=[{"id": 'a" onclick="x', "label": HOSTILE, "pro": "<b>p</b>", "con": "<i>c</i>", "recommended": True}],
    )
    h = run_card_page(tmp_path, MT.card_page(MT.validate_card(data), 1, "<img src=x onerror=1>"))["html"]
    assert "<script>" not in h and "<img" not in h and "<b>p</b>" not in h and "<i>c</i>" not in h
    assert "&lt;/script&gt;&lt;script&gt;alert(1)" in h
    assert 'value="a&quot; onclick=&quot;x"' in h


def test_page_answers_and_submit(tmp_path):
    ops = [
        ["answerFor", "fixed-24h", ""],
        ["answerFor", "other", "  mine  "],
        ["answerFor", "other", "   "],
        ["answerFor", None, ""],
        ["send", {"choice": "delegate"}],
    ]
    out = run_card_page(tmp_path, page_for(card()), ops)
    assert [r["value"] for r in out["results"][:4]] == [
        {"choice": "fixed-24h"}, {"choice": "other", "text": "mine"}, None, None
    ]  # fmt: skip
    submits = [r for r in out["requests"] if r[0] == "session.overlay.submit"]
    assert submits == [
        ["session.overlay.submit", {"args": {"value": json.dumps({"choice": "delegate"}).replace(" ", "")}}]
    ]


def test_page_fits_height_from_own_tree_entry(tmp_path):
    tree_reply = {
        "result": {
            "sessions": [
                {"htmlOverlays": [{"title": "make-task question 2", "zoom": 9}]},
                {"htmlOverlays": [{"title": "make-task question 3", "zoom": 2}, {"title": "dash", "pane": "right"}]},
            ]
        }
    }
    out = run_card_page(tmp_path, page_for(card(), n=3), tree_reply=tree_reply)
    assert ["session.overlay.resize", {"args": {"height": 204}}] in out["requests"]


def test_page_keeps_size_without_own_tree_entry(tmp_path):
    out = run_card_page(tmp_path, page_for(card(), n=5), tree_reply={"htmlOverlays": [{"title": "other"}]})
    assert [r for r in out["requests"] if r[0] == "session.overlay.resize"] == []


def submits(out: dict[str, Any]) -> list[Any]:
    return [json.loads(r[1]["args"]["value"]) for r in out["requests"] if r[0] == "session.overlay.submit"]


def test_page_number_key_then_enter_sends_that_option(tmp_path):
    out = run_card_page(tmp_path, page_for(card()), [["key", "2"], ["key", "Enter"]])
    assert out["results"][0]["value"] == {"prevented": True, "picked": 1, "focused": None}
    assert submits(out) == [{"choice": "fixed-24h"}]
    assert out["results"][1]["status"] == "Answer sent" and out["results"][1]["disabled"] is True


def test_page_free_text_row_key_focuses_the_field(tmp_path):
    out = run_card_page(tmp_path, page_for(card()), [["key", "4"], ["key", "5"]])
    assert out["results"][0]["value"]["picked"] == 3 and out["results"][0]["value"]["focused"] == "other"
    assert out["results"][1]["value"] == {"prevented": False, "picked": 3, "focused": "other"}, "no fifth row"


def test_page_delegate_key_sends_once(tmp_path):
    out = run_card_page(tmp_path, page_for(card()), [["key", "D"], ["key", "Enter"], ["click", "delegate"]])
    assert submits(out) == [{"choice": "delegate"}]


def test_page_keys_typed_into_the_free_text_field_are_text(tmp_path):
    ops = [["key", "d", "other"], ["key", "1", "other"], ["key", "Enter", "other"]]
    out = run_card_page(tmp_path, page_for(card()), ops)
    assert submits(out) == [] and all(r["value"]["prevented"] is False for r in out["results"])
    assert out["results"][-1]["value"]["picked"] == 0


@pytest.mark.parametrize("mods", [{"metaKey": True}, {"ctrlKey": True}, {"altKey": True}])
def test_page_ignores_modified_keys(tmp_path, mods):
    out = run_card_page(tmp_path, page_for(card()), [["key", "d", None, mods], ["key", "2", None, mods]])
    assert submits(out) == []
    assert [r["value"]["prevented"] for r in out["results"]] == [False, False]
    assert out["results"][-1]["value"]["picked"] == 0, "⌘2 does not move the selection"


def test_page_enter_on_a_focused_button_is_left_to_the_button(tmp_path):
    out = run_card_page(tmp_path, page_for(card()), [["key", "Enter", "delegate"], ["click", "delegate"]])
    assert out["results"][0]["value"]["prevented"] is False
    assert submits(out) == [{"choice": "delegate"}], "the button's own click, not the selected radio"


def test_page_failed_submit_can_be_sent_again(tmp_path):
    out = run_card_page(tmp_path, page_for(card()), [["rejectNext", 1], ["submit"], ["submit"]])
    first, second = out["results"][1], out["results"][2]
    assert first["status"] == "Could not send the answer; try again" and first["statusClass"] == "err"
    assert first["disabled"] is False
    assert second["status"] == "Answer sent" and second["disabled"] is True
    assert submits(out) == [{"choice": "sliding-30d"}, {"choice": "sliding-30d"}]
