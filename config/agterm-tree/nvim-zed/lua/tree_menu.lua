-- tree_menu — a YAML-configured context menu for the nvim-tree pane.
--
-- nvim's built-in PopUp (right-click) is a *buffer* menu — Paste / Select All / Inspect — none of
-- which act on the node under the cursor. This is the file menu: node-aware, and defined in
-- ~/.config/agterm-projects/tree-menu.yaml so adding an entry needs no Lua.
--
-- YAML has no parser in nvim and there's no yq/PyYAML on this machine, so we convert with system
-- ruby (psych + json are stdlib — zero installs). ~120ms, so the result is cached and only re-read
-- when the file's mtime changes: edit the YAML, next open picks it up. No restart.
--
-- Entry format (see the yaml for a worked example):
--   name:    label in the menu (required)
--   key:     optional "[key] " hint prefix naming the tree keybinding that does the same thing.
--            COSMETIC — it binds nothing. Rebind a key in on_attach and you must update this by hand.
--   cmd:     shell command; $ABS/$REL/$DIR/$PARENT/$NAME/$STEM/$EXT are substituted SHELL-ESCAPED,
--            so paths with spaces need no quoting in the yaml
--   action:  instead of cmd — a built-in nvim-tree action (see ACTIONS below)
--   run:     exec (default, detached, no terminal) | overlay (agterm overlay on THIS session)
--            | session (new agterm session)
--   size:    overlay size percent (default 90)
--   type:    submenu — with a nested `menu:` list
--   isDir/isFile/ext: visibility filters, ANDed
local M = {}

local CONFIG = vim.fn.expand("~/.config/agterm-projects/tree-menu.yaml")
local AGT = "/opt/homebrew/bin/agtermctl"

local cache = { mtime = -1, menu = nil }

-- ── config ───────────────────────────────────────────────────────────────────

local function load_config()
    if vim.fn.filereadable(CONFIG) == 0 then
        vim.notify("tree-menu: no config at " .. CONFIG, vim.log.levels.WARN)
        return nil
    end
    local mtime = vim.fn.getftime(CONFIG)
    if cache.menu and mtime == cache.mtime then return cache.menu end

    -- system ruby: psych + json are stdlib, so no install. Fails loudly — a broken yaml should
    -- say so, not silently produce an empty menu.
    local out = vim.fn.system({ "ruby", "-ryaml", "-rjson", "-e",
        "puts YAML.load_file(ARGV[0]).to_json", CONFIG })
    if vim.v.shell_error ~= 0 then
        vim.notify("tree-menu: YAML parse failed\n" .. out, vim.log.levels.ERROR)
        return nil
    end
    local ok, data = pcall(vim.json.decode, out)
    if not ok or type(data) ~= "table" or type(data.menu) ~= "table" then
        vim.notify("tree-menu: config must have a top-level `menu:` list", vim.log.levels.ERROR)
        return nil
    end
    cache.mtime, cache.menu = mtime, data.menu
    return data.menu
end

-- ── node context ─────────────────────────────────────────────────────────────

