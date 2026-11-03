#!/usr/bin/env python3
"""Stand-in for agtermctl in tests. Never talks to the real terminal.

Each call appends {"argv": [...]} as one JSON line to $FAKE_AGTERMCTL_LOG.

Replies come from the JSON file $FAKE_AGTERMCTL_REPLIES, keyed by the leading
non-option words of the call ("tree", "session status", "session overlay open"):
    {"session overlay open": {"stdout": "...", "exit": 0, "sleep": 0}}
The longest matching key wins; no match prints nothing and exits 0.

Switches:
    FAKE_AGTERMCTL_FAIL=1          every call exits 1
    FAKE_AGTERMCTL_FAIL=<key>      calls whose key starts with <key> exit 1
    FAKE_AGTERMCTL_SLEEP=<secs>    sleep before replying (every call)
"""

from __future__ import annotations

import json
import os
import sys
import time


def call_key(argv: list[str]) -> str:
    words: list[str] = []
    for a in argv:
        if a.startswith("-"):
            break
        words.append(a)
    return " ".join(words)


def main() -> int:
    argv = sys.argv[1:]
    log = os.environ.get("FAKE_AGTERMCTL_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps({"argv": argv}) + "\n")

    key = call_key(argv)
    sleep = float(os.environ.get("FAKE_AGTERMCTL_SLEEP") or 0)
    fail = os.environ.get("FAKE_AGTERMCTL_FAIL")

    reply: dict = {}
    path = os.environ.get("FAKE_AGTERMCTL_REPLIES")
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            replies = json.load(f)
        matches = [k for k in replies if key == k or key.startswith(k + " ")]
        if matches:
            reply = replies[max(matches, key=len)]

    sleep = float(reply.get("sleep", sleep))
    if sleep:
        time.sleep(sleep)
    if fail and (fail == "1" or key == fail or key.startswith(fail + " ")):
        print(f"fake agtermctl: forced failure for {key!r}", file=sys.stderr)
        return 1
    if reply.get("stdout"):
        sys.stdout.write(reply["stdout"])
    if reply.get("stderr"):
        sys.stderr.write(reply["stderr"])
    return int(reply.get("exit", 0))


if __name__ == "__main__":
    sys.exit(main())
