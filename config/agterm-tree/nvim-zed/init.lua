-- ── bootstrap lazy.nvim ───────────────────────────────────────────────────────
local lazypath = vim.fn.stdpath("data") .. "/lazy/lazy.nvim"
if not vim.loop.fs_stat(lazypath) then
    vim.fn.system({
        "git", "clone", "--filter=blob:none",
        "https://github.com/folke/lazy.nvim.git", "--branch=stable", lazypath
    })
end
vim.opt.rtp:prepend(lazypath)

-- ── basics ───────────────────────────────────────────────────────────────────
vim.g.mapleader = " "
vim.opt.number = true
vim.opt.relativenumber = false
vim.opt.termguicolors = true
vim.opt.mouse = "a"                 -- IDE: full mouse support (Zed-style click-to-open)
vim.opt.expandtab = true
vim.opt.shiftwidth = 2
vim.opt.tabstop = 2
vim.opt.smartindent = true
vim.opt.clipboard = "unnamedplus" -- use system clipboard
vim.opt.showmode = false          -- lualine already shows the mode; drop the redundant "-- VISUAL --" line
vim.opt.cmdheight = 0             -- remove the empty command-line row so the statusline sits on the bottom line (aligns with the tmux tab bar)
vim.opt.splitright = true
vim.opt.splitbelow = true

-- nvim-zed: which pane are we? "tree" = the right-split file browser (files open in an
-- agterm overlay); "preview" = the single-file quick-look opened inside that overlay.
local NVIM_ZED_TREE = vim.env.NVIM_ZED_ROLE == "tree"
local NVIM_ZED_PREVIEW = vim.env.NVIM_ZED_ROLE == "preview"

