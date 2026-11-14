"use strict";
/*
 * Patrol MDT — a police tactical-response terminal built ONLY on the HybridEdge Agents API (/v1).
 * It knows nothing about clouds, failover or checkpoints: it sends a Responses request with
 * agent "police_tactical", follows the stream, and turns tool results (threat score, formation)
 * into an incident strip, a live map, a units board and resource requests.
 */
const API = "/v1";
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const LEVEL = { 1: "LOW", 2: "GUARDED", 3: "ELEVATED", 4: "CRITICAL" };
const TOOL_DOING = {
  assess_threat: "Scoring the threat level",
  plan_formation: "Assigning officers to positions",
  lookup_protocol: "Checking procedure",
  log_event: "Writing to the incident log",
  get_incident_log: "Reading the incident log",
  start_triage: "Running START triage",
};

const blank = () => ({
  incident: null, // {no, type, location, created}
  sessionId: null, responseId: null, busy: false,
  threat: null, formation: null, units: [], requests: {},
  orders: "", flow: null, where: null, agents: {}, order: [],
});
const S = Object.assign(blank(), {
  form: { type: "Barricaded suspect", weapon: "firearm", flags: { barricaded: true, hostages: false, violent: true, building: true }, subjects: 1, officers: 9 },
  link: null,
});
try {
  const saved = JSON.parse(localStorage.getItem("patrol.v2") || "{}");
  Object.assign(S, saved, { busy: false, link: null });
  S.units = (S.units || []).map((u) => ({ ...u, status: "inpos" }));
} catch {}
const save = () => { try { const { link, busy, ...rest } = S; localStorage.setItem("patrol.v2", JSON.stringify(rest)); } catch {} };

/* ------------------------------------------------------------------ helpers */
function md(src) {
  const lines = esc(src || "").split("\n");
  let html = "", list = null;
  const inline = (s) => s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>").replace(/`([^`]+)`/g, "<code>$1</code>");
  const close = () => { if (list) { html += `</${list}>`; list = null; } };
  for (const raw of lines) {
    const line = raw.trimEnd(); let m;
    if ((m = line.match(/^\s*#{1,4}\s+(.*)$/))) { close(); html += `<h3>${inline(m[1])}</h3>`; }
    else if ((m = line.match(/^\s*[-*•]\s+(.*)$/))) { if (list !== "ul") { close(); html += "<ul>"; list = "ul"; } html += `<li>${inline(m[1])}</li>`; }
    else if ((m = line.match(/^\s*\d+[.)]\s+(.*)$/))) { if (list !== "ol") { close(); html += "<ol>"; list = "ol"; } html += `<li>${inline(m[1])}</li>`; }
    else if (!line.trim()) close();
    else { close(); html += `<p>${inline(line)}</p>`; }
  }
  close();
  return html;
}
let toastT = 0;
function toast(msg, ok = false) {
  const t = $("#toast");
  t.textContent = msg; t.className = `toast${ok ? " ok" : ""}`; t.hidden = false;
  clearTimeout(toastT); toastT = setTimeout(() => (t.hidden = true), 3500);
}
const hhmm = (ts) => new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
const whereName = (t) => (!t || t === "edge" ? "this device" : (S.link && S.link.running_on === t ? S.link.label : t[0].toUpperCase() + t.slice(1)));

/* --------------------------------------------------------------------- link */
async function pollLink() {
  try {
    const s = await (await fetch(`${API}/status`)).json();
    const prev = S.link;
    S.link = s;
    $("#link").className = `link ${s.connected ? "" : "device"}`;
    $("#linkText").textContent = s.connected ? `Cloud · ${s.label}` : s.network ? "Device only" : "No network";
    if (prev && prev.connected !== s.connected && !S.busy) toast(s.connected ? `Cloud restored · ${s.label}` : "Cloud lost · running on device", s.connected);
  } catch {
    $("#link").className = "link down";
    $("#linkText").textContent = "Agents API unreachable";
  }
  setTimeout(pollLink, 1500);
}
setInterval(() => { $("#clock").textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }); renderIncident(); }, 1000);

