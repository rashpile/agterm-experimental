---
name: view
description: Use when the user wants to see, preview, open, read or "view" a markdown file (README, plan, notes, report, docs) rendered, or asks to write a document or explainer with diagrams, schemes or charts and show it as a page in agterm — "view this", "/view plan.md", "show the README", "open it in the web view", "render this markdown", "draw a diagram and show it".
allowed-tools: Bash
---

# View markdown in an agterm overlay

`scripts/view.py` renders markdown to a static HTML page and opens it in a **companion session**
of this one (listed under it in the sidebar), as an HTML overlay. The page takes its colors from the terminal theme,
highlights code, supports GFM tables, task lists, footnotes and heading anchors, inlines local
images, and renders Mermaid diagrams.

Resolve the script against this skill's directory, not your working directory. Run it with `uv`:

```bash
uv run -q --script <this-skill-dir>/scripts/view.py <file.md>
```

## Writing a new document with diagrams

When you write the markdown yourself and it needs a diagram, **read [diagrams.md](diagrams.md) first**.
Draw diagrams as Mermaid blocks or with the page's SVG kit. ASCII-art schemes are only for a
terminal, not for a page. Do not write your own SVG styles, markers or colors: the page provides
them, themed from the terminal.

## Quick reference

| Goal | Command |
|------|---------|
| Show a file in a companion session, switch the user to it | `view.py plan.md` |
| Same, without switching the user | `view.py plan.md --no-follow` |
| User asks to show it **here** / over this session | `view.py plan.md --here` |
| Floating panel over this session, 70% | `view.py plan.md --size 70` |
| Cover one split pane of this session | `view.py plan.md --pane right` |
| Show markdown you wrote (an answer, a summary) | `cat <<'EOF' \| view.py - --title "Summary"` … `EOF` |
| Render only, no overlay | `view.py plan.md -o /tmp/plan.html` |
| Look at the page yourself (PNG via headless Chrome) | `view.py doc.md --shot /tmp/doc.png --shot-only` (+ `--shot-dark`) |

- Running it again on the same file reloads the open page. After you edit the markdown, run it again.
- Default: a companion session named `view: <title>` under this one. Closing the page (⌘W) closes that
  session too. Running it again on the same file reloads that session.
- `--here`, `--size`, `--pane` or `--target` show an overlay over a session instead. There a page
  from another `view` call in the same slot is replaced. A running program overlay is never
  closed: the command then fails with `overlay already open`. Tell the user instead of closing it.
- Outside agterm (`AGTERM_ENABLED` unset) it opens the page in the default browser.
- Output: `opened: <html path>` or `reloaded: <html path>`. The HTML is cached in `~/.cache/agterm-view/`.
- With Mermaid blocks it also prints `diagrams: ok`, or exits with code 3 and prints
  `MERMAID ERROR: diagram N: …`. Fix the block and run again.

## Limits

- Math blocks show as plain code. Pages with Mermaid open with `--js` (offline, bundled
  `assets/mermaid.min.js` v11.4.1); pages without it run with JavaScript off.
- Links to other `.md` files do not open. http(s) links open in the browser after the user confirms.
- Leave the page up. The user closes it with ⌘W or the close button.
