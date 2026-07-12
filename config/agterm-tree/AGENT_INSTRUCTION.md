# Setup instructions — for an agent

You are installing **agterm-tree** (see [README.md](README.md) for what it is) onto the user's machine.

Read this whole file before running anything. Work through the steps in order. **Stop and ask the user**
at each ⚠️ — those are the points where a wrong guess overwrites something they care about.

## 0. What you are installing

| File | Goes to | Purpose |
|---|---|---|
| `open-nvim-zed.sh` | `~/.config/agterm-projects/` | The toggle. Opens/hides/reveals the split; self-heals orphaned trees. |
| `nvim-zed-tree-run.sh` | `~/.config/agterm-projects/` | Pane entrypoint. Launches the tree; owns the restart marker. |
| `open-nvim-zed-preview.sh` | `~/.config/agterm-projects/` | Opens one file in a 90% overlay (respawn-per-open = quick-look). |
| `tree-menu.yaml` | `~/.config/agterm-projects/` | The context menu. User-editable; no restart. |
| `nvim-zed/init.lua` | `~/.config/nvim-zed/` | The tree config. Role-aware (`NVIM_ZED_ROLE=tree`/`preview`). |
| `nvim-zed/lua/tree_menu.lua` | `~/.config/nvim-zed/lua/` | Reads the yaml, filters by node, runs the entry. |
| `nvim-zed/lazy-lock.json` | `~/.config/nvim-zed/` | Plugin pins (known-good set). |
| `keymap.conf` | **append** to `~/.config/agterm/keymap.conf` | The `ctrl+b>z` binding. |
| `zshrc-snippet.sh` | **append** to the end of `~/.zshrc` | Brings the tree back after an agterm restart. |

All paths are `$HOME`-relative. Nothing to rewrite.

## 1. Prerequisites

```sh
command -v agtermctl nvim jq ruby   # all four must resolve
[ -n "$AGTERM_ENABLED" ] && echo "inside agterm"
```

- **`jq`** is not optional — the toggle reads `agtermctl tree --json` to decide whether the split is
  visible. Without it, toggling gets unreliable. `brew install jq`.
- **`ruby`** parses the menu yaml (`ruby -ryaml -rjson`). psych and json are stdlib, so **macOS system
  ruby is enough — install nothing.** It was chosen for exactly that reason: nvim has no yaml parser,
  and neither `yq` nor PyYAML is a safe assumption. It runs once per yaml edit (cached against mtime),
  not once per menu open.

## 2. ⚠️ Ask the user before you touch these

Ask all of these **up front**, in one go — don't discover them halfway through:

1. **Does `~/.config/nvim-zed/` already exist?** If so, **do not overwrite it.** It may be an entire
   personal IDE config. Back it up and ask whether to merge or replace. Merging is a judgment call, not
   a copy.
2. **Is `ctrl+b>z` already bound** in `~/.config/agterm/keymap.conf`? If so, ask for a different chord.
   Never silently rebind.
3. **What is their agterm theme?** `agtermctl theme list | grep '\*'`. See step 7 — the colors are keyed
   to it. Do not change their terminal theme without asking.
4. **Which optional tools do they have?** `command -v revdiff glow` and whether Typora is installed. The
   shipped `tree-menu.yaml` has entries calling these. Delete the entries for tools they don't have —
   each is self-contained, so removing one breaks nothing else.

## 3. Install the files

```sh
mkdir -p ~/.config/agterm-projects ~/.config/nvim-zed/lua
cp open-nvim-zed.sh nvim-zed-tree-run.sh open-nvim-zed-preview.sh tree-menu.yaml ~/.config/agterm-projects/
chmod +x ~/.config/agterm-projects/{open-nvim-zed,nvim-zed-tree-run,open-nvim-zed-preview}.sh
cp nvim-zed/init.lua nvim-zed/lazy-lock.json ~/.config/nvim-zed/
cp nvim-zed/lua/tree_menu.lua ~/.config/nvim-zed/lua/
```

## 4. Bootstrap the plugins

```sh
NVIM_APPNAME=nvim-zed nvim --headless "+Lazy! sync" +qa
```

Clones lazy.nvim and the plugin set into `~/.local/share/nvim-zed/` — isolated from the user's normal
nvim config. That isolation is the whole point of the separate `NVIM_APPNAME`.

## 5. Bind the key

Append `keymap.conf` to `~/.config/agterm/keymap.conf` (or the chord the user chose in step 2), then:

```sh
agtermctl keymap reload      # prints the parse-diagnostic count; expect 0
```

## 6. Install the restart hook

Append `zshrc-snippet.sh` to the **end** of `~/.zshrc` (or `~/.config/zsh/.zshrc` if they use `ZDOTDIR`).
It must be last: it `exec`s, so anything after it never runs.

