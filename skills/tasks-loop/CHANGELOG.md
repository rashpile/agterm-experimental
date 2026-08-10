# Changelog — the migration log

**An entry exists only when a project must act.** New wording, better reasoning, a clearer
example: none of it belongs here. This file is `--update`'s work list, not a history of the
skill. A changelog that collects everything is one nobody maintains and `--update` cannot trust.

**Newest first.** `--update` applies every entry **above** the `skill-rev` recorded in a
project's config, in order, oldest of those first. No version arithmetic and no git required —
position in this file is the ordering.

**It is never the only check.** `--update` verifies the config against the current docs even
after applying every entry, because a forgotten entry would otherwise pass in silence. The
changelog says what is known to have changed; it is not evidence that nothing else did.

Entry shape:

```markdown
## <rev> — <what changed, in one line>
**Action:** what an existing project must do, concretely enough to do it.
**Why:** one or two sentences, so a reader can judge whether it applies to them.
```

---

## 30cc35d — the squash-verify recipe reported clean merges as lost commits

**Action:** if the config's `## 5. Push` (or its deploy/ship section) spells out its own branch
cleanup with `git diff <branch-tip> <merge-commit>`, replace that check with the workflow doc's
scoped form, or delete the sentence and let the workflow's own recipe apply.

**Why:** a whole-tree diff between the branch tip and the merge commit measures everything the
default branch did since the branch was cut, not whether the branch landed. Any project whose
default branch moves during a run — every batched one — gets a false "lost commit" that halts
cleanup on a perfectly good merge.
