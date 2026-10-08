"""The single page served by `ia-router ui` (HTML, CSS and JS inline: no build step, no external requests, no dependencies).

`__TOKEN__` is replaced per run. Answers are rendered by a tiny markdown renderer that escapes everything first, so model output can never inject HTML.
"""

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ia-router</title>
<style>
:root { --bg:#fff; --panel:#f5f6f8; --line:#e2e4e9; --text:#1b1f27; --dim:#6b7280; --accent:#4f46e5; --ok:#15803d; --bad:#b91c1c; --warn:#b45309; --code:#eef0f4; }
@media (prefers-color-scheme: dark) { :root { --bg:#0f1115; --panel:#171a21; --line:#272b35; --text:#e6e8ee; --dim:#8b93a3; --accent:#818cf8; --ok:#4ade80; --bad:#f87171; --warn:#fbbf24; --code:#1e222b; } }
* { box-sizing:border-box; }
body { margin:0; height:100vh; display:flex; background:var(--bg); color:var(--text); font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif; }
aside { width:290px; flex:none; background:var(--panel); border-right:1px solid var(--line); padding:16px; overflow-y:auto; display:flex; flex-direction:column; gap:18px; }
main { flex:1; display:flex; flex-direction:column; min-width:0; }
h1 { font-size:17px; margin:0; } h1 small { color:var(--dim); font-weight:400; }
h2 { font-size:11px; text-transform:uppercase; letter-spacing:.08em; color:var(--dim); margin:0 0 6px; }
button, select, textarea { font:inherit; color:inherit; }
button { background:var(--bg); border:1px solid var(--line); border-radius:8px; padding:6px 12px; cursor:pointer; }
button:hover { border-color:var(--accent); } button.primary { background:var(--accent); border-color:var(--accent); color:#fff; } button:disabled { opacity:.5; cursor:default; }
select { background:var(--bg); border:1px solid var(--line); border-radius:8px; padding:5px 8px; }
.row { display:flex; align-items:center; gap:8px; } .dim { color:var(--dim); } .small { font-size:12px; }
.model { display:flex; justify-content:space-between; gap:8px; font-size:13px; } .ok { color:var(--ok); } .bad { color:var(--bad); } .warn { color:var(--warn); }
.sess { display:flex; align-items:center; gap:6px; padding:4px 6px; border-radius:6px; cursor:pointer; font-size:13px; }
.sess:hover, .sess.on { background:var(--line); } .sess span { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; } .sess b { opacity:0; font-weight:400; color:var(--dim); } .sess:hover b { opacity:1; }
table { width:100%; border-collapse:collapse; font-size:12px; } td, th { padding:2px 0; text-align:right; } td:first-child, th:first-child { text-align:left; }
#log { flex:1; overflow-y:auto; padding:24px max(24px, calc((100% - 820px)/2)); }
.msg { margin:0 0 22px; } .you { background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:10px 14px; white-space:pre-wrap; }
.meta { font-size:12px; color:var(--dim); margin:0 0 6px; } .answer { overflow-wrap:anywhere; } .raw { white-space:pre-wrap; }
.answer pre { background:var(--code); border-radius:8px; padding:10px 12px; overflow-x:auto; } .answer code { background:var(--code); border-radius:4px; padding:1px 4px; font-size:13px; }
.answer pre code { background:none; padding:0; } .answer h3, .answer h4 { margin:14px 0 4px; } .answer ul, .answer ol { padding-left:22px; margin:6px 0; } .answer p { margin:6px 0; }
.status { font-size:12px; color:var(--dim); } .err { color:var(--bad); }
.cmp { display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:14px; } .cmp > div { border:1px solid var(--line); border-radius:12px; padding:10px 14px; min-width:0; }
.banner { margin:12px 24px 0; padding:10px 14px; border:1px solid var(--warn); border-radius:10px; font-size:13px; white-space:pre-wrap; }
footer { border-top:1px solid var(--line); padding:12px max(24px, calc((100% - 820px)/2)); display:flex; flex-direction:column; gap:8px; }
textarea { width:100%; resize:none; background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:10px 14px; outline:none; } textarea:focus { border-color:var(--accent); }
.empty { color:var(--dim); text-align:center; margin-top:18vh; }
@media (max-width: 760px) { aside { display:none; } }
</style>
</head>
<body>
<aside>
  <div><h1>ia-router <small id="ver"></small></h1><div class="small dim">Local only: nothing leaves this machine except what the CLIs send.</div></div>
  <div><button id="new" class="primary" style="width:100%">New conversation</button></div>
  <div><h2>Models</h2><div id="models"></div></div>
  <div><h2>Usage</h2><div id="usage"></div></div>
  <div><h2>Connectors</h2><div id="conns" class="small"></div></div>
  <div><h2>Conversations</h2><div id="sessions"></div></div>
</aside>
<main>
  <div id="banner"></div>
  <div id="log"><div class="empty">Ask anything. The router picks the model.</div></div>
  <footer>
    <textarea id="task" rows="3" placeholder="Write a task (Enter sends, Shift+Enter adds a line)" autofocus></textarea>
    <div class="row">
      <label class="small dim">Model <select id="model"><option value="auto">auto (routed)</option></select></label>
      <label class="small dim">Connectors <select id="connectors"><option value="auto">auto</option><option value="on">all</option><option value="off">none</option></select></label>
      <label class="small dim" title="Runs the task on two models and shows both answers. Spends quota on each."><input type="checkbox" id="compare"> Compare two models</label>
      <span style="flex:1"></span><button id="send" class="primary">Send</button>
    </div>
  </footer>
</main>
<script>
const TOKEN = "__TOKEN__";
const $ = id => document.getElementById(id);
let session = null, busy = false;

const api = (path, body) => fetch(path, body === undefined ? {headers: {"X-Token": TOKEN}} :
  {method: "POST", headers: {"X-Token": TOKEN, "Content-Type": "application/json"}, body: JSON.stringify(body)});
const esc = s => String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

// Minimal markdown: everything is escaped first, then a few constructs are turned into tags.
function md(src) {
  const blocks = [];
  let s = esc(src).replace(/```[^\n]*\n([\s\S]*?)```/g, (_, c) => { blocks.push("<pre><code>" + c + "</code></pre>"); return "\u0000" + (blocks.length - 1) + "\u0000"; });
  const inline = t => t.replace(/`([^`\n]+)`/g, "<code>$1</code>").replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  const out = []; let list = null;
  const close = () => { if (list) { out.push("</" + list + ">"); list = null; } };
  for (const line of s.split("\n")) {
    let m;
    if ((m = line.match(/^(#{1,4})\s+(.*)$/))) { close(); out.push("<h" + (m[1].length < 3 ? 3 : 4) + ">" + inline(m[2]) + "</h" + (m[1].length < 3 ? 3 : 4) + ">"); }
    else if ((m = line.match(/^\s*[-*]\s+(.*)$/))) { if (list !== "ul") { close(); out.push("<ul>"); list = "ul"; } out.push("<li>" + inline(m[1]) + "</li>"); }
    else if ((m = line.match(/^\s*\d+[.)]\s+(.*)$/))) { if (list !== "ol") { close(); out.push("<ol>"); list = "ol"; } out.push("<li>" + inline(m[1]) + "</li>"); }
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
  return esc((r.model || "no model") + (r.model_id ? " (" + r.model_id + ")" : "") + " · in " + k(t.input || 0) + " out " + k(t.output || 0) + " · " + r.seconds + "s · " + why + fb) + esc(money(r.estimated_cost_usd));
}

function add(html, cls) { const d = document.createElement("div"); d.className = "msg " + (cls || ""); d.innerHTML = html; const log = $("log"); if (log.querySelector(".empty")) log.innerHTML = ""; log.appendChild(d); log.scrollTop = log.scrollHeight; return d; }

function renderResult(r) {
  if (!r.ok) return '<div class="err">' + esc(r.error || "failed") + "</div>" + (r.attempts.length ? '<div class="meta">' + esc(r.attempts.map(a => a.model + ": " + (a.error || "ok")).join("; ")) + "</div>" : "");
  return '<div class="meta">' + metaLine(r) + '</div><div class="answer">' + md(r.output) + "</div>";
}

async function send() {
  const task = $("task").value.trim();
  if (!task || busy) return;
  busy = true; $("send").disabled = true; $("task").value = "";
  const compare = $("compare").checked;
  add(esc(task), "you");
  const box = add('<div class="status">… routing</div><div class="answer raw"></div>');
  const status = box.children[0], raw = box.children[1];
  try {
    const resp = await api("/api/ask", {task, model: $("model").value, connectors: $("connectors").value, compare, session});
    if (!resp.ok) throw new Error((await resp.json()).error || resp.status);
    const reader = resp.body.getReader(), dec = new TextDecoder(); let buf = "", text = "";
    for (;;) {
      const {value, done} = await reader.read();
      if (done) break;
      buf += dec.decode(value, {stream: true});
      let i;
      while ((i = buf.indexOf("\n")) >= 0) {
        const ev = JSON.parse(buf.slice(0, i)); buf = buf.slice(i + 1);
        if (ev.type === "text") { text += ev.text; status.textContent = "writing…"; raw.textContent = text; }
        else if (ev.type === "status") status.textContent = "⚙ " + ev.text;
        else if (ev.type === "reset") { text = ""; raw.textContent = ""; status.textContent = "attempt failed, trying the next model…"; }
        else if (ev.type === "error") box.innerHTML = '<div class="err">' + esc(ev.text) + "</div>";
        else if (ev.type === "done") {
          if (ev.compare) box.innerHTML = '<div class="cmp">' + ev.compare.map(renderResult).map(h => "<div>" + h + "</div>").join("") + "</div>";
          else {
            box.innerHTML = renderResult(ev);
            if (ev.session) session = ev.session;
            (ev.usage_warnings || []).forEach(w => box.insertAdjacentHTML("beforeend", '<div class="meta warn">⚠ ' + esc(w) + "</div>"));
          }
        }
      }
    }
  } catch (e) { box.innerHTML = '<div class="err">' + esc(e.message || e) + "</div>"; }
  busy = false; $("send").disabled = false; $("task").focus(); $("log").scrollTop = $("log").scrollHeight;
  refresh();
}

async function refresh() {
  const [st, ss] = await Promise.all([api("/api/state").then(r => r.json()), api("/api/sessions").then(r => r.json())]);
  $("ver").textContent = "v" + st.version;
  $("models").innerHTML = st.models.map(m => '<div class="model"><span>' + (m.installed ? (m.auth === "missing" ? '<span class="bad">✗</span>' : '<span class="ok">✔</span>') : '<span class="bad">✗</span>') + " " + esc(m.name) +
    '</span><span class="dim small">' + esc(!m.installed ? "not installed" : m.auth === "missing" ? "not logged in" : (m.model_id || "ready")) + "</span></div>").join("");
  const sel = $("model"), keep = sel.value;
  sel.innerHTML = '<option value="auto">auto (routed)</option>' + st.models.map(m => '<option value="' + esc(m.name) + '">' + esc(m.name) + "</option>").join(""); sel.value = keep || "auto";
  $("banner").innerHTML = st.advice.length ? '<div class="banner">' + esc(st.advice.join("\n")) + "</div>" : "";
  const rows = Object.entries(st.usage);
  $("usage").innerHTML = rows.length ? "<table><tr class='dim'><th></th><th>5h</th><th>24h</th><th>7d</th><th>limit</th></tr>" + rows.map(([n, r]) =>
    "<tr><td>" + esc(n) + "</td><td>" + k(r.tokens_window) + "</td><td>" + k(r.tokens_24h) + "</td><td>" + k(r.tokens_7d) + "</td><td>" + (r.used_ratio == null ? "n/a" : Math.round(r.used_ratio * 100) + "%") + "</td></tr>").join("") +
    "</table>" + st.usage_warnings.map(w => '<div class="small warn">⚠ ' + esc(w) + "</div>").join("") : '<span class="small dim">Nothing recorded yet.</span>';
  $("conns").innerHTML = st.connectors.length ? st.connectors.map(c => esc(c.name) + (c.enabled ? "" : " (off)")).join(", ") : '<span class="dim">None. Add one with <code>ia-router connectors add</code>.</span>';
  $("sessions").innerHTML = !ss.enabled ? '<span class="small dim">Saving is off.</span>' : !ss.sessions.length ? '<span class="small dim">None yet.</span>' :
    ss.sessions.map(s => '<div class="sess ' + (s.id === session ? "on" : "") + '" data-id="' + esc(s.id) + '"><span>' + esc(s.title) + '</span><b data-del="' + esc(s.id) + '" title="Delete">✕</b></div>').join("");
}

async function openSession(id) {
  const d = await api("/api/sessions/" + id).then(r => r.json());
  session = d.id; $("log").innerHTML = "";
  d.turns.forEach(t => { add(esc(t.user), "you"); add('<div class="meta">' + esc(t.model) + '</div><div class="answer">' + md(t.answer) + "</div>"); });
  refresh();
}

$("sessions").addEventListener("click", async e => {
  const del = e.target.dataset.del;
  if (del) { await api("/api/sessions/delete", {id: del}); if (del === session) newChat(); return refresh(); }
  const row = e.target.closest(".sess"); if (row) openSession(row.dataset.id);
});
function newChat() { session = null; $("log").innerHTML = '<div class="empty">Ask anything. The router picks the model.</div>'; refresh(); $("task").focus(); }
$("new").onclick = newChat;
$("send").onclick = send;
$("task").addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); } });
refresh();
</script>
</body>
</html>
"""
