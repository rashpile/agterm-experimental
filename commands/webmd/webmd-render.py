#!/usr/bin/env python3
"""Render a markdown file to a single self-contained HTML page.

    webmd-render.py <in.md> <out.html> [title]

Everything is inlined: the markdown is embedded in the page and converted by a
small renderer that runs in the browser. No network, no CDN, no build step —
which matters because the page is displayed by terminal-browser inside a
terminal pane, where a blocked request just means a page that never finishes.

This is the stopgap for the Go `webmd` (see repos/webmd/task.md). When that
exists, this script and its shell wrapper go away.
"""

from __future__ import annotations

import pathlib
import sys

TEMPLATE = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>__TITLE__</title>
<style>
:root { --bg:#fbfbfa; --fg:#2c2c2c; --muted:#8a8a8a; --line:#e3e3e0;
        --code-bg:#f2f2ef; --accent:#3a6ea5; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#1c1c1c; --fg:#d8d8d5; --muted:#8a8a8a; --line:#333;
          --code-bg:#262626; --accent:#7aa7d4; }
}
* { box-sizing: border-box; }
/* Base size for the whole page — everything else is relative, so this one number
   scales the document. A terminal pane sits further from the eye than an editor
   window, so it runs a little larger than a web default. */
body { margin:0; background:var(--bg); color:var(--fg);
  font:18px/1.75 -apple-system, "Helvetica Neue", sans-serif; }
main { max-width: 900px; margin:0 auto; padding: 56px 40px 120px; }
h1,h2,h3,h4 { line-height:1.3; margin:1.8em 0 .6em; font-weight:650; }
h1 { font-size:2em; margin-top:0; }
h2 { font-size:1.45em; padding-bottom:.3em; border-bottom:1px solid var(--line); }
h3 { font-size:1.15em; }
p, li { margin:.7em 0; }
a { color:var(--accent); }
code { font:15px/1.5 ui-monospace, "JetBrains Mono", Menlo, monospace;
  background:var(--code-bg); padding:.15em .35em; border-radius:3px; }
pre { background:var(--code-bg); padding:14px 16px; border-radius:6px;
  overflow-x:auto; border:1px solid var(--line); }
pre code { background:none; padding:0; }
blockquote { margin:1em 0; padding:.1em 1em; border-left:3px solid var(--line);
  color:var(--muted); }
table { border-collapse:collapse; width:100%; margin:1.2em 0; font-size:.93em;
  display:block; overflow-x:auto; }
th,td { border:1px solid var(--line); padding:8px 11px; text-align:left;
  vertical-align:top; }
th { background:var(--code-bg); font-weight:600; }
hr { border:none; border-top:1px solid var(--line); margin:2.2em 0; }
ul,ol { padding-left:1.4em; }
footer { max-width:860px; margin:0 auto; padding:0 40px 48px;
  color:var(--muted); font-size:13.5px; border-top:1px solid var(--line);
  padding-top:14px; }
footer .hint { float:right; }
/* Copy feedback. The pane draws no cursor and no menu bar, so without this there
   is nothing on screen to say the keypress registered. */
#toast { position:fixed; left:50%; bottom:28px; padding:8px 15px; border-radius:6px;
  background:var(--fg); color:var(--bg); font-size:14px; opacity:0;
  pointer-events:none; transform:translate(-50%, 8px);
  transition:opacity .15s ease, transform .15s ease; }
#toast.show { opacity:.93; transform:translate(-50%, 0); }
</style></head>
<body><main id="doc"></main>
<footer>__SOURCE__<span class="hint">alt+c copies</span></footer>
<script type="text/markdown" id="src">__MD__</script>
<script>
const esc = s => s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
const SENT = "%%CODE%%";

