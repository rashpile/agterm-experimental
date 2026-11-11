#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["markdown-it-py>=3", "mdit-py-plugins>=0.4", "linkify-it-py>=2", "pygments>=2.17"]
# ///
"""Render markdown to a static, theme-aware HTML page and show it in an agterm HTML overlay.

The page uses only HTML + CSS (agterm runs overlay pages with JavaScript off), takes its
colors from the terminal theme via the --agterm-* CSS variables, highlights code with
Pygments at render time, and inlines local images as data URIs (an overlay page has no
file access without --cwd, and --cwd must contain the HTML file).

The HTML lands in ~/.cache/agterm-view/, one stable file per source markdown path, so
viewing the same file again reloads the open overlay instead of stacking a new one.

Usage:
    view.py FILE.md [--no-follow]              # companion session; closing the page closes it
    view.py FILE.md --here [--size N | --pane left|right] [--target SID]   # overlay instead
    some-cmd | view.py - --title "Notes"       # markdown from stdin
    view.py FILE.md -o out.html                # render only, do not open
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from markdown_it import MarkdownIt
from mdit_py_plugins.anchors import anchors_plugin
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.front_matter import front_matter_plugin
from mdit_py_plugins.tasklists import tasklists_plugin
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

CACHE_DIR = Path.home() / ".cache" / "agterm-view"
MERMAID_JS = Path(__file__).resolve().parent.parent / "assets" / "mermaid.min.js"
DONE_MARK = "\u200b"  # appended to <title> by the page once diagrams rendered fine
ERROR_MARK = "MERMAID ERROR"


def die(msg: str) -> None:
    print(f"view: {msg}", file=sys.stderr)
    sys.exit(1)


# --- markdown → html ---------------------------------------------------------


def highlight_code(code: str, lang: str, _attrs: str) -> str:
    lang = (lang or "").strip().split()[0] if lang else ""
    if not lang:
        return ""  # markdown-it escapes and wraps it as plain code
    if lang == "mermaid":
        return f'<pre class="mmd">{html.escape(code)}</pre>'
    try:
        lexer = get_lexer_by_name(lang)
    except ClassNotFound:
        return ""
    body = highlight(code, lexer, HtmlFormatter(nowrap=True))
    return f'<pre class="hl"><code class="language-{html.escape(lang)}">{body}</code></pre>'


def inline_image(src: str, base: Path | None) -> str:
    if base is None or re.match(r"^[a-z][a-z0-9+.-]*:", src, re.I) or src.startswith("//"):
        return src
    path = (base / src.split("#")[0].split("?")[0]).expanduser()
    if not path.is_file():
        return src
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def render(text: str, base: Path | None) -> str:
    md = (
        MarkdownIt("gfm-like", {"highlight": highlight_code, "typographer": False})
        .use(front_matter_plugin)
        .use(footnote_plugin)
        .use(tasklists_plugin)
        .use(anchors_plugin, max_level=4, permalink=False)
    )
    env: dict = {}
    tokens = md.parse(text, env)
    for tok in tokens:
        for child in tok.children or []:
            if child.type == "image" and child.attrs.get("src"):
                child.attrs["src"] = inline_image(str(child.attrs["src"]), base)
    return md.renderer.render(tokens, md.options, env)


def first_heading(text: str) -> str | None:
    m = re.search(r"^#{1,6}\s+(.+?)\s*#*\s*$", text, re.M)
    return m.group(1).strip() if m else None


CSS = """
body { margin: 0; background: var(--agterm-background, Canvas); color: var(--agterm-foreground, CanvasText);
  font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Helvetica Neue", sans-serif; }
.doc { --fg: var(--agterm-foreground, CanvasText); --muted: color-mix(in srgb, var(--fg) 60%, transparent);
  --line: color-mix(in srgb, var(--fg) 15%, transparent); --panel: color-mix(in srgb, var(--fg) 6%, transparent);
  --accent: var(--agterm-color-4, #3b82f6);
  max-width: 880px; margin: 0 auto; padding: 28px 32px 64px; }
h1, h2, h3, h4, h5, h6 { line-height: 1.25; margin: 1.6em 0 .6em; }
h1 { font-size: 1.9em; padding-bottom: .3em; border-bottom: 1px solid var(--line); margin-top: .4em; }
h2 { font-size: 1.45em; padding-bottom: .25em; border-bottom: 1px solid var(--line); }
h3 { font-size: 1.2em; } h4 { font-size: 1.05em; } h5, h6 { font-size: 1em; color: var(--muted); }
p, ul, ol, table, pre, blockquote { margin: 0 0 1em; }
a { color: var(--accent); text-decoration: none; } a:hover { text-decoration: underline; }
ul, ol { padding-left: 1.6em; } li + li { margin-top: .2em; }
li.task-list-item { list-style: none; margin-left: -1.4em; } li.task-list-item input { margin-right: .5em; }
code, pre { font-family: "SF Mono", ui-monospace, Menlo, monospace; font-size: .88em; }
:not(pre) > code { background: var(--panel); padding: .12em .35em; border-radius: 4px; }
pre { background: var(--panel); border: 1px solid var(--line); border-radius: 6px; padding: 12px 14px;
  overflow-x: auto; line-height: 1.45; }
blockquote { border-left: 3px solid var(--line); padding: .1em 1em; color: var(--muted); }
table { border-collapse: collapse; display: block; overflow-x: auto; }
th, td { border: 1px solid var(--line); padding: 6px 12px; text-align: left; vertical-align: top; }
th { background: var(--panel); }
hr { border: 0; border-top: 1px solid var(--line); margin: 2em 0; }
img, svg { max-width: 100%; }
.mmd .node rect, .mmd .cluster rect { rx: 6px; ry: 6px; }
pre.mmd { background: none; border: 0; padding: 0; text-align: center; font-family: inherit; white-space: pre-wrap; }
pre.mmd-error { text-align: left; border: 1px solid var(--agterm-color-1, red); padding: 12px 14px; }
.mermaid-msg { color: var(--agterm-color-1, red); font-weight: 600; }
/* Mermaid node colors: A:::accent / :::ok / :::bad / :::warn / :::muted (flowchart, state) */
.mmd .accent > :is(rect, polygon, circle, path) { fill: color-mix(in srgb, var(--agterm-color-4, blue) 22%, var(--agterm-background, Canvas)) !important; stroke: var(--agterm-color-4, blue) !important; stroke-width: 2px !important; }
.mmd .ok > :is(rect, polygon, circle, path) { fill: color-mix(in srgb, var(--agterm-color-2, green) 22%, var(--agterm-background, Canvas)) !important; stroke: var(--agterm-color-2, green) !important; stroke-width: 2px !important; }
.mmd .bad > :is(rect, polygon, circle, path) { fill: color-mix(in srgb, var(--agterm-color-1, red) 22%, var(--agterm-background, Canvas)) !important; stroke: var(--agterm-color-1, red) !important; stroke-width: 2px !important; }
.mmd .warn > :is(rect, polygon, circle, path) { fill: color-mix(in srgb, var(--agterm-color-3, olive) 22%, var(--agterm-background, Canvas)) !important; stroke: var(--agterm-color-3, olive) !important; stroke-width: 2px !important; }
.mmd .muted > :is(rect, polygon, circle, path) { fill: transparent !important; stroke-dasharray: 4 3 !important; opacity: .7; }
/* Hand-drawn SVG kit: <svg class="dg" viewBox="..."> — see diagrams.md. Text is centered on (x, y). */
svg.dg { display: block; width: 100%; height: auto; margin: 0 auto 1.2em; font-size: 13px; overflow: visible; }
svg.dg text { fill: currentColor; text-anchor: middle; dominant-baseline: central; }
svg.dg text.l { text-anchor: start; } svg.dg text.r { text-anchor: end; }
svg.dg .t { font-weight: 600; }
svg.dg .s { font-size: 11.5px; fill-opacity: .65; }
svg.dg .lbl { font-size: 11.5px; paint-order: stroke; stroke: var(--agterm-background, Canvas); stroke-width: 5px; stroke-linejoin: round; }
svg.dg .box { fill: currentColor; fill-opacity: .06; stroke: currentColor; stroke-opacity: .45; stroke-width: 1.2; }
svg.dg .box.muted { fill-opacity: .02; stroke-dasharray: 4 3; }
svg.dg .line { fill: none; stroke: currentColor; stroke-opacity: .3; stroke-width: 1; }
svg.dg .edge { fill: none; stroke: currentColor; stroke-opacity: .6; stroke-width: 1.5; marker-end: url(#dg-arrow); }
svg.dg .edge.dash, svg.dg .line.dash { stroke-dasharray: 5 4; }
svg.dg .edge.both { marker-start: url(#dg-arrow-start); }
svg.dg .muted-text { fill-opacity: .5; }
"""

for name, slot in [("accent", 4), ("ok", 2), ("bad", 1), ("warn", 3), ("info", 6)]:
    v = f"var(--agterm-color-{slot})"
    CSS += (f"svg.dg .box.{name} {{ fill: {v}; fill-opacity: .14; stroke: {v}; stroke-opacity: 1; stroke-width: 2; }}\n"
            f"svg.dg .edge.{name} {{ stroke: {v}; stroke-opacity: 1; marker-end: url(#dg-arrow-{name}); }}\n"
            f"svg.dg text.{name}, svg.dg .dot.{name} {{ fill: {v}; fill-opacity: 1; }}\n")

_ARROW = '<marker id="dg-arrow{id}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="{o}"><path d="M0,0 L10,5 L0,10 z" style="fill:{c}"/></marker>'
SVG_DEFS = (
    '<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>'
    + _ARROW.format(id="", o="auto", c="currentColor")
    + _ARROW.format(id="-start", o="auto-start-reverse", c="currentColor")
    + "".join(_ARROW.format(id=f"-{n}", o="auto", c=f"var(--agterm-color-{k})")
              for n, k in [("accent", 4), ("ok", 2), ("bad", 1), ("warn", 3), ("info", 6)])
    + "</defs></svg>"
)
CSS += """
.footnotes { font-size: .9em; color: var(--muted); }
/* Pygments short classes mapped to the terminal ANSI palette */
.hl .c, .hl .c1, .hl .cm, .hl .cs, .hl .ch, .hl .cp, .hl .cpf { color: var(--agterm-color-8, gray); font-style: italic; }
.hl .k, .hl .kd, .hl .kn, .hl .kr, .hl .kt, .hl .kc, .hl .kp, .hl .ow { color: var(--agterm-color-5, purple); }
.hl .s, .hl .s1, .hl .s2, .hl .sb, .hl .sc, .hl .sd, .hl .se, .hl .sh, .hl .si, .hl .sx, .hl .sr, .hl .ss, .hl .sa, .hl .dl { color: var(--agterm-color-2, green); }
.hl .m, .hl .mi, .hl .mf, .hl .mh, .hl .mo, .hl .mb, .hl .il { color: var(--agterm-color-3, olive); }
.hl .nf, .hl .fm, .hl .nc, .hl .nn { color: var(--agterm-color-4, blue); }
.hl .nb, .hl .bp, .hl .nd, .hl .ne, .hl .nt { color: var(--agterm-color-6, teal); }
.hl .na, .hl .nv, .hl .vc, .hl .vg, .hl .vi { color: var(--agterm-color-1, red); }
.hl .gd { color: var(--agterm-color-1, red); } .hl .gi { color: var(--agterm-color-2, green); }
.hl .gh, .hl .gu { color: var(--agterm-color-4, blue); font-weight: bold; }
.hl .err { color: inherit; background: none; }
"""


# Renders every pre.mermaid with a mermaid "base" theme built from the terminal palette.
# Reports back through document.title (the only page field agterm exposes in `tree`):
# DONE_MARK appended on success, ERROR_MARK prefix on the first failing diagram.
MERMAID_INIT = r"""
window.addEventListener('load', async () => {
  const cs = getComputedStyle(document.documentElement);
  const v = (n, f) => cs.getPropertyValue(n).trim() || f;
  const rgb = c => { const d = document.createElement('div'); d.style.color = c; document.body.append(d);
    const m = getComputedStyle(d).color.match(/[\d.]+/g).map(Number); d.remove(); return m.slice(0, 3); };
  const mix = (a, b, t) => { const A = rgb(a), B = rgb(b);
    return '#' + A.map((x, i) => Math.round(x * (1 - t) + B[i] * t).toString(16).padStart(2, '0')).join(''); };
  const bg = v('--agterm-background', '#ffffff'), fg = v('--agterm-foreground', '#1f2328');
  const c = i => v('--agterm-color-' + i, fg);
  const dark = rgb(bg).reduce((s, x) => s + x, 0) / 765 < 0.5;
  const tv = {
    darkMode: dark, background: bg, fontSize: '14px', textColor: fg, titleColor: fg,
    primaryColor: mix(bg, c(4), 0.2), primaryTextColor: fg, primaryBorderColor: c(4),
    secondaryColor: mix(bg, c(6), 0.2), secondaryTextColor: fg, secondaryBorderColor: c(6),
    tertiaryColor: mix(bg, c(5), 0.15), tertiaryTextColor: fg, tertiaryBorderColor: c(5),
    mainBkg: mix(bg, c(4), 0.2), nodeBorder: c(4), lineColor: mix(bg, fg, 0.55),
    clusterBkg: mix(bg, fg, 0.04), clusterBorder: mix(bg, fg, 0.25), edgeLabelBackground: bg,
    noteBkgColor: mix(bg, c(3), 0.2), noteBorderColor: c(3), noteTextColor: fg,
    actorBkg: mix(bg, c(4), 0.2), actorBorder: c(4), actorTextColor: fg, actorLineColor: mix(bg, fg, 0.4),
    signalColor: fg, signalTextColor: fg, labelBoxBkgColor: mix(bg, c(6), 0.2), labelTextColor: fg,
    activationBkgColor: mix(bg, c(6), 0.25), activationBorderColor: c(6),
  };
  [1, 2, 3, 4, 5, 6, 9, 10, 11, 12, 13, 14].forEach((n, i) => { tv['pie' + (i + 1)] = c(n); tv['cScale' + i] = mix(bg, c(n), 0.35); });
  mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: 'base', themeVariables: tv,
    fontFamily: '-apple-system, BlinkMacSystemFont, "Helvetica Neue", sans-serif' });
  const title = document.title; let failed = null;
  const els = [...document.querySelectorAll('pre.mmd')];
  for (const [i, el] of els.entries()) {
    const src = el.textContent;
    try {
      const svg = (await mermaid.render('mmd' + i, src)).svg;
      // Mermaid renders a label it reads as a markdown list ("1. find", "- x") as this text instead of failing.
      const bad = svg.match(/Unsupported markdown: \w+/);
      if (bad) throw new Error(bad[0] + ' (a label starts like a markdown list: write "1\\. x" or reword it)');
      el.innerHTML = svg;
    }
    catch (e) {
      const msg = String(e && e.message || e).split('\n').slice(0, 4).join(' ');
      failed = failed || `diagram ${i + 1}: ${msg}`;
      el.classList.add('mmd-error'); el.textContent = src;
      const p = document.createElement('div'); p.className = 'mermaid-msg'; p.textContent = 'Mermaid: ' + msg;
      el.before(p);
      document.getElementById('dmmd' + i)?.remove();
    }
  }
  document.title = failed ? `""" + ERROR_MARK + r""": ${failed}` : title + '""" + DONE_MARK + r"""';
});
"""


def page(title: str, body: str, mermaid: bool) -> str:
    scripts = f'<script src="mermaid.min.js"></script><script>{MERMAID_INIT}</script>' if mermaid else ""
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        f"<title>{html.escape(title)}</title><style>{CSS}</style></head>"
        f'<body>{SVG_DEFS}<main class="doc">{body}</main>{scripts}</body></html>\n'
    )


def install_mermaid(dest_dir: Path) -> None:
    dest = dest_dir / "mermaid.min.js"
    if not dest.exists() or dest.stat().st_size != MERMAID_JS.stat().st_size:
        shutil.copyfile(MERMAID_JS, dest)


# --- agterm overlay ----------------------------------------------------------


def ctl(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    cmd = ["agtermctl", *args]
    if sock := os.environ.get("AGTERM_SOCKET"):
        cmd += ["--socket", sock]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if check and res.returncode != 0:
        die(f"{' '.join(args[:3])}: {(res.stderr or res.stdout).strip()}")
    return res


def sessions() -> list[dict]:
    args = ["tree", "--json"]
    if win := os.environ.get("AGTERM_WINDOW_ID"):
        args += ["--window", win]
    res = ctl(*args, check=False)
    if res.returncode != 0:
        return []
    tree = json.loads(res.stdout).get("result", {}).get("tree", {})
    return [s for ws in tree.get("workspaces", []) for s in ws.get("sessions", [])]


def session_node(target: str) -> dict | None:
    return next((s for s in sessions() if s["id"].lower().startswith(target.lower())), None)


def overlay_flags(js: bool) -> list[str]:
    # Mermaid needs the page's JavaScript and read access to the cached mermaid.min.js.
    return ["--js", "--cwd", str(CACHE_DIR)] if js else []


def show_in_companion(out: Path, anchor: str, title: str, follow: bool, js: bool) -> str:
    # Same document already shown in its own session: reload it there (reopen if JS need changed).
    for s in sessions():
        if s["id"].lower().startswith(anchor.lower()):
            continue
        pages = [p for p in s.get("htmlOverlays", []) if p.get("file") == str(out)]
        if not pages:
            continue
        if bool(pages[0].get("javascript")) == js:
            ctl("session", "overlay", "reload", "--target", s["id"])
            if follow:
                ctl("session", "select", "--target", s["id"], check=False)
            return "reloaded"
        ctl("session", "overlay", "close", "--target", s["id"], check=False)  # its session closes itself
    # A page companion: its own sidebar row under the anchor, closed together with its page.
    res = ctl("session", "overlay", "open", "--html", str(out), "--companion", "--target", anchor,
              *overlay_flags(js), "--json")
    sid = json.loads(res.stdout).get("result", {}).get("id")
    if sid:
        ctl("session", "rename", f"view: {title}"[:40], "--target", sid, check=False)
        parent = session_node(anchor) or {}
        if parent.get("flagged"):  # a flagged session's page joins the flagged set, right after it
            ctl("session", "flag", "on", "--target", sid, check=False)
            ctl("session", "flag", "move", "--target", sid, "--after", parent["id"], check=False)
        if follow:
            ctl("session", "select", "--target", sid, check=False)
    return "opened in companion session"


def show(out: Path, target: str, size: int | None, pane: str | None, follow: bool, js: bool) -> str:
    node = session_node(target) or {}
    pages = node.get("htmlOverlays", [])
    mine = [p for p in pages if p.get("file") == str(out) and p.get("pane") == pane]
    if mine and bool(mine[0].get("javascript")) == js:
        args = ["session", "overlay", "reload", "--target", target]
        if pane:
            args += ["--pane", pane]
        ctl(*args)
        if follow:
            ctl("session", "select", "--target", target, check=False)
        return "reloaded"
    # Replace another page we can safely close in the same slot; never touch a running program.
    other = [p for p in pages if p.get("pane") == pane and (p.get("file") or "").startswith(str(CACHE_DIR))]
    if other:
        args = ["session", "overlay", "close", "--target", target]
        if pane:
            args += ["--pane", pane]
        ctl(*args, check=False)
    args = ["session", "overlay", "open", "--html", str(out), "--target", target, *overlay_flags(js)]
    if size:
        args += ["--size-percent", str(size)]
    if pane:
        args += ["--pane", pane]
    if follow:
        args.append("--follow")
    ctl(*args)
    return "opened"


def check_diagrams(out: Path, timeout: float = 10.0) -> int:
    """Wait for the page to report its Mermaid result through the title. Exit code for main()."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for s in sessions():
            for p in s.get("htmlOverlays", []):
                if p.get("file") != str(out):
                    continue
                t = p.get("title") or ""
                if t.startswith(ERROR_MARK):
                    print(f"view: {t}", file=sys.stderr)
                    return 3
                if t.endswith(DONE_MARK):
                    print("diagrams: ok")
                    return 0
        time.sleep(0.4)
    print("diagrams: not checked (page did not report in time; a background session may not render until shown)")
    return 0


CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def screenshot(out: Path, png: Path, width: int = 980, height: int = 2400, dark: bool = False) -> None:
    """Render the page in headless Chrome so the agent can look at it (Read the PNG).

    Chrome has no agterm theme, so the page falls back to a light or dark preset palette.
    """
    if not Path(CHROME).exists():
        die("--shot needs Google Chrome in /Applications")
    preset = DARK_PRESET if dark else LIGHT_PRESET
    tmp = out.with_name(out.stem + ".shot.html")
    tmp.write_text(out.read_text(encoding="utf-8").replace("<style>", f"<style>:root{{{preset}}}", 1), encoding="utf-8")
    subprocess.run(
        [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--allow-file-access-from-files",
         "--virtual-time-budget=4000", f"--window-size={width},{height}", f"--screenshot={png}", tmp.as_uri()],
        capture_output=True, timeout=60,
    )
    tmp.unlink(missing_ok=True)
    if not png.exists():
        die("screenshot failed")
    print(f"screenshot: {png}")


# Stand-ins for the --agterm-* variables when the page is rendered outside agterm (--shot only).
LIGHT_PRESET = ("--agterm-background:#ffffff;--agterm-foreground:#1f2328;--agterm-color-1:#cf222e;"
                "--agterm-color-2:#1a7f37;--agterm-color-3:#9a6700;--agterm-color-4:#0969da;--agterm-color-5:#8250df;"
                "--agterm-color-6:#1b7c83;--agterm-color-8:#6e7781;--agterm-color-9:#a40e26;--agterm-color-10:#2da44e;"
                "--agterm-color-11:#bf8700;--agterm-color-12:#218bff;--agterm-color-13:#a475f9;--agterm-color-14:#3192aa;")
DARK_PRESET = ("--agterm-background:#1e1f29;--agterm-foreground:#e6e6e6;--agterm-color-1:#ff6b6b;"
               "--agterm-color-2:#69db7c;--agterm-color-3:#ffd43b;--agterm-color-4:#74c0fc;--agterm-color-5:#da77f2;"
               "--agterm-color-6:#66d9e8;--agterm-color-8:#868e96;--agterm-color-9:#ff8787;--agterm-color-10:#8ce99a;"
               "--agterm-color-11:#ffe066;--agterm-color-12:#a5d8ff;--agterm-color-13:#e599f7;--agterm-color-14:#99e9f2;"
               "color-scheme:dark;")


def main() -> None:
    ap = argparse.ArgumentParser(description="Show markdown in an agterm HTML overlay.")
    ap.add_argument("file", nargs="?", default="-", help="markdown file, or - for stdin")
    ap.add_argument("--title", help="page title (default: first heading or file name)")
    ap.add_argument("--size", type=int, metavar="N", help="floating panel, N%% of the session (1-100)")
    ap.add_argument("--pane", choices=["left", "right"], help="cover one split pane only")
    ap.add_argument("--no-follow", action="store_true", help="open quietly, do not switch the user")
    ap.add_argument("--here", action="store_true", help="overlay over this session instead of a new session")
    ap.add_argument("--new-session", action="store_true", help=argparse.SUPPRESS)  # the default; kept for old calls
    ap.add_argument("--target", default=os.environ.get("AGTERM_SESSION_ID"), help="session id (default: own)")
    ap.add_argument("-o", "--output", help="write HTML here and do not open an overlay")
    ap.add_argument("--shot", metavar="PNG", help="also save a headless-Chrome screenshot of the page")
    ap.add_argument("--shot-dark", action="store_true", help="screenshot with a dark palette")
    ap.add_argument("--shot-only", action="store_true", help="with --shot: do not open an overlay")
    a = ap.parse_args()

    explicit_target = any(x == "--target" or x.startswith("--target=") for x in sys.argv[1:])
    here = a.here or a.size is not None or bool(a.pane) or explicit_target
    if a.new_session and here:
        die("--new-session cannot be combined with --here, --size, --pane or --target")
    if a.size is not None and a.pane:
        die("--size and --pane cannot be combined (a pane overlay is always full-pane)")
    if a.shot_only and not a.shot:
        die("--shot-only needs --shot PNG")

    if a.file == "-":
        text, base, key = sys.stdin.read(), Path.cwd(), "stdin"
        stem = "stdin"
    else:
        src = Path(a.file).expanduser().resolve()
        if not src.is_file():
            die(f"no such file: {a.file}")
        text, base, key, stem = src.read_text(encoding="utf-8", errors="replace"), src.parent, str(src), src.stem
    title = a.title or first_heading(text) or (stem if stem != "stdin" else "markdown")
    body = render(text, base)
    js = 'class="mmd"' in body
    doc = page(title, body, js)

    if a.output:
        dest = Path(a.output)
        dest.write_text(doc, encoding="utf-8")
        if js:
            install_mermaid(dest.resolve().parent)
        print(a.output)
        return

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if js:
        install_mermaid(CACHE_DIR)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", stem)[:60] or "doc"
    out = CACHE_DIR / f"{safe}-{hashlib.sha1(key.encode()).hexdigest()[:8]}.html"
    out.write_text(doc, encoding="utf-8")

    if a.shot:
        screenshot(out, Path(a.shot).expanduser().resolve(), dark=a.shot_dark)
        if a.shot_only:
            return
    if os.environ.get("AGTERM_ENABLED") != "1" or not a.target:
        subprocess.run(["open", str(out)])
        print(f"not in agterm, opened in browser: {out}")
        return
    if not here:
        print(f"{show_in_companion(out, a.target, title, not a.no_follow, js)}: {out}")
    else:
        print(f"{show(out, a.target, a.size, a.pane, not a.no_follow, js)}: {out}")
    if js:
        sys.exit(check_diagrams(out))


if __name__ == "__main__":
    main()
