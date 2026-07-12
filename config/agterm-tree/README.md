# agterm-tree — a file tree panel for agterm, built on the control API

A persistent file tree in the session's **right split pane**, and files opened into **agterm overlays**.
agterm itself knows nothing about files — this is nvim-tree plus `agtermctl`, wired together with a
shell script and some Lua.

| | |
|---|---|
| **Toggle** | `ctrl+b>z` (rebindable) |
| **Tree** | nvim-tree in the right split, under its own `NVIM_APPNAME=nvim-zed` |
| **Open a file** | 90% agterm overlay — nvim for code, an image viewer for images, glow/Typora for markdown |
| **Depends on** | agterm + `agtermctl`, neovim ≥ 0.9, `jq` |

Why a split pane rather than a TUI file manager in an overlay: the tree stays **visible next to the
agent session**. When a coding agent is working, the files it's touching are right there — the session
isn't a chat window that happens to touch a repo somewhere.

## What's here

| File | Goes to | Purpose |
|---|---|---|
| `open-nvim-zed.sh` | `~/.config/agterm-projects/` | The toggle. Opens/hides/reveals the split; self-heals orphaned trees. |
| `nvim-zed-tree-run.sh` | `~/.config/agterm-projects/` | Pane entrypoint. Launches the tree and owns the restart marker. |
| `open-nvim-zed-preview.sh` | `~/.config/agterm-projects/` | Opens one file in a 90% overlay (respawn-per-open = quick-look). |
| `nvim-zed/init.lua` | `~/.config/nvim-zed/` | The tree config. Role-aware (`NVIM_ZED_ROLE=tree`/`preview`). |
| `nvim-zed/lazy-lock.json` | `~/.config/nvim-zed/` | Plugin pins (known-good set). |
| `keymap.conf` | append to `~/.config/agterm/keymap.conf` | The `ctrl+b>z` binding. |
| `zshrc-snippet.sh` | append to the **end** of `~/.zshrc` | Brings the tree back after an agterm restart. |

All paths in the scripts are `$HOME`-relative — nothing to rewrite.

---

# Setup instructions (for an agent)

Read all of this before running anything. Steps 1–5 are the working tree; 6–7 are optional polish.

## 1. Check prerequisites

```sh
command -v agtermctl nvim jq        # all three must resolve
[ -n "$AGTERM_ENABLED" ] && echo "inside agterm"
```

`jq` is not optional: the toggle reads `agtermctl tree --json` to decide whether the split is visible.
Without it the script degrades to fixed sleeps and toggling gets unreliable. `brew install jq`.

## 2. Install the scripts and the nvim config

```sh
mkdir -p ~/.config/agterm-projects ~/.config/nvim-zed
cp open-nvim-zed.sh nvim-zed-tree-run.sh open-nvim-zed-preview.sh ~/.config/agterm-projects/
chmod +x ~/.config/agterm-projects/{open-nvim-zed,nvim-zed-tree-run,open-nvim-zed-preview}.sh
cp nvim-zed/init.lua nvim-zed/lazy-lock.json ~/.config/nvim-zed/
```

**If `~/.config/nvim-zed/init.lua` already exists, do not clobber it — back it up and ask the user.**
That config may be a whole personal IDE setup; merging is a judgment call, not a copy.

## 3. Bootstrap the plugins

```sh
NVIM_APPNAME=nvim-zed nvim --headless "+Lazy! sync" +qa
```

Clones lazy.nvim and the plugin set into `~/.local/share/nvim-zed/` — isolated from the user's normal
nvim config, which is the entire point of the separate `NVIM_APPNAME`.

## 4. Bind the key

Append `keymap.conf` to `~/.config/agterm/keymap.conf`, then:

```sh
agtermctl keymap reload      # prints the parse-diagnostic count; expect 0
```

If `ctrl+b>z` is already taken on this machine, pick another chord — don't silently overwrite.

## 5. Install the restart hook

Append `zshrc-snippet.sh` to the **end** of `~/.zshrc` (or `~/.config/zsh/.zshrc` if the user uses
`ZDOTDIR`). It must be last: it `exec`s, so anything after it never runs.

Without this the tree still works — it just won't come back by itself after an agterm restart, because
agterm restores the split pane but not the program inside it.

## 6. Verify

