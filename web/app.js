"use strict";
/* HybridEdge web client — no build step, no external assets (works fully offline). */

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const ICON = {
  cloud: '<svg class="i" viewBox="0 0 20 20"><path d="M6 15h8.5a3.5 3.5 0 0 0 .4-7A5 5 0 0 0 5.2 9.2 3 3 0 0 0 6 15z"/></svg>',
  device: '<svg class="i" viewBox="0 0 20 20"><rect x="3" y="4" width="14" height="9" rx="1.5"/><path d="M1.5 16h17"/></svg>',
  check: '<svg viewBox="0 0 20 20"><path d="M5 10.5l3 3 7-7"/></svg>',
  redo: '<svg viewBox="0 0 20 20"><path d="M15 8a6 6 0 1 0 1 5"/><path d="M15 3v5h-5"/></svg>',
  play: '<svg viewBox="0 0 20 20"><path d="M7 5l8 5-8 5z"/></svg>',
};

const state = {
  status: null,
  threads: [],
  threadId: null,
  thread: null, // server view: {thread, messages, checkpoints, events, running, next}
  live: null, // in-flight turn
  sending: false,
  flows: {}, // turn_id -> flow
  modal: null, // {kind: "flow", id} | {kind: "connection"}
  view: "chat", // "chat" | "settings"
  settings: null,
  prevActive: null,
};

/* ------------------------------------------------------------------ helpers */
function targetName(t) {
  if (!t || t === "edge") return "Device";
  const p = state.status?.providers?.find((x) => x.name === t);
  return p ? p.label : t;
}
function tag(target, extra = "") {
  const edge = !target || target === "edge";
  return `<span class="tag ${edge ? "edge" : "cloud"}">${edge ? ICON.device : ICON.cloud}${esc(targetName(target))}${extra}</span>`;
}
function ago(ts) {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return "Just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return new Date(ts * 1000).toLocaleDateString();
}
const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);
const ROLE_TITLES = { lead: "Workflow", medical: "Medical", fire_hazmat: "Fire & Hazmat", scene_safety: "Scene Safety", comms: "Command & Comms", generalist: "Field Assistant" };
const clock = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

const SOURCE = { "incident-command": "Incident Command", patrol: "Patrol", bench: "Benchmark", test: "Test", api: "API" };