local function node_ctx(node)
    local api = require("nvim-tree.api")
    local abs = node.absolute_path
    local is_dir = node.type == "directory"

    -- tree ROOT, not vim.fn.getcwd() — the tree can be re-rooted (C/u) and the cwd doesn't follow.
    local root
    local ok_core, core = pcall(require, "nvim-tree.core")
    if ok_core and core.get_cwd then root = core.get_cwd() end
    if not root or root == "" then root = vim.fn.getcwd() end

    local name = vim.fn.fnamemodify(abs, ":t")
    local rel = abs:sub(#root + 2)                  -- strip "root/" prefix
    if rel == "" then rel = "." end
    return {
        is_dir = is_dir,
        ext = (not is_dir) and (abs:lower():match("%.(%w+)$") or "") or "",
        vars = {
            ABS = abs,
            REL = rel,
            DIR = root,
            PARENT = vim.fn.fnamemodify(abs, ":h"),
            NAME = name,
            STEM = vim.fn.fnamemodify(name, ":r"),
            EXT = (abs:lower():match("%.(%w+)$") or ""),
        },
        _api = api,
    }
end

-- Substitute $VARS, shell-escaped. Longest names first so $PARENT isn't eaten by a $PA... prefix.
local function expand(cmd, vars)
    local keys = {}
    for k in pairs(vars) do keys[#keys + 1] = k end
    table.sort(keys, function(a, b) return #a > #b end)
    for _, k in ipairs(keys) do
        cmd = cmd:gsub("%$" .. k, function() return vim.fn.shellescape(vars[k]) end)
    end
    return cmd
end

-- ── visibility ───────────────────────────────────────────────────────────────

local function visible(item, ctx)
    if item.isDir == false and ctx.is_dir then return false end
    if item.isFile == false and not ctx.is_dir then return false end
    if item.ext then
        if ctx.is_dir then return false end          -- an ext filter implies "files only"
        local want = false
        for _, e in ipairs(item.ext) do
            if e:lower() == ctx.ext then want = true break end
        end
        if not want then return false end
    end
    return true
end

-- ── execution ────────────────────────────────────────────────────────────────

local function run_cmd(item, ctx)
    local cmd = expand(item.cmd, ctx.vars)
    local mode = item.run or "exec"
    local sid = vim.env.AGTERM_SESSION_ID or "active"
    -- Every mode runs the command from the tree root, so a `cmd` can use $REL directly.
    local inner = "cd " .. vim.fn.shellescape(ctx.vars.DIR) .. " && " .. cmd

    -- A command that exits takes the overlay/session down with it, so anything that just PRINTS
    -- (echo, git log, a script's result) vanishes before it can be read. Both of these keep the pty
    -- alive afterwards, and both are appended with `;` — so they still run when the command FAILS.
    -- That's the point: otherwise an error message flashes past and you never see it.
    if item.shell then
        if item["continue"] then
            -- Read the command's output FIRST, then choose: Enter drops into the shell, Esc (or
            -- anything else) closes. Without this, `shell: true` swallows the output behind a
            -- prompt you never asked for.
            inner = inner .. "; printf '\\n── Enter: shell · Esc: close ──'"
                .. "; read -k1 __k; [[ $__k == $'\\n' || $__k == $'\\r' ]] && exec zsh -i"
        else
            inner = inner .. "; exec zsh -i"    -- keep working in the same cwd
        end
    elseif item.hold then
        inner = inner .. "; printf '\\n── done, press any key ──'; read -k1"
    end
    if (item.shell or item.hold) and mode == "exec" then
        vim.notify("tree-menu: `" .. item.name .. "` sets shell/hold but run: exec has no terminal"
            .. " — use run: overlay or session", vim.log.levels.WARN)
    end
    if item["continue"] and not item.shell then
        vim.notify("tree-menu: `" .. item.name .. "` sets continue: true, which only applies with"
            .. " shell: true — ignored (use hold: true to just wait for a key)", vim.log.levels.WARN)
    end

    if mode == "exec" then
        -- detached, no terminal: GUI apps (`open -a …`), one-shot shell work
        vim.fn.jobstart({ "zsh", "-lc", inner }, { detach = true })
    elseif mode == "overlay" then
        -- a real pty on THIS session (the tree's own), not the user's active one
        vim.fn.jobstart({
            AGT, "session", "overlay", "open",
            "zsh -lc " .. vim.fn.shellescape(inner),
            "--size-percent", tostring(item.size or 90),
            "--target", sid,
        }, { detach = true })
    elseif mode == "session" then
        vim.fn.jobstart({
            AGT, "session", "new",
            "--cwd", ctx.vars.DIR,
            "--name", item.name,
            "--command", "zsh -lc " .. vim.fn.shellescape(inner),
        }, { detach = true })
    else
        vim.notify("tree-menu: unknown run mode: " .. tostring(mode), vim.log.levels.ERROR)
    end
end

-- Built-in nvim-tree actions — the things that aren't shell commands. `handlers` carries the
-- pane's own actions (quicklook / persistent), which live in init.lua's on_attach.
local function run_action(item, ctx, handlers)
    local api = ctx._api
    local ACTIONS = {
        quicklook  = handlers.quicklook,
        persistent = handlers.persistent,
        expand     = api.node.open.edit,
        set_root   = api.tree.change_root_to_node,
        create     = api.fs.create,
        rename     = api.fs.rename,
        -- macOS Trash (recoverable). NOT api.fs.remove: nvim-tree's `trash = { cmd = … }` setup opt
        -- only routes api.fs.trash — api.fs.remove deletes unconditionally, whatever trash is set to
        -- (separate code paths: trash.lua reads config.g.trash.cmd, remove-file.lua never looks).
        -- This entry is labelled "to Trash" and was calling remove, i.e. permanently deleting.
        delete     = api.fs.trash,
        cut        = api.fs.cut,
        copy       = api.fs.copy.node,
        paste      = api.fs.paste,
        copy_name  = api.fs.copy.filename,
        copy_rel   = api.fs.copy.relative_path,
        copy_abs   = api.fs.copy.absolute_path,
    }
    local fn = ACTIONS[item.action]
    if not fn then
        vim.notify("tree-menu: unknown action: " .. tostring(item.action), vim.log.levels.ERROR)
        return
    end
    fn()
end

-- ── menu ─────────────────────────────────────────────────────────────────────

-- `parent` (optional) is a thunk that re-opens the menu we descended FROM. When present, a ".."
-- entry is offered first and selecting it backs up — otherwise a submenu is a one-way trip whose
-- only exit is Esc, which cancels the whole menu. The top-level menu has no parent, so no "..".
local function pick(entries, ctx, handlers, title, parent)
    -- keep only what applies to this node; a submenu whose children all filtered out is dropped
    local items = {}
    if parent then items[#items + 1] = { back = true } end
    for _, e in ipairs(entries) do
        if type(e) == "table" and e.name and visible(e, ctx) then
            if e.type == "submenu" then
                local kids = {}
                for _, k in ipairs(e.menu or {}) do
                    if type(k) == "table" and k.name and visible(k, ctx) then kids[#kids + 1] = k end
                end
                if #kids > 0 then items[#items + 1] = { entry = e, kids = kids } end
            else
                items[#items + 1] = { entry = e }
            end
        end
    end
    -- a lone ".." means nothing in this submenu applied — treat it as empty
    if #items == 0 or (parent and #items == 1) then
        vim.notify("tree-menu: nothing applies to this node", vim.log.levels.INFO)
        return
    end

    vim.ui.select(items, {
        prompt = title,
        format_item = function(it)
            if it.back then return ".." end
            -- `key` is a COSMETIC hint at the tree keybinding that does the same thing. It binds
            -- nothing; it is kept in sync with on_attach by hand.
            local key = it.entry.key and ("[" .. it.entry.key .. "] ") or ""
            return key .. it.entry.name .. (it.kids and " ▸" or "")
        end,
    }, function(choice)
        if not choice then return end
        if choice.back then return parent() end                          -- ".." → back up
        if choice.kids then
            -- descend; the child's ".." re-opens THIS menu, parent chain intact
            local reopen = function() pick(entries, ctx, handlers, title, parent) end
            return pick(choice.kids, ctx, handlers, choice.entry.name, reopen)
        end
        local e = choice.entry
        if e.cmd then
            run_cmd(e, ctx)
        elseif e.action then
            run_action(e, ctx, handlers)
        else
            vim.notify("tree-menu: entry has neither cmd nor action: " .. e.name, vim.log.levels.ERROR)
        end
    end)
end

--- Open the context menu for the node under the cursor.
--- @param handlers table  { quicklook = fn, persistent = fn } — the tree pane's own open actions
function M.open(handlers)
    local menu = load_config()
    if not menu then return end
    local node = require("nvim-tree.api").tree.get_node_under_cursor()
    if not node or not node.absolute_path then return end
    local ctx = node_ctx(node)
    pick(menu, ctx, handlers or {}, ctx.vars.NAME)
end

return M
