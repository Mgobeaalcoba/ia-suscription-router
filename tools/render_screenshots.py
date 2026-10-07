#!/usr/bin/env python3
"""Generates PNG screenshots of the terminal (for the site and the README) from the REAL output of ia-router: the header, the input
box, the scores, the routing and the priority questions. It converts ANSI codes to HTML and uses headless Chrome to render
with real fonts. For maintainers only; it is not part of the package.

    python3 tools/render_screenshots.py [output_folder]      (default docs/img)

It uses your real state (~/.ia-router): the screenshots show your models and whatever metrics you have. It spends no quota.
"""
import html
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ia_router import adapters, banner, core, editor, envfile, metrics, priorities, scoring, select  # noqa: E402
from ia_router import __version__  # noqa: E402

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
BASIC = ["#1f2430", "#ff6b81", "#7ee787", "#f2cc60", "#7aa2f7", "#d2a8ff", "#56d4dd", "#c9d1d9",
         "#6e7681", "#ff8fa3", "#9ef0a6", "#ffe08a", "#9bb8ff", "#e2c5ff", "#7ee8f0", "#ffffff"]
SGR = re.compile(r"\033\[([0-9;]*)m")
OSC = re.compile(r"\033\][^\033\007]*(?:\033\\|\007)")


def xterm256(n: int) -> str:
    if n < 16:
        return BASIC[n]
    if n >= 232:
        v = 8 + (n - 232) * 10
        return f"#{v:02x}{v:02x}{v:02x}"
    n -= 16
    steps = [0, 95, 135, 175, 215, 255]
    return "#%02x%02x%02x" % (steps[n // 36], steps[(n // 6) % 6], steps[n % 6])


def ansi_to_html(text: str) -> str:
    """One line (or several) with SGR codes -> HTML with <span style>. Ignores OSC 8 hyperlinks."""
    text = OSC.sub("", text)
    out, pos = [], 0
    st = {"fg": None, "bg": None, "b": False, "d": False, "i": False, "u": False, "s": False}

    def style() -> str:
        css = []
        fg = st["fg"]
        if st["d"] and not fg:
            css.append("opacity:.55")
        elif st["d"]:
            css.append("opacity:.7")
        if fg:
            css.append(f"color:{fg}")
        if st["bg"]:
            css.append(f"background:{st['bg']}")
        if st["b"]:
            css.append("font-weight:700")
        if st["i"]:
            css.append("font-style:italic")
        deco = [d for k, d in (("u", "underline"), ("s", "line-through")) if st[k]]
        if deco:
            css.append("text-decoration:" + " ".join(deco))
        return ";".join(css)

    for m in SGR.finditer(text):
        chunk = text[pos:m.start()]
        if chunk:
            out.append(f'<span style="{style()}">{html.escape(chunk)}</span>' if style() else html.escape(chunk))
        pos = m.end()
        codes = [int(c) if c else 0 for c in m.group(1).split(";")]
        i = 0
        while i < len(codes):
            c = codes[i]
            if c == 0:
                st.update(fg=None, bg=None, b=False, d=False, i=False, u=False, s=False)
            elif c == 1: st["b"] = True
            elif c == 2: st["d"] = True
            elif c == 3: st["i"] = True
            elif c == 4: st["u"] = True
            elif c == 9: st["s"] = True
            elif c == 22: st["b"] = st["d"] = False
            elif c == 23: st["i"] = False
            elif c == 24: st["u"] = False
            elif c == 29: st["s"] = False
            elif 30 <= c <= 37: st["fg"] = BASIC[c - 30]
            elif 90 <= c <= 97: st["fg"] = BASIC[c - 90 + 8]
            elif c == 39: st["fg"] = None
            elif 40 <= c <= 47: st["bg"] = BASIC[c - 40]
            elif 100 <= c <= 107: st["bg"] = BASIC[c - 100 + 8]
            elif c == 49: st["bg"] = None
            elif c in (38, 48):
                key = "fg" if c == 38 else "bg"
                if i + 1 < len(codes) and codes[i + 1] == 2 and i + 4 < len(codes):
                    st[key] = "#%02x%02x%02x" % tuple(codes[i + 2:i + 5])
                    i += 4
                elif i + 1 < len(codes) and codes[i + 1] == 5 and i + 2 < len(codes):
                    st[key] = xterm256(codes[i + 2])
                    i += 2
            i += 1
    rest = text[pos:]
    if rest:
        out.append(f'<span style="{style()}">{html.escape(rest)}</span>' if style() else html.escape(rest))
    return "".join(out)


PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;background:#0f192b}
.wrap{padding:28px;display:inline-block;background:linear-gradient(135deg,#0b1220,#101a2e)}
.win{width:%(w)dpx;border-radius:12px;overflow:hidden;background:#0d1117;box-shadow:0 24px 60px rgba(0,0,0,.55),0 0 0 1px rgba(255,255,255,.08)}
.bar{height:36px;background:#161b22;display:flex;align-items:center;padding:0 14px;gap:8px;border-bottom:1px solid rgba(255,255,255,.06)}
.dot{width:12px;height:12px;border-radius:50%%}
.title{flex:1;text-align:center;color:#8b949e;font:13px -apple-system,Helvetica,sans-serif;margin-right:44px}
pre{margin:0;padding:18px 22px 22px;color:#c9d1d9;font:15px/1.38 "SF Mono",Menlo,Monaco,Consolas,monospace;white-space:pre;tab-size:4}
</style></head><body><div class="wrap"><div class="win"><div class="bar"><span class="dot" style="background:#ff5f57"></span><span class="dot" style="background:#febc2e"></span><span class="dot" style="background:#28c840"></span><span class="title">%(title)s</span></div><pre>%(body)s</pre></div></div></body></html>"""


def wrap_lines(lines, cols):
    """Splits the plain-text lines that exceed the width (the ones with ANSI codes are left alone: they are already measured boxes)."""
    import textwrap
    out = []
    for l in lines:
        out.extend(textwrap.wrap(l, cols - 2, subsequent_indent="  ") or [""] if "\033" not in l and len(l) > cols - 2 else [l])
    return out


def shot(lines, out: Path, title: str, cols: int = 100) -> None:
    lines = wrap_lines(lines, cols)
    body = "\n".join(ansi_to_html(l) for l in lines)
    char_w = 9.0   # cell width of SF Mono 15px
    w = int(cols * char_w + 44)
    h = int(len(lines) * 15 * 1.38 + 36 + 18 + 22 + 56)
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
        f.write(PAGE % {"w": w, "title": html.escape(title), "body": body})
        page = f.name
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=2", f"--window-size={w + 56},{h}",
                    f"--screenshot={out}", f"file://{page}"], check=True, capture_output=True, timeout=60)
    os.unlink(page)
    print("OK", out, f"{out.stat().st_size // 1024} KB")


def cli(*args) -> str:
    return subprocess.run([sys.executable, str(ROOT / "cli.py"), *args], capture_output=True, text=True).stdout.rstrip()


def main() -> int:
    envfile.load()
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "img"
    out.mkdir(parents=True, exist_ok=True)
    cfg = core.load_config()
    names = list(cfg["models"])
    ok = {n: adapters.is_available(n, cfg["models"][n]) for n in names}
    COLS = 100

    # 1) header + input box
    head = banner.render(__version__, metrics.status_line(), names, ok, "~/projects/my-app", color=True, width=COLS).split("\n")
    st = editor.State(commands=[], cwd=None)
    frame = editor.render_frame(st, COLS, 40, "auto", "metrics " + (metrics.active().get("arena_at") or "?")[:10], True).lines
    shot(head + frame, out / "ia-router-header.png", "ia-router", COLS)

    # 2) score with accuracy, speed and cost
    prompt = editor.echo_lines("/scores", [], [], COLS, True)
    shot(prompt + [""] + scoring.render_table(cfg).split("\n"), out / "ia-router-scores.png", "ia-router · /scores", COLS)

    # 3) explained routing
    task = "Fix this bug in my Python function, the test fails"
    shot(editor.echo_lines(task, [], [], COLS, True) + ["", "\033[2m… routing\033[0m"] + core.format_ranking(core.route(task, cfg)).split("\n"),
         out / "ia-router-ruteo.png", "ia-router · a task", COLS)

    # 4) priority questions
    keys = ["precision", "balanced", "speed", "cost"]
    opts = [select.Option(scoring.PRESET_LABELS[k], priorities.OPTION_TEXT[k]) for k in keys]
    lines = editor.echo_lines("/priorities", [], [], COLS, True) + select.render_lines("For code and debugging, what do you prioritize?", opts, 2, "", True, "1/6", COLS)
    shot(lines, out / "ia-router-prioridades.png", "ia-router · /priorities", COLS)

    # 5) connectors: REAL output of the official MCP servers (filesystem and memory) in a throwaway state folder, never your real registry
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as docs:
        env = dict(os.environ, ROUTER_HOME=home, NO_COLOR="1")

        def run(*args):
            r = subprocess.run([sys.executable, str(ROOT / "cli.py"), *args], capture_output=True, text=True, env=env, timeout=240)
            text = (r.stdout + r.stderr).rstrip().replace(docs, "~/Documents")
            return re.sub(r"/(?:private/)?var/folders/\S+", "~/Documents", text)  # `list` shortens long paths, so the temp folder may be cut

        steps = [("connectors", "add", "files", "--", "npx", "-y", "@modelcontextprotocol/server-filesystem", docs),
                 ("connectors", "add", "memory", "--", "npx", "-y", "@modelcontextprotocol/server-memory"),
                 ("connectors", "test"), ("connectors", "list")]
        lines = []
        for args in steps:
            shown = " ".join(a if a != docs else "~/Documents" for a in args)
            lines += ["\033[1;38;2;200;90;160m$\033[0m ia-router " + shown] + run(*args).split("\n") + [""]
        shot(lines[:-1], out / "ia-router-connectors.png", "ia-router · connectors", COLS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