function md(src) {
  const lines = esc(src || "").split("\n");
  let html = "", list = null, table = null;
  const inline = (s) => s
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
    .replace(/&lt;br\s*\/?&gt;/gi, "<br>");
  const closeTable = () => {
    if (!table) return;
    const [head, ...rows] = table.head ? table : [null, ...table];
    html += `<div class="md-table"><table>${head ? `<thead><tr>${head.map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead>` : ""}<tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
    table = null;
  };
  const close = () => { closeTable(); if (list) { html += `</${list}>`; list = null; } };
  for (const raw of lines) {
    const line = raw.trimEnd();
    let m;
    if (/^\s*\|.*\|\s*$/.test(line)) {
      if (list) { html += `</${list}>`; list = null; }
      const cells = line.trim().slice(1, -1).split("|").map((c) => c.trim());
      if (cells.every((c) => /^:?-{2,}:?$/.test(c))) { if (table && table.length === 1) table.head = true; continue; }
      (table ||= []).push(cells);
      continue;
    }
    closeTable();
    if ((m = line.match(/^\s*#{1,4}\s+(.*)$/))) { close(); html += `<h3>${inline(m[1])}</h3>`; }
    else if ((m = line.match(/^\s*[-*•]\s+(.*)$/))) { if (list !== "ul") { close(); html += "<ul>"; list = "ul"; } html += `<li>${inline(m[1])}</li>`; }
    else if ((m = line.match(/^\s*\d+[.)]\s+(.*)$/))) { if (list !== "ol") { close(); html += "<ol>"; list = "ol"; } html += `<li>${inline(m[1])}</li>`; }
    else if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) { close(); html += "<hr>"; }
    else if (!line.trim()) { close(); }
    else { close(); html += `<p>${inline(line)}</p>`; }
  }
  close();
  return html;
}

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "content-type": "application/json" }, ...opts });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}

/* ------------------------------------------------------------------- status */
let pollTimer = 0;
async function pollStatus() {
  clearTimeout(pollTimer);
  try {
    const s = await api("/api/status");
    const prev = state.prevActive;
    const settled = state.status?.last_check != null; // ignore the first probe after a server start
    state.status = s;
    state.prevActive = s.active;
    if (prev && settled && prev !== s.active) onLinkChange(prev, s.active);
  } catch {
    state.status = null;
  }
  renderStatus();
  clearTimeout(pollTimer); // an event-triggered poll may have run concurrently: keep exactly one loop
  pollTimer = setTimeout(pollStatus, 1000);
}

function onLinkChange(from, to) {
  if (to === "edge") {
    toast(`Cloud link lost — continuing on this device`);
  } else if (from === "edge") {
    const canRerun = lastTurnDegraded() && !state.sending;
    toast(`${targetName(to)} is back online`, canRerun ? { label: "Re-run last turn on cloud", fn: rerun } : null);
  } else {
    toast(`Switched cloud: ${targetName(from)} → ${targetName(to)}`);
  }
  renderFeed();
}

function renderStatus() {
  const s = state.status;
  const pill = $("#linkPill");
  const text = pill.querySelector(".link-text");
  pill.classList.remove("cloud", "edge");
  if (!s) {
    text.textContent = "Edge server unreachable";
  } else if (s.active !== "edge") {
    const p = s.providers.find((x) => x.name === s.active);
    pill.classList.add("cloud");
    text.innerHTML = `${esc(p.label)} <span class="hw">· ${esc(p.hardware)}</span> ${p.latency_ms != null ? `<span class="lat">${p.latency_ms} ms</span>` : ""}`;
  } else {
    pill.classList.add("edge");
    text.innerHTML = `On-device <span class="hw">· ${esc(s.edge.model)}</span>`;
  }

  renderStatusBar(s);

  if (!$("#linkPop").hidden) renderPopover();
  if (state.view === "settings") renderSettings();
  $("#send").disabled = state.sending || !$("#input").value.trim();
}

function connectionInfo(s) {
  if (!s) return { cls: "sb-down", title: "Edge server unreachable", why: "The app can't reach the HybridEdge server on this device. Restart it with ./start.sh." };
  if (s.active !== "edge") {
    const p = s.providers.find((x) => x.name === s.active);
    return { cls: "sb-online", title: "Connected", short: `${p.label}`, why: `${p.label} is ${s.providers[0].name === p.name ? "the primary cloud and healthy" : "taking work because higher-priority clouds aren't available"}.` };
  }
  if (!s.edge.ok) return { cls: "sb-down", title: "No model available", why: "No cloud is reachable and the on-device model (Ollama) isn't running." };
  if (!s.network.up) return { cls: "sb-nonet", title: "Offline", short: "No network", why: "This device has no network, so agents run on the on-device model. It checks for a connection twice a second and moves work back to the cloud the moment one is available — even part-way through a step." };
  if (s.offline) return { cls: "sb-edge", title: "Offline", short: "Clouds disconnected", why: "All cloud links are cut by an active drill, so agents run on this device. End the drill to reconnect." };
  if (s.reconnecting) return { cls: "sb-reconnect", title: "Reconnecting…", why: "The network is back; checking cloud providers. Work continues on this device meanwhile." };
  const reasons = s.providers.filter((p) => p.configured).map((p) => `${p.label}: ${p.disabled ? "outage drill on" : p.last_error || "unreachable"}`);
  return { cls: "sb-edge", title: "Offline", short: "No cloud reachable", why: `No cloud provider is responding (${reasons.join("; ")}). Agents run on this device. Clouds are re-checked every second, and work moves back the moment one answers — even part-way through a step.` };
}

function renderDrillBar(s) {
  const drills = [];
  if (s && s.offline) drills.push("all clouds disconnected");
  for (const p of (s && s.providers) || []) if (p.disabled) drills.push(`${p.label} outage`);
  const bar = $("#drillBar");
  bar.hidden = !drills.length;
  if (drills.length) $("#drillText").textContent = drills.map((d) => d[0].toUpperCase() + d.slice(1)).join(" · ");
}

function renderStatusBar(s) {
  renderDrillBar(s);
  const bar = $("#statusBar");
  const c = connectionInfo(s);
  bar.className = `statusbar ${c.cls}`;
  bar.querySelector(".sb-title").textContent = c.title;
  bar.querySelector(".sb-detail").textContent = c.short ? `· ${c.short}` : "";
  requestAnimationFrame(() => document.documentElement.style.setProperty("--sb-h", bar.offsetHeight + "px"));
  document.title = s && s.active !== "edge" ? "HybridEdge" : "HybridEdge · Offline";
  if (state.modal && state.modal.kind === "connection") renderModal();
}

/* -------------------------------------------------------------------- modal */
function openModal(m) {
  state.modal = m;
  $("#modal").hidden = false;
  renderModal();
  $("#modalClose").focus();
}
function closeModal() {
  state.modal = null;
  $("#modal").hidden = true;
}
function renderModal() {
  const m = state.modal;
  if (!m) return;
  const s = state.status;
  if (m.kind === "flow") {
    const flow = state.flows[m.id];
    $("#modalTitle").textContent = "How this task ran";
    $("#modalBody").innerHTML = flow ? HybridFlow.render(flow) : `<p class="muted">No flow recorded for this task.</p>`;
    return;
  }
  const c = connectionInfo(s);
  $("#modalTitle").textContent = "Connection";
  if (!s) { $("#modalBody").innerHTML = `<p>${esc(c.why)}</p>`; return; }
  const rows = s.providers.map((p) => {
    const [cls, text] = providerState(p, s);
    return `<div class="prov"><div class="grow"><div class="n">${esc(p.label)}</div><div class="d">${esc(p.hardware)} · ${esc(p.model)}</div></div><span class="pill ${cls}">${esc(text)}</span></div>`;
  }).join("");
  const cap = s.capability || {};
  $("#modalBody").innerHTML = `
    <div class="conn-status ${c.cls}"><span class="sb-dot"></span><b>${esc(c.title)}</b></div>
    <p class="conn-why">${esc(c.why)}</p>
    ${s.offline || s.providers.some((p) => p.disabled) ? `<p class="conn-drill"><span class="drill-tag">Drill</span> Simulated</p>` : ""}
    <dl class="kv conn-kv">
      <dt>Agents run on</dt><dd>${esc(s.active === "edge" ? `This device · ${s.edge.model}` : `${targetName(s.active)} · ${cap.model || ""}`)}</dd>
      <dt>Capacity</dt><dd>${esc(cap.summary || "")}</dd>
      <dt>Network</dt><dd>${s.network.up ? "Connected" : "No network on this device"}</dd>
    </dl>
    <h4 class="conn-h">Failover order</h4>
    ${rows}
    <div class="prov"><div class="grow"><div class="n">This device</div><div class="d">${esc(s.edge.model)}</div></div><span class="pill ${s.edge.ok ? (s.active === "edge" ? "warn" : "idle") : "bad"}">${s.edge.ok ? (s.active === "edge" ? "In use" : "Ready") : "Not running"}</span></div>
    <div class="conn-actions"><button class="btn-sm" data-open-settings>Settings and test mode</button></div>`;
}

function providerState(p, s) {
  if (!p.configured) return ["idle", "Not set up"];
  if (!s.network.up) return ["bad", "No network"];
  if (s.offline || p.disabled) return ["warn", "Drill: disconnected"];
  if (p.usable) return ["ok", s.active === p.name ? `Connected · ${p.latency_ms ?? "–"} ms` : `Ready · ${p.latency_ms ?? "–"} ms`];
  if (p.idle) return ["idle", "Standby"];
  return ["bad", "Unreachable"];
}

function renderPopover() {
  const s = state.status;
  if (!s) return;
  const rows = s.providers.map((p) => {
    const [cls, text] = providerState(p, s);
    return `<div class="prov"><span class="sdot ${cls === "ok" ? "ok" : cls === "idle" ? "idle" : cls === "warn" ? "edge" : "bad"}"></span><div class="grow"><div class="n">${esc(p.label)}</div><div class="d">${esc(p.hardware)} · Cloud</div></div><span class="pill ${cls}">${esc(text)}</span></div>`;
  }).join("");
  $("#linkPop").innerHTML = `<div class="pop-h">Where agents run</div>${rows}
    <div class="prov"><span class="sdot ${s.edge.ok ? "edge" : "bad"}"></span><div class="grow"><div class="n">This device</div><div class="d">${esc(s.edge.model)} · Used when offline</div></div><span class="pill ${s.edge.ok ? (s.active === "edge" ? "warn" : "idle") : "bad"}">${s.edge.ok ? (s.active === "edge" ? "In use" : "Ready") : "Not running"}</span></div>
    <div class="pop-sep"></div>
    <button class="pop-link" data-open-settings>Settings and test mode →</button>`;
}

async function setLink(body) {
  state.status = await api("/api/link", { method: "POST", body: JSON.stringify(body) });
  const prev = state.prevActive;
  state.prevActive = state.status.active;
  if (prev && prev !== state.status.active) onLinkChange(prev, state.status.active);
  renderStatus();
}

/* ----------------------------------------------------------------- settings */
async function openSettings() {
  closeSettings();
  state.view = "settings";
  try { state.settings = await api("/api/settings"); } catch { state.settings = null; }
  showPage("#settings", "#settingsBtn", "Settings");
  renderSettings();
}

async function openBench() {
  closeSettings();
  state.view = "bench";
  showPage("#bench", "#benchBtn", "Benchmarks");
  $("#bench").innerHTML = `<div class="col"><p class="muted">Loading…</p></div>`;
  try { state.bench = await api("/api/benchmarks"); } catch { state.bench = null; }
  renderBench();
}

function showPage(sel, btn, title) {
  $("#feed").hidden = true;
  $(".composer-wrap").hidden = true;
  $(sel).hidden = false;
  $(btn).classList.add("on");
  $("#title").textContent = title;
  $("#sidebar").classList.remove("open");
}

// Closes whichever full-page view (settings, benchmarks) is open and returns to the incident.
function closeSettings() {
  if (state.view === "chat") return;
  state.view = "chat";
  $("#feed").hidden = false;
  $(".composer-wrap").hidden = false;
  for (const id of ["#settings", "#bench"]) $(id).hidden = true;
  for (const id of ["#settingsBtn", "#benchBtn"]) $(id).classList.remove("on");
  $("#title").textContent = state.thread?.thread?.title || "New incident";
}

/* --------------------------------------------------------------- benchmarks */
const NAMES = { gsm8k: "GSM8K", bfcl_simple: "BFCL simple", bfcl_multiple: "BFCL multiple", bfcl_parallel: "BFCL parallel" };
const hbar = (v, cls, who, tip) => `<div class="bb-row" title="${esc(who)}: ${esc(tip)}"><span class="bb-who">${who}</span><span class="bb-track"><i class="${cls}" style="width:${v}%"></i></span><span class="bb-val">${v}%</span></div>`;

// Accuracy vs link-loss rate. Cloud only loses cut answers; HybridEdge answers them on the device.
function curve(c, d, p0, measured) {
  const W = 820, H = 260, L = 44, R = 16, T = 14, B = 38;
  const x = (p) => L + p * (W - L - R), y = (v) => T + (1 - v / 100) * (H - T - B);
  const line = (f) => Array.from({ length: 21 }, (_, i) => i / 20).map((p, i) => `${i ? "L" : "M"}${x(p).toFixed(1)},${y(f(p)).toFixed(1)}`).join("");
  const hyb = (p) => c * (1 - p) + d * p, co = (p) => c * (1 - p);
  const grid = [0, 25, 50, 75, 100].map((v) => `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" class="cv-grid"/><text x="${L - 8}" y="${y(v) + 4}" class="cv-ax" text-anchor="end">${v}%</text>`).join("");
  const xt = [0, 0.25, 0.5, 0.75, 1].map((p) => `<text x="${x(p)}" y="${H - 12}" class="cv-ax" text-anchor="middle">${p * 100}%</text>`).join("");
  return `<div class="cv" data-c="${c}" data-d="${d}"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="GSM8K accuracy versus link loss">
    ${grid}${xt}<text x="${(L + W - R) / 2}" y="${H}" class="cv-ax" text-anchor="middle">Answers with the link cut</text>
    <path d="${line(co)}" class="cv-l c"/><path d="${line(() => d)}" class="cv-l d"/><path d="${line(hyb)}" class="cv-l h"/>
    <circle cx="${x(p0)}" cy="${y(measured)}" r="5" class="cv-dot"/>
    <text x="${x(0.98)}" y="${y(hyb(0.98)) - 22}" class="cv-lab" text-anchor="end">HybridEdge</text><text x="${x(0.6)}" y="${y(co(0.6)) - 10}" class="cv-lab">Cloud only</text><text x="${x(0.02)}" y="${y(d) + 18}" class="cv-lab">Device only</text>
    <line class="cv-hair" x1="0" x2="0" y1="${T}" y2="${H - B}" style="display:none"/></svg><div class="cv-tip" hidden></div></div>`;
}

function scenBars(scen) {
  const max = Math.max(...scen.map((x) => x.total_s || 0), 1);
  return scen.map((x) => {
    const where = [x.advisers_cloud ? `${x.advisers_cloud} on cloud` : "", x.advisers_device ? `${x.advisers_device} on device` : "", x.deferred && x.deferred.length && !x.advisers_cloud ? `${x.deferred.length} wait for cloud` : ""].filter(Boolean).join(" · ");
    return `<div class="sc-row" title="${esc(x.label)}: brief done at ${x.total_s} s · ${esc(where)}"><span class="sc-l">${esc(x.label)}</span>
      <span class="bb-track"><i class="${x.advisers_cloud ? "h" : "d"}" style="width:${(100 * x.total_s) / max}%"></i></span><span class="bb-val">${x.total_s} s</span><span class="sc-w">${esc(where)}</span></div>`;
  }).join("");
}

document.addEventListener("mousemove", (e) => {
  const cv = e.target.closest && e.target.closest(".cv");
  document.querySelectorAll(".cv .cv-hair").forEach((h) => { if (!cv || !cv.contains(h)) h.style.display = "none"; });
  document.querySelectorAll(".cv .cv-tip").forEach((t) => { if (!cv || !cv.contains(t)) t.hidden = true; });
  if (!cv) return;
  const svg = cv.querySelector("svg"), r = svg.getBoundingClientRect();
  const sx = ((e.clientX - r.left) / r.width) * 820, p = Math.min(1, Math.max(0, (sx - 44) / (820 - 60)));
  const c = +cv.dataset.c, d = +cv.dataset.d, h = cv.querySelector(".cv-hair"), tip = cv.querySelector(".cv-tip");
  h.setAttribute("x1", 44 + p * 760); h.setAttribute("x2", 44 + p * 760); h.style.display = "";
  tip.hidden = false; tip.style.left = `${Math.min(r.width - 170, (e.clientX - r.left) + 12)}px`;
  tip.innerHTML = `<b>${Math.round(p * 100)}% cut</b><span><i class="h"></i>HybridEdge ${(c * (1 - p) + d * p).toFixed(1)}%</span><span><i class="c"></i>Cloud only ${(c * (1 - p)).toFixed(1)}%</span><span><i class="d"></i>Device only ${d.toFixed(1)}%</span>`;
});

function renderBench() {
  const b = state.bench;
  const el = $("#bench");
  if (!b || !b.models) { el.innerHTML = `<div class="col"><div class="err">No benchmark results yet. Run <code>uv run python bench/models.py</code> and <code>bench/system.py</code>.</div></div>`; return; }
  const m = b.models, sys = b.system || {};
  const cloud = m.cloud || {}, dev = m.device || {};
  const ROWS = [
    ["gsm8k", "GSM8K", "Grade-school maths, multi-step reasoning"],
    ["bfcl_simple", "BFCL simple", "One function call from one tool"],
    ["bfcl_multiple", "BFCL multiple", "Pick the right tool from several"],
    ["bfcl_parallel", "BFCL parallel", "Several calls in one turn"],
  ].filter(([k]) => cloud[k] || dev[k]);
  const bar = (r, cls, who) => r ? `<div class="bb-row" title="${who}: ${r.correct} of ${r.n} correct · median ${r.p50_s}s per item">
      <span class="bb-who">${who}</span><span class="bb-track"><i class="${cls}" style="width:${r.accuracy}%"></i></span><span class="bb-val">${r.accuracy}%</span></div>` : "";
  const quality = ROWS.map(([k, name, desc]) => `<div class="bb">
      <div class="bb-h"><b>${name}</b><span class="muted">${desc}</span><span class="bb-n">n = ${(cloud[k] || dev[k]).n}${cloud[k] && dev[k] && cloud[k].n !== dev[k].n ? ` / ${dev[k].n}` : ""}</span></div>
      ${bar(cloud[k], "c", "Cloud")}${bar(dev[k], "d", "Device")}</div>`).join("");
  const tools = ["bfcl_simple", "bfcl_multiple", "bfcl_parallel"].filter((k) => cloud[k] && dev[k]);
  const keep = tools.length ? Math.round(100 * tools.reduce((a, k) => a + dev[k].accuracy, 0) / tools.reduce((a, k) => a + cloud[k].accuracy, 0)) : null;
  const sc = sys.scenarios || {};
  const scen = Object.values(sc);
  const fo = sys.failover, cp = sys.checkpoint;
  const tile = (v, l, sub) => `<div class="kpi"><div class="kpi-v">${v}</div><div class="kpi-l">${l}</div>${sub ? `<div class="kpi-s">${sub}</div>` : ""}</div>`;
  const hy = b.hybrid;
  const hyKeys = hy ? ["gsm8k", "bfcl_simple", "bfcl_multiple", "bfcl_parallel"].filter((k) => hy[k] && cloud[k]) : [];
  const cloudOnly = (k) => Math.round(10 * cloud[k].accuracy * (hy[k].n - hy[k].cut) / hy[k].n) / 10; // cut items fail without a fallback
  const hyN = hyKeys.reduce((a, k) => a + hy[k].n, 0), hyCut = hyKeys.reduce((a, k) => a + hy[k].cut, 0);
  const kpis = [
    hy ? tile(`${hyN} of ${hyN}`, `Answers delivered with ${Math.round(hy.outage * 100)}% link loss`, `Cloud only: ${hyN - hyCut} of ${hyN}`) : "",
    hy && hy.gsm8k ? tile(`${hy.gsm8k.accuracy}%`, "GSM8K through link loss", `Cloud only ${cloudOnly("gsm8k")}% · device only ${dev.gsm8k ? dev.gsm8k.accuracy : "–"}%`) : "",
    scen.length ? tile(`${scen.filter((x) => x.completed).length} of ${scen.length}`, "Network scenarios completed", "Full MTA workflow, every brief delivered") : "",
    fo && fo.resume_ms_p50 != null ? tile(`${(fo.resume_ms_p50 / 1000).toFixed(1)} s`, "Link lost → device writing", `Median of ${fo.n} cuts mid-answer`) : "",
    cp ? tile(`${cp.ms_per_checkpoint} ms`, "Per durable checkpoint", "SQLite, written before every step") : "",
  ].join("");
  const scRows = scen.map((x) => `<tr><td>${esc(x.label)}</td><td>${x.completed ? `<span class="ok-t">Completed</span>` : `<span class="bad-t">Failed</span>`}</td>
      <td>${x.first_brief_s ?? "—"} s</td><td>${x.total_s ?? "—"} s</td>
      <td>${x.advisers_cloud ? `<span class="tag cloud">${x.advisers_cloud} cloud</span>` : ""}${x.advisers_device ? `<span class="tag edge">${x.advisers_device} device</span>` : ""}</td>
      <td>${x.deferred && x.deferred.length ? (x.advisers_cloud ? `${x.deferred.length} → cloud` : `${x.deferred.length} waiting`) : "—"}</td>
      <td>${x.review ? (x.review.device ? "Rule checks" : `${x.review.issues} issues`) : "—"}</td><td>${x.checkpoints || "—"}</td></tr>`).join("");
  const foRuns = fo ? fo.runs.map((r) => r.resume_ms).filter((x) => x != null) : [];
  const foMax = Math.max(1, ...foRuns, 1);
  const foViz = fo ? `<div class="card"><div class="fo">${fo.runs.map((r, i) => `<div class="fo-row" title="Run ${i + 1}: detected in ${r.detect_ms} ms, device writing after ${r.resume_ms} ms">
      <span class="bb-who">Run ${i + 1}</span><span class="bb-track"><i class="d" style="width:${Math.max(2, (100 * (r.resume_ms || 0)) / foMax)}%"></i><em class="fo-det" style="left:${(100 * (r.detect_ms || 0)) / foMax}%"></em></span>
      <span class="bb-val">${r.resume_ms != null ? (r.resume_ms / 1000).toFixed(2) + " s" : "—"}</span></div>`).join("")}</div>
      <p class="muted bb-foot">Marker: cut detected and the step handed to the device (median ${fo.detect_ms_p50} ms). Bar: first token from the device model, continuing the same answer. ${fo.completed} of ${fo.n} answers completed.</p></div>` : "";
  el.innerHTML = `<div class="col bench">
    <div class="kpis">${kpis}</div>
    ${hy ? `<div class="set-sec"><h2>Through link loss</h2><p>The same items with the cloud link cut mid-answer on ${Math.round(hy.outage * 100)}% of them (${hyCut} of ${hyN}). HybridEdge finishes those on the device; a cloud-only agent loses them.</p>
      <div class="card">${hyKeys.map((k) => `<div class="bb"><div class="bb-h"><b>${NAMES[k]}</b><span class="bb-n">n = ${hy[k].n} · ${hy[k].cut} cut</span></div>
        ${hbar(hy[k].accuracy, "h", "HybridEdge", `${hy[k].accuracy}% — measured, every item answered`)}${hbar(cloudOnly(k), "c", "Cloud only", `${cloudOnly(k)}% — cut items fail`)}${dev[k] ? hbar(dev[k].accuracy, "d", "Device only", `${dev[k].accuracy}% — measured`) : ""}</div>`).join("")}
        <div class="bb-legend"><span><i class="h"></i>HybridEdge (measured)</span><span><i class="c"></i>Cloud only (cut items fail)</span><span><i class="d"></i>Device only (measured)</span></div></div></div>
      ${hy.gsm8k && dev.gsm8k ? `<div class="set-sec"><h2>Any amount of link loss</h2><p>GSM8K accuracy as the share of cut answers grows, from each tier's measured accuracy. The dot is the measured HybridEdge run.</p><div class="card">${curve(cloud.gsm8k.accuracy, dev.gsm8k.accuracy, hy.outage, hy.gsm8k.accuracy)}</div></div>` : ""}` : ""}
    ${scen.length ? `<div class="set-sec"><h2>Time to commander's brief</h2><p>Full marauding-attack workflow, three field reports. Every scenario delivered a brief.</p><div class="card">${scenBars(scen)}</div></div>` : ""}
    <div class="set-sec"><h2>Each tier alone</h2><p>Each tier in its production setup: Nemotron 3.5 Lightning 30B on Verda with reasoning, Nemotron 3.5 Bolt E4B on this MacBook Air without.</p>
      <div class="card">${quality}<div class="bb-legend"><span><i class="c"></i>Cloud · Lightning 30B</span><span><i class="d"></i>Device · Bolt E4B</span></div></div></div>
    ${scen.length ? `<div class="set-sec"><h2>Network scenarios</h2><p>The full marauding-attack workflow (5 advisers, review, brief) from three field reports.</p>
      <div class="card tbl-wrap"><table class="tbl"><thead><tr><th>Network</th><th>Result</th><th>Brief starts</th><th>Done</th><th>Advisers</th><th>Deferred</th><th>Review</th><th>Checkpoints</th></tr></thead><tbody>${scRows}</tbody></table></div></div>` : ""}
    ${fo ? `<div class="set-sec"><h2>Failover</h2><p>The link is cut mid-answer; the same answer carries on from this device.</p>${foViz}</div>` : ""}
    <p class="muted bb-foot">GSM8K test set and BFCL v3 (AST match), first n items, temperature 0. Link loss is injected 1–4 s into the cloud answer; cut items restart on the device, as the router does. Updated ${esc(b.updated || "")}. Re-run with <code>bench/models.py</code> and <code>bench/system.py</code>.</p>
  </div>`;
}

function drillsHTML(s) {
  if (!s) return "";
  const row = (title, desc, active, attrs) => `<div class="drill-row"><div class="grow"><div class="n">${title}</div><div class="d">${desc}</div></div>
    <button class="btn-sm ${active ? "danger" : ""}" ${attrs}>${active ? "End drill" : "Start drill"}</button></div>`;
  const rows = s.providers.filter((p) => p.configured).map((p) =>
    row(`Simulate a ${esc(p.label)} outage`, `Agents switch to the next provider mid-answer.`, p.disabled, `data-outage="${esc(p.name)}" data-on="${p.disabled}"`));
  rows.push(row("Simulate losing the network", "All clouds disconnect; agents keep working on this device.", s.offline, `data-offline data-on="${s.offline}"`));
  return `<div class="card drills">${rows.join("")}</div>`;
}

function renderSettings() {
  const cfg = state.settings, s = state.status;
  if (!cfg) { $("#settings").innerHTML = `<div class="col"><div class="err">Couldn't load settings from the edge server.</div></div>`; return; }
  const live = (name) => s?.providers?.find((p) => p.name === name);
  const provCards = cfg.providers.map((p) => {
    const l = live(p.name) || {};
    const [pc, pt] = s && l.name ? providerState(l, s) : ["idle", "Unknown"];
    const load = Math.min(100, Math.round(((l.inflight || 0) / p.capacity) * 100));
    return `<div class="card"><div class="card-h"><span class="n">${esc(p.label)}</span><span class="muted">Priority ${p.priority}${p.priority === 1 ? " · Primary" : ""}</span>
      <div class="r"><span class="pill ${pc}">${esc(pt)}</span></div></div>
      <dl class="kv">
        <dt>Endpoint</dt><dd><code>${esc(p.base_url || "—")}</code></dd>
        <dt>Model</dt><dd>${esc(p.model_label)} <span class="muted">(<code>${esc(p.model)}</code>)</span></dd>
        <dt>Hardware</dt><dd>${esc(p.hardware)}</dd>
        <dt>Mode</dt><dd>${p.standby ? "Cold standby — scales to zero; woken on failover or when higher-priority clouds are full" : "Always on"}</dd>
        <dt>Load</dt><dd>${l.inflight || 0} of ${p.capacity} concurrent · ${l.served || 0} served this session<div class="meter"><i style="width:${load}%"></i></div></dd>
        <dt>API key</dt><dd>${p.api_key ? `Set <span class="muted">(${esc(p.api_key)})</span>` : "Not set"}</dd>
      </dl></div>`;
  }).join("");
  const e = cfg.edge, le = s?.edge || {};
  const edgeCard = `<div class="card"><div class="card-h"><span class="n">This device</span><span class="muted">Always available · Last in the failover order</span>
    <div class="r"><span class="pill ${le.ok ? "ok" : "bad"}">${le.ok ? "Ready" : "Ollama not running"}</span></div></div>
    <dl class="kv">
      <dt>Endpoint</dt><dd><code>${esc(e.base_url)}</code></dd>
      <dt>Model</dt><dd>${esc(e.label)} <span class="muted">(<code>${esc(e.model)}</code>)</span></dd>
      <dt>Concurrency</dt><dd>${e.concurrency} generation at a time · ${le.inflight || 0} running now</dd>
      <dt>Context budget</dt><dd>${e.context_chars.toLocaleString()} characters (older turns are trimmed on-device)</dd>
    </dl></div>`;
  const sc = cfg.scaling, fo = cfg.failover;
  const policy = `<div class="card"><dl class="kv">
      <dt>Agents per task</dt><dd>Up to ${sc.cloud_max_agents} in parallel on cloud · up to ${sc.edge_max_agents} on this device</dd>
      <dt>Routing</dt><dd>Per step: highest-priority healthy cloud with free capacity; overflow goes to the next cloud; otherwise this device</dd>
      <dt>Steps per agent</dt><dd>Up to ${sc.max_agent_steps}</dd>
      <dt>Cloud reasoning</dt><dd>${sc.cloud_thinking ? "On" : "Off (lower latency)"}</dd>
    </dl></div>
    <div class="card"><dl class="kv">
      <dt>Network watch</dt><dd>Every ${fo.network_watch_s}s — losing the device's network cuts cloud streams immediately</dd>
      <dt>Health checks</dt><dd>Every ${fo.health_interval_s}s · ${fo.health_timeout_s}s timeout · down after ${fo.fail_threshold} failures (1 on connection errors)</dd>
      <dt>Stream guards</dt><dd>${fo.first_token_timeout_s}s to first token · ${fo.stall_timeout_s}s max stall</dd>
    </dl></div>`;
  const cp = `<div class="card"><dl class="kv">
      <dt>Store</dt><dd><code>${esc(cfg.checkpoints.path)}</code></dd>
      <dt>Durability</dt><dd>Synchronous — each workflow and agent step is on disk before the next starts</dd>
    </dl></div>`;
  $("#settings").innerHTML = `<div class="col">
    <div class="set-sec"><h2>Cloud providers</h2><p>Tried in order. Each agent step picks a provider at the moment it runs.</p>${provCards}</div>
    <div class="set-sec"><h2>On-device model</h2><p>Used whenever no cloud is reachable.</p>${edgeCard}</div>
    <div class="set-sec"><h2>Scaling and failover</h2><p>How work is spread across providers and how quickly failures are detected.</p>${policy}</div>
    <div class="set-sec"><h2>Test mode</h2><p>Rehearse outages safely. Drills only affect this app; they don't touch the real providers.</p>${drillsHTML(s)}</div>
    <div class="set-sec"><h2>Clean up</h2><p>Keep the 10% most recently used sessions and delete the rest, with their checkpoints and logs. Running sessions are kept.</p>
      <div class="card drills"><div class="drill-row"><div class="grow"><div class="n">Delete old sessions</div><div class="d">${state.threads.length} sessions now · keeps the newest ${Math.max(1, Math.ceil(state.threads.length * 0.1))}</div></div>
      ${state.confirmCleanup ? `<button class="btn-sm" data-cleanup-cancel>Cancel</button><button class="btn-sm danger" data-cleanup-go>Delete ${Math.max(0, state.threads.length - Math.max(1, Math.ceil(state.threads.length * 0.1)))}</button>` : `<button class="btn-sm" data-cleanup ${state.threads.length < 2 ? "disabled" : ""}>Clean up</button>`}</div></div></div>
    <div class="set-sec"><h2>Checkpointing</h2><p>Lets runs survive disconnects, crashes and provider switches.</p>${cp}</div>
    <p class="muted" style="font-size:12px;margin:0">To change these settings, edit <code>.env</code> in the project folder and restart the server. API keys are never sent to the browser.</p>
  </div>`;
}

/* ------------------------------------------------------------- live sync */
// Sessions can be created and driven by other apps on this device through the Agents API
// (/v1). Keep the incident list fresh and join any run on the open incident as it starts.
async function syncSessions() {
  if (document.hidden) return;
  try {
    const before = state.threads.find((t) => t.id === state.threadId);
    await loadThreads();
    if (!state.threadId || state.sending || state.view !== "chat") return;
    const now = state.threads.find((t) => t.id === state.threadId);
    if (!now) return;
    if (now.running) return openThread(now.id); // re-attaches to the live run
    if (before && now.updated_at !== before.updated_at) openThread(now.id, { attach: false });
  } catch {}
}

function openFromHash() {
  if (location.hash === "#benchmarks") { if (state.view !== "bench") openBench(); return; }
  const m = location.hash.match(/session=([a-z0-9]+)/i);
  if (m && m[1] !== state.threadId) openThread(m[1]).catch(() => {});
}

/* ------------------------------------------------------------------ threads */
async function loadThreads() {
  state.threads = await api("/api/threads");
  renderThreads();
}
function renderThreads() {
  $("#threads").innerHTML = state.threads.map((t) =>
    `<button class="thread ${t.id === state.threadId ? "active" : ""}" data-thread="${t.id}"><span class="t">${esc(t.title)}</span><span class="s">${t.running ? `<span class="live">Live</span> · ` : ""}${t.source && t.source !== "console" ? `${esc(SOURCE[t.source] || cap(t.source))} · ` : ""}${ago(t.updated_at)}</span></button>`
  ).join("") || `<div class="muted" style="padding:8px 10px;font-size:12.5px">No incidents yet.</div>`;
}

async function openThread(id, { attach = true } = {}) {
  closeSettings();
  state.threadId = id;
  localStorage.setItem("he.thread", id);
  state.thread = await api(`/api/threads/${id}`);
  $("#title").textContent = state.thread.thread.title;
  renderThreads();
  renderFeed(true);
  renderInspector();
  $("#sidebar").classList.remove("open");
  if (attach && state.thread.running && !state.sending) {
    runStream(fetch(`/api/threads/${id}/attach`), null);
  }
}

async function newThread() {
  const t = await api("/api/threads", { method: "POST", body: "{}" });
  state.threads.unshift(t);
  await openThread(t.id);
  $("#input").focus();
}

/* --------------------------------------------------------------------- feed */
function lastTurnDegraded() {
  const msgs = state.thread?.messages || [];
  const last = [...msgs].reverse().find((m) => m.role === "assistant");
  if (!last) return false;
  const wf = last.meta?.workflow;
  const targets = wf ? wf.results.flatMap((r) => r.targets) : [];
  return last.meta?.target === "edge" || targets.includes("edge");
}

function agentHTML(a, expanded) {
  const st = a.status === "done" ? `<span class="st done">${ICON.check}</span>` : a.status === "running" ? `<span class="st"><span class="spin"></span></span>` : `<span class="st"><span class="ring"></span></span>`;
  const targets = [...new Set(a.targets || [])];
  const chip = targets.length ? targets.map((t) => tag(t)).join("") : a.target ? tag(a.target) : `<span class="tag plain">${a.status === "deferred" ? "Waits for cloud" : "Queued"}</span>`;
  const sub = a.status === "running" && a.preview ? `<div class="agent-preview">${esc(a.preview)}</div>` : `<div class="agent-task">${esc(a.task)}</div>`;
  const notes = (a.failovers || []).map(noteHTML).join("");
  const tools = (a.tools || []).length ? `<div class="tools">${a.tools.map((t) => `<span class="tool">${esc(t)}</span>`).join("")}</div>` : "";
  const body = expanded ? `<div class="agent-body">${notes}${tools}<div class="md">${a.output ? md(a.output) : `<span class="muted">${a.status === "done" ? "No output." : "Working…"}</span>`}</div></div>` : "";
  return `<div class="agent"><button class="agent-row" data-agent="${esc(a.id)}">${st}<div style="min-width:0"><div class="agent-name">${esc(a.title)}</div>${sub}</div><div class="agent-meta">${a.steps ? `<span class="muted" style="font-size:12px">${a.steps} step${a.steps > 1 ? "s" : ""}</span>` : ""}${chip}</div></button>${!expanded && notes ? `<div class="agent-body" style="padding-bottom:8px">${notes}</div>` : ""}${body}</div>`;
}

const expanded = new Set();
const collapsedWf = new Set();
const noteHTML = (f) => f.upgrade
  ? `<div class="note note-up">↩ Back on ${esc(targetName(f.to))}</div>`
  : f.generic
  ? `<div class="note">↪ Switched provider</div>`
  : `<div class="note">↪ ${esc(targetName(f.from))} dropped → ${esc(targetName(f.to))}</div>`;

function workflowHTML(plan, agents, running, flow) {
  if (!plan) return "";
  const n = plan.tasks.length;
  const collapsed = collapsedWf.has(plan.turn_id);
  if (flow) state.flows[plan.turn_id] = flow;
  const head = `<div class="wf-head"><button class="wf-h" data-wf="${esc(plan.turn_id)}" aria-expanded="${!collapsed}"><svg class="chev" viewBox="0 0 20 20"><path d="M7 5l5 5-5 5"/></svg><b>${n === 1 ? "1 agent" : flow && flow.layout === "serial" ? `${n} agents, one at a time` : `${n} agents in parallel`}</b><span>· Planned on ${esc(targetName(plan.planned_on))}</span><span class="r">${running ? "Running" : "Complete"}</span></button>${flow ? `<button class="wf-flowbtn" data-flow="${esc(plan.turn_id)}">How it ran</button>` : ""}</div>`;
  const deferred = (plan.deferred || []).map((t) => agentHTML(agents[t.id] ? { ...agents[t.id], title: `${t.title} · deferred to cloud` } : { id: t.id, title: t.title, task: t.task, status: "deferred" }, expanded.has(t.id))).join("");
  return `<div class="wf ${collapsed ? "collapsed" : ""}">${head}${plan.tasks.map((t) => agentHTML(agents[t.id] || { id: t.id, title: t.title, task: t.task, status: "queued" }, expanded.has(t.id))).join("")}${deferred}</div>`;
}

function answerHTML(text, meta, { streaming = false, isLast = false, flow = null, flowLink = false } = {}) {
  const degraded = meta?.target === "edge" || (meta?.workflow?.results || []).some((r) => r.targets.includes("edge"));
  const fail = meta?.failover?.length || (meta?.workflow?.results || []).some((r) => r.failover);
  const bits = [];
  if (meta?.target) bits.push(tag(meta.target));
  if (meta?.model) bits.push(`<span>${esc(meta.model)}</span>`);
  if (meta?.ms) bits.push(`<span>${(meta.ms / 1000).toFixed(1)}s</span>`);
  const routeNote = flow && window.HybridFlow ? HybridFlow.note(flow) : "";
  if (routeNote) bits.push(`<span class="route-note">${esc(routeNote)}</span>`);
  else if (fail) bits.push(`<span class="route-note">Switched provider mid-run</span>`);
  if (flowLink && flow) bits.push(`<button class="link-btn" data-flow="${esc(flow.task_id || "")}">How it ran</button>`);
  if (isLast && degraded && state.status?.active && state.status.active !== "edge" && !state.sending)
    bits.push(`<button class="btn-sm primary" data-rerun>${ICON.redo}Re-run on ${esc(targetName(state.status.active))}</button>`);
  return `<div class="answer ${degraded ? "degraded" : ""}"><div class="md ${streaming ? "cursor" : ""}">${md(text)}</div>${bits.length && !streaming ? `<div class="answer-meta">${bits.join("")}</div>` : ""}</div>`;
}

function agentsFromResults(wf) {
  const out = {};
  const byRole = {};
  for (const t of [...wf.plan.tasks, ...(wf.plan.deferred || [])]) byRole[t.role] = t.id;
  for (const t of [...wf.plan.tasks, ...(wf.plan.deferred || [])]) if ((wf.waiting || []).includes(t.title)) out[t.id] = { id: t.id, title: t.title, task: t.task, status: "deferred" };
  for (const r of wf.results) out[byRole[r.role] || r.task_id] = { id: r.task_id, title: r.title, task: r.task, status: "done", output: r.output, targets: r.targets, steps: r.steps, tools: r.tools, failovers: r.failover ? [{ generic: true }] : [] };
  return out;
}

function renderFeed(scroll = false) {
  const feed = $("#feed");
  const nearBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 120;
  const msgs = state.thread?.messages || [];
  let html = "";
  if (!state.threadId || (!msgs.length && !state.live)) {
    html = `<div class="empty"><h2>What's happening?</h2><p>Agents plan in the cloud when it's reachable and keep working on this device when it isn't.</p><div class="chips">
      ${["Two-car collision, fuel leaking, driver trapped and breathing 34/min. Crowd forming.", "Triage: adult, not walking, breathing 22/min, no radial pulse.", "Structure fire, smoke from 2nd floor, possible occupant inside.", "Draft a CAN report for command."]
        .map((c) => `<button class="chip" data-chip="${esc(c)}">${esc(c.length > 52 ? c.slice(0, 50) + "…" : c)}</button>`).join("")}</div></div>`;
  } else {
    const lastAssistant = msgs.map((m) => m.role).lastIndexOf("assistant");
    const parts = [];
    msgs.forEach((m, i) => {
      if (m.role === "user") parts.push(`<div class="user">${esc(m.content)}</div>`);
      else if (m.role === "assistant") {
        const wf = m.meta?.workflow;
        const showCard = wf && wf.plan.tasks.length > 1;
        if (m.flow && m.flow.task_id) state.flows[m.flow.task_id] = m.flow;
        parts.push(`<div class="turn">${showCard ? workflowHTML(wf.plan, agentsFromResults(wf), false, m.flow) : ""}${answerHTML(m.content, m.meta, { isLast: i === lastAssistant && !state.live, flow: m.flow, flowLink: !showCard })}</div>`);
      }
    });
    if (state.live) parts.push(liveHTML());
    if (state.thread?.next?.length && !state.thread.running && !state.live)
      parts.push(`<div class="resume">This run stopped before finishing. State is saved at the last checkpoint.<button class="btn-sm" data-resume style="margin-left:auto">${ICON.play}Resume</button></div>`);
    html = `<div class="col">${parts.join("")}</div>`;
  }
  feed.innerHTML = html;
  if (scroll || nearBottom) feed.scrollTop = feed.scrollHeight;
}

function liveHTML() {
  const L = state.live;
  let html = "";
  if (L.userText) html += `<div class="user">${esc(L.userText)}</div>`;
  const parts = [];
  if (!L.plan) parts.push(`<div class="thinking"><span class="st"><span class="spin"></span></span>${L.planning ? `Planning on ${esc(targetName(L.planning))}…` : "Starting…"}</div>${L.planThought ? `<div class="plan-think">${esc(L.planThought.replace(/\s+/g, " ").slice(-420))}</div>` : ""}`);
  if (L.plan) parts.push(workflowHTML(L.plan, L.agents, !L.final, L.flow));
  if (L.plan && L.plan.tasks.length === 1) {
    const a = Object.values(L.agents)[0];
    if (a && !L.final) {
      const notes = (a.failovers || []).map(noteHTML).join("");
      const tools = (a.tools || []).length ? `<div class="answer-meta" style="margin:0 0 8px">${a.tools.map((t) => `<span class="tool">${esc(t)}</span>`).join("")}</div>` : "";
      parts.push(`${notes}${tools}${a.output ? answerHTML(a.output, null, { streaming: true }) : `<div class="thinking"><span class="st"><span class="spin"></span></span>Working on ${esc(targetName(a.target))}…</div>`}`);
    }
  }
  if (L.reviewing && !L.review) parts.push(`<div class="thinking"><span class="st"><span class="spin"></span></span>Reviewing the plan for conflicts and gaps…</div>${L.reviewThought ? `<div class="plan-think">${esc(L.reviewThought.replace(/\s+/g, " ").slice(-360))}</div>` : ""}`);
  if (L.review && L.review.verdict) {
    const n = (L.review.issues || []).length, sent = (L.review.revisions || []).length;
    parts.push(`<div class="review-note"><b>Plan review</b> — ${esc(L.review.verdict)}${n ? ` · ${n} issue${n > 1 ? "s" : ""}${sent ? `, ${sent} sent back` : ""}` : ""}</div>`);
  }
  if (L.synthNotes) parts.push(L.synthNotes);
  if (L.final && !L.final.text && L.synthThought) parts.push(`<div class="thinking"><span class="st"><span class="spin"></span></span>Writing the answer…</div><div class="plan-think">${esc(L.synthThought.replace(/\s+/g, " ").slice(-420))}</div>`);
  if (L.final) parts.push(answerHTML(L.final.text, L.final.done ? L.final.meta : null, { streaming: !L.final.done, flow: L.flow }));
  else if (L.synthesizing && !L.final) parts.push(`<div class="thinking"><span class="st"><span class="spin"></span></span>Merging reports on ${esc(targetName(L.synthesizing))}…</div>`);
  if (L.error) parts.push(`<div class="err">${esc(L.error)}</div>`);
  return html + `<div class="turn">${parts.join("")}</div>`;
}

let raf = 0;
function schedule() {
  if (raf) return;
  raf = requestAnimationFrame(() => { raf = 0; renderFeed(); });
}

/* ------------------------------------------------------------------ running */
function handleEvent(ev) {
  const L = state.live;
  if (!L) return;
  const agentId = ev.task_id;
  const agentFor = () => (L.agents[agentId] ||= { id: agentId, title: ev.agent, task: "", status: "running", steps: 0, tools: [], failovers: [], targets: [], output: "", preview: "" });
  switch (ev.type) {
    case "step_start":
      if (ev.task_id === "plan") L.planning = ev.target;
      else if (ev.task_id === "synth") { L.synthesizing = ev.target; L.final = { text: "", done: false }; }
      else { const a = agentFor(); a.status = "running"; a.target = ev.target; a.output = ""; a.preview = ""; }
      break;
    case "reasoning":
      if (ev.task_id === "plan") { L.planThought = ((L.planThought || "") + ev.text).slice(-2000); }
      else if (ev.task_id === "synth") { L.synthThought = ((L.synthThought || "") + ev.text).slice(-2000); }
      else if (ev.task_id === "review") { L.reviewThought = ((L.reviewThought || "") + ev.text).slice(-2000); L.reviewing = true; }
      else if (L.agents[agentId] && !L.agents[agentId].output) { const a = L.agents[agentId]; a.thought = ((a.thought || "") + ev.text).slice(-2000); a.preview = "Thinking: " + a.thought.replace(/\s+/g, " ").slice(-110); }
      break;
    case "token":
      if (ev.task_id === "synth") { L.final ||= { text: "", done: false }; L.final.text += ev.text; }
      else if (L.agents[agentId]) { const a = L.agents[agentId]; a.output += ev.text; a.preview = a.output.replace(/\s+/g, " ").slice(-90); }
      break;
    case "failover": {
      if (ev.task_id === "synth") { L.final = null; L.synthNotes = `<div class="note">↪ ${esc(targetName(ev.from))} dropped → ${esc(targetName(ev.to))}</div>`; }
      else if (ev.task_id !== "plan") agentFor().failovers.push({ from: ev.from, to: ev.to, reason: ev.reason });
      break;
    }
    case "upgrade":
      if (ev.task_id === "synth") { L.final = null; L.synthNotes = `<div class="note note-up">↩ Back on ${esc(targetName(ev.to))}</div>`; }
      else if (ev.task_id !== "plan") agentFor().failovers.push({ upgrade: true, to: ev.to });
      break;
    case "step_end":
      if (ev.task_id !== "plan" && ev.task_id !== "synth") { const a = agentFor(); a.steps += 1; a.targets.push(ev.meta.target); }
      if (ev.task_id === "synth" && L.final) { L.final.done = true; L.final.meta = ev.meta; }
      break;
    case "tool_call": { const a = agentFor(); a.tools.push(ev.name); a.output = ""; break; }
    case "plan":
      L.plan = ev.plan;
      for (const t of ev.plan.tasks) L.agents[t.id] = { id: t.id, title: t.title, task: t.task, status: "queued", steps: 0, tools: [], failovers: [], targets: [], output: "", preview: "" };
      break;
    case "deferred": { const a = agentFor(); a.status = "deferred"; a.preview = ""; break; }
    case "agent_done": if (ev.result.deferred) break; { const a = agentFor(); a.status = "done"; a.output = ev.result.output; a.targets = ev.result.targets; break; }
    case "final": L.final = { text: ev.content, done: true, meta: ev.meta }; break;
    case "checkpoint": L.checkpoints += 1; bumpCheckpoints(ev); break;
    case "review": L.review = ev.review; L.reviewing = false; break;
    case "revise": if (ev.deferred) { for (const t of ev.tasks) L.agents[t.id] = { id: t.id, title: t.title, task: t.task, status: "queued", steps: 0, tools: [], failovers: [], targets: [], output: "", preview: "" }; break; }
      for (const t of ev.tasks) { L.plan.tasks.push({ id: t.id, role: t.role, title: t.next ? t.title : `${t.title} · ${t.added ? "added by review" : "revision"}`, task: t.task }); L.agents[t.id] = { id: t.id, title: t.title, task: t.task, status: "queued", steps: 0, tools: [], failovers: [], targets: [], output: "", preview: "" }; } break;
    case "flow":
      L.flow = ev.flow;
      if (ev.flow.task_id) state.flows[ev.flow.task_id] = ev.flow;
      if (state.modal && state.modal.kind === "flow") renderModal();
      break;
    case "error": L.error = ev.message; break;
  }
  schedule();
}

function bumpCheckpoints(ev) {
  if (!state.thread) return;
  state.thread.checkpoints.unshift({ checkpoint_id: ev.checkpoint_id, ns: ev.ns, step: ev.step, source: ev.source, ts: new Date(ev.ts * 1000).toISOString(), fresh: true, agent: ev.agent });
  if (!$("#inspector").hidden) renderInspector();
}

async function runStream(responsePromise, userText) {
  state.sending = true;
  state.live = { userText, plan: null, agents: {}, final: null, checkpoints: 0, error: null };
  renderStatus();
  renderFeed(true);
  const id = state.threadId;
  try {
    const r = await responsePromise;
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, i);
        buf = buf.slice(i + 2);
        const line = chunk.split("\n").find((l) => l.startsWith("data: "));
        if (line) handleEvent(JSON.parse(line.slice(6)));
      }
    }
  } catch (e) {
    if (state.live) state.live.error = `Connection to the edge server dropped (${e.message}). The run continues server-side; reopen the incident to re-attach.`;
  } finally {
    state.sending = false;
    if (state.threadId === id) {
      const err = state.live?.error;
      state.live = null;
      await openThread(id, { attach: false }).catch(() => {});
      if (err && !state.thread?.running) toastErr(err);
    }
    loadThreads().catch(() => {});
    renderStatus();
  }
}