/* -------------------------------------------------------------------- form */
function renderForm() {
  const f = S.form;
  document.querySelectorAll("#types button").forEach((b) => b.classList.toggle("on", b.dataset.type === f.type));
  document.querySelectorAll("#weapon button").forEach((b) => b.classList.toggle("on", b.dataset.v === f.weapon));
  document.querySelectorAll(".chk").forEach((c) => c.classList.toggle("on", !!f.flags[c.dataset.k]));
  $("#subjects").textContent = f.subjects;
  $("#officers").textContent = f.officers;
}
function composeInput() {
  const f = S.form;
  const facts = [
    `weapon: ${f.weapon}`,
    f.flags.barricaded ? "subject barricaded" : null,
    f.flags.hostages ? "hostages or vulnerable persons present" : "no known hostages",
    f.flags.violent ? "history of violence" : null,
    f.flags.building ? "subject inside a building" : "subject in the open",
    `${f.subjects} subject${f.subjects > 1 ? "s" : ""}`,
  ].filter(Boolean).join("; ");
  return `Incident: ${f.type}. Location: ${S.incident.location}. Intel: ${facts}. Officers available: ${f.officers}. Produce the tactical plan.`;
}

/* ----------------------------------------------------------------- incident */
function level() {
  if (S.threat) return S.threat;
  if (S.formation) return { intel_level: S.formation.intel_level, label: LEVEL[S.formation.intel_level], posture: S.formation.posture, standoff_m: S.formation.standoff_m };
  return null;
}
function renderIncident() {
  const el = $("#incident");
  if (!S.incident) {
    el.className = "incident none"; el.removeAttribute("data-l");
    $("#incLvl").textContent = "—"; $("#incTitle").textContent = "No active incident"; $("#incSub").textContent = "Create an incident to get a tactical plan."; $("#incMeta").textContent = "";
    return;
  }
  const t = level();
  el.className = "incident";
  if (t) el.dataset.l = t.intel_level; else el.removeAttribute("data-l");
  $("#incLvl").innerHTML = t ? `L${t.intel_level}<br>${t.label}` : "…";
  $("#incTitle").textContent = `${S.incident.type} — ${S.incident.location}`;
  $("#incSub").textContent = t ? `${t.posture} · stand-off ${t.standoff_m} m${S.threat && S.threat.factors ? ` · ${S.threat.factors.join(", ")}` : ""}` : "Assessing threat…";
  const mins = Math.floor((Date.now() - S.incident.created) / 60000);
  $("#incMeta").innerHTML = `<b>${esc(S.incident.no)}</b><br>Opened ${hhmm(S.incident.created)} · ${mins < 1 ? "just now" : `${mins} min ago`}`;
}

/* ---------------------------------------------------------------------- map */
const W = 800, H = 600, C = { x: 405, y: 250 }, RV = { x: 70, y: 560 };
const SHORT = { cp: "Command post", inner_north: "Inner N", inner_east: "Inner E", inner_south: "Inner S", inner_west: "Inner W", overwatch: "Overwatch", arrest: "Arrest team", contact: "Contact", cover: "Cover", rear: "Rear", outer_a: "Cordon A", outer_b: "Cordon B", outer_c: "Cordon C" };
let mapKey = "";

function radii(f) {
  const inner = Math.max(55, Math.min(130, f.standoff_m * 1.1));
  const outer = Math.max(inner + 45, Math.min(225, f.outer_m * 0.55));
  return { inner, outer, overwatch: (inner + outer) / 2, staging: outer + 32 };
}
function posXY(p, f) {
  const R = radii(f);
  const r = R[p.ring] ?? R.outer;
  const a = (p.bearing * Math.PI) / 180;
  return { x: Math.max(30, Math.min(W - 30, C.x + r * Math.sin(a))), y: Math.max(24, Math.min(H - 24, C.y - r * Math.cos(a))) };
}

