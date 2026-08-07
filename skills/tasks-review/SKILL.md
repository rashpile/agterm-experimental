---
name: tasks-review
description: Use when reviewing a pull request as a tasks-loop review worker, or when asked to "tasks-review" a PR / re-review disputes on a PR thread. Read-only code review that reports into the PR comment thread.
---

# Tasks Review

## Overview

Read-only, multi-agent code review of a PR, reporting a single structured comment into the
PR thread. Part of the `tasks-loop` family: the reviewer NEVER fixes — a separate session
(the PR's author) evaluates findings, fixes confirmed ones, and disputes false positives.
Disputes are debated in the PR thread until resolved.

**Hard rule: no fixes, no commits, no pushes — ever.** The moment you edit code you stop
being an independent reviewer.

Run `tea` from inside the repo/worktree — it resolves the Gitea repo from the `origin`
remote; `--repo <owner>/<repo>` only when outside.

## Modes

- **First review** — full multi-agent review of the PR diff (default).
- **Re-review** — a fix/dispute round happened: answer disputes, review new commits only,
  and when clean, run the completion gate.

## First review

1. **Context**: you are in a worktree on the PR branch. Diff: `git diff <base>...HEAD`
   written to a temp file (pass the *path* to agents, never inline the diff).
2. **Resolve agents** — shared with the external-code-review skill (project > user > built-in):
   - `./.claude/external-code-review/agents/*.txt`
   - `~/.claude/external-code-review/agents/*.txt`
   - the external-code-review skill's bundled `agents/` directory (if installed)
   First level with any `.txt` wins. Filename = agent name, content = agent prompt.
   None found → run three inline perspectives instead: correctness/security, goal-coverage,
   test-quality.
3. **Launch all agents in parallel** (Task tool, single message): prompt = agent prompt +
   "Code changes to review are in: `<diff_path>`. Read that file first, then analyze."
4. **Verify every finding yourself** before reporting: Read the code at file:line, trace
   callers. False positives die here, not on the PR. Judge severity honestly.
5. **Post ONE structured comment** on the PR:

   ```
   ## Code review — <scope>
   **Verdict: BLOCKING** | **Verdict: NOTHING BLOCKING**

   ### Blocking
   - <file:line> — what breaks, why, concrete failure scenario
   ### Major / ### Minor
   - ...
   ```

   `tea comment <pr-idx> "<comment>"` — verify it landed (`tea pull <pr-idx> --comments`).
6. Final message: `DONE <verdict> — <one-line summary>`.

## Re-review (after a fix/dispute round)

1. Read the full PR thread (`tea pull <pr-idx> --comments`): the author has pushed fixes
   and/or posted dispute replies.
2. **Answer every dispute in-thread**, one of:
   - `WITHDRAW` — the author is right; say why in one line.
   - `MAINTAIN` — provide NEW evidence (code you can cite), not repetition.
   - `COMPROMISE` — narrower real issue; state the reduced scope.
3. Review only the new commits (`git diff <last-reviewed-sha>...HEAD`) for regressions
   and for whether each CONFIRMED finding is actually fixed.
4. Post the round comment (same structure, plus the dispute answers), verdict again.
5. **Completion gate** (only when verdict is NOTHING BLOCKING): confirm every finding in
   the thread is terminal — fixed, withdrawn, or dismissed-with-rationale — and check the
   branch CI status on the PR. Report gate result in the final message:
   `DONE NOTHING-BLOCKING gate=pass|fail(<reason>)|ci-unverified`.

## Judging findings — be the skeptic

Agents overcall. For each candidate finding ask: does the failure scenario actually
execute? Is it reachable with real inputs? Is it a preference dressed as a bug? Report
only what you'd stake the verdict on. One strong blocking finding beats ten maybes.
This cuts both ways in re-review: when the author's evidence beats yours, WITHDRAW
plainly — a reviewer that never withdraws is a noise source, not a check.

## Common mistakes

| Mistake | Fix |
|---|---|
| Inlining a large diff into agent prompts | Write diff to temp file, pass the path. |
| Posting agent output raw | Verify each finding against the code first. |
| MAINTAIN by repetition | New evidence or withdraw — repetition burns a round. |
| Fixing "just this small thing" | Never. Report it; the author session fixes. |
| Verdict buried in prose | Verdict is the first line of the comment. |