Why it exists: agterm restores a split *pane* on restart, but not the *program* inside it. The tree
therefore re-launches itself — while running it keeps a marker file naming its session, and a restored
right-pane shell finds that marker and re-execs the tree. Without the hook the tree still works; it just
won't come back by itself after a restart.

## 7. Theme

Out of the box the tree uses the `dawnfox` colorscheme with a **transparent background**, so the pane
shows the terminal's own background and there's no seam.

That's keyed to agterm's **Dawnfox** theme, which is the ghostty port of nightfox.nvim's `dawnfox` — the
same palette on both sides, by name, rather than hand-matched hexes.

- `* Dawnfox` → nothing to do.
- Another **nightfox** variant (nightfox / dayfox / duskfox / nordfox / terafox / carbonfox) → change
  `colorscheme dawnfox` and both `palette.load("dawnfox")` calls in `init.lua` to that name.
- Anything else → the tree stays transparent and readable, but accent colors (root folder, git status)
  won't match. Leave it, or **ask** before changing their terminal theme.

Preserve this if you touch the colors: git-dirty uses **yellow** (`pal.yellow.base`), not orange.
Dawnfox's orange is a rose sitting right next to its red, which would make *modified* and *deleted*
files nearly indistinguishable.

## 8. Verify

```sh
NVIM_APPNAME=nvim-zed NVIM_ZED_ROLE=tree nvim --headless -c 'lua
  print("colorscheme      : "..(vim.g.colors_name or "?"))
  print("tree_menu module : "..tostring((pcall(require,"tree_menu"))))
  local x = vim.api.nvim_get_hl(0,{name="NvimTreeNormal",link=false})
  print("NvimTreeNormal bg: "..(x.bg and "OPAQUE (bad)" or "NONE (transparent)"))' -c qa
```

Pass criteria: `dawnfox`, `true`, and **`NONE (transparent)`**. An opaque `NvimTreeNormal` means the
tree will render as a grey slab on top of the terminal — the one thing this config exists to avoid.

Then, in a real agterm session:

1. `ctrl+b>z` → right split opens at ~25%, tree rooted at the session's cwd.
2. Click a file → opens in a 90% overlay. Close it → back at the tree.
3. `ctrl+b>z` again → hides the split. Again → reveals the **same** tree instantly, no relaunch flash.
4. `<leader>m` or right-click a file → context menu opens in an fzf window, navigable with the keyboard.

If the split opens but sits at a shell prompt: the `session type` into the fresh pane raced the shell
coming up — check `nvim-zed-tree-run.sh` is executable.

## 9. The context menu format

`tree-menu.yaml` is the whole menu; `tree_menu.lua` only interprets it. It re-reads on mtime change, so
edits need no restart. Tell the user this — it's the part they'll actually customize.

```yaml
menu:
  - name: Open                        # a built-in nvim-tree action
    isDir: false
    action: quicklook

  - name: Open in                     # a submenu
    type: submenu
    menu:
      - { name: Finder, cmd: open -R $ABS }
      - { name: Typora, cmd: open -a Typora $ABS, ext: [md] }   # only for .md

  - name: Shell here                  # a shell command in a new agterm session
    isFile: false                     # directories only
    run: session
    cmd: cd $ABS && exec zsh
```

**Variables** — substituted **shell-escaped**, so never quote them in the yaml; paths with spaces work:

| | |
|---|---|
| `$ABS` | absolute path of the node |
| `$REL` | relative to the tree root — the *repo-relative* one git tools want |
| `$DIR` | tree root (also the cwd every `cmd` runs in) |
| `$PARENT` `$NAME` `$STEM` `$EXT` | parent dir, basename, basename sans extension, extension |

**`run:`** — where the command runs:

| | |
|---|---|
| `exec` *(default)* | detached, no terminal. GUI apps (`open -a …`). |
| `overlay` | agterm overlay over **this** session. Anything with a TUI. `size:` = percent (default 90). |
| `session` | a new agterm session, cwd at the tree root. |

**Keeping the pane alive.** A command that exits takes its overlay/session down with it — so anything
that just *prints* (`echo`, `git log`) vanishes before it can be read. That's agterm correctly tearing
down a dead process, not a bug. Three ways out (`overlay`/`session` only; all fire even when the command
**fails**, so errors stay on screen):

| | |
|---|---|
| `hold: true` | wait for a keypress, then close. For output you just want to read. |
| `shell: true` | drop into an interactive shell afterwards, cwd intact. |
| `continue: true` | only with `shell: true`. Read the output first, *then* Enter → shell, Esc → close. |

**Filters** (optional, ANDed): `isDir: false` hides it on directories · `isFile: false` hides it on files
· `ext: [md]` shows it only for those extensions (implies files only). A submenu whose children all
filter out is hidden.

**`action:`** — instead of `cmd:`, for what isn't a shell command: `quicklook` `persistent` `expand`
`set_root` `create` `rename` `delete` `cut` `copy` `paste` `copy_name` `copy_rel` `copy_abs`.

