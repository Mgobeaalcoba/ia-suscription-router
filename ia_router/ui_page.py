"""The single page served by `ia-router ui` (HTML, CSS and JS inline: no build step, no external requests, no dependencies).

Laid out like a modern chat app: conversations grouped by date on the left, a welcome screen with suggestions, a rounded composer with the
model and connector pickers under it, user bubbles, and copy / regenerate actions on every answer. Status (CLIs, usage, connectors) lives in a dialog.

The brand (three-dot mark, gradient wordmark, tagline and credit) comes from `banner.py`, the same source the terminal header uses, so the CLI and the UI are
one identity. `__TOKEN__` is replaced per run. Answers are rendered by a tiny markdown renderer that escapes everything first, so model output can never inject HTML.
"""

from urllib.parse import quote

from . import banner

TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ia-router</title>
<link rel="icon" href="__FAVICON__">
<style>
:root { --bg:#ffffff; --side:#f6f7f9; --panel:#f3f4f6; --line:#e5e7eb; --text:#111827; --dim:#6b7280; --c1:__C1__; --c2:__C2__; --c3:__C3__; --grad:linear-gradient(90deg,__S1__,__S2__,__S3__); --accent:#4285f4; --accent-fg:#fff; --ok:#16a34a; --bad:#dc2626; --warn:#d97706; --code:#f3f4f6; --bubble:#eef2ff; --shadow:0 4px 24px rgba(0,0,0,.07); }
@media (prefers-color-scheme: dark) { :root { --bg:#0b0f19; --side:#111827; --panel:#1f2937; --line:#263041; --text:#e5e7eb; --dim:#9ca3af; --accent:#4285f4; --ok:#4ade80; --bad:#f87171; --warn:#fbbf24; --code:#0f1626; --bubble:#1f2937; --shadow:0 4px 24px rgba(0,0,0,.4); } }
* { box-sizing:border-box; }
html, body { height:100%; }
body { margin:0; display:flex; background:var(--bg); color:var(--text); font:15px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif; }
button, select, textarea { font:inherit; color:inherit; }
button { background:none; border:0; cursor:pointer; padding:0; }
svg { width:18px; height:18px; fill:none; stroke:currentColor; stroke-width:2; stroke-linecap:round; stroke-linejoin:round; flex:none; }
.dim { color:var(--dim); } .small { font-size:12px; } .ok { color:var(--ok); } .bad { color:var(--bad); } .warn { color:var(--warn); }

/* sidebar */
aside { width:272px; flex:none; background:var(--side); border-right:1px solid var(--line); display:flex; flex-direction:column; padding:12px; gap:10px; }
.brand { display:flex; align-items:center; gap:9px; padding:6px 8px; font-size:17px; } .brand small { color:var(--dim); font-weight:400; font-size:12px; } .brand svg { width:40px; height:28px; }
.word-t { font-weight:750; letter-spacing:-.01em; background:var(--grad); -webkit-background-clip:text; background-clip:text; color:transparent; }
#new { display:flex; align-items:center; gap:8px; width:100%; padding:9px 12px; border:1px solid var(--line); background:var(--bg); border-radius:12px; font-weight:550; }
#new:hover { border-color:var(--accent); }
#sessions { flex:1; overflow-y:auto; margin:0 -4px; padding:0 4px; }
.group { font-size:11px; font-weight:600; color:var(--dim); padding:12px 8px 4px; }
.sess { display:flex; align-items:center; gap:4px; padding:7px 8px; border-radius:10px; cursor:pointer; font-size:14px; }
.sess:hover, .sess.on { background:var(--panel); } .sess span { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.sess b { opacity:0; color:var(--dim); font-weight:400; padding:0 4px; } .sess:hover b { opacity:1; } .sess b:hover { color:var(--bad); }
.foot { border-top:1px solid var(--line); padding-top:10px; display:flex; flex-direction:column; gap:2px; } .foot .credit { padding:8px 8px 2px; }
.foot button { display:flex; align-items:center; gap:10px; padding:8px; border-radius:10px; text-align:left; width:100%; } .foot button:hover { background:var(--panel); }
.dot { width:8px; height:8px; border-radius:50%; background:var(--dim); margin-left:auto; } .dot.ok { background:var(--ok); } .dot.bad { background:var(--bad); }

/* main */
main { flex:1; display:flex; flex-direction:column; min-width:0; position:relative; }
.top { display:none; align-items:center; gap:10px; padding:10px 14px; border-bottom:1px solid var(--line); } .top button { padding:4px; }
#banner { margin:12px auto 0; width:min(780px, calc(100% - 32px)); }
.banner { padding:10px 14px; border:1px solid var(--warn); border-radius:12px; font-size:13px; white-space:pre-wrap; }
#log { flex:1; overflow-y:auto; scroll-behavior:smooth; }
.col { width:min(780px, calc(100% - 32px)); margin:0 auto; }
.hero { text-align:center; padding-top:11vh; } .hero .mark svg { width:92px; height:64px; }
.word { margin:14px auto 6px; display:inline-block; text-align:left; font:600 clamp(11px,2.8vw,19px)/1.02 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; background:var(--grad); -webkit-background-clip:text; background-clip:text; color:transparent; white-space:pre; }
.hero .tag { color:var(--dim); margin:0 0 4px; } .credit { font-size:13px; color:var(--dim); } .credit a { color:var(--text); font-weight:600; text-decoration:underline; text-decoration-color:var(--line); text-underline-offset:3px; } .credit a:hover { text-decoration-color:var(--accent); }
.hero p.about { margin:22px auto 26px; max-width:520px; color:var(--dim); }
.cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:10px; text-align:left; }
.card { border:1px solid var(--line); border-radius:14px; padding:12px 14px; font-size:14px; color:var(--dim); } .card:hover { background:var(--panel); border-color:var(--accent); color:var(--text); }
.msg { display:flex; gap:12px; padding:14px 0; } .msg.user { justify-content:flex-end; }
.bubble { background:var(--bubble); border-radius:18px; padding:9px 16px; max-width:85%; white-space:pre-wrap; overflow-wrap:anywhere; }
.avatar { width:34px; flex:none; margin-top:3px; } .avatar svg { width:34px; height:24px; }
.body { flex:1; min-width:0; }
.meta { font-size:12px; color:var(--dim); margin-bottom:4px; } .status { font-size:13px; color:var(--dim); } .status::before { content:""; display:inline-block; width:8px; height:8px; margin-right:8px; border-radius:50%; background:var(--accent); animation:pulse 1s infinite alternate; }
@keyframes pulse { from { opacity:.25; } to { opacity:1; } }
.answer { overflow-wrap:anywhere; } .raw { white-space:pre-wrap; }
.answer p { margin:8px 0; } .answer h3, .answer h4 { margin:18px 0 6px; } .answer ul, .answer ol { padding-left:24px; margin:8px 0; } .answer li { margin:2px 0; }
.answer blockquote { margin:8px 0; padding:2px 14px; border-left:3px solid var(--line); color:var(--dim); } .answer hr { border:0; border-top:1px solid var(--line); margin:16px 0; }
.answer a { color:var(--accent); } .answer code { background:var(--code); border-radius:5px; padding:1px 5px; font:13px ui-monospace,SFMono-Regular,Menlo,monospace; }
.codebox { margin:10px 0; border:1px solid var(--line); border-radius:12px; overflow:hidden; background:var(--code); }
.codehead { display:flex; justify-content:space-between; align-items:center; padding:5px 12px; font-size:12px; color:var(--dim); border-bottom:1px solid var(--line); } .codehead button { color:var(--dim); font-size:12px; } .codehead button:hover { color:var(--text); }
.codebox pre { margin:0; padding:12px; overflow-x:auto; } .codebox code { background:none; padding:0; }
.actions { display:flex; gap:2px; margin-top:6px; } .actions button { padding:5px 7px; border-radius:8px; color:var(--dim); display:flex; align-items:center; gap:6px; font-size:12px; } .actions button:hover { background:var(--panel); color:var(--text); }
.err { color:var(--bad); } .cmp { display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:14px; width:100%; } .cmp > div { border:1px solid var(--line); border-radius:14px; padding:12px 16px; min-width:0; }

/* composer */
.dock { padding:8px 0 18px; }
.composer { border:1px solid var(--line); background:var(--bg); border-radius:22px; box-shadow:var(--shadow); padding:10px 12px 8px 18px; } .composer:focus-within { border-color:var(--accent); }
.composer textarea { width:100%; border:0; outline:0; resize:none; background:none; max-height:220px; padding:6px 0; }
.tools { display:flex; align-items:center; gap:6px; flex-wrap:wrap; margin-top:2px; }
.pill { display:flex; align-items:center; gap:5px; border:1px solid var(--line); border-radius:999px; padding:2px 6px 2px 10px; font-size:12px; color:var(--dim); } .pill select { border:0; background:none; color:var(--text); outline:0; font-size:12px; max-width:150px; cursor:pointer; }
.pill.on { border-color:var(--accent); color:var(--accent); } .pill input { accent-color:var(--accent); margin:0; } .pill label { cursor:pointer; padding-right:6px; display:flex; align-items:center; gap:6px; }
#send { margin-left:auto; width:34px; height:34px; border-radius:50%; background:var(--accent); color:var(--accent-fg); display:grid; place-items:center; } #send:disabled { opacity:.35; cursor:default; } #send.stop { background:var(--text); color:var(--bg); }
.note { text-align:center; font-size:11px; color:var(--dim); margin-top:8px; }

/* status dialog */
dialog { border:1px solid var(--line); border-radius:18px; background:var(--bg); color:var(--text); width:min(560px, calc(100% - 32px)); padding:0; box-shadow:var(--shadow); } dialog::backdrop { background:rgba(0,0,0,.45); }
#status { width:min(800px, calc(100% - 32px)); }
.dhead { display:flex; justify-content:space-between; align-items:center; padding:14px 18px; border-bottom:1px solid var(--line); font-weight:600; } .dbody { padding:6px 18px 18px; max-height:70vh; overflow-y:auto; }
.dbody h3 { font-size:11px; text-transform:uppercase; letter-spacing:.08em; color:var(--dim); margin:16px 0 6px; }
.model { display:flex; justify-content:space-between; gap:10px; padding:3px 0; font-size:14px; }
table { width:100%; border-collapse:collapse; font-size:13px; } td, th { padding:3px 0; text-align:right; font-weight:400; } td:first-child, th:first-child { text-align:left; } th { color:var(--dim); }

.btn { border:1px solid var(--line); border-radius:9px; padding:4px 11px; font-size:13px; background:var(--bg); } .btn:hover { border-color:var(--accent); } .btn.primary { background:var(--accent); border-color:var(--accent); color:var(--accent-fg); } .btn.danger:hover { border-color:var(--bad); color:var(--bad); }
.crow { border:1px solid var(--line); border-radius:12px; padding:10px 12px; margin:8px 0; } .chead { display:flex; align-items:center; gap:8px; flex-wrap:wrap; } .chead b { font-size:14px; } .chead .grow { flex:1; }
.badge { font-size:11px; border:1px solid var(--line); border-radius:999px; padding:0 8px; color:var(--dim); } .badge.on { color:var(--ok); border-color:var(--ok); }
.target { font:12px ui-monospace,SFMono-Regular,Menlo,monospace; color:var(--dim); overflow-wrap:anywhere; margin-top:4px; } .tres { font-size:12px; margin-top:6px; overflow-wrap:anywhere; }
.tabs { display:flex; gap:4px; margin:6px 0 10px; } .tabs button { border:1px solid var(--line); border-radius:999px; padding:3px 12px; font-size:13px; color:var(--dim); } .tabs button.on { border-color:var(--accent); color:var(--accent); }
.form label { display:block; font-size:12px; color:var(--dim); margin:8px 0; } .form input, .form textarea, .form select { display:block; width:100%; margin-top:3px; padding:7px 10px; border:1px solid var(--line); border-radius:9px; background:var(--bg); color:var(--text); font:13px ui-monospace,SFMono-Regular,Menlo,monospace; outline:0; } .form input:focus, .form textarea:focus, .form select:focus { border-color:var(--accent); }
.confirm { border:1px solid var(--warn); border-radius:12px; padding:10px 12px; margin:10px 0; font-size:13px; } .confirm code { display:block; margin:6px 0; overflow-wrap:anywhere; white-space:pre-wrap; }
.tabbar { display:flex; gap:2px; padding:8px 12px 0; border-bottom:1px solid var(--line); overflow-x:auto; } .tabbar button { padding:6px 12px; font-size:13px; color:var(--dim); border-bottom:2px solid transparent; white-space:nowrap; } .tabbar button.on { color:var(--text); border-bottom-color:var(--accent); }
.mono { font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace; background:var(--code); border:1px solid var(--line); border-radius:10px; padding:10px 12px; overflow-x:auto; white-space:pre; margin:8px 0; max-height:46vh; }
.pg { margin:12px 0; } .opt { display:flex; gap:8px; align-items:flex-start; padding:3px 0; font-size:13px; cursor:pointer; } .opt input { margin-top:4px; accent-color:var(--accent); }
.chips { display:flex; flex-wrap:wrap; gap:6px; } .chips:not(:empty) { margin-bottom:6px; } .chip { display:flex; align-items:center; gap:6px; border:1px solid var(--line); background:var(--panel); border-radius:10px; padding:2px 6px 2px 10px; font-size:12px; } .chip button { color:var(--dim); padding:0 4px; } .chip button:hover { color:var(--bad); }
.route { margin:0 0 8px; border:1px solid var(--line); border-radius:14px; padding:10px 14px; font-size:13px; } .route .mono { margin:6px 0 0; max-height:30vh; }
details.why { margin-top:6px; font-size:12px; color:var(--dim); } details.why summary { cursor:pointer; } details.why div { padding:4px 0; }
.pill button { color:var(--dim); font-size:12px; display:flex; align-items:center; gap:5px; padding:2px 6px 2px 2px; } .pill button:hover { color:var(--text); } .drop::after { content:"Drop files to attach them"; position:absolute; inset:0; display:grid; place-items:center; background:color-mix(in srgb, var(--bg) 85%, transparent); border:2px dashed var(--accent); border-radius:12px; font-weight:600; z-index:4; pointer-events:none; }
@media (max-width: 800px) {
  aside { position:fixed; inset:0 auto 0 0; z-index:5; transform:translateX(-100%); transition:transform .2s; box-shadow:var(--shadow); } body.open aside { transform:none; }
  .top { display:flex; } .hero { padding-top:6vh; }
}
</style>
</head>
<body>
<aside>
  <div class="brand"><span id="side-mark"></span><span class="word-t">ia-router</span><small id="ver"></small></div>
  <button id="new"><svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>New chat</button>
  <div id="sessions"></div>
  <div class="foot">
    <button id="open-conn"><svg viewBox="0 0 24 24"><path d="M9 7V3M15 7V3M7 7h10v5a5 5 0 0 1-10 0V7zM12 17v4"/></svg>Connectors<span class="dim small" id="conn-count" style="margin-left:auto"></span></button>
    <button id="open-status"><svg viewBox="0 0 24 24"><path d="M3 12h4l3-8 4 16 3-8h4"/></svg>Settings &amp; status<span class="dot" id="dot"></span></button>
    __CREDIT__
  </div>
</aside>
<main>
  <div class="top"><button id="menu" aria-label="Menu"><svg viewBox="0 0 24 24"><path d="M3 6h18M3 12h18M3 18h18"/></svg></button><b>ia-router</b></div>
  <div id="banner"></div>
  <div id="log"><div class="col" id="thread"></div></div>
  <div class="col dock">
    <div id="route-out"></div>
    <div class="composer">
      <div class="chips" id="chips"></div>
      <textarea id="task" rows="1" placeholder="Ask anything. The router picks the model." autofocus></textarea>
      <div class="tools">
        <span class="pill" title="Attach files (text, images, PDFs). You can also drop them here or write a path in your message."><button id="attach" type="button"><svg viewBox="0 0 24 24"><path d="M21 12.5l-8.6 8.6a5.5 5.5 0 0 1-7.8-7.8l8.6-8.6a3.7 3.7 0 0 1 5.2 5.2l-8.6 8.6a1.8 1.8 0 0 1-2.6-2.6l7.9-7.9"/></svg>Attach</button><input type="file" id="file" multiple hidden></span>
        <span class="pill" title="Which model answers. Auto picks by metrics.">Model <select id="model"><option value="auto">Auto</option></select></span>
        <span class="pill" title="Which MCP connectors the model may use. Auto gives it only the ones the task needs.">Connectors <select id="connectors"><option value="auto">Auto</option><option value="on">All</option><option value="off">None</option></select></span>
        <span class="pill" id="cmp-pill" title="Runs the task on two models and shows both answers. Spends quota on each. Pick both models, or leave them on Auto for the two best."><label><input type="checkbox" id="compare">Compare</label></span>
        <span class="pill" id="vs-pill" style="display:none">with <select id="vs"><option value="auto">Auto</option></select></span>
        <span class="pill" title="Shows which model the router would pick for what you wrote, and why. Spends no quota."><button id="why" type="button">Preview routing</button></span>
        <button id="send" aria-label="Send" disabled><svg viewBox="0 0 24 24"><path d="M12 19V5M5 12l7-7 7 7"/></svg></button>
      </div>
    </div>
    <div class="note">Runs on your own CLIs and subscriptions. Local only: nothing leaves this machine except what the CLIs send.</div>
  </div>
</main>
<dialog id="conn"><div class="dhead">Connectors (MCP)<button id="close-conn" aria-label="Close"><svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg></button></div><div class="dbody">
  <div class="small dim">Let the models use your other apps (Gmail, Calendar, Slack, GitHub…). Every model gets them through one proxy. Each server logs in on its own: the router never handles your passwords.</div>
  <h3>Registered</h3><div id="conn-list"></div>
  <h3>Antigravity</h3><div class="small dim">claude and codex get the connectors on every call. Antigravity has no per-call option, so it needs a one-time registration (the same as <code>ia-router connectors install agy</code>).</div>
  <div class="chead" style="margin:8px 0"><button class="btn" data-cact="agy-install">Register in Antigravity</button><button class="btn" data-cact="agy-uninstall">Remove registration</button></div><pre class="mono" id="agy-out" style="display:none"></pre>
  <h3>Add a connector</h3>
  <div class="tabs" id="c-tabs"><button data-mode="template" class="on">Template</button><button data-mode="command">Command</button><button data-mode="url">URL</button></div>
  <div class="form">
    <label>Name<input id="c-name" placeholder="e.g. gmail (lowercase letters, digits and hyphens)" autocomplete="off"></label>
    <div data-for="template"><label>Template<select id="c-template"></select></label><div class="small dim" id="c-tinfo"></div><label id="c-args-l">Arguments<input id="c-args" placeholder="e.g. /Users/me/projects" autocomplete="off"></label></div>
    <div data-for="command"><label>Command that starts the MCP server<textarea id="c-command" rows="2" placeholder="npx -y @modelcontextprotocol/server-memory" spellcheck="false"></textarea></label>
      <div class="small dim">Runs as a program on this machine, with no shell: <code>;</code>, <code>|</code> and <code>$(...)</code> are just characters. You will see the exact command before it is saved.</div></div>
    <div data-for="url"><label>Remote server URL<input id="c-url" placeholder="https://example.com/mcp" autocomplete="off"></label></div>
    <label data-for="command template">Environment variables, one per line<textarea id="c-env" rows="2" placeholder="TOKEN=${MY_TOKEN}" spellcheck="false"></textarea></label>
    <label data-for="url template">Headers, one per line<textarea id="c-headers" rows="2" placeholder="Authorization: Bearer ${MY_TOKEN}" spellcheck="false"></textarea></label>
    <div class="small dim" data-for="command url template">Write secrets as <code>${NAME}</code> and put NAME in your <code>.env</code>, so the key is not stored in the registry file.</div>
    <div id="c-confirm"></div>
    <div class="chead" style="margin-top:8px"><button class="btn primary" id="c-add">Add connector</button><span id="c-msg" class="small grow"></span></div>
  </div></div></dialog>
<dialog id="status"><div class="dhead">Settings<button id="close-status" aria-label="Close"><svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg></button></div>
  <div class="tabbar" id="s-tabs"><button data-tab="status" class="on">Status</button><button data-tab="scores">Scores</button><button data-tab="prio">Priorities</button><button data-tab="metrics">Metrics</button><button data-tab="chats">Conversations</button></div>
  <div class="dbody">
    <div data-pane="status" id="pane-status"></div>
    <div data-pane="scores" style="display:none"><div class="small dim">Score per model and kind of task, from the metrics and your priorities (the same table as <code>ia-router scores</code>).</div>
      <label class="small dim form">Category <select id="sc-cat" style="max-width:240px"><option value="">All</option></select></label><pre class="mono" id="sc-out"></pre></div>
    <div data-pane="prio" style="display:none"><div class="small dim">What you prioritize for each kind of task. It rebuilds the routing from objective metrics (the same as <code>ia-router priorities</code>).</div><div id="prio"></div></div>
    <div data-pane="metrics" style="display:none"><div class="small dim">Where the metrics come from. Updating downloads Arena (and Artificial Analysis if you have a key). It uses the network, never model quota.</div>
      <div class="chead" style="margin:8px 0"><button class="btn primary" id="mt-update">Update metrics</button><label class="small dim"><input type="checkbox" id="mt-force"> even if recent</label></div><pre class="mono" id="mt-out"></pre></div>
    <div data-pane="chats" style="display:none"><div class="small dim">Conversations are stored only on this machine, in files readable just by you. They hold what you and the models said.</div>
      <label class="opt" style="margin:12px 0"><input type="checkbox" id="ch-save"><span>Save conversations</span></label>
      <button class="btn danger" id="ch-clear">Delete all saved conversations</button><div class="small" id="ch-msg" style="margin-top:8px"></div></div>
  </div></dialog>
<script>
const TOKEN = "__TOKEN__";
const $ = id => document.getElementById(id);
const ICON = {
  copy: '<svg viewBox="0 0 24 24"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/></svg>',
  redo: '<svg viewBox="0 0 24 24"><path d="M3 12a9 9 0 0 1 15.5-6.2L21 8M21 3v5h-5M21 12a9 9 0 0 1-15.5 6.2L3 16M3 21v-5h5"/></svg>',
  send: '<svg viewBox="0 0 24 24"><path d="M12 19V5M5 12l7-7 7 7"/></svg>', stop: '<svg viewBox="0 0 24 24" style="fill:currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>'
};
let session = null, busy = false, ctrl = null, lastTask = "", health = {}, pending = [];
const BRAND = ["var(--c1)", "var(--c2)", "var(--c3)"];
// The terminal header's mark: three providers entering the router from the left. A provider that is not ready is gray, as in the terminal.
const mark = on => '<svg viewBox="0 0 42 30" aria-hidden="true"><g fill="none" stroke="var(--dim)" stroke-width="1.6" stroke-linecap="round"><path d="M10 5H17Q22 5 22 10V15"/><path d="M10 15H29"/><path d="M10 25H17Q22 25 22 20V15"/><circle cx="34" cy="15" r="5.2" stroke="currentColor" stroke-width="1.8"/></g><circle cx="34" cy="15" r="2.2" fill="currentColor"/>' +
  [5, 15, 25].map((y, i) => '<circle cx="6" cy="' + y + '" r="3.4" fill="' + (on[i] === false ? "var(--dim)" : BRAND[i]) + '"/>').join("") + "</svg>";
let ready = [true, true, true];
const SUGGESTIONS = ["Fix this bug in my Python function and explain what was wrong", "Summarize the pros and cons of microservices versus a monolith",
  "Write a SQL query that returns the top 5 customers by revenue per month", "Explain how DNS resolution works, step by step, for a beginner"];

const api = (path, body, signal) => fetch(path, body === undefined ? {headers: {"X-Token": TOKEN}} :
  {method: "POST", headers: {"X-Token": TOKEN, "Content-Type": "application/json"}, body: JSON.stringify(body), signal});
const esc = s => String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

// Minimal markdown: everything is escaped first, then a few constructs are turned into tags.
function md(src) {
  const blocks = [];
  let s = esc(src).replace(/```([^\n]*)\n([\s\S]*?)```/g, (_, lang, c) => {
    blocks.push('<div class="codebox"><div class="codehead"><span>' + (lang.trim() || "code") + '</span><button data-act="copy-code">Copy</button></div><pre><code>' + c + "</code></pre></div>");
    return "\u0000" + (blocks.length - 1) + "\u0000";
  });
  const inline = t => t.replace(/`([^`\n]+)`/g, "<code>$1</code>").replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>").replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, "$1<em>$2</em>")
    .replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  const out = []; let list = null;
  const close = () => { if (list) { out.push("</" + list + ">"); list = null; } };
  for (const line of s.split("\n")) {
    let m;
    if ((m = line.match(/^(#{1,4})\s+(.*)$/))) { close(); const h = m[1].length < 3 ? 3 : 4; out.push("<h" + h + ">" + inline(m[2]) + "</h" + h + ">"); }
    else if ((m = line.match(/^\s*[-*]\s+(.*)$/))) { if (list !== "ul") { close(); out.push("<ul>"); list = "ul"; } out.push("<li>" + inline(m[1]) + "</li>"); }
    else if ((m = line.match(/^\s*\d+[.)]\s+(.*)$/))) { if (list !== "ol") { close(); out.push("<ol>"); list = "ol"; } out.push("<li>" + inline(m[1]) + "</li>"); }
    else if ((m = line.match(/^&gt;\s?(.*)$/))) { close(); out.push("<blockquote>" + inline(m[1]) + "</blockquote>"); }
    else if (/^\s*([-*_])\1\1+\s*$/.test(line)) { close(); out.push("<hr>"); }
    else if (!line.trim()) close();
    else if (/^\u0000\d+\u0000$/.test(line)) { close(); out.push(line); }
    else { close(); out.push("<p>" + inline(line) + "</p>"); }
  }
  close();
  return out.join("").replace(/\u0000(\d+)\u0000/g, (_, i) => blocks[+i]);
}

const k = n => n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? (n / 1e3).toFixed(1) + "k" : String(n);
const money = v => v == null ? "" : " · ≈$" + v.toFixed(v < 0.01 ? 4 : 2);
function metaLine(r) {
  const t = r.tokens || {}, why = Object.entries(r.routing.weights).map(([c, w]) => c + "×" + w).join(", ") || "general";
  const fb = r.attempts.length > 1 ? " · fallback after " + r.attempts.slice(0, -1).map(a => a.model).join(", ") : "";
  return esc((r.model || "no model") + (r.model_id ? " (" + r.model_id + ")" : "") + " · in " + k(t.input || 0) + " out " + k(t.output || 0) + " · " + r.seconds + "s · " + why + fb + money(r.estimated_cost_usd));
}
const actions = () => '<div class="actions"><button data-act="copy" title="Copy">' + ICON.copy + '</button><button data-act="redo" title="Ask again (spends quota)">' + ICON.redo + "</button></div>";

const whyHtml = r => '<details class="why"><summary>Why this model</summary><div>' + esc("Kind of task: " + (Object.entries(r.routing.weights).map(([c, w]) => c + "×" + w).join(", ") || "general") + (r.routing.metrics ? " (scored with objective metrics)" : " (models.json estimate)")) +
  "</div><div>" + esc(r.routing.ranking.map(x => x.model + " " + x.score + (x.usable ? "" : " (unavailable)")).join("  ·  ")) + "</div>" + (r.connectors && r.connectors.length ? "<div>" + esc("Connectors: " + r.connectors.join(", ")) + "</div>" : "") + "</details>";
async function ndjson(resp, on) {
  const reader = resp.body.getReader(), dec = new TextDecoder(); let buf = "";
  for (;;) {
    const {value, done} = await reader.read(); if (done) break;
    buf += dec.decode(value, {stream: true}); let i;
    while ((i = buf.indexOf("\n")) >= 0) { on(JSON.parse(buf.slice(0, i))); buf = buf.slice(i + 1); }
  }
}
function answerHtml(r) {
  if (!r.ok) return '<div class="err">' + esc(r.error || "failed") + "</div>" + (r.attempts.length ? '<div class="meta">' + esc(r.attempts.map(a => a.model + ": " + (a.error || "ok")).join("; ")) + "</div>" : "");
  return '<div class="meta">' + metaLine(r) + '</div><div class="answer">' + md(r.output) + "</div>" + whyHtml(r);
}

function hero() {
  $("thread").innerHTML = '<div class="hero"><div class="mark">' + mark(ready) + '</div><pre class="word" aria-label="ia-router">__WORDMARK__</pre><p class="tag">__TAGLINE__</p>__CREDIT__<p class="about">Your AI subscriptions, one chat. The router picks the best model for each task, and falls back to another if one is rate limited.</p><div class="cards">' +
    SUGGESTIONS.map(s => '<button class="card" data-suggest="' + esc(s) + '">' + esc(s) + "</button>").join("") + "</div></div>";
}
function put(html, cls) {
  const t = $("thread"); if (t.querySelector(".hero")) t.innerHTML = "";
  const d = document.createElement("div"); d.className = "msg " + cls; d.innerHTML = html; t.appendChild(d); scroll(); return d;
}
const scroll = () => { const l = $("log"); l.scrollTop = l.scrollHeight; };
const user = text => put('<div class="bubble">' + esc(text) + "</div>", "user");
const bot = html => put('<div class="avatar">' + mark(ready) + '</div><div class="body">' + html + "</div>", "bot");

function setBusy(on) {
  busy = on; const b = $("send"); b.classList.toggle("stop", on); b.innerHTML = on ? ICON.stop : ICON.send; b.setAttribute("aria-label", on ? "Stop" : "Send"); b.disabled = !on && !$("task").value.trim();
}

async function send(text) {
  const task = (text || $("task").value).trim();
  if (!task || busy) return;
  lastTask = task; $("task").value = ""; grow(); setBusy(true);
  const compare = $("compare").checked, files = pending.filter(f => f.path).map(f => f.path), shown = pending.filter(f => f.path).map(f => "📎 " + f.label);
  pending = []; drawChips(); $("route-out").innerHTML = "";
  user(task + (shown.length ? "\n" + shown.join("\n") : ""));
  const msg = bot('<div class="status">Routing…</div><div class="answer raw"></div>'), body = msg.querySelector(".body");
  const status = body.children[0], raw = body.children[1];
  ctrl = new AbortController();
  try {
    const resp = await api("/api/ask", {task, model: $("model").value, connectors: $("connectors").value, compare, session, files,
      models: compare && $("model").value !== "auto" && $("vs").value !== "auto" && $("model").value !== $("vs").value ? [$("model").value, $("vs").value] : undefined}, ctrl.signal);
    if (!resp.ok) throw new Error((await resp.json()).error || resp.status);
    const reader = resp.body.getReader(), dec = new TextDecoder(); let buf = "", text = "";
    for (;;) {
      const {value, done} = await reader.read();
      if (done) break;
      buf += dec.decode(value, {stream: true});
      let i;
      while ((i = buf.indexOf("\n")) >= 0) {
        const ev = JSON.parse(buf.slice(0, i)); buf = buf.slice(i + 1);
        if (ev.type === "text") { text += ev.text; status.textContent = "Writing…"; raw.textContent = text; scroll(); }
        else if (ev.type === "status") status.textContent = "Using " + ev.text + "…";
        else if (ev.type === "reset") { text = ""; raw.textContent = ""; status.textContent = "That attempt failed, trying the next model…"; }
        else if (ev.type === "error") body.innerHTML = '<div class="err">' + esc(ev.text) + "</div>";
        else if (ev.type === "done") {
          if (ev.compare) {
            msg.querySelector(".avatar").remove(); msg.innerHTML = '<div class="cmp">' + ev.compare.map(r => "<div>" + answerHtml(r) + "</div>").join("") + "</div>";
          } else {
            body.innerHTML = answerHtml(ev) + (ev.ok ? actions() : ""); body._raw = ev.output;
            if (ev.session) { session = ev.session; history.replaceState(null, "", "#" + session); }
            (ev.usage_warnings || []).forEach(w => body.insertAdjacentHTML("beforeend", '<div class="meta warn">⚠ ' + esc(w) + "</div>"));
          }
        }
      }
    }
  } catch (e) {
    body.innerHTML = e.name === "AbortError" ? '<div class="meta">Stopped. The model may still finish in the background and use quota.</div>' : '<div class="err">' + esc(e.message || e) + "</div>";
  }
  ctrl = null; setBusy(false); $("task").focus(); scroll(); refresh();
}

function grow() { const t = $("task"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 220) + "px"; $("send").disabled = !busy && !t.value.trim(); }

const dayStart = d => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
function groupOf(updated) {
  const days = Math.round((dayStart(new Date()) - dayStart(new Date(updated))) / 864e5);
  return days <= 0 ? "Today" : days === 1 ? "Yesterday" : days <= 7 ? "Previous 7 days" : "Older";
}

async function refresh() {
  const [st, ss] = await Promise.all([api("/api/state").then(r => r.json()), api("/api/sessions").then(r => r.json())]);
  $("ver").textContent = "v" + st.version;
  const sel = $("model"), keep = sel.value;
  sel.innerHTML = '<option value="auto">Auto</option>' + st.models.map(m => '<option value="' + esc(m.name) + '"' + (m.installed && m.auth !== "missing" ? "" : " disabled") + ">" + esc(m.name) + "</option>").join("");
  sel.value = keep || "auto"; if (sel.value !== keep && keep) sel.value = "auto";
  const vs = $("vs"), keepVs = vs.value; vs.innerHTML = sel.innerHTML.replace("Auto", "Auto (top two)"); vs.value = keepVs || "auto"; if (vs.value !== keepVs && keepVs) vs.value = "auto";
  $("cmp-pill").classList.toggle("on", $("compare").checked);
  $("banner").innerHTML = st.advice.length ? '<div class="banner">' + esc(st.advice.join("\n")) + "</div>" : "";
  $("dot").className = "dot " + (st.ready.length ? (st.usage_warnings.length ? "" : "ok") : "bad");
  ready = st.models.slice(0, 3).map(m => m.installed && m.auth !== "missing"); $("side-mark").innerHTML = mark(ready);
  const h = document.querySelector(".hero .mark"); if (h) h.innerHTML = mark(ready);
  $("conn-count").textContent = st.connectors.length ? st.connectors.filter(c => c.enabled).length + " on" : "";
  health = st; renderStatus();
  let html = "", last = "";
  if (!ss.enabled) html = '<div class="group">Saving is off</div>';
  else if (!ss.sessions.length) html = '<div class="group">No conversations yet</div>';
  for (const s of ss.sessions) {
    const g = groupOf(s.updated); if (g !== last) { html += '<div class="group">' + g + "</div>"; last = g; }
    html += '<div class="sess ' + (s.id === session ? "on" : "") + '" data-id="' + esc(s.id) + '"><span>' + esc(s.title) + '</span><b data-del="' + esc(s.id) + '" title="Delete">✕</b></div>';
  }
  $("sessions").innerHTML = html;
}

function renderStatus() {
  const st = health; if (!st.models) return;
  const rows = Object.entries(st.usage), runs = Object.entries(st.stats || {});
  $("pane-status").innerHTML = "<h3>CLIs</h3>" + st.models.map(m => '<div class="model"><span>' + (m.installed && m.auth !== "missing" ? '<span class="ok">✔</span>' : '<span class="bad">✗</span>') + " " + esc(m.name) +
      '</span><span class="dim small">' + esc(!m.installed ? "not installed" : m.auth === "missing" ? "not logged in" : (m.model_id || "ready")) + (st.cooldowns[m.name] > 0 ? " · cooling down " + st.cooldowns[m.name] + "s" : "") + "</span></div>").join("") +
    (st.advice.length ? '<div class="small warn" style="white-space:pre-wrap;margin-top:6px">' + esc(st.advice.join("\n")) + "</div>" : "") +
    '<div class="chead" style="margin-top:8px"><button class="btn" data-sact="probe">Check logins</button><button class="btn" data-sact="reset">Reset cooldowns</button></div><div id="probe-out"></div>' +
    "<h3>Usage (tokens)</h3>" + (rows.length ? "<table><tr><th></th><th>5h</th><th>24h</th><th>7d</th><th>vs limit</th></tr>" + rows.map(([n, r]) => "<tr><td>" + esc(n) + "</td><td>" + k(r.tokens_window) + "</td><td>" + k(r.tokens_24h) + "</td><td>" + k(r.tokens_7d) + "</td><td>" + (r.used_ratio == null ? "n/a" : Math.round(r.used_ratio * 100) + "%") + "</td></tr>").join("") +
      "</table>" + st.usage_warnings.map(w => '<div class="small warn">⚠ ' + esc(w) + "</div>").join("") : '<div class="dim small">Nothing recorded yet.</div>') +
    "<h3>History per model</h3>" + (runs.length ? "<table><tr><th></th><th>runs</th><th>success</th><th>avg s</th><th>limits</th></tr>" + runs.map(([n, m]) => "<tr><td>" + esc(n) + "</td><td>" + m.runs + "</td><td>" + Math.round(m.ok_rate * 100) + "%</td><td>" + m.avg_seconds + "</td><td>" + m.rate_limits + "</td></tr>").join("") + "</table>" : '<div class="dim small">No history yet.</div>') +
    "<h3>Metrics</h3><div class='small dim'>" + esc(st.metrics_line) + "</div>" +
    "<h3>Connectors</h3><div style='margin-bottom:6px'><button class='btn' data-act='manage'>Manage connectors</button></div>" + (st.connectors.length ? st.connectors.map(c => '<div class="model"><span>' + esc(c.name) + '</span><span class="dim small">' + (c.enabled ? "on" : "off") + (c.remote ? " · remote" : "") + "</span></div>").join("") :
      '<div class="dim small">None yet.</div>');
  $("ch-save").checked = !!st.sessions_enabled;
}

// ---- settings tabs ----
async function loadScores() {
  const c = $("sc-cat").value, d = await api("/api/scores" + (c ? "?category=" + encodeURIComponent(c) : "")).then(r => r.json());
  if ($("sc-cat").options.length === 1) $("sc-cat").innerHTML += d.categories.map(x => '<option value="' + esc(x) + '">' + esc(x) + "</option>").join("");
  $("sc-out").textContent = d.table + (d.explain ? "\n\n" + d.explain : "");
}
async function loadPrio(note) {
  const d = await api("/api/priorities").then(r => r.json());
  $("prio").innerHTML = (d.blocked ? '<div class="small warn" style="margin:10px 0">' + esc(d.notes.join(" ")) + " There is nothing to prioritize between accuracy, speed and cost until you have more metrics (add your free Artificial Analysis key and update the metrics).</div>" :
    d.groups.map(g => '<div class="pg"><b>' + esc(g.title) + "</b>" + d.options.map(o => '<label class="opt"><input type="radio" name="pg-' + esc(g.key) + '" value="' + esc(o.key) + '"' + (d.answers[g.key] === o.key ? " checked" : "") + "><span><b>" + esc(o.label) + '</b> <span class="dim small">' + esc(o.text) + "</span></span></label>").join("") + "</div>").join("") +
    (d.notes.length ? '<div class="small dim">' + esc(d.notes.join(" · ")) + "</div>" : "") + '<div class="chead" style="margin:10px 0"><button class="btn" data-sact="prio-preview">Preview</button><button class="btn primary" data-sact="prio-save">Save priorities</button><span class="small grow" id="prio-msg"></span></div>') +
    '<div class="small dim">The router would pick:</div><pre class="mono" id="prio-out">' + esc(d.preview.join("\n")) + "</pre>";
  if (note) $("prio-msg") && ($("prio-msg").textContent = note);
}
async function prioSubmit(save) {
  const answers = {}; document.querySelectorAll("#prio input:checked").forEach(i => answers[i.name.slice(3)] = i.value);
  try { const r = await cj("/api/priorities", {answers, save}); $("prio-out").textContent = r.preview.join("\n"); $("prio-msg").className = "small grow ok"; $("prio-msg").textContent = save ? "Saved. They apply right away." : "Preview only: nothing saved."; }
  catch (e) { $("prio-msg").className = "small grow err"; $("prio-msg").textContent = e.message; }
}
async function loadMetrics() { $("mt-out").textContent = (await api("/api/metrics").then(r => r.json())).sources; }
async function updateMetrics() {
  const out = $("mt-out"), btn = $("mt-update"); out.textContent = ""; btn.disabled = true;
  try {
    const resp = await api("/api/metrics/refresh", {force: $("mt-force").checked});
    await ndjson(resp, ev => { if (ev.type === "line") { out.textContent += ev.text + "\n"; out.scrollTop = out.scrollHeight; } else if (ev.type === "done") out.textContent += "\n" + ev.sources; else if (ev.type === "error") out.textContent += "\nError: " + ev.text; });
  } catch (e) { out.textContent += "\nError: " + (e.message || e); }
  btn.disabled = false; refresh();
}
function showTab(t) {
  document.querySelectorAll("#s-tabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === t));
  document.querySelectorAll("[data-pane]").forEach(p => p.style.display = p.dataset.pane === t ? "" : "none");
  if (t === "scores") loadScores(); else if (t === "prio") loadPrio(); else if (t === "metrics") loadMetrics(); else if (t === "status") refresh();
}
$("s-tabs").addEventListener("click", e => { if (e.target.dataset.tab) showTab(e.target.dataset.tab); });
$("sc-cat").onchange = loadScores;
$("mt-update").onclick = updateMetrics;
$("ch-save").onchange = async () => { const r = await cj("/api/sessions/saving", {on: $("ch-save").checked}); $("ch-msg").textContent = r.enabled ? "Conversations will be saved." : "Nothing new will be saved. Old conversations stay until you delete them."; refresh(); };
$("ch-clear").onclick = async () => { if (!confirm("Delete every saved conversation? This cannot be undone.")) return; const r = await cj("/api/sessions/clear", {}); $("ch-msg").textContent = "Deleted " + r.deleted + " conversation(s)."; newChat(); };
$("status").addEventListener("click", async e => {
  const b = e.target.closest("[data-sact]"); if (!b) return;
  const act = b.dataset.sact;
  if (act === "prio-preview") return prioSubmit(false);
  if (act === "prio-save") return prioSubmit(true);
  if (act === "reset") { await cj("/api/reset-cooldowns", {}); return refresh(); }
  if (act === "probe") {
    $("probe-out").innerHTML = '<div class="confirm">This sends one minimal query to each installed CLI to check the login and learn which model it uses. It spends a pinch of quota.<div class="chead" style="margin-top:8px"><button class="btn primary" data-sact="probe-go">Check now</button><button class="btn" data-sact="probe-no">Cancel</button></div></div>';
  } else if (act === "probe-no") $("probe-out").innerHTML = "";
  else if (act === "probe-go") {
    $("probe-out").innerHTML = '<div class="small dim">Checking…</div>';
    try { const r = await cj("/api/probe", {}); await refresh(); $("probe-out").innerHTML = Object.entries(r.checked).map(([n, x]) => '<div class="small ' + (x.auth === "ok" ? "ok" : "warn") + '">' + esc(n + ": " + (x.auth === "ok" ? "logged in" : x.auth === "missing" ? "not logged in" : "check failed" + (x.probe_error ? " (" + x.probe_error + ")" : "")) + (x.version ? " · " + x.version : "") + (x.probe_seconds ? " · " + x.probe_seconds + "s" : "")) + "</div>").join(""); }
    catch (e) { $("probe-out").innerHTML = '<div class="small err">' + esc(e.message) + "</div>"; }
  }
});

async function openSession(id) {
  const resp = await api("/api/sessions/" + id);
  if (!resp.ok) return newChat();
  const d = await resp.json(); session = d.id; $("thread").innerHTML = ""; history.replaceState(null, "", "#" + session);
  d.turns.forEach(t => { user(t.user); const m = bot('<div class="meta">' + esc(t.model) + '</div><div class="answer">' + md(t.answer) + "</div>" + actions()); m.querySelector(".body")._raw = t.answer; });
  document.body.classList.remove("open"); refresh(); $("log").style.scrollBehavior = "auto"; scroll(); $("log").style.scrollBehavior = "";
}
function newChat() { session = null; history.replaceState(null, "", location.pathname + location.search); hero(); document.body.classList.remove("open"); refresh(); $("task").focus(); }

// ---- connectors manager ----
let cdata = {connectors: [], templates: []}, cmode = "template";
const cj = (path, body) => api(path, body).then(async r => { const d = await r.json(); if (!r.ok) throw new Error(d.error || r.status); return d; });
function setMode(m) {
  cmode = m; $("c-confirm").innerHTML = "";
  document.querySelectorAll("#c-tabs button").forEach(b => b.classList.toggle("on", b.dataset.mode === m));
  document.querySelectorAll("[data-for]").forEach(el => el.style.display = el.dataset.for.split(" ").includes(m) ? "" : "none");
  tinfo();
}
function tinfo() {
  const t = cdata.templates.find(x => x.key === $("c-template").value);
  $("c-tinfo").textContent = t ? t.description + (t.requires ? " Requires " + t.requires + "." : "") + (t.verified ? "" : " Not verified by the maintainers.") + (t.needs_env.length ? " Needs " + t.needs_env.join(", ") + " in your environment or .env." : "") : "";
  $("c-args-l").style.display = cmode === "template" && t && t.needs_args ? "" : "none";
  if (t && t.needs_args) $("c-args").placeholder = t.needs_args;
}
function renderConnectors() {
  $("c-template").innerHTML = cdata.templates.map(t => '<option value="' + esc(t.key) + '">' + esc(t.key) + " (" + t.kind + ")</option>").join("");
  $("conn-list").innerHTML = cdata.connectors.length ? cdata.connectors.map(c => '<div class="crow" data-name="' + esc(c.name) + '"><div class="chead"><b>' + esc(c.name) + '</b><span class="badge ' + (c.enabled ? "on" : "") + '">' + (c.enabled ? "on" : "off") + '</span><span class="badge">' + c.kind +
    '</span><span class="grow"></span><button class="btn" data-cact="test">Test</button><button class="btn" data-cact="' + (c.enabled ? "disable" : "enable") + '">' + (c.enabled ? "Turn off" : "Turn on") + '</button><button class="btn danger" data-cact="remove">Remove</button></div><div class="target">' + esc(c.target) + "</div>" +
    (c.env.length || c.headers.length ? '<div class="small dim">Variables: ' + esc(c.env.concat(c.headers).join(", ")) + " (values are never shown)</div>" : "") +
    (c.literal_secrets.length ? '<div class="small warn">⚠ ' + esc(c.literal_secrets.join(", ")) + " holds a literal secret in the registry file. Prefer ${NAME} and your .env.</div>" : "") + '<div class="tres"></div></div>').join("") :
    '<div class="small dim">None yet. Add one below.</div>';
  tinfo();
}
async function loadConnectors() { cdata = await api("/api/connectors").then(r => r.json()); renderConnectors(); setMode(cmode); }
function connectorBody(confirm) {
  return {name: $("c-name").value, confirm, ...(cmode === "template" ? {template: $("c-template").value, args: $("c-args").value, env: $("c-env").value, headers: $("c-headers").value} :
    cmode === "command" ? {command: $("c-command").value, env: $("c-env").value} : {url: $("c-url").value, headers: $("c-headers").value})};
}
async function addConnector(confirmed) {
  const msg = $("c-msg"); msg.className = "small grow"; msg.textContent = "";
  if (cmode === "command" && !confirmed) {
    const cmd = $("c-command").value.trim();
    if (!cmd) { msg.className = "small grow err"; msg.textContent = "Write the command first."; return; }
    $("c-confirm").innerHTML = '<div class="confirm"><b>This command will run on your machine</b> every time a model uses this connector, with your user\'s permissions:<code></code>Only continue if you trust it.<div class="chead" style="margin-top:8px"><button class="btn primary" data-cact="confirm-add">Yes, add it</button><button class="btn" data-cact="cancel-add">Cancel</button></div></div>';
    $("c-confirm").querySelector("code").textContent = cmd; return;
  }
  $("c-confirm").innerHTML = "";
  try {
    const r = await cj("/api/connectors/add", connectorBody(confirmed));
    msg.className = "small grow ok"; msg.textContent = "Added “" + r.added + "”. Press Test to check that it starts." + (r.warnings.length ? " " + r.warnings.join(" ") : "");
    ["c-name", "c-command", "c-url", "c-args", "c-env", "c-headers"].forEach(i => $(i).value = "");
    await loadConnectors(); refresh();
  } catch (e) { msg.className = "small grow err"; msg.textContent = e.message; }
}
async function connectorAction(act, row) {
  const name = row.dataset.name, res = row.querySelector(".tres");
  if (act === "remove" && !confirm("Remove the connector “" + name + "”? Its registry entry is deleted (the app itself is not touched).")) return;
  if (act === "test") {
    res.className = "tres dim"; res.textContent = "Starting it… (spends no model quota)";
    try { const r = await cj("/api/connectors/test", {name}); res.className = "tres " + (r.ok ? "ok" : "err"); res.textContent = r.ok ? "Works: " + r.tools.length + " tools" + (r.tools.length ? " (" + r.tools.slice(0, 8).join(", ") + (r.tools.length > 8 ? ", …" : "") + ")" : "") : "Failed: " + r.error; }
    catch (e) { res.className = "tres err"; res.textContent = e.message; }
    return;
  }
  try { await cj("/api/connectors/" + act, {name}); await loadConnectors(); refresh(); } catch (e) { res.className = "tres err"; res.textContent = e.message; }
}
async function agy(install) {
  const out = $("agy-out"); out.style.display = ""; out.textContent = "Working…";
  try { const r = await cj("/api/connectors/agy", {install}); out.textContent = r.lines.join("\n") + (r.ok ? "" : "\n(it failed)"); } catch (e) { out.textContent = e.message; }
}
$("open-conn").onclick = async () => { await loadConnectors(); $("conn").showModal(); };
$("close-conn").onclick = () => $("conn").close();
$("conn").addEventListener("click", e => { if (e.target === $("conn")) $("conn").close(); });
$("c-tabs").addEventListener("click", e => { if (e.target.dataset.mode) setMode(e.target.dataset.mode); });
$("c-template").onchange = tinfo;
$("c-add").onclick = () => addConnector(false);
$("conn").addEventListener("click", e => {
  const b = e.target.closest("[data-cact]"); if (!b) return;
  if (b.dataset.cact === "confirm-add") addConnector(true);
  else if (b.dataset.cact === "cancel-add") $("c-confirm").innerHTML = "";
  else if (b.dataset.cact.startsWith("agy-")) agy(b.dataset.cact === "agy-install");
  else connectorAction(b.dataset.cact, b.closest(".crow"));
});

document.addEventListener("click", async e => {
  const t = e.target.closest("[data-act],[data-suggest],[data-del],.sess");
  if (!t) return;
  const act = t.dataset.act;
  if (t.dataset.suggest) { $("task").value = t.dataset.suggest; grow(); $("task").focus(); }
  else if (t.dataset.del) { e.stopPropagation(); await api("/api/sessions/delete", {id: t.dataset.del}); if (t.dataset.del === session) newChat(); else refresh(); }
  else if (act === "copy-code") { try { await navigator.clipboard.writeText(t.closest(".codebox").querySelector("code").textContent); t.textContent = "Copied"; setTimeout(() => t.textContent = "Copy", 1200); } catch (_) {} }
  else if (act === "copy") { try { await navigator.clipboard.writeText(t.closest(".body")._raw || ""); } catch (_) {} }
  else if (act === "manage") { $("status").close(); $("open-conn").click(); }
  else if (act === "redo") { if (lastTask) send(lastTask); }
  else if (t.classList.contains("sess")) openSession(t.dataset.id);
});
// ---- attachments, routing preview ----
const drawChips = () => { $("chips").innerHTML = pending.map((f, i) => '<span class="chip">' + esc(f.label || f.name) + (f.path ? "" : " · uploading…") + '<button data-chip="' + i + '" title="Remove">✕</button></span>').join(""); };
function addFiles(list) {
  [...list].forEach(file => {
    if (file.size > 25e6) { pending.push({name: file.name, label: file.name + " (too large, 25 MB max)", failed: true}); drawChips(); return; }
    const item = {name: file.name}; pending.push(item); drawChips();
    const rd = new FileReader();
    rd.onload = async () => {
      try { const r = await cj("/api/upload", {name: file.name, data: String(rd.result).split(",")[1] || ""}); item.path = r.path; item.label = r.label; }
      catch (e) { item.failed = true; item.label = file.name + " (" + e.message + ")"; }
      drawChips();
    };
    rd.readAsDataURL(file);
  });
}
$("attach").onclick = () => $("file").click();
$("file").onchange = () => { addFiles($("file").files); $("file").value = ""; };
$("chips").addEventListener("click", e => { const i = e.target.dataset.chip; if (i !== undefined) { pending.splice(+i, 1); drawChips(); } });
["dragenter", "dragover"].forEach(t => document.addEventListener(t, e => { if (e.dataTransfer && [...e.dataTransfer.types].includes("Files")) { e.preventDefault(); document.querySelector("main").classList.add("drop"); } }));
["dragleave", "drop"].forEach(t => document.addEventListener(t, e => { if (t === "drop" || e.target === document.documentElement || !e.relatedTarget) document.querySelector("main").classList.remove("drop"); }));
document.addEventListener("drop", e => { if (e.dataTransfer && e.dataTransfer.files.length) { e.preventDefault(); addFiles(e.dataTransfer.files); } });
$("why").onclick = async () => {
  const task = $("task").value.trim(), box = $("route-out");
  if (!task) { box.innerHTML = '<div class="route dim">Write the task first; this shows which model the router would pick for it.</div>'; return; }
  try {
    const r = await cj("/api/route", {task, files: pending.filter(f => f.path).map(f => f.path)});
    box.innerHTML = '<div class="route"><b>The router would pick ' + esc(r.chosen || "no model") + "</b>" + (r.connectors.length ? '<span class="dim"> · connectors: ' + esc(r.connectors.join(", ")) + "</span>" : '<span class="dim"> · no connectors needed</span>') + '<pre class="mono">' + esc(r.text) + "</pre></div>";
  } catch (e) { box.innerHTML = '<div class="route err">' + esc(e.message) + "</div>"; }
};
$("new").onclick = newChat;
$("menu").onclick = () => document.body.classList.toggle("open");
$("send").onclick = () => busy ? ctrl && ctrl.abort() : send();
$("compare").onchange = () => { $("cmp-pill").classList.toggle("on", $("compare").checked); $("vs-pill").style.display = $("compare").checked ? "" : "none"; };
$("open-status").onclick = () => { renderStatus(); $("status").showModal(); };
$("close-status").onclick = () => $("status").close();
$("status").addEventListener("click", e => { if (e.target === $("status")) $("status").close(); });
$("task").addEventListener("input", grow);
$("task").addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); } });
hero(); $("side-mark").innerHTML = mark(ready); refresh();
if (location.hash.length > 1) openSession(location.hash.slice(1));
</script>
</body>
</html>
"""


def _mark_svg() -> str:
    """The same mark as a standalone SVG (all three providers on) for the tab icon."""
    c = ["rgb(%d,%d,%d)" % tuple(x) for x in banner._BRAND]
    dots = "".join(f'<circle cx="6" cy="{y}" r="3.4" fill="{c[i]}"/>' for i, y in enumerate((5, 15, 25)))
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="-2 -4 46 38"><g fill="none" stroke="#9ca3af" stroke-width="1.8" stroke-linecap="round">'
            '<path d="M10 5H17Q22 5 22 10V15"/><path d="M10 15H29"/><path d="M10 25H17Q22 25 22 20V15"/><circle cx="34" cy="15" r="5.2" stroke="#6b7280"/></g>'
            f'<circle cx="34" cy="15" r="2.2" fill="#6b7280"/>{dots}</svg>')


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _credit_html() -> str:
    return (f'<div class="credit">by <a href="{banner._GITHUB}" target="_blank" rel="noopener noreferrer">{banner._AUTHOR}</a> · '
            f'<a href="{banner._URL}" target="_blank" rel="noopener noreferrer">{banner._SITE}</a></div>')


def render(token: str) -> str:
    """The page with the per-run token and the brand taken from the terminal header (`banner.py`)."""
    rgb = lambda c: "rgb(%d,%d,%d)" % tuple(c)
    word = "\\n".join("".join(banner._GLYPHS[ch][r] for ch in banner._WORD) for r in range(3))
    vals = {"__TOKEN__": token, "__FAVICON__": "data:image/svg+xml," + quote(_mark_svg()), "__WORDMARK__": _esc(word), "__TAGLINE__": _esc(banner._TAGLINE),
            "__CREDIT__": _credit_html(), **{f"__C{i + 1}__": rgb(c) for i, c in enumerate(banner._BRAND)}, **{f"__S{i + 1}__": rgb(c) for i, c in enumerate(banner._STOPS)}}
    out = TEMPLATE
    for key, value in vals.items():
        out = out.replace(key, value)
    return out