async function send(text) {
  text = text.trim();
  if (!text || state.sending) return;
  if (!state.threadId) {
    const t = await api("/api/threads", { method: "POST", body: "{}" });
    state.threadId = t.id;
    state.thread = { thread: t, messages: [], checkpoints: [], events: [], next: [], running: false };
    localStorage.setItem("he.thread", t.id);
  }
  if (!state.thread?.messages?.length) $("#title").textContent = text.slice(0, 60);
  $("#input").value = "";
  autosize();
  runStream(fetch(`/api/threads/${state.threadId}/messages`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ text }) }), text);
}

function rerun() {
  if (state.sending || !state.threadId) return;
  hideToast();
  const msgs = state.thread.messages;
  const lastUser = [...msgs].reverse().find((m) => m.role === "user");
  state.thread.messages = msgs.slice(0, msgs.lastIndexOf(lastUser));
  runStream(fetch(`/api/threads/${state.threadId}/rerun`, { method: "POST" }), lastUser?.content);
}

function resume() {
  if (state.sending || !state.threadId) return;
  runStream(fetch(`/api/threads/${state.threadId}/resume`, { method: "POST" }), null);
}

/* ---------------------------------------------------------------- inspector */
const panels = { log: true, checkpoints: false };
try { Object.assign(panels, JSON.parse(localStorage.getItem("he.panels") || "{}")); } catch {}

