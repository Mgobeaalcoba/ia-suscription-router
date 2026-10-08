#!/usr/bin/env python3
"""Screenshots of `ia-router ui` (for the README, the guide and the website), taken from the REAL page in headless Chrome.

    python3 tools/render_ui_screenshots.py [output_folder]        (default docs/img)

What it does: starts `ia-router ui` on a temporary state folder (only your models_seen.json is copied into it, so the scores use the real metrics) and from an EMPTY working folder (a model that explores its folder would
leak the repo into the answer), drives the page through the Chrome DevTools Protocol and saves PNGs.

It runs REAL models: one chat answer and one comparison (three tiny prompts: a pinch of quota). Everything else spends nothing: the real
`memory` connector (needs Node's npx and the network, no model quota), the routing preview, the settings, the scores.
For maintainers only; it is not part of the package. Standard library only: a minimal WebSocket client talks to Chrome.
"""
import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
WIDTH, HEIGHT = 1280, 820


class Cdp:
    """Just enough of a WebSocket client (RFC 6455) and of the DevTools protocol to evaluate scripts and take screenshots."""

    def __init__(self, url: str) -> None:
        u = urlparse(url)
        self.sock = socket.create_connection((u.hostname, u.port), timeout=300)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            head += self.sock.recv(1)
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise RuntimeError("Chrome refused the WebSocket: " + head.decode(errors="replace")[:200])
        self.n = 0

    def _read(self, size: int) -> bytes:
        data = b""
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            if not chunk:
                raise RuntimeError("Chrome closed the connection")
            data += chunk
        return data

    def _frame(self) -> bytes:
        message = b""
        while True:
            b1, b2 = self._read(2)
            size = b2 & 0x7F
            if size == 126:
                size = struct.unpack(">H", self._read(2))[0]
            elif size == 127:
                size = struct.unpack(">Q", self._read(8))[0]
            payload = self._read(size)
            if b1 & 0x0F in (1, 0, 2):
                message += payload
            if b1 & 0x80:
                return message

    def call(self, method: str, **params):
        self.n += 1
        payload = json.dumps({"id": self.n, "method": method, "params": params}).encode()
        mask = os.urandom(4)
        length = len(payload)
        head = bytes([0x81]) + (bytes([0x80 | length]) if length < 126 else bytes([0x80 | 126]) + struct.pack(">H", length) if length < 65536 else bytes([0x80 | 127]) + struct.pack(">Q", length))
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))
        while True:
            msg = json.loads(self._frame())
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def js(self, expression: str):
        res = self.call("Runtime.evaluate", expression=expression, awaitPromise=True, returnByValue=True)
        if res.get("exceptionDetails"):
            raise RuntimeError("page script failed: " + json.dumps(res["exceptionDetails"])[:400])
        return res.get("result", {}).get("value")

    def wait(self, condition: str, timeout: float = 30.0, what: str = "") -> None:
        end = time.time() + timeout
        while time.time() < end:
            if self.js(f"Boolean({condition})"):
                return
            time.sleep(0.3)
        raise RuntimeError(f"timed out waiting for {what or condition}")

    def shot(self, path: Path) -> None:
        time.sleep(0.4)   # let transitions settle
        data = self.call("Page.captureScreenshot", format="png")["data"]
        path.write_bytes(base64.b64decode(data))
        print(f"  {path} ({path.stat().st_size // 1024} KB)")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    out = Path(next((a for a in sys.argv[1:] if not a.startswith("-")), ROOT / "docs" / "img"))
    out.mkdir(parents=True, exist_ok=True)
    if not Path(CHROME).exists():
        print("Chrome not found at " + CHROME, file=sys.stderr)
        return 1
    tmp = Path(tempfile.mkdtemp(prefix="ia-router-ui-shots-"))
    (tmp / "workdir").mkdir()
    (tmp / "home").mkdir()
    seen = Path(os.environ.get("ROUTER_HOME") or Path.home() / ".ia-router") / "models_seen.json"
    if seen.exists():   # which real model each CLI uses (no prompts, nothing personal): lets the scores and the routing use the real metrics from the start
        shutil.copy(seen, tmp / "home" / "models_seen.json")
    else:
        print("note: no models_seen.json in your state folder, so the scores will show the models.json estimate (use ia-router once first)")
    env = dict(os.environ, ROUTER_HOME=str(tmp / "home"), PYTHONUNBUFFERED="1")
    server = subprocess.Popen([sys.executable, str(ROOT / "cli.py"), "ui", "--no-open"], cwd=tmp / "workdir", env=env, stdout=subprocess.PIPE, text=True)
    port = free_port()
    chrome = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu", f"--remote-debugging-port={port}", f"--user-data-dir={tmp / 'chrome'}", "--hide-scrollbars",
                               "--no-first-run", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        url = next(l.split("ia-router UI: ")[1].strip() for l in server.stdout if "ia-router UI: " in l)
        for _ in range(50):
            try:
                targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json"))
                page = next(t for t in targets if t["type"] == "page")
                break
            except (OSError, StopIteration):
                time.sleep(0.2)
        cdp = Cdp(page["webSocketDebuggerUrl"])
        cdp.call("Emulation.setDeviceMetricsOverride", width=WIDTH, height=HEIGHT, deviceScaleFactor=2, mobile=False)
        cdp.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": "dark"}])
        cdp.call("Page.navigate", url=url)
        cdp.wait('document.querySelector(".hero") && document.querySelector("#models, #sessions") && $("ver").textContent', what="the page")
        time.sleep(0.5)

        def ask(text: str, compare: bool = False) -> None:
            cdp.js(f'$("compare").checked = {str(compare).lower()}; $("compare").dispatchEvent(new Event("change")); $("task").value = {json.dumps(text)}; $("task").dispatchEvent(new Event("input")); $("send").click(); 0')
            cdp.wait("!busy", 240, "the answer")
            cdp.wait('document.querySelector(".msg:last-child .actions, .msg:last-child .cmp, .msg:last-child .err")', 10)

        print("1. welcome")
        cdp.shot(out / "ia-router-ui.png")

        print("2. a real answer (one tiny prompt)")
        ask("List three differences between a mutex and a semaphore, one short line each.")
        cdp.js('document.querySelector("details.why").open = true; $("task").blur(); 0')
        cdp.shot(out / "ia-router-ui-chat.png")

        print("3. compare (two tiny prompts)")
        cdp.js("newChat(); 0")
        cdp.wait('document.querySelector(".hero")', 5)
        ask("In one sentence, what is DNS?", compare=True)
        cdp.js('$("compare").checked = false; $("compare").dispatchEvent(new Event("change")); $("task").blur(); 0')
        cdp.shot(out / "ia-router-ui-compare.png")

        print("4. routing preview (spends nothing)")
        cdp.js("newChat(); 0")
        cdp.wait('document.querySelector(".hero")', 5)
        cdp.js('$("task").value = "Refactor this SQL query to use a CTE and explain the change"; $("task").dispatchEvent(new Event("input")); $("why").click(); 0')
        cdp.wait('document.querySelector("#route-out .route .mono")', 10, "the routing preview")
        cdp.shot(out / "ia-router-ui-routing.png")
        cdp.js('$("route-out").innerHTML = ""; $("task").value = ""; $("task").dispatchEvent(new Event("input")); 0')

        print("5. connectors: the real memory connector, tested (no model quota)")
        cdp.js('$("open-conn").click(); 0')
        cdp.wait('document.querySelector("#conn[open]")', 5)
        cdp.js('$("c-name").value = "memory"; $("c-template").value = "memory"; $("c-template").dispatchEvent(new Event("change")); $("c-add").click(); 0')
        cdp.wait('document.querySelector("#conn-list .crow")', 10, "the connector to be added")
        cdp.js('document.querySelector("[data-cact=test]").click(); 0')
        cdp.wait('document.querySelector(".tres.ok")', 120, "the connector test (needs npx and the network)")
        cdp.shot(out / "ia-router-ui-connectors.png")

        print("6. connectors: the confirmation before a free command is saved (nothing is saved)")
        cdp.js('document.querySelector("#c-tabs [data-mode=command]").click(); $("c-name").value = "gmail"; $("c-command").value = "npx -y @your-org/gmail-mcp-server"; '
               '$("c-env").value = "GMAIL_TOKEN=${GMAIL_TOKEN}"; $("c-add").click(); document.querySelector(".confirm").scrollIntoView({block: "center"}); 0')
        cdp.wait('document.querySelector("[data-cact=confirm-add]")', 5)
        cdp.shot(out / "ia-router-ui-confirm.png")
        cdp.js('document.querySelector("[data-cact=cancel-add]").click(); $("conn").close(); 0')

        print("7. settings: status and usage, scores")
        cdp.js('$("open-status").click(); 0')
        cdp.wait('document.querySelector("#status[open] #pane-status .model")', 5)
        cdp.shot(out / "ia-router-ui-settings.png")
        cdp.js('document.querySelector("#s-tabs [data-tab=scores]").click(); 0')
        cdp.wait('$("sc-out").textContent.length > 20', 10)
        cdp.js('$("sc-cat").value = "coding"; $("sc-cat").dispatchEvent(new Event("change")); 0')
        cdp.wait('$("sc-out").textContent.includes("coding")', 10)
        cdp.shot(out / "ia-router-ui-scores.png")
    finally:
        chrome.terminate()
        server.terminate()
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