function baseMap(f) {
  const streets = `
    <rect width="${W}" height="${H}" fill="#e8edf3"/>
    ${[[40, 40, 170, 50], [270, 40, 290, 50], [610, 40, 150, 50], [40, 150, 170, 130], [270, 150, 290, 100], [610, 150, 150, 130], [40, 330, 170, 140], [270, 330, 290, 140], [610, 330, 150, 140], [40, 530, 170, 50], [270, 530, 290, 50], [610, 530, 150, 50]]
      .map(([x, y, w, h]) => `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="4" fill="#d6dde7"/>`).join("")}
    <g stroke="#fff" stroke-width="30" stroke-linecap="square">
      <line x1="0" y1="115" x2="${W}" y2="115"/><line x1="0" y1="300" x2="${W}" y2="300"/><line x1="0" y1="500" x2="${W}" y2="500"/>
      <line x1="240" y1="0" x2="240" y2="${H}"/><line x1="585" y1="0" x2="585" y2="${H}"/>
    </g>
    <g fill="#94a3b8" font-size="11" font-weight="600" letter-spacing=".04em">
      <text x="20" y="104">MILL LANE</text><text x="20" y="289">HARBOUR RD</text><text x="20" y="489">DOCK ST</text>
      <text x="249" y="20" transform="rotate(90 249 20)">QUAY ST</text><text x="594" y="20" transform="rotate(90 594 20)">MARKET ST</text>
    </g>`;
  if (!f) {
    return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Area map">${streets}
      <text x="${W / 2}" y="${H / 2}" text-anchor="middle" fill="#64748b" font-size="15">${S.busy ? "Planning formation…" : "No active incident"}</text></svg>`;
  }
  const R = radii(f);
  const fixed = f.positions.filter((p) => p.id === "ems" || p.id === "cp").map((p) => {
    const { x, y } = posXY(p, f);
    return p.id === "ems"
      ? `<g transform="translate(${x} ${y})"><rect x="-13" y="-13" width="26" height="26" rx="5" fill="#fff" stroke="#16a34a" stroke-width="2"/><path d="M-6 0h12M0 -6v12" stroke="#16a34a" stroke-width="3.5"/><text y="27" text-anchor="middle" font-size="11" font-weight="700" fill="#15803d">EMS</text></g>`
      : "";
  }).join("");
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Incident map">${streets}
    <circle cx="${C.x}" cy="${C.y}" r="${R.outer}" fill="#f59e0b" fill-opacity=".06" stroke="#d97706" stroke-width="2" stroke-dasharray="8 6"/>
    <circle cx="${C.x}" cy="${C.y}" r="${R.inner}" fill="#ef4444" fill-opacity=".10" stroke="#dc2626" stroke-width="2" stroke-dasharray="6 5"/>
    <text x="${C.x + R.outer * Math.sin(2.0) + 8}" y="${C.y - R.outer * Math.cos(2.0)}" font-size="11" font-weight="700" fill="#b45309">OUTER ${f.outer_m} m</text>
    <text x="${C.x + R.inner * Math.sin(2.0) + 8}" y="${C.y - R.inner * Math.cos(2.0)}" font-size="11" font-weight="700" fill="#b91c1c">INNER ${f.standoff_m} m</text>
    <rect x="${C.x - 26}" y="${C.y - 20}" width="52" height="40" rx="3" fill="#fff" stroke="#b91c1c" stroke-width="2"/>
    <circle cx="${C.x}" cy="${C.y}" r="7" fill="#dc2626"><animate attributeName="r" values="6;10;6" dur="1.6s" repeatCount="indefinite"/></circle>
    <text x="${C.x}" y="${C.y + 36}" text-anchor="middle" font-size="11" font-weight="800" fill="#b91c1c">SUBJECT</text>
    <g transform="translate(${RV.x} ${RV.y})"><rect x="-22" y="-12" width="44" height="24" rx="5" fill="#0f172a"/><text y="4" text-anchor="middle" font-size="11" font-weight="700" fill="#fff">RV</text></g>
    ${fixed}
    <g id="unitLayer"></g>
  </svg>`;
}

const UNIT_COLORS = { dispatched: "#64748b", enroute: "#f59e0b", inpos: "#16a34a" };
function markerFill(u) { return u.id === "cp" ? "#1d4ed8" : u.id === "overwatch" ? "#7c3aed" : UNIT_COLORS[u.status]; }

function renderMap() {
  const f = S.formation;
  const key = f ? JSON.stringify(f.positions.map((p) => [p.id, p.officers])) + f.standoff_m : `none-${S.busy}`;
  if (key !== mapKey) {
    mapKey = key;
    $("#map").innerHTML = baseMap(f);
  }
  const layer = document.getElementById("unitLayer");
  if (!layer || !f) return;
  for (const u of S.units) {
    let g = document.getElementById(`mk-${u.id}`);
    if (!g) {
      g = document.createElementNS("http://www.w3.org/2000/svg", "g");
      g.id = `mk-${u.id}`;
      g.setAttribute("class", "unit-mk");
      g.innerHTML = `<rect x="-26" y="-12" width="52" height="24" rx="6" stroke="#fff" stroke-width="2"/><text y="4" text-anchor="middle" font-size="11.5" font-weight="800" fill="#fff"></text><text class="sub" y="25" text-anchor="middle" font-size="10" font-weight="600" fill="#334155"></text>`;
      g.style.transform = `translate(${u.status === "inpos" ? u.x : RV.x}px, ${u.status === "inpos" ? u.y : RV.y}px)`;
      layer.appendChild(g);
    }
    g.querySelector("rect").setAttribute("fill", markerFill(u));
    g.querySelector("text").textContent = u.call;
    g.querySelector(".sub").textContent = `${u.short} · ${u.officers}`;
    if (u.status !== "dispatched") g.style.transform = `translate(${u.x}px, ${u.y}px)`;
  }
}

/* -------------------------------------------------------------------- units */
let moveTimers = [];
function deployUnits(f) {
  moveTimers.forEach(clearTimeout); moveTimers = [];
  const prev = Object.fromEntries(S.units.map((u) => [u.id, u]));
  S.units = f.positions.filter((p) => p.officers > 0).map((p, i) => {
    const { x, y } = posXY(p, f);
    const was = prev[p.id];
    return { id: p.id, call: `7A-${i + 1}`, label: p.label, short: SHORT[p.id] || p.label, officers: p.officers, x, y, status: was ? was.status : "dispatched" };
  });
  renderMap(); renderUnits();
  S.units.forEach((u, i) => {
    if (u.status === "inpos") return;
    moveTimers.push(setTimeout(() => { u.status = "enroute"; renderMap(); renderUnits(); }, 300 + i * 450));
    moveTimers.push(setTimeout(() => { u.status = "inpos"; renderMap(); renderUnits(); save(); }, 3600 + i * 450));
  });
}
function renderUnits() {
  const label = { dispatched: "Dispatched", enroute: "En route", inpos: "In position" };
  $("#units").innerHTML = S.units.map((u) => `<tr><td>${esc(u.call)}</td><td>${esc(u.label)} <span class="officers">· ${u.officers} officer${u.officers > 1 ? "s" : ""}</span></td><td><span class="st ${u.status}">${label[u.status]}</span></td></tr>`).join("")
    || `<tr><td colspan="3" class="placeholder">${S.busy ? "Waiting for the formation plan…" : "No units assigned."}</td></tr>`;
  const inpos = S.units.filter((u) => u.status === "inpos").length;
  const officers = S.units.reduce((n, u) => n + u.officers, 0);
  $("#unitsSum").textContent = S.units.length ? `${inpos}/${S.units.length} in position · ${officers} officers` : "";
}

/* ----------------------------------------------------------------- requests */
function renderRequests() {
  const list = S.formation ? S.formation.requests : [];
  $("#reqs").innerHTML = list.map((r) => `<li><span>${esc(r)}</span><button data-req="${esc(r)}" class="${S.requests[r] ? "done" : ""}">${S.requests[r] ? `Requested ${esc(S.requests[r])}` : "Request"}</button></li>`).join("")
    || `<li class="placeholder">${S.busy ? "Waiting for the plan…" : "No requests."}</li>`;
}

/* ----------------------------------------------------------- planning cell */
function renderAgents() {
  const w = S.where;
  const el = $("#where");
  if (w) {
    const dev = !w.target || w.target === "edge";
    el.className = `where ${dev ? "device" : "cloud"}`;
    el.innerHTML = `<b>${S.busy ? "Planning on" : "Last plan made on"} ${esc(dev ? "this device" : whereName(w.target))}</b>${w.reason ? ` — ${esc(w.reason)}` : ""}`;
  } else el.innerHTML = "";
  $("#agents").innerHTML = S.order.map((id) => {
    const a = S.agents[id];
    const dev = a.target === "edge";
    return `<li><span class="dot ${a.status}"></span><div><div class="ag-name">${esc(a.title)}${a.target ? `<span class="ag-where ${dev ? "device" : "cloud"}">${dev ? "Device" : esc(whereName(a.target))}</span>` : ""}</div><div class="ag-act ${a.tone || ""}">${esc(a.action)}</div></div></li>`;
  }).join("") || `<li><span class="dot running"></span><div><div class="ag-name">Duty inspector</div><div class="ag-act">Deciding which specialists to involve…</div></div></li>`;
  $("#howBtn").hidden = !S.flow;
}

function renderOrders() {
  $("#orders").innerHTML = S.orders ? `<div class="${S.busy ? "cursor" : ""}">${md(S.orders)}</div>` : `<p class="placeholder">${S.busy ? "Specialists are working — orders follow." : "No orders yet."}</p>`;
  $("#ordersState").textContent = S.busy ? "Drafting…" : S.orders ? "Issued" : "";
}

function renderAll() {
  const active = !!S.incident;
  $("#newPanel").hidden = active;
  for (const id of ["unitsPanel", "planPanel", "ordersPanel", "reqPanel", "updatePanel"]) $(`#${id}`).hidden = !active;
  if (S.sessionId) $("#consoleLink").href = `/#session=${S.sessionId}`;
  $("#updateBtn").disabled = S.busy;
  $("#closeInc").disabled = S.busy;
  renderIncident(); renderMap(); renderUnits(); renderRequests(); renderAgents(); renderOrders();
}

/* ------------------------------------------------------------------- stream */
let raf = 0;
const schedule = (fn) => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; fn(); }); };