-- Quick-look overlay: `q` (normal mode) closes it. If the file was changed, ask Save / Discard /
-- Cancel first so a stray `q` can't discard edits silently. Set in a VimEnter autocmd so it wins
-- over any plugin-set global `q` map. (This shadows macro-record `q`, which quick-look doesn't need.)
if NVIM_ZED_PREVIEW then
    vim.api.nvim_create_autocmd("VimEnter", {
        callback = function()
            vim.keymap.set("n", "q", function()
                if vim.bo.modified then
                    local choice = vim.fn.confirm("File was changed", "&Save\n&Discard\n&Cancel", 1)
                    if choice == 1 then
                        vim.cmd("write | quit")
                    elseif choice == 2 then
                        vim.cmd("quit!")
                    end
                    -- choice 3 (Cancel) or 0 (Esc) → stay open
                else
                    vim.cmd("quit")
                end
            end, { desc = "Quick-look: close (prompt if modified)" })
        end,
    })
end

-- ── plugins ──────────────────────────────────────────────────────────────────
require("lazy").setup({
    -- File tree
    {
        "nvim-tree/nvim-tree.lua",
        dependencies = { "nvim-tree/nvim-web-devicons" },
        config = function()
            require("nvim-tree").setup({
                hijack_cursor = true,
                view = {
                    width = 34,
                    adaptive_size = false,
                },
                renderer = {
                    group_empty = true,
                    highlight_git = true,
                    indent_markers = { enable = true },
                    icons = {
                        show = { file = true, folder = true, folder_arrow = true, git = true },
                    },
                },
                -- Tree pane shows all files by default; `gm` toggles git-CHANGED-only
                -- (git_clean hides unchanged).
                filters = { dotfiles = false, git_ignored = false, git_clean = false },
                actions = {
                    open_file = {
                        quit_on_open = false,
                        resize_window = true,
                    },
                },
                git = { enable = true },
                update_focused_file = {
                    enable = true,
                    update_root = true,
                },
                respect_buf_cwd = true,
                -- Delete → macOS Trash (recoverable, like Zed) instead of permanent rm.
                -- delete-to-trash: macOS `trash`, Linux `gio trash` (so the same config works on the remote host)
                trash = { cmd = (vim.fn.has("mac") == 1) and "trash" or "gio trash" },

                -- ✅ The modern way to define key mappings:
                on_attach = function(bufnr)
                    local api = require("nvim-tree.api")
                    local function opts(desc)
                        return { desc = "NvimTree: " .. desc, buffer = bufnr, noremap = true, silent = true, nowait = true }
                    end

                    -- Default mappings
                    api.config.mappings.default_on_attach(bufnr)

                    -- Custom overrides
                    -- "Pin open": open the file for real (a permanent bufferline tab) and focus the
                    -- editor. Clears the bufhidden=delete that PREVIEW stamps on, so a file that was
                    -- first previewed then opened here isn't wiped when the next preview replaces it.
                    local function pin_open()
                        api.node.open.edit()
                        if vim.bo.buftype == "" then      -- landed on a real file buffer (not the tree)
                            vim.bo.bufhidden = ""
                            vim.bo.buflisted = true
                        end
                    end
                    vim.keymap.set("n", "l", pin_open, opts("Open (pin)"))
                    vim.keymap.set("n", "<CR>", pin_open, opts("Open (pin)"))
                    vim.keymap.set("n", "o", pin_open, opts("Open (pin)"))
                    -- Zed-style tabs:
                    --  • single click → PREVIEW: opens the file in an ephemeral tab (focus stays in the
                    --    tree); single-clicking another file REPLACES it. Editing it (or double-click /
                    --    Enter) promotes it to a permanent tab.
                    --  • double click → PIN: open & keep it as a permanent tab, and land in the editor.
                    vim.keymap.set("n", "<LeftRelease>", function()
                        local node = api.tree.get_node_under_cursor()
                        if not node then return end
                        if node.type == "directory" then
                            api.node.open.edit()          -- expand / collapse
                        else
                            api.node.open.preview()       -- ephemeral preview, keeps tree focus
                        end
                    end, opts("Single click: expand folder / preview file"))
                    vim.keymap.set("n", "<2-LeftMouse>", function()
                        local node = api.tree.get_node_under_cursor()
                        if not node then return end
                        if node.type == "directory" then api.node.open.edit() else pin_open() end
                    end, opts("Double click: open & pin file as a tab"))
                    vim.keymap.set("n", "h", api.node.navigate.parent_close, opts("Close Directory"))
                    vim.keymap.set("n", "v", api.node.open.vertical, opts("Open: Vertical Split"))
                    vim.keymap.set("n", "s", api.node.open.horizontal, opts("Open: Horizontal Split"))
                    vim.keymap.set("n", "C", api.tree.change_root_to_node, opts("Change root to node"))
                    vim.keymap.set("n", "u", api.tree.change_root_to_parent, opts("Change root to parent"))
                    -- vim.keymap.set('n', 'Y', api.fs.copy.absolute_path, opts("Copy Absolute Path"))
                    -- vim.keymap.set('n', 'gY', api.fs.copy.root_to_clipboard, opts("Copy Root Path"))
                    --
                    -- Copy path of the node under cursor.
                    -- default_on_attach binds single-key `y` ("Copy Name") with nowait, so it
                    -- fires the instant you press `y` and the ya/yr/yn sequences below can never
                    -- complete. Delete it so `y` becomes a prefix; `yy` restores the copy-name.
                    pcall(vim.keymap.del, "n", "y", { buffer = bufnr })
                    vim.keymap.set("n", "yy", api.fs.copy.filename, opts("Copy filename (name)")) -- just the name
                    vim.keymap.set("n", "ya", api.fs.copy.absolute_path, opts("Copy ABS path")) -- /full/path/to/file
                    vim.keymap.set("n", "yr", api.fs.copy.relative_path, opts("Copy REL path")) -- relative to tree root
                    vim.keymap.set("n", "yn", api.fs.copy.filename, opts("Copy filename"))      -- just the name

                    -- Copy the *current directory* (tree root) path.
                    -- Use the TREE root via nvim-tree.core.get_cwd(), not vim.fn.getcwd() —
                    -- nvim's cwd can be the parent dir while the tree is rooted at the real
                    -- project/repo (same reason gc does this; seen on romanian).
                    vim.keymap.set("n", "gY", function()
                        local cwd
                        local ok_core, core = pcall(require, "nvim-tree.core")
                        if ok_core and core.get_cwd then cwd = core.get_cwd() end
                        if not cwd or cwd == "" then cwd = vim.fn.getcwd() end
                        vim.fn.setreg("+", cwd) -- system clipboard
                        print("Copied root: " .. cwd)
                    end, opts("Copy current root path"))

                    -- gc: git compare. Opens an agterm overlay (no --target → lands on this focused
                    -- editor) running the fzf picker → revdiff. Repo-wide (vs base / vs branch), plus
                    -- file-scoped options when the cursor is on a file. agtermctl auto-discovers the
                    -- socket. Use the TREE root as cwd, not vim.fn.getcwd() — nvim's cwd can be the
                    -- parent dir while the tree is rooted at the real project/repo (seen on romanian).
                    -- The file path is handed to the script via a temp file (dodges path-quoting).
                    vim.keymap.set("n", "gc", function()
                        local dir
                        local ok_core, core = pcall(require, "nvim-tree.core")
                        if ok_core and core.get_cwd then dir = core.get_cwd() end
                        if not dir or dir == "" then dir = vim.fn.getcwd() end
                        local arg = ""
                        local node = api.tree.get_node_under_cursor()
                        if node and node.type ~= "directory" and node.absolute_path then
                            local tf = vim.fn.tempname()
                            vim.fn.writefile({ node.absolute_path }, tf)
                            arg = " " .. tf   -- tempname is space/quote-free → safe inside the quotes
                        end
                        vim.fn.jobstart({
                            "/opt/homebrew/bin/agtermctl", "session", "overlay", "open",
                            "zsh -ic '" .. os.getenv("HOME") .. "/.config/agterm-projects/ide-compare.sh" .. arg .. "'",
                            "--cwd", dir, "--size-percent", "95",
                        }, { detach = true })
                    end, opts("Git compare (revdiff): repo/file, base or branch"))

                    -- gd: open the file under the cursor in revdiff — the WHOLE file, not a diff
                    -- (for diffs, that's `gc`). A review is long-lived, so it gets its own agterm
                    -- session (named revdiff:<file>, placed right after this one) rather than a modal
                    -- overlay; the session closes when revdiff exits. --command replaces the login
                    -- shell, so .zprofile never runs and /opt/homebrew is off PATH — hence the script,
                    -- which exports PATH itself. Path handed over by temp file (dodges quoting). Tree
                    -- root as cwd, not vim.fn.getcwd() (same idiom as gc above).
                    vim.keymap.set("n", "gd", function()
                        local node = api.tree.get_node_under_cursor()
                        if not node or node.type == "directory" or not node.absolute_path then
                            vim.notify("gd: put the cursor on a file", vim.log.levels.WARN)
                            return
                        end
                        local dir
                        local ok_core, core = pcall(require, "nvim-tree.core")
                        if ok_core and core.get_cwd then dir = core.get_cwd() end
                        if not dir or dir == "" then dir = vim.fn.getcwd() end
                        local tf = vim.fn.tempname()
                        vim.fn.writefile({ node.absolute_path }, tf)
                        local argv = {
                            "/opt/homebrew/bin/agtermctl", "session", "new",
                            "--cwd", dir,
                            "--command", os.getenv("HOME") .. "/.config/agterm-projects/ide-revdiff-file.sh " .. tf,
                            "--name", "revdiff:" .. vim.fn.fnamemodify(node.absolute_path, ":t"),
                        }
                        -- Sit next to the session we were launched from, when we know which that is.
                        local sid = vim.env.AGTERM_SESSION_ID
                        if sid and sid ~= "" then
                            table.insert(argv, "--after")
                            table.insert(argv, sid)
                        end
                        vim.fn.jobstart(argv, { detach = true })
                    end, opts("Revdiff this file (new session)"))

                    -- ── File operations (Zed-style) ────────────────────────────────────────
                    -- a: new file/folder — type a name, or end with "/" to make a folder
                    vim.keymap.set("n", "a", api.fs.create, opts("New file / folder (end with /)"))
                    vim.keymap.set("n", "r", api.fs.rename, opts("Rename"))
                    vim.keymap.set("n", "<F2>", api.fs.rename, opts("Rename"))
                    vim.keymap.set("n", "c", api.fs.copy.node, opts("Copy (then p to paste)"))
                    vim.keymap.set("n", "x", api.fs.cut, opts("Cut (then p to paste)"))
                    vim.keymap.set("n", "p", api.fs.paste, opts("Paste into folder under cursor"))
                    -- duplicate = copy this node, then paste it right back into the same folder
                    vim.keymap.set("n", "<C-d>", function()
                        api.fs.copy.node()
                        api.fs.paste()
                    end, opts("Duplicate"))
                    -- d → Trash (recoverable);  D → permanent delete
                    vim.keymap.set("n", "d", api.fs.trash, opts("Delete → Trash"))
                    vim.keymap.set("n", "D", api.fs.remove, opts("Delete permanently"))

                    -- ── nvim-zed tree role: files quick-look in a 90% agterm overlay ─────────
                    -- Opening a FILE (<CR>/l/double-click) launches it in an ephemeral overlay
                    -- (single file, no tabs, respawn-per-open) instead of an editor window — there is
                    -- none in this tree-only pane. `o` instead opens it in the PERSISTENT nvim-ide
                    -- editor (see open_persistent below). Single click only expands/collapses
                    -- directories. Overrides the pin/preview maps set earlier in on_attach.
                    if NVIM_ZED_TREE then
                        local function quicklook()
                            local node = api.tree.get_node_under_cursor()
                            if not node then return end
                            if node.type == "directory" then
                                api.node.open.edit()   -- expand / collapse
                            elseif node.absolute_path then
                                vim.fn.jobstart(
                                    { (os.getenv("HOME") .. "/.config/agterm-projects/open-nvim-zed-preview.sh"), node.absolute_path },
                                    { detach = true }
                                )
                            end
                        end
                        -- `o` is the other half: open the file in the PERSISTENT nvim-ide editor
                        -- (ide-persist.sh — a tmux-backed nvim rendered in a 95% overlay). Unlike
                        -- quick-look, that editor keeps every file you send it as a bufferline tab
                        -- and survives closing the popup. cmd+shift+o brings it back.
                        local function open_persistent()
                            local node = api.tree.get_node_under_cursor()
                            if not node then return end
                            if node.type == "directory" then
                                api.node.open.edit()   -- expand / collapse
                            elseif node.absolute_path then
                                -- tree root, not vim.fn.getcwd() (same idiom as gc/gY above)
                                local dir
                                local ok_core, core = pcall(require, "nvim-tree.core")
                                if ok_core and core.get_cwd then dir = core.get_cwd() end
                                if not dir or dir == "" then dir = vim.fn.getcwd() end
                                vim.fn.jobstart({
                                    vim.fn.expand("~/.config/agterm-projects/ide-persist.sh"), "open",
                                    vim.env.AGTERM_SESSION_ID or "", dir, node.absolute_path,
                                }, { detach = true })
                            end
                        end
                        vim.keymap.set("n", "<CR>", quicklook, opts("Quick-look file in overlay"))
                        vim.keymap.set("n", "l", quicklook, opts("Quick-look file in overlay"))
                        vim.keymap.set("n", "<2-LeftMouse>", quicklook, opts("Quick-look file in overlay"))
                        vim.keymap.set("n", "o", open_persistent, opts("Open in persistent nvim-ide"))
                        vim.keymap.set("n", "<LeftRelease>", function()
                            local node = api.tree.get_node_under_cursor()
                            if node and node.type == "directory" then api.node.open.edit() end
                        end, opts("Single click: expand directory"))
                        -- gm: git-changed only. Toggle git_clean AND gitignore together — ignored
                        -- files have git status "!!" (non-empty), so git_clean alone keeps showing them.
                        vim.keymap.set("n", "gm", function()
                            api.tree.toggle_git_clean_filter()
                            api.tree.toggle_gitignore_filter()
                        end, opts("Toggle git-changed only"))
                        -- gp: open the markdown file under the cursor in Typora.
                        -- Uses the pane's `g` prefix (gm/gc/gY) because the single-key maps `m`
                        -- (Toggle Bookmark) and `p` (Paste) both carry nowait, so they fire before
                        -- an `mp`/`pm` sequence could ever complete.
                        vim.keymap.set("n", "gp", function()
                            local node = api.tree.get_node_under_cursor()
                            if not node or node.type == "directory" or not node.absolute_path then return end
                            local path = node.absolute_path
                            local ext = path:lower():match("%.(%w+)$")
                            if ext ~= "md" and ext ~= "markdown" and ext ~= "mdx" then
                                vim.notify("Not a markdown file: " .. vim.fn.fnamemodify(path, ":t"),
                                    vim.log.levels.WARN)
                                return
                            end
                            -- argv form: no shell, so spaces/quotes in the path need no escaping.
                            -- `open` returns as soon as Typora is launched, so no detach — that lets
                            -- on_exit report a missing app instead of failing silently.
                            vim.fn.jobstart({ "/usr/bin/open", "-a", "Typora", path }, {
                                on_exit = function(_, code)
                                    if code ~= 0 then
                                        vim.schedule(function()
                                            vim.notify("Typora failed to open (exit " .. code .. ")",
                                                vim.log.levels.ERROR)
                                        end)
                                    end
                                end,
                            })
                        end, opts("Open markdown in Typora (gp)"))

                        -- gl: render the markdown file under the cursor with glow, in a 85% agterm
                        -- overlay (press q to close). Markdown only, like gp. Path travels through a
                        -- temp file to dodge quoting across nvim→sh→zsh (as gd does), read+removed
                        -- before glow runs. Login shell (`zsh -lc`) so glow inherits Homebrew PATH
                        -- from .zprofile. No --target → the overlay lands on this focused session (as
                        -- gc does). Uses glow-style.json when present.
                        vim.keymap.set("n", "gl", function()
                            local node = api.tree.get_node_under_cursor()
                            if not node or node.type == "directory" or not node.absolute_path then return end
                            local path = node.absolute_path
                            local ext = path:lower():match("%.(%w+)$")
                            if ext ~= "md" and ext ~= "markdown" and ext ~= "mdx" then
                                vim.notify("Not a markdown file: " .. vim.fn.fnamemodify(path, ":t"),
                                    vim.log.levels.WARN)
                                return
                            end
                            local tf = vim.fn.tempname()
                            vim.fn.writefile({ path }, tf)
                            local style = vim.fn.expand("~/.config/agterm-projects/glow-style.json")
                            local style_arg = (vim.fn.filereadable(style) == 1) and ("-s " .. style .. " ") or ""
                            local inner = "p=$(cat " .. tf .. "); rm -f " .. tf ..
                                "; exec /opt/homebrew/bin/glow " .. style_arg .. "-p \"$p\""
                            vim.fn.jobstart({
                                "/opt/homebrew/bin/agtermctl", "session", "overlay", "open",
                                "zsh -lc '" .. inner .. "'",
                                "--size-percent", "85",
                            }, { detach = true })
                        end, opts("Glow markdown in overlay (gl)"))
                    end
                end,
            })
            -- Zed-style launch: started with no file args → open the tree SIDEBAR next to an
            -- empty editor window (instead of `nvim .` where the tree hijacks the whole window),
            -- and land focus in the editor.
            vim.api.nvim_create_autocmd("VimEnter", {
                callback = function(data)
                    if NVIM_ZED_TREE then
                        -- dedicated tree pane: no bufferline (there are no file tabs here — files
                        -- open in overlays), no end-of-buffer `~` markers, tree fills the window.
                        vim.opt.showtabline = 0
                        vim.opt.laststatus = 0   -- no bottom status bar (NORMAL / line:col) in a file browser
                        vim.opt.fillchars:append({ eob = " " })
                        require("nvim-tree.api").tree.open({ current_window = true })
                        return
                    end
                    if data.file == "" and vim.fn.argc() == 0 then
                        require("nvim-tree.api").tree.open()
                        vim.cmd.wincmd("p")
                    end
                end,
            })

            -- Keymap: <leader>e toggles the tree
            vim.keymap.set("n", "<leader>e", "<cmd>NvimTreeToggle<cr>", { desc = "Toggle file tree" })
            -- Keymap: <leader>o always focuses the tree (opens it if needed)
            vim.keymap.set("n", "<leader>o", "<cmd>NvimTreeFocus<cr>", { desc = "Focus file tree" })
        end,
    },

    -- Sensible statusline (tiny, optional)
    {
        "nvim-lualine/lualine.nvim",
        dependencies = { "nvim-tree/nvim-web-devicons" },
        cond = not NVIM_ZED_TREE,   -- tree pane is a file browser; no statusline (see laststatus=0)
        config = function() require("lualine").setup({ options = { theme = "auto" } }) end,
    },

    -- Better syntax highlighting (fast win with default config)
    { "nvim-treesitter/nvim-treesitter", build = ":TSUpdate" },

    -- Theme: dawnfox — the SAME palette agterm renders. agterm's "Dawnfox" terminal theme is the
    -- ghostty port of this nightfox variant, so nvim and the terminal draw from one source of
    -- truth instead of two hand-matched palettes (the old catppuccin-latte was a cool blue-grey
    -- family against agterm's warm cream — that mismatch is what made the tree look pasted on).
    --
    -- Transparency is nightfox's own `options.transparent`, which is why transparent.nvim is gone:
    -- it clears Normal AND the NvimTree groups, so the tree pane shows agterm's bg (#faf4ed) with
    -- no seam. (transparent.nvim existed to fight catppuccin's repainting, and its exclude_groups
    -- list is precisely what kept the tree opaque and grey.)
    {
        "EdenEast/nightfox.nvim",
        lazy = false,
        priority = 1000,
        config = function()
            require("nightfox").setup({
                options = { transparent = true },
            })
            -- Pin LIGHT deterministically: nvim's light/dark auto-detection (OSC 11 terminal query)
            -- is unreliable over ssh+tmux and falls back to dark.
            vim.o.background = "light"
            vim.cmd("colorscheme dawnfox")

            -- Tree colors, read from nightfox's palette rather than pasted hexes — they cannot
            -- drift from what agterm renders. Re-applied on ColorScheme so a re-source keeps them.
            local function tree_hl()
                local pal = require("nightfox.palette").load("dawnfox")
                local set = function(g, o) vim.api.nvim_set_hl(0, g, o) end

                -- Full-width selection bar (Zed-style). CursorLine is cleared by transparency, so
                -- it needs an explicit bg: agterm's own `selection-background`, so a selected tree
                -- row and selected terminal text are literally the same color.
                set("NvimTreeCursorLine", { bg = "#d0d8d8" })

                set("NvimTreeRootFolder", { fg = pal.blue.base, bold = true })
                set("NvimTreeFolderName", { fg = pal.blue.base })
                set("NvimTreeOpenedFolderName", { fg = pal.blue.base, bold = true })
                set("NvimTreeEmptyFolderName", { fg = pal.blue.dim })
                set("NvimTreeFolderIcon", { fg = pal.blue.base })
                set("NvimTreeIndentMarker", { fg = pal.bg4 })

                -- git status, in-palette. Dirty uses YELLOW, not orange: dawnfox's orange (#d7827e)
                -- is a rose that sits right next to its red (#b4637a), so modified vs deleted would
                -- be near-indistinguishable. Yellow (#ea9d34) is agterm's own palette slot 3.
                set("NvimTreeGitFileNewHL", { fg = pal.green.base })
                set("NvimTreeGitFileDirtyHL", { fg = pal.yellow.base })
                set("NvimTreeGitFileStagedHL", { fg = pal.green.base })
                set("NvimTreeGitFileMergeHL", { fg = pal.red.base })
                set("NvimTreeGitFileRenamedHL", { fg = pal.magenta.base })
                set("NvimTreeGitFileDeletedHL", { fg = pal.red.base })
                -- ignored files (.DS_Store, .coverage) recede instead of competing
                set("NvimTreeGitFileIgnoredHL", { fg = pal.comment, italic = true })
            end
            vim.api.nvim_create_autocmd("ColorScheme", { callback = tree_hl })
            tree_hl()

            -- `nvim .` makes nvim-tree hijack the first window, and windows that later show
            -- files INHERIT its window-local winhighlight (Normal:NvimTreeNormal, …). Strip it
            -- from every non-tree window.
            vim.api.nvim_create_autocmd("BufWinEnter", {
                callback = function()
                    if vim.bo.filetype ~= "NvimTree" and vim.wo.winhighlight:find("NvimTree") then
                        vim.wo.winhighlight = ""
                    end
                end,
            })
        end,
    },

    -- Harpoon: quick file navigation
    {
        "ThePrimeagen/harpoon",
        branch = "harpoon2",
        dependencies = { "nvim-lua/plenary.nvim" },
        config = function()
            local harpoon = require("harpoon")
            harpoon:setup()

            -- Add file to list
            vim.keymap.set("n", "<leader>a", function()
                harpoon:list():add()
            end, { desc = "Add file to Harpoon" })

            -- Toggle quick menu (the popup)
            vim.keymap.set("n", "<leader>h", function()
                harpoon.ui:toggle_quick_menu(harpoon:list())
            end, { desc = "Open Harpoon menu" })

            -- Quick jump to files 1-4
            vim.keymap.set("n", "<leader>1", function() harpoon:list():select(1) end)
            vim.keymap.set("n", "<leader>2", function() harpoon:list():select(2) end)
            vim.keymap.set("n", "<leader>3", function() harpoon:list():select(3) end)
            vim.keymap.set("n", "<leader>4", function() harpoon:list():select(4) end)
        end,
    },

    -- Bufferline: open files shown as tabs across the top (Zed-style)
    {
        "akinsho/bufferline.nvim",
        dependencies = { "nvim-tree/nvim-web-devicons" },
        config = function()
            -- Tab-bar colors: bufferline derives its shades from Normal, which transparency clears
            -- → it falls back to near-black, so the bar must be pinned explicitly. Taken from the
            -- dawnfox palette (not hardcoded hexes) so it tracks the same source as agterm: the bar
            -- sits one warm step back (bg0) and the ACTIVE tab is bg1 — which IS agterm's terminal
            -- background, so the current tab reads as flush with the terminal.
            local pal  = require("nightfox.palette").load("dawnfox")
            local tree = pal.bg0     -- tab-bar background (one step back)
            local base = pal.bg1     -- editor/terminal background (active tab) = #faf4ed
            local off  = { bg = tree, fg = pal.comment }              -- inactive
            local vis  = { bg = tree, fg = pal.fg2 }                  -- visible (non-current)
            local sel  = { bg = base, fg = pal.fg1, bold = true }     -- current tab
            local sep  = { bg = tree, fg = tree }                     -- blend separators into the bar
            require("bufferline").setup({
                options = {
                    mode = "buffers",
                    show_buffer_close_icons = true,
                    show_close_icon = false,
                    offsets = { { filetype = "NvimTree", text = "Files", separator = true } },
                },
                highlights = {
                    fill = { bg = tree },
                    background = off,  numbers = off,  modified = off,  duplicate = off,  close_button = off,
                    buffer_visible = vis, numbers_visible = vis, modified_visible = vis, duplicate_visible = vis, close_button_visible = vis,
                    buffer_selected = sel, numbers_selected = sel, modified_selected = { bg = base, fg = pal.green.base }, duplicate_selected = sel, close_button_selected = sel,
                    separator = sep, separator_visible = sep, separator_selected = { bg = base, fg = tree }, offset_separator = sep,
                    indicator_visible = { bg = tree, fg = tree }, indicator_selected = { bg = base, fg = pal.blue.base },
                },
            })
            -- Cycle through open-file tabs
            vim.keymap.set("n", "<S-l>", "<cmd>BufferLineCycleNext<cr>", { desc = "Next file tab" })
            vim.keymap.set("n", "<S-h>", "<cmd>BufferLineCyclePrev<cr>", { desc = "Prev file tab" })
            -- Jump to any tab by pressing its letter
            vim.keymap.set("n", "<leader>bb", "<cmd>BufferLinePick<cr>", { desc = "Pick file tab" })
            -- Reorder tabs
            vim.keymap.set("n", "<leader>b.", "<cmd>BufferLineMoveNext<cr>", { desc = "Move tab right" })
            vim.keymap.set("n", "<leader>b,", "<cmd>BufferLineMovePrev<cr>", { desc = "Move tab left" })
        end,
    },

    -- gitsigns: inline git signs, hunk preview/stage, blame
    {
        "lewis6991/gitsigns.nvim",
        config = function()
            require("gitsigns").setup()
            local gs = require("gitsigns")
            vim.keymap.set("n", "]c", function() gs.nav_hunk("next") end, { desc = "Next git hunk" })
            vim.keymap.set("n", "[c", function() gs.nav_hunk("prev") end, { desc = "Prev git hunk" })
            vim.keymap.set("n", "<leader>gp", gs.preview_hunk, { desc = "Preview hunk" })
            vim.keymap.set("n", "<leader>gs", gs.stage_hunk, { desc = "Stage hunk" })
            vim.keymap.set("n", "<leader>gr", gs.reset_hunk, { desc = "Reset hunk" })
            vim.keymap.set("n", "<leader>gb", function() gs.blame_line({ full = true }) end, { desc = "Blame line" })
        end,
    },

    -- diffview: full side-by-side diff view + file history
    {
        "sindrets/diffview.nvim",
        dependencies = { "nvim-lua/plenary.nvim" },
        config = function()
            require("diffview").setup()
            vim.keymap.set("n", "<leader>gd", "<cmd>DiffviewOpen<cr>", { desc = "Diff view (working tree)" })
            vim.keymap.set("n", "<leader>gh", "<cmd>DiffviewFileHistory %<cr>", { desc = "File history (current file)" })
            vim.keymap.set("n", "<leader>gc", "<cmd>DiffviewClose<cr>", { desc = "Close diff view" })
        end,
    },

    -- fzf-lua: fuzzy switch between open files and recent-file history
    {
        "ibhagwan/fzf-lua",
        dependencies = { "nvim-tree/nvim-web-devicons" },
        config = function()
            require("fzf-lua").setup({})
            -- Switch between currently OPEN files (most-recent first)
            vim.keymap.set("n", "<leader><leader>", "<cmd>FzfLua buffers<cr>", { desc = "Switch open file" })
            -- History of files opened previously (persists across sessions)
            vim.keymap.set("n", "<leader>fr", "<cmd>FzfLua oldfiles<cr>", { desc = "Recent files (history)" })
            -- Bonus: find/grep in the project
            vim.keymap.set("n", "<leader>ff", "<cmd>FzfLua files<cr>", { desc = "Find files" })
            vim.keymap.set("n", "<leader>fg", "<cmd>FzfLua live_grep<cr>", { desc = "Grep project" })
        end,
    },
})


-- ── quick quality-of-life keymaps ────────────────────────────────────────────
-- Cmd+c: copy the visual selection to the system clipboard
-- (clipboard=unnamedplus already, so "+y and y are equivalent here)
vim.keymap.set("v", "<D-c>", '"+y', { desc = "Copy selection to clipboard" })

-- Cmd+z: undo, Cmd+Shift+z: redo (normal + insert + visual modes)
vim.keymap.set({ "n", "v" }, "<D-z>", "u", { desc = "Undo" })
vim.keymap.set({ "n", "v" }, "<D-S-z>", "<C-r>", { desc = "Redo" })
vim.keymap.set("i", "<D-z>", "<C-o>u", { desc = "Undo" })
vim.keymap.set("i", "<D-S-z>", "<C-o><C-r>", { desc = "Redo" })

-- Save / Quit
vim.keymap.set("n", "<leader>w", "<cmd>w<cr>", { desc = "Write file" })
vim.keymap.set("n", "<leader>q", "<cmd>q<cr>", { desc = "Quit window" })
vim.keymap.set("n", "<leader>Q", "<cmd>qa!<cr>", { desc = "Quit all (force)" })

-- Windows/Splits
vim.keymap.set("n", "<leader>sv", "<cmd>vsplit<cr>", { desc = "Vertical split" })
vim.keymap.set("n", "<leader>sh", "<cmd>split<cr>", { desc = "Horizontal split" })
vim.keymap.set("n", "<leader>wh", "<C-w>h", { desc = "Left window" })
vim.keymap.set("n", "<leader>wj", "<C-w>j", { desc = "Down window" })
vim.keymap.set("n", "<leader>wk", "<C-w>k", { desc = "Up window" })
vim.keymap.set("n", "<leader>wl", "<C-w>l", { desc = "Right window" })

-- Buffers (tabs are projects; buffers are files)
vim.keymap.set("n", "<leader>bn", "<cmd>bnext<cr>", { desc = "Next buffer" })
vim.keymap.set("n", "<leader>bp", "<cmd>bprevious<cr>", { desc = "Prev buffer" })
vim.keymap.set("n", "<leader>bd", "<cmd>bd<cr>", { desc = "Delete buffer" })
