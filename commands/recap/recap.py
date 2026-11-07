#!/usr/bin/env python3
"""Recap the last turns of a Claude Code session with a local Ollama model.

Reads the session's transcript, builds a compact digest of its last turns (user messages,
answers the user gave to the agent's questions, assistant text, one line per tool call),
sends it to Ollama and streams the summary to stdout as it is written.

Usage:
    recap.py <transcript.jsonl> [--model gemma4:e4b] [--turns 6] [--out recap.md] [--title NAME]
    recap.py <transcript.jsonl> --digest      # print the digest only, no model call

Env: RECAP_MODEL (default gemma4:e4b), OLLAMA_HOST (default http://127.0.0.1:11434).
Runs on the system python3 (3.9): standard library only.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

DEFAULT_MODEL = os.environ.get("RECAP_MODEL", "gemma4:e4b")
OLLAMA = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
if not OLLAMA.startswith("http"):
    OLLAMA = "http://" + OLLAMA

TEXT_CAP = 600         # an assistant text block mid-turn
FINAL_CAP = 4000       # the last assistant text of a turn: that is where the state is reported
USER_CAP = 1500
MAX_CHARS = 60000      # the digest keeps its newest lines within this

SYSTEM = (
    "Ты помогаешь разработчику вспомнить, что происходило в сессии агента Claude Code. "
    "Отвечай по-русски. Опирайся только на журнал, ничего не выдумывай: "
    "если чего-то нет в журнале, не пиши об этом."
)
PROMPT = (
    "Ниже журнал последних ходов сессии: сообщения пользователя (### USER), ответы пользователя "
    "на вопросы агента (USER ANSWER), текст ассистента (ASSISTANT), вызовы инструментов (- tool) и "
    "отчёты субагентов. Составь сводку состояния сессии, не больше 250 слов, разделы:\n"
    "1. Что сделано (по пунктам, с именами коммитов, веток и файлов, если они есть в журнале).\n"
    "2. Решения пользователя.\n"
    "3. Текущее состояние: что запущено, что изменено, что влито, что не запушено.\n"
    "4. Что осталось сделать или проверить.\n\n"
    "ЖУРНАЛ:\n"
)


def die(msg):
    print(f"recap: {msg}", file=sys.stderr)
    raise SystemExit(1)


def text_of(content):
    if isinstance(content, str):
        return content
    return " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")


def is_real_user(e):
    """A prompt the user typed: not a tool result, not a meta or local-command echo."""
    if e.get("type") != "user" or e.get("isMeta"):
        return False
    c = e.get("message", {}).get("content")
    if isinstance(c, list) and not any(isinstance(b, dict) and b.get("type") == "text" for b in c):
        return False
    t = text_of(c).lstrip()
    return bool(t) and not t.startswith(("<local-command", "<command-name>/remote-control"))


def clean_user(t):
    t = re.sub(r"(?s)<command-message>.*?</command-message>|<command-args></command-args>", "", t)
    return re.sub(r"(?s)Base directory for this skill.*", "", t).strip()


def build_digest(rows, turns):
    starts = [i for i, e in enumerate(rows) if is_real_user(e)]
    start = starts[-turns] if len(starts) >= turns else 0
    items = []   # (kind, text)
    asks = set()
    for e in rows[start:]:
        t = e.get("type")
        ts = (e.get("timestamp") or "")[11:16]
        msg = e.get("message") or {}
        if t == "user":
            c = msg.get("content")
            if isinstance(c, list):
                for b in c:
                    if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") in asks:
                        r = b.get("content")
                        r = r if isinstance(r, str) else text_of(r or [])
                        items.append(("line", f"  - USER ANSWER: {r.strip()[-400:]}"))
            if is_real_user(e):
                items.append(("user", f"\n### USER {ts} UTC\n{clean_user(text_of(c))[:USER_CAP]}"))
        elif t == "attachment":
            a = e.get("attachment") or {}
            if a.get("type") == "queued_command":
                if (a.get("origin") or {}).get("kind") == "human":
                    items.append(("user", f"\n### USER (sent mid-turn) {ts} UTC\n{a.get('prompt', '')[:USER_CAP]}"))
                else:
                    items.append(("line", f"  - subagent report arrived: {a.get('prompt', '')[:300]}"))
        elif t == "assistant":
            for b in msg.get("content") or []:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and b.get("text", "").strip():
                    items.append(("text", b["text"].strip()))
                elif b.get("type") == "tool_use":
                    i = b.get("input") or {}
                    d = i.get("description") or (i.get("command") or "")[:80] or i.get("file_path") or i.get("query") or ""
                    items.append(("line", f"  - tool {b.get('name')}: {str(d)[:120]}"))
                    cmd = i.get("command") or ""
                    if b.get("name") == "AskUserQuestion" or (b.get("name") == "Bash" and re.search(r"\bmt\b.*\bask\b", cmd)):
                        asks.add(b.get("id"))
    # The last assistant text before each user message, and at the end, is the turn's report:
    # that is where the state is written down, so it is kept nearly whole.
    final, last = set(), None
    for idx, (kind, _) in enumerate(items):
        if kind == "text":
            last = idx
        elif kind == "user" and last is not None:
            final.add(last)
            last = None
    if last is not None:
        final.add(last)
    out = []
    for idx, (kind, text) in enumerate(items):
        if kind == "text":
            out.append("ASSISTANT: " + cap(text, FINAL_CAP if idx in final else TEXT_CAP))
        else:
            out.append(text)
    digest = "\n".join(out)
    if len(digest) > MAX_CHARS:
        digest = "(начало журнала обрезано)\n" + digest[-MAX_CHARS:]
    return digest


def cap(text, limit):
    return text if len(text) <= limit else text[:limit] + " …"


def num_ctx_for(digest):
    need = len(digest) // 3 + 2500
    for n in (8192, 16384, 32768, 65536):
        if need <= n:
            return n
    return 65536


def stream_chat(model, digest, sink):
    body = {
        "model": model, "stream": True, "keep_alive": "5m",
        "options": {"num_ctx": num_ctx_for(digest), "temperature": 0.2},
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": PROMPT + digest}],
    }
    req = urllib.request.Request(f"{OLLAMA}/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req, timeout=900)
    except urllib.error.HTTPError as exc:
        die(f"ollama answered {exc.code}: {exc.read().decode(errors='replace')[:300]}")
    except (urllib.error.URLError, OSError) as exc:
        die(f"ollama is not reachable at {OLLAMA} ({exc}); start it with: ollama serve")
    parts, stats = [], {}
    for raw in resp:
        if not raw.strip():
            continue
        chunk = json.loads(raw)
        if chunk.get("error"):
            die(f"ollama: {chunk['error']}")
        piece = (chunk.get("message") or {}).get("content", "")
        if piece:
            parts.append(piece)
            sink.write(piece)
            sink.flush()
        if chunk.get("done"):
            stats = chunk
    return "".join(parts), stats


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("transcript")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--turns", type=int, default=6, help="user turns to include (default 6)")
    ap.add_argument("--out", help="write the recap as markdown here")
    ap.add_argument("--title", default="", help="session name for the heading")
    ap.add_argument("--digest", action="store_true", help="print the digest and stop")
    a = ap.parse_args()

    try:
        rows = [json.loads(l) for l in open(a.transcript) if l.strip()]
    except OSError as exc:
        die(f"cannot read the transcript: {exc}")
    digest = build_digest(rows, max(a.turns, 1))
    if not digest.strip():
        die("the transcript has no turns yet")
    if a.digest:
        print(digest)
        return

    t0 = time.time()
    text, stats = stream_chat(a.model, digest, sys.stdout)
    print()
    took = time.time() - t0
    meta = f"{a.model} · {time.strftime('%Y-%m-%d %H:%M')} · последних ходов: {a.turns} · {took:.0f} с"
    if stats.get("eval_count"):
        meta += f" · {stats.get('prompt_eval_count', '?')}→{stats['eval_count']} ток."
    if a.out:
        head = f"# Recap{': ' + a.title if a.title else ''}\n\n_{meta}_\n\n"
        with open(a.out, "w") as f:
            f.write(head + text.strip() + "\n")
    print(f"\n{meta}", file=sys.stderr)


if __name__ == "__main__":
    main()