function renderInspector() {
  for (const [name, open] of Object.entries(panels)) {
    const h = document.querySelector(`[data-toggle="${name}"]`);
    if (h) h.setAttribute("aria-expanded", String(open));
  }
  const T = state.thread;
  $("#logCount").textContent = T ? T.events.length : "";
  $("#cpCount").textContent = T ? T.checkpoints.length : "";
  if (!T) {
    $("#logBody").innerHTML = $("#cpBody").innerHTML = `<div class="muted">Open an incident.</div>`;
    return;
  }
  // Incident log
  const evs = T.events;
  $("#logBody").innerHTML = evs.length
    ? [...evs].reverse().map((e) => `<div class="ev ${esc(e.priority)}"><div class="h"><b>${esc(cap(e.priority))}</b><span>${clock(e.ts)}</span><span style="margin-left:auto">${e.tier === "edge" ? "Device" : "Cloud"}</span></div>${esc(e.text)}</div>`).join("")
    : `<div class="muted">Agents add entries here as they work (triage results, hazards, decisions).</div>`;
  // Checkpoints
  const cps = T.checkpoints;
  const sub = cps.filter((c) => c.ns).length;
  const list = cps.slice(0, 120).map((c) => {
    const who = c.ns ? (ROLE_TITLES[c.agent] || "Agent subgraph") : "Workflow";
    const t = c.ts ? new Date(c.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "";
    return `<div class="cp ${c.ns ? "sub" : ""} ${c.fresh ? "cp-fresh" : ""}"><div class="line"><i></i></div><div><div class="w">${esc(who)} · Step ${c.step ?? "–"}</div><div class="x">${esc(c.checkpoint_id.slice(-12))} · ${esc(cap(c.source || ""))}</div></div><div class="x">${t}</div></div>`;
  }).join("");
  $("#cpBody").innerHTML = `${T.next?.length && !T.running ? `<div class="resume">Run interrupted.<button class="btn-sm" data-resume style="margin-left:auto">${ICON.play}Resume</button></div>` : ""}
    <div class="stat"><div><div class="v">${cps.length}</div><div class="k">Checkpoints</div></div><div><div class="v">${sub}</div><div class="k">In agent subgraphs</div></div></div>
    ${list || `<div class="muted">No checkpoints yet.</div>`}
    <div class="muted" style="font-size:11.5px;margin-top:12px">Stored in SQLite on this device. Every workflow and agent step is saved before the next one starts.</div>`;
}

/* -------------------------------------------------------------------- toast */
let toastTimer = 0;
function toast(msg, action) {
  const t = $("#toast");
  t.hidden = false;
  t.innerHTML = `<span>${esc(msg)}</span>${action ? `<button id="toastAct">${esc(action.label)}</button>` : ""}`;
  if (action) $("#toastAct").onclick = action.fn;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(hideToast, action ? 9000 : 3500);
}
function toastErr(msg) { toast(msg); }
function hideToast() { $("#toast").hidden = true; }

/* ------------------------------------------------------------------- wiring */
function autosize() {
  const i = $("#input");
  i.style.height = "auto";
  i.style.height = Math.min(i.scrollHeight, 180) + "px";
  $("#send").disabled = state.sending || !i.value.trim();
}

$("#composer").addEventListener("submit", (e) => { e.preventDefault(); send($("#input").value); });
$("#input").addEventListener("input", autosize);
$("#input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send($("#input").value); }
});
$("#newThread").addEventListener("click", newThread);
$("#settingsBtn").addEventListener("click", () => (state.view === "settings" ? closeSettings() : openSettings()));
$("#benchBtn").addEventListener("click", () => (state.view === "bench" ? closeSettings() : openBench()));
$("#settings").addEventListener("click", async (e) => {
  if (e.target.closest("[data-cleanup]")) { state.confirmCleanup = true; return renderSettings(); }
  if (e.target.closest("[data-cleanup-cancel]")) { state.confirmCleanup = false; return renderSettings(); }
  if (e.target.closest("[data-cleanup-go]")) {
    state.confirmCleanup = false;
    const r = await api("/api/cleanup", { method: "POST", body: JSON.stringify({ keep_fraction: 0.1 }) });
    await loadThreads();
    if (state.threadId && !state.threads.some((t) => t.id === state.threadId)) {
      state.threadId = null; state.thread = null;
      try { localStorage.removeItem("he.thread"); } catch {}
    }
    toast(`Deleted ${r.deleted} old sessions · kept ${r.kept}`);
    return renderSettings();
  }
  const o = e.target.closest("[data-outage]");
  if (o) return setLink({ provider: o.dataset.outage, disabled: o.dataset.on !== "true" });
  const off = e.target.closest("[data-offline]");
  if (off) return setLink({ offline: off.dataset.on !== "true" });
});
$("#menuBtn").addEventListener("click", () => {
  if (window.matchMedia("(max-width: 760px)").matches) $("#sidebar").classList.toggle("open");
  else setSidebar(false);
});
$("#threads").addEventListener("click", (e) => {
  const b = e.target.closest("[data-thread]");
  if (b && !state.sending) openThread(b.dataset.thread);
});
$("#statusBar").addEventListener("click", () => openModal({ kind: "connection" }));
$("#drillEnd").addEventListener("click", async () => {
  const s = state.status;
  if (!s) return;
  if (s.offline) await setLink({ offline: false });
  for (const p of s.providers) if (p.disabled) await setLink({ provider: p.name, disabled: false });
});
$("#statusBar").addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openModal({ kind: "connection" }); } });
$("#modalClose").addEventListener("click", closeModal);
$("#modal").addEventListener("click", (e) => {
  if (e.target === $("#modal")) return closeModal();
  if (e.target.closest("[data-open-settings]")) { closeModal(); openSettings(); }
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && state.modal) closeModal(); });
$("#feed").addEventListener("click", (e) => {
  const fb = e.target.closest("[data-flow]");
  if (fb) return openModal({ kind: "flow", id: fb.dataset.flow });
  const chip = e.target.closest("[data-chip]");
  if (chip) return send(chip.dataset.chip);
  const wf = e.target.closest("[data-wf]");
  if (wf) { const id = wf.dataset.wf; collapsedWf.has(id) ? collapsedWf.delete(id) : collapsedWf.add(id); return renderFeed(); }
  const ag = e.target.closest("[data-agent]");
  if (ag) { const id = ag.dataset.agent; expanded.has(id) ? expanded.delete(id) : expanded.add(id); return renderFeed(); }
  if (e.target.closest("[data-rerun]")) return rerun();
  if (e.target.closest("[data-resume]")) return resume();
});
$("#inspector").addEventListener("click", (e) => {
  if (e.target.closest("[data-resume]")) return resume();
  const h = e.target.closest("[data-toggle]");
  if (h) {
    panels[h.dataset.toggle] = !panels[h.dataset.toggle];
    try { localStorage.setItem("he.panels", JSON.stringify(panels)); } catch {}
    renderInspector();
  }
});
$("#linkPill").addEventListener("click", (e) => {
  e.stopPropagation();
  const pop = $("#linkPop");
  pop.hidden = !pop.hidden;
  $("#linkPill").setAttribute("aria-expanded", String(!pop.hidden));
  if (!pop.hidden) renderPopover();
});
$("#linkPop").addEventListener("click", (e) => {
  e.stopPropagation();
  if (e.target.closest("[data-open-settings]")) {
    $("#linkPop").hidden = true;
    openSettings();
  }
});
document.addEventListener("click", () => { $("#linkPop").hidden = true; $("#linkPill").setAttribute("aria-expanded", "false"); });
function setInspector(open) {
  $("#inspector").hidden = !open;
  $("#inspectorBtn").classList.toggle("on", open);
  $("#inspectorBtn").setAttribute("aria-expanded", String(open));
  try { localStorage.setItem("he.inspector", open ? "1" : "0"); } catch {}
  if (open) renderInspector();
}
$("#inspectorBtn").addEventListener("click", () => setInspector($("#inspector").hidden));
$("#inspectorClose").addEventListener("click", () => setInspector(false));
// On narrow screens the panel overlays the page: clicking anywhere outside it closes it.
document.addEventListener("click", (e) => {
  if ($("#inspector").hidden || !window.matchMedia("(max-width: 980px)").matches) return;
  if (e.target.closest("#inspector, #inspectorBtn")) return;
  setInspector(false);
});
const syncBarHeight = () => document.documentElement.style.setProperty("--sb-h", $("#statusBar").offsetHeight + "px");
window.addEventListener("resize", syncBarHeight);
function setSidebar(collapsed) {
  $("#app").classList.toggle("sb-collapsed", collapsed);
  try { localStorage.setItem("he.sidebar", collapsed ? "0" : "1"); } catch {}
}
$("#sidebarClose").addEventListener("click", () => {
  if (window.matchMedia("(max-width: 760px)").matches) $("#sidebar").classList.remove("open");
  else setSidebar(true);
});
// The device itself reports connectivity changes; re-check immediately instead of waiting for the next poll.
window.addEventListener("online", () => pollStatus());
window.addEventListener("offline", () => pollStatus());

(async function boot() {
  try {
    if (localStorage.getItem("he.inspector") === "1") { $("#inspector").hidden = false; $("#inspectorBtn").classList.add("on"); }
    if (localStorage.getItem("he.sidebar") === "0") $("#app").classList.add("sb-collapsed");
  } catch {}
  pollStatus();
  await loadThreads().catch(() => {});
  let last = null;
  try { last = localStorage.getItem("he.thread"); } catch {}
  if (last && state.threads.some((t) => t.id === last)) await openThread(last);
  else renderFeed();
  setInterval(syncSessions, 2500);
  window.addEventListener("hashchange", openFromHash);
  openFromHash();
})();
