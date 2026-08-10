#!/usr/bin/env python3
"""Attribute a tasks-loop orchestrator run from its transcript.

    inspect-run.py                  # newest session for the cwd's project
    inspect-run.py <session-id>     # that session, searched across all projects
    inspect-run.py <path.jsonl>     # that file
    inspect-run.py <project-dir>    # newest session for that working directory

Safe to run against a LIVE run — the transcript is append-only. A snapshot of a running
session looks identical to a stalled one, so check the file's mtime before reading anything
into a gap.

Three things this exists to get right, because reading the JSON naively gets them wrong:
  * thinking text is stripped (signature only), so it is derived, never read;
  * tool_use inputs are not all {command,file_path} — AskUserQuestion has neither;
  * the LABELS on backgrounded waits are the diagnosis, so they are printed verbatim.
"""

import collections
import json
import os
import re
import sys

PROJECTS = os.path.expanduser("~/.claude/projects")


def slug(path):
    return re.sub(r"[^a-zA-Z0-9]", "-", os.path.abspath(path))


def newest(d):
    files = [os.path.join(d, f) for f in os.listdir(d) if f.endswith(".jsonl")]
    return max(files, key=os.path.getmtime) if files else None


def resolve(arg):
    if arg is None:
        return newest(os.path.join(PROJECTS, slug(os.getcwd())))
    if arg.endswith(".jsonl") and os.path.exists(arg):
        return arg
    if os.path.isdir(arg):
        return newest(os.path.join(PROJECTS, slug(arg)))
    hit = f"{arg.lower()}.jsonl"
    for root, _, files in os.walk(PROJECTS):
        if hit in files:
            return os.path.join(root, hit)
    return None


def category(name, inp):
    """Intent, not tool name — what the orchestrator was trying to do."""
    if name == "Read":
        fp = str(inp.get("file_path", ""))
        if "/skills/tasks-loop/" in fp:
            return "skill-doc"
        if "scratchpad" in fp or "/tasks/" in fp:
            return "worker-output"
        return "project-file"
    if name != "Bash":
        return name
    cmd = inp.get("command", "")
    # The verb is the token right after the executable — matching it anywhere in the command
    # puts every `cd .../agent-driver && git …` in the driver bucket.
    verbs = re.findall(r"(?:claude-agterm|\$D|\$DRIVER)\s+(\w+)", cmd)
    if verbs:
        for group, label in (
            ({"spawn"}, "driver-spawn"),
            ({"wait", "status"}, "driver-wait"),
            ({"read", "last"}, "driver-read"),
            ({"say", "answer", "interrupt", "close"}, "driver-say"),
            ({"skill", "help", "usage", "list"}, "driver-meta"),
        ):
            if group & set(verbs):
                return label
        return "driver-other"
    if re.match(r"^\s*tea\b", cmd) or re.search(r"\btea\b", cmd[:40]):
        return "tracker"
    if "scratchpad" in cmd or "/tasks/" in cmd:
        return "worker-output"
    if re.search(r"\bgit\b", cmd[:80]):
        return "git"
    if re.match(r"^\s*(sed|grep|awk|head|cat|tail|rg|ls|wc|find)\b", cmd.strip()):
        return "repo-inspect"
    return "other-bash"


def main():
    path = resolve(sys.argv[1] if len(sys.argv) > 1 else None)
    if not path:
        sys.exit("no transcript found — pass a session id, a .jsonl, or a project directory")

    uses, results, order = {}, {}, []
    users, notifications, waits = [], 0, []
    out_tokens = visible = 0
    msgs = 0
    ctx_first = ctx_last = None
    models = set()

    for line in open(path):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        m = d.get("message") or {}
        content = m.get("content")
        ts = d.get("timestamp", "")

        if d.get("type") == "user":
            text = content if isinstance(content, str) else "".join(
                b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
            ) if isinstance(content, list) else ""
            stripped = text.strip()
            if "<task-notification>" in text:
                notifications += 1
            elif stripped and not stripped.startswith(("<system-reminder>", "<local-command",
                                                       "<command-name>", "<command-message>",
                                                       "Base directory for this skill:")):
                users.append((ts[11:19], " ".join(text.split())[:150]))

        if d.get("type") == "assistant":
            msgs += 1
            if m.get("model"):
                models.add(m["model"])
            u = m.get("usage") or {}
            ctx = u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + \
                u.get("cache_creation_input_tokens", 0)
            if ctx:
                ctx_first = ctx_first or (ts[11:19], ctx)
                ctx_last = (ts[11:19], ctx)
            out_tokens += u.get("output_tokens", 0)

        if not isinstance(content, list):
            continue
        for b in content:
            if b.get("type") == "tool_use":
                inp = b.get("input", {})
                uses[b["id"]] = (b["name"], inp, ts)
                order.append(b["id"])
                if d.get("type") == "assistant":
                    visible += len(json.dumps(inp))
                if b["name"] == "Bash" and inp.get("run_in_background"):
                    waits.append((ts[11:19], inp.get("description", "")))
            elif b.get("type") == "tool_result":
                t = b.get("content")
                if isinstance(t, list):
                    t = "".join(x.get("text", "") for x in t if isinstance(x, dict))
                results[b.get("tool_use_id")] = len(t or "")
            elif b.get("type") == "text" and d.get("type") == "assistant":
                visible += len(b.get("text", ""))

    print(f"transcript  {path}")
    print(f"model       {', '.join(sorted(models)) or '?'}")
    print(f"messages    {msgs} assistant · {len(users)} user turns · {notifications} wakeups")
    if ctx_first and ctx_last:
        print(f"context     {ctx_first[1]:,} at {ctx_first[0]}  ->  {ctx_last[1]:,} at {ctx_last[0]}"
              f"   (+{ctx_last[1] - ctx_first[1]:,})")
    think = out_tokens - visible // 4
    print(f"output      {out_tokens:,} tokens · ~{visible // 4:,} visible · ~{think:,} thinking"
          f" ({100 * think // max(out_tokens, 1)}%, derived — the text is stripped)")

    print("\n== where the context went ==")
    chars, count = collections.Counter(), collections.Counter()
    for tid in order:
        name, inp, _ = uses[tid]
        k = category(name, inp)
        chars[k] += results.get(tid, 0)
        count[k] += 1
    for k, v in chars.most_common():
        print(f"  {v:>9,} chars  {count[k]:>4} calls  {k}")
    print(f"  {sum(chars.values()):>9,} chars  {sum(count.values()):>4} calls  TOTAL in")

    print("\n== biggest single results ==")
    for tid, n in sorted(results.items(), key=lambda x: -x[1])[:10]:
        name, inp, _ = uses.get(tid, ("?", {}, ""))
        what = inp.get("command") or inp.get("file_path") or json.dumps(inp)
        print(f"  {n:>7,}  {name:<6} {' '.join(str(what).split())[:96]}")

    if waits:
        print("\n== backgrounded waits, in order (the labels are the diagnosis) ==")
        for ts, desc in waits:
            print(f"  {ts}  {desc}")

    if users:
        print("\n== user turns (every one is an interruption the loop did not handle) ==")
        for ts, text in users:
            print(f"  {ts}  {text}")


if __name__ == "__main__":
    main()
