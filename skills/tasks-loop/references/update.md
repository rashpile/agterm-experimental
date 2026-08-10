# Update — verify, reconcile, record

Read this when the run carries `--update`. It **dispatches nothing**: it checks the project
against the current docs, offers what it would change, and writes only what the user accepts.

**Refuse to run mid-flight.** Any task the tracker reports as in flight means live workers whose
prompts were built from the config as it stands. Rewriting it underneath them is how a run stops
making sense halfway. Report what is in flight and stop.

## 1. Verify

Report each, pass or fail — this half never changes anything:

| Check | Fails when |
|---|---|
| every frontmatter name or path resolves | a bundled name that no longer exists, a project path that was deleted |
| the tracker and workflow docs read | ditto, plus the sub-docs they name |
| `<agent-driver-cmd> skill` exits 0 | the driver moved, or was never installed |
| `# Checks` exists and is non-empty | nothing gates a merge |
| no empty phase heading that must not be empty | an empty `## <n>. Deploy` reads as configured |

## 2. Apply the migration log

Read `MIGRATIONLOG.md` and take every entry **above** the config's `skill-rev`, oldest of those
first. Each entry says what to do. **No `skill-rev` recorded → skip this step entirely**: an
unknown baseline means there is no way to know which entries were already applied, and applying
them all would undo work someone did by hand. Step 3 covers that case.

## 3. Verify the config against the current workflow

**This runs whether or not step 2 did**, because a forgotten entry passes in silence
and "the log said nothing" is not evidence that nothing changed.

- A `## <n>. <Phase>` heading the workflow's table no longer has → say what replaced it.
- A phase in the table with no heading → that phase is off. Correct if intended; say it anyway,
  because absence is the off switch and a phase lost to a rename looks identical.
- Numbering that does not match the table → the workflow renumbered. Show the mapping.
- Keys the tracker or workflow doc requires that the frontmatter lacks.

## 4. Reconcile the tracker

Run the tracker doc's `## Install` section — **its checks only, never its questions** — and
follow it to a sibling file where it names one. For Gitea that is `gitea-install.md`: read the
repo's labels and name the default ones it lacks. **Offer; never
create silently**, and never touch a label that exists: its wording and colour are the project's.

## 5. The driver

- **A git checkout** → report how far behind `origin` it is and offer to pull. Say what the pull
  would bring, and that picking up a driver change is deliberate.
- **A symlink into a working tree** → say so and skip. Updating it is the user's own edit, and
  the driver is the loop's only channel to every live worker.
- Either way, re-run `<agent-driver-cmd> skill` afterwards and report any verb or option the
  config depends on that is still missing — a configured `model:` against a driver with no model
  option is inert, and the user should hear it here rather than discover it at the next dispatch.

## 6. The skill itself

Not yet. The skill is not distributed, so there is nothing to pull. Report the recorded
`skill-rev` against the current one and stop.

## 7. Record

Show the full diff of what would change **before writing anything**, and write only what the
user accepts. Then set, in the config frontmatter:

```yaml
skill-rev: <the topmost MIGRATIONLOG entry's rev, or the skill's current rev if there are none>
driver-rev: <the driver's rev, omitted when it is a working-tree symlink>
```

A rev is a **fact about what this config was reconciled against**, not a version. There is no
compatibility matrix behind it: `--update` can say what differs now, never what changed between
two revs beyond what the migration log spells out. Do not write "upgrading from X to Y" language the
data cannot support.