```sh
# tree config loads and the tree role is recognised
NVIM_APPNAME=nvim-zed NVIM_ZED_ROLE=tree nvim --headless \
  -c 'lua print("colorscheme: " .. (vim.g.colors_name or "?"))' -c qa
```

Then, in a real agterm session, press `ctrl+b>z`. Expected:

1. A right split opens at ~25% width with the file tree rooted at the session's cwd.
2. Clicking a file opens it in a 90% overlay; closing the overlay returns to the tree.
3. Pressing `ctrl+b>z` again hides the split; pressing it once more reveals the **same** tree instantly
   (no relaunch flash) — that's the toggle keeping nvim alive behind the hidden pane.

If the split opens but stays at a shell prompt, the `session type` into the fresh pane raced the shell
coming up — check that `nvim-zed-tree-run.sh` is executable and on the path it's typed with.

## 7. Optional: match the tree to the terminal theme

Out of the box the tree renders on the `dawnfox` colorscheme with a **transparent background**, so the
pane shows the terminal's own background and there's no seam between the panes.

This is tuned for agterm's **Dawnfox** theme, which is the ghostty port of nightfox.nvim's `dawnfox` —
the same palette on both sides, by name, not hand-matched hexes.

```sh
agtermctl theme list | grep '\*'      # what's actually set
```

- `* Dawnfox` → nothing to do.
- A different **nightfox** variant (nightfox / dayfox / duskfox / nordfox / terafox / carbonfox) →
  change `colorscheme dawnfox` and both `palette.load("dawnfox")` calls in `init.lua` to that name.
- Anything else → the tree stays transparent and readable, but accent colors (root folder, git status)
  won't match the terminal. Leave it, or ask the user before changing their terminal theme.

One deliberate choice to preserve if you touch the colors: git-dirty uses **yellow** (`pal.yellow.base`),
not orange. Dawnfox's orange is a rose sitting right next to its red, which would make *modified* and
*deleted* files nearly indistinguishable.

## Optional dependencies

The tree's extra open-actions shell out to scripts that are **not bundled** (they're part of a larger
personal IDE setup). Absent them, the key just fails — the tree itself is unaffected. Either copy them
from the source machine or delete the corresponding keymaps in `init.lua`:

| Key (in the tree) | Needs | If missing |
|---|---|---|
| `o` | `ide-persist.sh` — persistent tmux-backed nvim | Use `<CR>`/`l` (overlay) instead |
| `gp` | Typora (`/usr/bin/open -a Typora`) | Remove the `gp` map, or point it at another app |
| `gl` | `glow` | `brew install glow`, or remove the `gl` map |
| image files | `img-view.sh` — kitty-graphics viewer | Images open in nvim as binary garbage; guard or remove the image branch in `open-nvim-zed-preview.sh` |
| diff/compare | `ide-compare.sh`, `ide-revdiff-file.sh` | Remove those maps |

## How it fits together

```
ctrl+b>z ──> open-nvim-zed.sh          (toggle: split on/off, reveal, self-heal)
                  │
                  │ agtermctl session split on + resize 0.75
                  │ agtermctl session type  ──> "exec nvim-zed-tree-run.sh <cwd>"
                  ▼
            nvim-zed-tree-run.sh        (writes state/tree-<session-id> marker)
                  │
                  │ env NVIM_APPNAME=nvim-zed NVIM_ZED_ROLE=tree nvim
                  ▼
            nvim-tree in the right pane
                  │
                  │ click a file ──> open-nvim-zed-preview.sh
                  ▼
            agtermctl session overlay open  (90%, replaces the previous overlay)
```

The marker file is what makes the tree survive an agterm restart: the zshrc hook sees it in a restored
right-pane shell and re-execs the tree. Quitting the tree deletes it.

Two things in `open-nvim-zed.sh` that look over-engineered but aren't:

- **It identifies its tree by process environment** (`AGTERM_SESSION_ID` + `AGTERM_PANE=right` +
  `NVIM_ZED_ROLE=tree`), not by the split flag — because the right pane is shared with other tools
  (e.g. a tmux IDE split), and agterm's tree JSON reports `split` as *visibility*, with no
  `splitForeground` in this build. This is what makes `z` leave a non-tree split alone.
- **It focuses the main pane before hiding.** `split off` maximizes the *focused* pane, so hiding while
  the tree has focus would leave the tree full-screen instead of hidden. Focus is async, so it waits for
  `splitFocused == false` before hiding.