async function run(body) {
  S.busy = true; S.agents = {}; S.order = []; S.where = null; S.orders = "";
  renderAll();
  try {
    const r = await fetch(`${API}/responses`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ ...body, stream: true, source: "patrol", agent: "police_tactical" }) });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    const reader = r.body.getReader(), dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const block = buf.slice(0, i); buf = buf.slice(i + 2);
        const data = block.split("\n").find((l) => l.startsWith("data: "));
        if (data) onEvent(JSON.parse(data.slice(6)));
      }
    }
  } catch (e) {
    toast(`Request failed: ${e.message}`);
  } finally {
    S.busy = false;
    for (const a of Object.values(S.agents)) if (a.status === "running") a.status = "done";
    save(); renderAll();
  }
}

function agent(ev) {
  return S.agents[ev.task_id] || null;
}

function onEvent(ev) {
  switch (ev.type) {
    case "response.created":
      S.sessionId = ev.response.session_id; S.responseId = ev.response.id; save(); renderAll(); break;
    case "hybridedge.step_start": {
      if (ev.task_id === "plan" || ev.task_id === "synth") { S.where = { target: ev.target, reason: ev.reason }; schedule(renderAgents); break; }
      const a = agent(ev);
      if (a) { a.status = "running"; a.target = ev.target; if (!a.result) { a.action = "Thinking…"; a.tone = ""; } }
      schedule(renderAgents); break;
    }
    case "hybridedge.plan":
      S.order = ev.plan.tasks.map((t) => t.id);
      for (const t of ev.plan.tasks) S.agents[t.id] = { title: t.title, status: "queued", action: t.task, target: null };
      renderAgents(); break;
    case "hybridedge.tool_call": {
      const a = agent(ev);
      if (a) {
        let extra = "";
        try { const args = JSON.parse(ev.args || "{}"); if (ev.name === "lookup_protocol" && args.query) extra = `: ${args.query}`; } catch {}
        a.action = `${TOOL_DOING[ev.name] || ev.name}${extra}…`; a.tone = "";
      }
      schedule(renderAgents); break;
    }
    case "hybridedge.tool_result": {
      const a = agent(ev), r = ev.result || {};
      let text = "";
      if (ev.name === "assess_threat" && r.intel_level) { S.threat = r; text = `Threat level ${r.intel_level} (${r.label}) — ${r.posture}`; renderIncident(); }
      else if (ev.name === "plan_formation" && r.positions) {
        S.formation = r; text = `${r.officers_assigned} officers across ${r.positions.filter((p) => p.officers).length} positions`;
        deployUnits(r); renderRequests(); renderIncident();
      }
      else if (ev.name === "lookup_protocol") text = r.found ? `Found: ${r.results.map((x) => x.title).join(", ")}` : "No matching procedure";
      else if (ev.name === "log_event") text = "Logged to the incident log";
      else if (ev.name === "start_triage") text = r.category ? `Triage: ${r.category}` : "Triage incomplete";
      if (a && text) { a.action = text; a.result = text; a.tone = "good"; }
      save(); schedule(renderAgents); break;
    }
    case "hybridedge.failover": {
      const a = agent(ev);
      const msg = ev.to === "edge" ? "Cloud lost → this device" : `${whereName(ev.from)} failed → ${whereName(ev.to)}`;
      if (a) { a.action = msg; a.tone = "warn"; a.target = ev.to; }
      S.where = { target: ev.to, reason: ev.to_reason };
      toast(msg); schedule(renderAgents); break;
    }
    case "hybridedge.upgrade": {
      const a = agent(ev);
      if (a) { a.action = `Back on ${whereName(ev.to)}`; a.tone = "good"; a.target = ev.to; }
      S.where = { target: ev.to, reason: ev.reason };
      toast(`Back on ${whereName(ev.to)}`, true); schedule(renderAgents); break;
    }
    case "hybridedge.agent_done": {
      const a = agent(ev);
      if (a) { a.status = "done"; if (!a.result) { a.action = "Report done"; a.tone = "good"; } }
      schedule(renderAgents); break;
    }
    case "hybridedge.flow":
      S.flow = ev.flow; if (!$("#modal").hidden) renderModal(); break;
    case "response.output_text.delta":
      S.orders += ev.delta; schedule(renderOrders); break;
    case "hybridedge.output_reset":
      S.orders = ""; schedule(renderOrders); break;
    case "response.completed":
      S.responseId = ev.response.id; S.orders = ev.response.output_text || S.orders; save(); break;
    case "response.failed":
      toast((ev.response.error && ev.response.error.message) || "Planning failed — try again"); break;
  }
}

