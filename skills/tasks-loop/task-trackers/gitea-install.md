# Gitea tracker — install

Read this from `references/install.md`, or from `--update` for its checks only. A run never
reads it.

1. **`tea`** — required, and logged in to this repo's host. Verify both: `tea login list` shows
   the host, and `tea issues list --limit 1` works from inside the repo.

2. **Default labels** — this tracker's whole vocabulary. Read what the repo already has
   (`tea labels list --output json`), show the user the table below with the missing ones
   marked, and **offer to create just those**. Never create silently, and never modify a label
   that already exists — its description may be the project's own.

   | Label | Color | Description |
   |---|---|---|
   | `implement` | `1d76db` | Build it — agents may work this |
   | `review` | `5319e7` | Review owed on the delivery |
   | `discuss` | `fbca04` | Talk it through before building |
   | `investigate` | `006b75` | Research only, no code |
   | `bug` | `d73a4a` | Defect — pair with `implement` to have it worked |
   | `done` | `0e8a16` | Approved to ship |
   | `pending` | `bfbfbf` | Frozen — agents skip it |
   | `progress` | `d93f0b` | An agent is on it now |
   | `priority/high` | `b60205` | Take this first |
   | `priority/med` | `e58f2a` | Ordinary priority, stated explicitly |
   | `priority/low` | `c2e0c6` | Whenever there is room |
   | `quick` | `c5def5` | Small task — start at Execute, one review leg |

   ```bash
   tea labels create --name implement --color 1d76db --description "Build it — agents may work this"
   ```

   Descriptions are **human hints only** — the semantics are `gitea.md`'s State table, which is
   why an existing label's wording is left alone.

   A project that uses different names keeps them: point `task-tracker` at a copy of `gitea.md`
   with the State table renamed, and note the mapping in the config's `# Notes`.