// Code spans are pulled out before anything else touches the text, so their
// contents never get treated as emphasis or links, then put back at the end.
function inline(t) {
  const code = [];
  t = t.replace(/`([^`]+)`/g, (_, c) => SENT + (code.push(c) - 1) + SENT);
  t = esc(t);
  t = t.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2">$1</a>');
  t = t.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  t = t.replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>");
  return t.replace(new RegExp(SENT + "(\\d+)" + SENT, "g"),
                   (_, i) => "<code>" + esc(code[i]) + "</code>");
}

function render(md) {
  const lines = md.split("\n"), out = [];
  let i = 0;
  const isTableSep = s => /^\|?[\s:|-]+\|[\s:|-]*$/.test(s) && s.includes("-");
  const cells = s => s.replace(/^\||\|$/g, "").split("|").map(c => c.trim());

  while (i < lines.length) {
    const line = lines[i];

    if (line.startsWith("```")) {
      const lang = line.slice(3).trim(); i++;
      const buf = [];
      while (i < lines.length && !lines[i].startsWith("```")) buf.push(lines[i++]);
      i++;
      out.push("<pre><code class=\"lang-" + esc(lang) + "\">" + esc(buf.join("\n")) + "</code></pre>");
      continue;
    }
    if (/^#{1,6} /.test(line)) {
      const n = line.match(/^#+/)[0].length;
      out.push("<h" + n + ">" + inline(line.slice(n + 1)) + "</h" + n + ">"); i++; continue;
    }
    if (/^(---|\*\*\*)\s*$/.test(line)) { out.push("<hr>"); i++; continue; }

    if (line.includes("|") && isTableSep(lines[i + 1] || "")) {
      const head = cells(line); i += 2;
      const rows = [];
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) rows.push(cells(lines[i++]));
      out.push("<table><thead><tr>" + head.map(h => "<th>" + inline(h) + "</th>").join("") +
        "</tr></thead><tbody>" + rows.map(r => "<tr>" +
        r.map(c => "<td>" + inline(c) + "</td>").join("") + "</tr>").join("") + "</tbody></table>");
      continue;
    }
    if (/^\s*[-*] /.test(line) || /^\s*\d+\. /.test(line)) {
      const ordered = /^\s*\d+\. /.test(line);
      const items = [];
      while (i < lines.length && (/^\s*[-*] /.test(lines[i]) || /^\s*\d+\. /.test(lines[i]) ||
             (/^\s{2,}\S/.test(lines[i]) && items.length))) {
        const m = lines[i].match(/^\s*(?:[-*]|\d+\.)\s+(.*)$/);
        if (m) items.push(m[1]); else items[items.length - 1] += " " + lines[i].trim();
        i++;
      }
      const tag = ordered ? "ol" : "ul";
      out.push("<" + tag + ">" + items.map(t => "<li>" + inline(t) + "</li>").join("") + "</" + tag + ">");
      continue;
    }
    if (line.startsWith("> ")) {
      const buf = [];
      while (i < lines.length && lines[i].startsWith(">")) buf.push(lines[i++].replace(/^>\s?/, ""));
      out.push("<blockquote>" + inline(buf.join(" ")) + "</blockquote>");
      continue;
    }
    if (!line.trim()) { i++; continue; }

    const para = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,6} |```|> |\s*[-*] |\s*\d+\. )/.test(lines[i])
           && !(lines[i].includes("|") && isTableSep(lines[i + 1] || ""))) para.push(lines[i++]);
    out.push("<p>" + inline(para.join(" ")) + "</p>");
  }
  return out.join("\n");
}

document.getElementById("doc").innerHTML = render(document.getElementById("src").textContent);

// alt+c copies. cmd+c cannot work here: agterm claims it for its own Edit > Copy,
// which acts on the terminal's text selection, and this pane holds a rendered image
// rather than text. Catching the key in the page sidesteps agterm entirely. Alt
// reaches the page as a real modifier, and the Electron layer grants
// clipboard-sanitized-write, so writeText needs no permission prompt.
function toast(msg) {
  let el = document.getElementById("toast");
  if (!el) { el = document.createElement("div"); el.id = "toast"; document.body.appendChild(el); }
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.remove("show"), 1500);
}

// The source rides in a <script> block with its closing tags escaped, so undo that
// before handing the text to the clipboard.
function markdownSource() {
  return document.getElementById("src").textContent.replace(/<\\\/script>/g, "<\/script>");
}

addEventListener("keydown", e => {
  if (!e.altKey || e.ctrlKey || e.metaKey) return;
  // Option may compose a character instead of yielding "c", so accept the physical
  // key too.
  if ((e.key || "").toLowerCase() !== "c" && e.code !== "KeyC") return;
  e.preventDefault();
  const selection = String(getSelection() || "");
  const selected = selection.trim().length > 0;
  navigator.clipboard.writeText(selected ? selection : markdownSource()).then(
    () => toast(selected ? "selection copied" : "markdown copied"),
    err => toast("copy failed: " + err.message)
  );
});

// terminal-browser hands the pane's palette to preloaded pages. Not available on a
// plain open, so the CSS media query stays the fallback.
const tb = globalThis.terminalBrowser;
if (tb && tb.theme) {
  const apply = t => { if (!t) return;
    document.documentElement.style.setProperty("--bg", "rgb(" + t.background.join(",") + ")");
    document.documentElement.style.setProperty("--fg", "rgb(" + t.foreground.join(",") + ")"); };
  apply(tb.theme()); tb.onTheme && tb.onTheme(apply);
}
</script></body></html>
"""


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2

    src = pathlib.Path(sys.argv[1])
    dst = pathlib.Path(sys.argv[2])
    title = sys.argv[3] if len(sys.argv) > 3 else src.name

    md = src.read_text(encoding="utf-8")
    # The markdown rides inside a <script> block, so a literal closing tag in the
    # text would end it early.
    body = md.replace("</script>", "<\\/script>")

    dst.write_text(
        TEMPLATE.replace("__TITLE__", esc(title))
        .replace("__SOURCE__", esc(str(src)))
        .replace("__MD__", body),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