/* -------------------------------------------------------------------- modal */
function renderModal() { $("#modalBody").innerHTML = S.flow ? HybridFlow.render(S.flow) : "<p>No plan yet.</p>"; }
$("#howBtn").addEventListener("click", () => { renderModal(); $("#modal").hidden = false; });
$("#modalClose").addEventListener("click", () => ($("#modal").hidden = true));
$("#modal").addEventListener("click", (e) => { if (e.target === $("#modal")) $("#modal").hidden = true; });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#modal").hidden = true; });

/* ------------------------------------------------------------------- wiring */
$("#types").addEventListener("click", (e) => { const b = e.target.closest("[data-type]"); if (b) { S.form.type = b.dataset.type; renderForm(); } });
$("#weapon").addEventListener("click", (e) => { const b = e.target.closest("[data-v]"); if (b) { S.form.weapon = b.dataset.v; renderForm(); } });
document.querySelectorAll(".chk").forEach((c) => c.addEventListener("click", () => { S.form.flags[c.dataset.k] = !S.form.flags[c.dataset.k]; renderForm(); }));
document.querySelectorAll("[data-step]").forEach((b) => b.addEventListener("click", () => {
  const k = b.dataset.step, lim = k === "officers" ? [2, 30] : [1, 9];
  S.form[k] = Math.max(lim[0], Math.min(lim[1], S.form[k] + Number(b.dataset.d))); renderForm();
}));
$("#go").addEventListener("click", () => {
  const now = Date.now();
  const d = new Date(now);
  Object.assign(S, blank(), {
    incident: {
      no: `INC-${String(d.getMonth() + 1).padStart(2, "0")}${String(d.getDate()).padStart(2, "0")}-${String(now % 10000).padStart(4, "0")}`,
      type: S.form.type, location: $("#location").value.trim() || "Unknown location", created: now,
    },
  });
  mapKey = "";
  renderAll();
  run({ input: composeInput(), title: `${S.incident.type} — ${S.incident.location}` });
});
$("#update").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = $("#updateText").value.trim();
  if (!text || S.busy || !S.responseId) return;
  $("#updateText").value = "";
  run({ input: `Intel update: ${text}. Re-assess the threat and update the formation and orders.`, previous_response_id: S.responseId });
});
$("#reqs").addEventListener("click", (e) => {
  const b = e.target.closest("[data-req]");
  if (!b) return;
  const k = b.dataset.req;
  S.requests[k] = S.requests[k] ? undefined : new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (!S.requests[k]) delete S.requests[k];
  save(); renderRequests();
});
$("#closeInc").addEventListener("click", () => {
  if (S.busy) return;
  moveTimers.forEach(clearTimeout);
  Object.assign(S, blank()); mapKey = ""; save(); renderAll();
});

renderForm();
renderAll();
pollLink();
