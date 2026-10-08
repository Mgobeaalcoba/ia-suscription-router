"""The single page served by `ia-router ui` (HTML, CSS and JS inline: no build step, no external requests, no dependencies).

Laid out like a modern chat app: conversations grouped by date on the left, a welcome screen with suggestions, a rounded composer with the
model and connector pickers under it, user bubbles, and copy / regenerate actions on every answer. Status (CLIs, usage, connectors) lives in a dialog.

`__TOKEN__` is replaced per run. Answers are rendered by a tiny markdown renderer that escapes everything first, so model output can never inject HTML.
"""

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ia-router</title>
<style>
:root { --bg:#ffffff; --side:#f6f7f9; --panel:#f3f4f6; --line:#e5e7eb; --text:#111827; --dim:#6b7280; --accent:#2563eb; --accent-fg:#fff; --ok:#16a34a; --bad:#dc2626; --warn:#d97706; --code:#f3f4f6; --bubble:#eef2ff; --shadow:0 4px 24px rgba(0,0,0,.07); }
@media (prefers-color-scheme: dark) { :root { --bg:#0b0f19; --side:#111827; --panel:#1f2937; --line:#263041; --text:#e5e7eb; --dim:#9ca3af; --accent:#3b82f6; --ok:#4ade80; --bad:#f87171; --warn:#fbbf24; --code:#0f1626; --bubble:#1f2937; --shadow:0 4px 24px rgba(0,0,0,.4); } }
* { box-sizing:border-box; }
html, body { height:100%; }
body { margin:0; display:flex; background:var(--bg); color:var(--text); font:15px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif; }
button, select, textarea { font:inherit; color:inherit; }
button { background:none; border:0; cursor:pointer; padding:0; }
svg { width:18px; height:18px; fill:none; stroke:currentColor; stroke-width:2; stroke-linecap:round; stroke-linejoin:round; flex:none; }
.dim { color:var(--dim); } .small { font-size:12px; } .ok { color:var(--ok); } .bad { color:var(--bad); } .warn { color:var(--warn); }

/* sidebar */
aside { width:272px; flex:none; background:var(--side); border-right:1px solid var(--line); display:flex; flex-direction:column; padding:12px; gap:10px; }
.brand { display:flex; align-items:center; gap:8px; padding:6px 8px; font-weight:650; font-size:16px; } .brand small { color:var(--dim); font-weight:400; }
.logo { width:26px; height:26px; border-radius:8px; background:linear-gradient(135deg,#2563eb,#7c3aed); color:#fff; display:grid; place-items:center; font-size:14px; flex:none; }
#new { display:flex; align-items:center; gap:8px; width:100%; padding:9px 12px; border:1px solid var(--line); background:var(--bg); border-radius:12px; font-weight:550; }
#new:hover { border-color:var(--accent); }
#sessions { flex:1; overflow-y:auto; margin:0 -4px; padding:0 4px; }
.group { font-size:11px; font-weight:600; color:var(--dim); padding:12px 8px 4px; }
.sess { display:flex; align-items:center; gap:4px; padding:7px 8px; border-radius:10px; cursor:pointer; font-size:14px; }
.sess:hover, .sess.on { background:var(--panel); } .sess span { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.sess b { opacity:0; color:var(--dim); font-weight:400; padding:0 4px; } .sess:hover b { opacity:1; } .sess b:hover { color:var(--bad); }
.foot { border-top:1px solid var(--line); padding-top:10px; display:flex; flex-direction:column; gap:2px; }
.foot button { display:flex; align-items:center; gap:10px; padding:8px; border-radius:10px; text-align:left; width:100%; } .foot button:hover { background:var(--panel); }
.dot { width:8px; height:8px; border-radius:50%; background:var(--dim); margin-left:auto; } .dot.ok { background:var(--ok); } .dot.bad { background:var(--bad); }

/* main */
main { flex:1; display:flex; flex-direction:column; min-width:0; position:relative; }
.top { display:none; align-items:center; gap:10px; padding:10px 14px; border-bottom:1px solid var(--line); } .top button { padding:4px; }
#banner { margin:12px auto 0; width:min(780px, calc(100% - 32px)); }
.banner { padding:10px 14px; border:1px solid var(--warn); border-radius:12px; font-size:13px; white-space:pre-wrap; }
#log { flex:1; overflow-y:auto; scroll-behavior:smooth; }
.col { width:min(780px, calc(100% - 32px)); margin:0 auto; }
.hero { text-align:center; padding-top:14vh; } .hero .logo { width:56px; height:56px; font-size:28px; border-radius:16px; margin:0 auto 16px; }
.hero h2 { margin:0 0 6px; font-size:28px; } .hero p { margin:0 auto 28px; max-width:520px; color:var(--dim); }
.cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:10px; text-align:left; }
.card { border:1px solid var(--line); border-radius:14px; padding:12px 14px; font-size:14px; color:var(--dim); } .card:hover { background:var(--panel); border-color:var(--accent); color:var(--text); }
.msg { display:flex; gap:12px; padding:14px 0; } .msg.user { justify-content:flex-end; }
.bubble { background:var(--bubble); border-radius:18px; padding:9px 16px; max-width:85%; white-space:pre-wrap; overflow-wrap:anywhere; }
.avatar { width:28px; height:28px; border-radius:8px; background:linear-gradient(135deg,#2563eb,#7c3aed); color:#fff; display:grid; place-items:center; font-size:14px; flex:none; margin-top:2px; }
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
.dhead { display:flex; justify-content:space-between; align-items:center; padding:14px 18px; border-bottom:1px solid var(--line); font-weight:600; } .dbody { padding:6px 18px 18px; max-height:70vh; overflow-y:auto; }
.dbody h3 { font-size:11px; text-transform:uppercase; letter-spacing:.08em; color:var(--dim); margin:16px 0 6px; }
.model { display:flex; justify-content:space-between; gap:10px; padding:3px 0; font-size:14px; }
table { width:100%; border-collapse:collapse; font-size:13px; } td, th { padding:3px 0; text-align:right; font-weight:400; } td:first-child, th:first-child { text-align:left; } th { color:var(--dim); }

@media (max-width: 800px) {
  aside { position:fixed; inset:0 auto 0 0; z-index:5; transform:translateX(-100%); transition:transform .2s; box-shadow:var(--shadow); } body.open aside { transform:none; }
  .top { display:flex; } .hero { padding-top:6vh; }
}
</style>
</head>
<body>
<aside>
  <div class="brand"><span class="logo">◇</span>ia-router <small id="ver"></small></div>
  <button id="new"><svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>New chat</button>
  <div id="sessions"></div>
  <div class="foot">
    <button id="open-status"><svg viewBox="0 0 24 24"><path d="M3 12h4l3-8 4 16 3-8h4"/></svg>Status &amp; usage<span class="dot" id="dot"></span></button>
  </div>
</aside>
<main>
  <div class="top"><button id="menu" aria-label="Menu"><svg viewBox="0 0 24 24"><path d="M3 6h18M3 12h18M3 18h18"/></svg></button><b>ia-router</b></div>
  <div id="banner"></div>
  <div id="log"><div class="col" id="thread"></div></div>
  <div class="col dock">
    <div class="composer">
      <textarea id="task" rows="1" placeholder="Ask anything. The router picks the model." autofocus></textarea>
      <div class="tools">
        <span class="pill" title="Which model answers. Auto picks by metrics.">Model <select id="model"><option value="auto">Auto</option></select></span>
        <span class="pill" title="Which MCP connectors the model may use. Auto gives it only the ones the task needs.">Connectors <select id="connectors"><option value="auto">Auto</option><option value="on">All</option><option value="off">None</option></select></span>
        <span class="pill" id="cmp-pill" title="Runs the task on two models and shows both answers. Spends quota on each."><label><input type="checkbox" id="compare">Compare</label></span>
        <button id="send" aria-label="Send" disabled><svg viewBox="0 0 24 24"><path d="M12 19V5M5 12l7-7 7 7"/></svg></button>
      </div>
    </div>
    <div class="note">Runs on your own CLIs and subscriptions. Local only: nothing leaves this machine except what the CLIs send.</div>
  </div>
</main>
<dialog id="status"><div class="dhead">Status &amp; usage<button id="close-status" aria-label="Close"><svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg></button></div><div class="dbody" id="status-body"></div></dialog>
<script>
const TOKEN = "__TOKEN__";
const $ = id => document.getElementById(id);
const ICON = {
  copy: '<svg viewBox="0 0 24 24"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/></svg>',
  redo: '<svg viewBox="0 0 24 24"><path d="M3 12a9 9 0 0 1 15.5-6.2L21 8M21 3v5h-5M21 12a9 9 0 0 1-15.5 6.2L3 16M3 21v-5h5"/></svg>',
  send: '<svg viewBox="0 0 24 24"><path d="M12 19V5M5 12l7-7 7 7"/></svg>', stop: '<svg viewBox="0 0 24 24" style="fill:currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>'
};
let session = null, busy = false, ctrl = null, lastTask = "", health = {};
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

function answerHtml(r) {
  if (!r.ok) return '<div class="err">' + esc(r.error || "failed") + "</div>" + (r.attempts.length ? '<div class="meta">' + esc(r.attempts.map(a => a.model + ": " + (a.error || "ok")).join("; ")) + "</div>" : "");
  return '<div class="meta">' + metaLine(r) + '</div><div class="answer">' + md(r.output) + "</div>";
}

function hero() {
  $("thread").innerHTML = '<div class="hero"><div class="logo">◇</div><h2>ia-router</h2><p>Your AI subscriptions, one chat. The router picks the best model for each task, and falls back to another if one is rate limited.</p><div class="cards">' +
    SUGGESTIONS.map(s => '<button class="card" data-suggest="' + esc(s) + '">' + esc(s) + "</button>").join("") + "</div></div>";
}
function put(html, cls) {
  const t = $("thread"); if (t.querySelector(".hero")) t.innerHTML = "";
  const d = document.createElement("div"); d.className = "msg " + cls; d.innerHTML = html; t.appendChild(d); scroll(); return d;
}
const scroll = () => { const l = $("log"); l.scrollTop = l.scrollHeight; };
const user = text => put('<div class="bubble">' + esc(text) + "</div>", "user");
const bot = html => put('<div class="avatar">◇</div><div class="body">' + html + "</div>", "bot");

function setBusy(on) {
  busy = on; const b = $("send"); b.classList.toggle("stop", on); b.innerHTML = on ? ICON.stop : ICON.send; b.setAttribute("aria-label", on ? "Stop" : "Send"); b.disabled = !on && !$("task").value.trim();
}

async function send(text) {
  const task = (text || $("task").value).trim();
  if (!task || busy) return;
  lastTask = task; $("task").value = ""; grow(); setBusy(true);
  const compare = $("compare").checked;
  user(task);
  const msg = bot('<div class="status">Routing…</div><div class="answer raw"></div>'), body = msg.querySelector(".body");
  const status = body.children[0], raw = body.children[1];
  ctrl = new AbortController();
  try {
    const resp = await api("/api/ask", {task, model: $("model").value, connectors: $("connectors").value, compare, session}, ctrl.signal);
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
  $("cmp-pill").classList.toggle("on", $("compare").checked);
  $("banner").innerHTML = st.advice.length ? '<div class="banner">' + esc(st.advice.join("\n")) + "</div>" : "";
  $("dot").className = "dot " + (st.ready.length ? (st.usage_warnings.length ? "" : "ok") : "bad");
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
  const rows = Object.entries(st.usage);
  $("status-body").innerHTML = "<h3>CLIs</h3>" + st.models.map(m => '<div class="model"><span>' + (m.installed && m.auth !== "missing" ? '<span class="ok">✔</span>' : '<span class="bad">✗</span>') + " " + esc(m.name) +
      '</span><span class="dim small">' + esc(!m.installed ? "not installed" : m.auth === "missing" ? "not logged in" : (m.model_id || "ready")) + "</span></div>").join("") +
    (st.advice.length ? '<div class="small warn" style="white-space:pre-wrap;margin-top:6px">' + esc(st.advice.join("\n")) + "</div>" : "") +
    "<h3>Usage (tokens)</h3>" + (rows.length ? "<table><tr><th></th><th>5h</th><th>24h</th><th>7d</th><th>vs limit</th></tr>" + rows.map(([n, r]) => "<tr><td>" + esc(n) + "</td><td>" + k(r.tokens_window) + "</td><td>" + k(r.tokens_24h) + "</td><td>" + k(r.tokens_7d) + "</td><td>" + (r.used_ratio == null ? "n/a" : Math.round(r.used_ratio * 100) + "%") + "</td></tr>").join("") +
      "</table>" + st.usage_warnings.map(w => '<div class="small warn">⚠ ' + esc(w) + "</div>").join("") : '<div class="dim small">Nothing recorded yet.</div>') +
    "<h3>Connectors</h3>" + (st.connectors.length ? st.connectors.map(c => '<div class="model"><span>' + esc(c.name) + '</span><span class="dim small">' + (c.enabled ? "on" : "off") + (c.remote ? " · remote" : "") + "</span></div>").join("") :
      '<div class="dim small">None yet. Add one with <code>ia-router connectors add</code>.</div>') +
    "<h3>Conversations</h3><div class=\"dim small\">" + (st.sessions_enabled ? "Saved on this machine only (readable just by you)." : "Saving is off.") + "</div>";
}

async function openSession(id) {
  const resp = await api("/api/sessions/" + id);
  if (!resp.ok) return newChat();
  const d = await resp.json(); session = d.id; $("thread").innerHTML = ""; history.replaceState(null, "", "#" + session);
  d.turns.forEach(t => { user(t.user); const m = bot('<div class="meta">' + esc(t.model) + '</div><div class="answer">' + md(t.answer) + "</div>" + actions()); m.querySelector(".body")._raw = t.answer; });
  document.body.classList.remove("open"); refresh(); $("log").style.scrollBehavior = "auto"; scroll(); $("log").style.scrollBehavior = "";
}
function newChat() { session = null; history.replaceState(null, "", location.pathname + location.search); hero(); document.body.classList.remove("open"); refresh(); $("task").focus(); }

document.addEventListener("click", async e => {
  const t = e.target.closest("[data-act],[data-suggest],[data-del],.sess");
  if (!t) return;
  const act = t.dataset.act;
  if (t.dataset.suggest) { $("task").value = t.dataset.suggest; grow(); $("task").focus(); }
  else if (t.dataset.del) { e.stopPropagation(); await api("/api/sessions/delete", {id: t.dataset.del}); if (t.dataset.del === session) newChat(); else refresh(); }
  else if (act === "copy-code") { try { await navigator.clipboard.writeText(t.closest(".codebox").querySelector("code").textContent); t.textContent = "Copied"; setTimeout(() => t.textContent = "Copy", 1200); } catch (_) {} }
  else if (act === "copy") { try { await navigator.clipboard.writeText(t.closest(".body")._raw || ""); } catch (_) {} }
  else if (act === "redo") { if (lastTask) send(lastTask); }
  else if (t.classList.contains("sess")) openSession(t.dataset.id);
});
$("new").onclick = newChat;
$("menu").onclick = () => document.body.classList.toggle("open");
$("send").onclick = () => busy ? ctrl && ctrl.abort() : send();
$("compare").onchange = () => $("cmp-pill").classList.toggle("on", $("compare").checked);
$("open-status").onclick = () => { renderStatus(); $("status").showModal(); };
$("close-status").onclick = () => $("status").close();
$("status").addEventListener("click", e => { if (e.target === $("status")) $("status").close(); });
$("task").addEventListener("input", grow);
$("task").addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); } });
hero(); refresh();
if (location.hash.length > 1) openSession(location.hash.slice(1));
</script>
</body>
</html>
"""