⚠️ **`delete` maps to `api.fs.trash`, and must stay that way.** nvim-tree's `trash = { cmd = … }` setup
option routes **only** `api.fs.trash` — `api.fs.remove` deletes unconditionally, whatever `trash` is set
to (separate code paths: `trash.lua` reads `config.g.trash.cmd`, `remove-file.lua` never looks at it).
This shipped as `api.fs.remove` under a menu item labelled "Delete (to Trash)", i.e. permanently deleting
files while promising they were recoverable. Requires the `trash` binary on macOS (`brew install trash`);
without it nvim-tree warns and deletes nothing, which is the correct way to fail.

**`key:`** — an optional `[key] ` hint prefix naming the tree keybinding that does the same thing, e.g.
`key: gd` renders as `[gd] Review File`. It is **cosmetic and binds nothing**. The shipped values match
the origin machine's `on_attach` — **re-derive them for this machine** by reading its keymaps, and match
on what each key *does*, not the letter. A stale hint is worse than no hint. Entries with no equivalent
keybinding (Finder, Shell here, submenu parents) simply omit it.

Note the origin swaps nvim-tree's defaults (`d` = trash, `D` = permanent), so `[d]` is right *there* — on
a stock config `d` is the permanent one. Check before copying that hint.

**Submenus get a `..` back-entry** as their first row, injected by the module (not in the yaml).
Selecting it returns to the parent menu — without it, descending is one-way and the only exit is Esc,
which cancels the whole menu.

A broken yaml fails **loudly** with the parser's error, rather than silently producing an empty menu.

## 10. Gotchas — do not "simplify" these away

Four things here look like over-engineering and are not.

**1. Right-click binds `<RightRelease>`, not `<RightMouse>`.** fzf-lua renders the menu as a *terminal*
buffer, which must be in terminal-mode to accept keys. Opening it on the mouse **press** leaves the
**release** still queued: it lands in the newly-focused float, knocks it out of terminal-mode, and then
nothing navigates — the menu draws, but keys go to nvim instead of fzf. Binding the release means no
mouse event survives the click. *(Symptom: menu appears, keyboard dead.)*

**2. `mousemodel = "extend"`, buffer-local.** nvim's default `popup_setpos` *consumes* `<RightMouse>` to
raise its own built-in PopUp, so a mapping never sees the click. `extend` frees it. Keep it
buffer-local — nvim's PopUp should still work everywhere else.

**3. `register_ui_select()` is required, not decorative.** The config sets `cmdheight = 0`, and the
default `vim.ui.select` renderer draws a numbered list *in the command line* — i.e. nowhere.

**4. `ide-revdiff-file.sh` / `ide-compare.sh` DELETE their argument.** (Not bundled, but the user may
have them.) Both take a *temp file whose contents are the path*, and `rm -f` it once read. Hand either a
real path — `cmd: ide-revdiff-file.sh $ABS` — and it `cat`s the file's text as a filename and then
**deletes the file**. That exact yaml entry silently destroyed five files before it was caught. From
yaml, call the underlying tool directly (as the shipped Review entries call `revdiff`), or `cd` in and
call the script bare.

**Never write a menu entry that passes `$ABS` to a script you haven't read.**

## 11. Optional dependencies

Extra open-actions in `init.lua` shell out to scripts from a wider personal setup that are **not
bundled**. Absent them the key just fails; the tree is unaffected. Copy them from the source machine, or
delete the corresponding keymaps:

| Key | Needs | If missing |
|---|---|---|
| `o` | `ide-persist.sh` — persistent tmux-backed nvim | Use `<CR>`/`l` (overlay) instead |
| `gp` | Typora | Remove the `gp` map, or point it at another app |
| `gl` | `glow` | `brew install glow`, or remove the map |
| images | `img-view.sh` — kitty-graphics viewer | Images open in nvim as binary garbage; remove the image branch in `open-nvim-zed-preview.sh` |
| `gd` / `gc` | `ide-revdiff-file.sh`, `ide-compare.sh` | Remove those maps |

## 12. How it fits together

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
                  ├─ click a file ──> open-nvim-zed-preview.sh ──> agtermctl session overlay open (90%)
                  └─ <leader>m ─────> tree_menu.lua ──> tree-menu.yaml ──> exec | overlay | session
```

Two things in `open-nvim-zed.sh` that also look over-engineered and aren't:

- **It identifies its tree by process environment** (`AGTERM_SESSION_ID` + `AGTERM_PANE=right` +
  `NVIM_ZED_ROLE=tree`), not by the split flag — the right pane is shared with other tools (e.g. a tmux
  IDE split), and agterm's tree JSON reports `split` as *visibility*, with no `splitForeground` in this
  build. This is what makes the toggle leave a non-tree split alone.
- **It focuses the main pane before hiding.** `split off` maximizes the *focused* pane, so hiding while
  the tree has focus would leave the tree full-screen instead of hidden. Focus is async, so it waits for
  `splitFocused == false` first.
