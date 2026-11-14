"use strict";
/*
 * Incident Command — marauding terrorist attack (MTA) exercise, built only on the HybridEdge
 * Agents API (/v1). Reports are placed on an OpenStreetMap map and sent to the session as
 * structured context; the "mta" agent team analyses them with deterministic tools whose
 * results (movement, zones, deployment) are drawn straight onto the map.
 */
const API = "/v1";
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// Real places in central Helsinki (coordinates from OpenStreetMap / Nominatim).
const PLACES = [
  { name: "Narinkka Square", lat: 60.16967, lon: 24.93493 },
  { name: "Kamppi Centre", lat: 60.1691, lon: 24.93315 },
  { name: "Kamppi Chapel", lat: 60.16944, lon: 24.93592 },
  { name: "Tennis Palace", lat: 60.16968, lon: 24.93035 },
  { name: "Lasipalatsi", lat: 60.17029, lon: 24.93689 },
  { name: "Amos Rex", lat: 60.17069, lon: 24.93644 },
  { name: "Kiasma", lat: 60.17204, lon: 24.93674 },
  { name: "Parliament House", lat: 60.17258, lon: 24.93294 },
  { name: "Music Centre", lat: 60.17379, lon: 24.93492 },
  { name: "Oodi Library", lat: 60.17394, lon: 24.93798 },
  { name: "Sanomatalo", lat: 60.17246, lon: 24.93788 },
  { name: "Post House", lat: 60.17155, lon: 24.93801 },
  { name: "Railway Square", lat: 60.17038, lon: 24.93985 },
  { name: "Helsinki Central Station", lat: 60.17155, lon: 24.94059 },
  { name: "Ateneum", lat: 60.17002, lon: 24.94407 },
  { name: "Stockmann", lat: 60.1683, lon: 24.94208 },
  { name: "Old Church Park", lat: 60.16609, lon: 24.93976 },
  { name: "Erottaja", lat: 60.16698, lon: 24.94413 },
  { name: "Esplanadi Park", lat: 60.16739, lon: 24.94491 },
  { name: "Kaisaniemi Park", lat: 60.175, lon: 24.94609 },
  { name: "Senate Square", lat: 60.16951, lon: 24.95227 },
];

// Exercise script: the attackers move east from Narinkka Square towards the Central Station.
const SCRIPT = [
  { min: 0, type: "Attack", lat: 60.16962, lon: 24.93455, attackers: 2, weapon: "knife", casualties: 4, text: "Two attackers with knives attacking people at Narinkka Square" },
  { min: 3, type: "Sighting", lat: 60.17012, lon: 24.93722, attackers: 2, weapon: "firearm", casualties: 2, text: "Attackers running east along Mannerheimintie past Lasipalatsi, one seen with a handgun" },
  { min: 6, type: "Shots fired", lat: 60.17058, lon: 24.93928, attackers: 2, weapon: "firearm", casualties: 3, text: "Shots fired on Railway Square, crowd fleeing" },
  { min: 8, type: "Sighting", lat: 60.17128, lon: 24.94062, attackers: 2, weapon: "firearm", casualties: 0, text: "Both attackers entered the Central Station main hall" },
];

const blank = () => ({
  sessionId: null, responseId: null, started: null, reports: [], busy: false, queued: false,
  movement: null, zones: null, deployment: null, brief: "", flow: null, where: null, agents: {}, order: [], scriptIdx: 0,
});
const S = Object.assign(blank(), { link: null, resources: { arv: 4, ambulance: 3 } });
try { Object.assign(S, JSON.parse(localStorage.getItem("command.v1") || "{}"), { busy: false, queued: false, link: null }); } catch {}
const save = () => { try { const { link, busy, queued, ...rest } = S; localStorage.setItem("command.v1", JSON.stringify(rest)); } catch {} };

/* ------------------------------------------------------------------ helpers */
function md(src) {
  const lines = esc(src || "").split("\n");
  let html = "", list = null;
  const inline = (s) => s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  const close = () => { if (list) { html += `</${list}>`; list = null; } };
  for (const raw of lines) {
    const line = raw.trimEnd(); let m;
    if ((m = line.match(/^\s*#{1,4}\s+(.*)$/))) { close(); html += `<p><strong>${inline(m[1])}</strong></p>`; }
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
  const t = $("#toast"); t.textContent = msg; t.className = `toast${ok ? " ok" : ""}`; t.hidden = false;
  clearTimeout(toastT); toastT = setTimeout(() => (t.hidden = true), 3500);
}
const hhmm = (ms) => new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
function dist(a, b) {
  const lat = ((a.lat + b.lat) / 2) * Math.PI / 180;
  return Math.hypot((b.lon - a.lon) * Math.PI / 180 * Math.cos(lat), (b.lat - a.lat) * Math.PI / 180) * 6371000;
}
function nearest(p) {
  const best = PLACES.reduce((x, q) => (dist(p, q) < dist(p, x) ? q : x), PLACES[0]);
  const d = dist(p, best);
  return d < 60 ? best.name : `${Math.round(d / 10) * 10} m from ${best.name}`;
}
const providerName = (t) => (!t || t === "edge" ? "this device" : S.link && S.link.running_on === t ? S.link.label : t[0].toUpperCase() + t.slice(1));

/* --------------------------------------------------------------------- link */
async function pollLink() {
  try {
    const s = await (await fetch(`${API}/status`)).json();
    const prev = S.link; S.link = s;
    $("#link").className = `link ${s.connected ? "" : "device"}`;
    $("#linkText").textContent = s.connected ? `Cloud · ${s.label}` : s.network ? "Device only" : "No network";
    if (prev && prev.connected !== s.connected) toast(s.connected ? `Cloud back · ${s.label}` : "Cloud lost · analysis runs on this device", s.connected);
  } catch {
    $("#link").className = "link down"; $("#linkText").textContent = "Agents API offline";
  }
  setTimeout(pollLink, 1500);
}
setInterval(() => {
  $("#clock").textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  if (S.started) { const m = Math.floor((Date.now() - S.started) / 60000); $("#incTime").textContent = `Declared ${hhmm(S.started)} · ${m} min`; }
}, 1000);

/* ---------------------------------------------------------------------- map */
const map = L.map("map", { zoomControl: true }).setView([60.1705, 24.9375], 16);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' }).addTo(map);
const layers = { zones: L.layerGroup().addTo(map), path: L.layerGroup().addTo(map), risk: L.layerGroup().addTo(map), reports: L.layerGroup().addTo(map), points: L.layerGroup().addTo(map), units: L.layerGroup().addTo(map) };
const legend = L.control({ position: "bottomleft" });
legend.onAdd = () => {
  const d = L.DomUtil.create("div", "legend");
  d.innerHTML = `<div><i style="background:#dc2626"></i>Report</div><div><i style="background:rgba(220,38,38,.35);border:2px solid #dc2626"></i>Hot zone</div><div><i style="background:rgba(245,158,11,.25);border:2px solid #d97706"></i>Warm zone</div><div><i style="border:2px dashed #2563eb"></i>Cold zone</div><div><i style="background:#1d4ed8"></i>ARV</div><div><i style="background:#15803d"></i>Ambulance</div><div><i style="background:#f59e0b"></i>Place at risk</div>`;
  return d;
};
legend.addTo(map);
const icon = (cls, html) => L.divIcon({ className: "", html: `<div class="mk ${cls}">${html}</div>`, iconSize: null });
const unitMarkers = {};

function drawReports() {
  layers.reports.clearLayers(); layers.path.clearLayers();
  S.reports.forEach((r, i) => {
    L.marker([r.lat, r.lon], { icon: icon(`mk-report${i < S.reports.length - 1 ? " old" : ""}`, i + 1), zIndexOffset: 800 + i })
      .bindTooltip(`<b>${esc(r.type)}</b> · ${hhmm(r.t * 1000)}<br>${esc(r.text)}`).addTo(layers.reports);
  });
  if (S.reports.length > 1) L.polyline(S.reports.map((r) => [r.lat, r.lon]), { color: "#dc2626", weight: 3, opacity: 0.7 }).addTo(layers.path);
  const mv = S.movement;
  if (mv && mv.predicted && mv.predicted.length) {
    const pts = [[mv.last_seen.lat, mv.last_seen.lon], ...mv.predicted.map((p) => [p.lat, p.lon])];
    L.polyline(pts, { color: "#dc2626", weight: 3, dashArray: "6 8" }).addTo(layers.path);
    mv.predicted.forEach((p) => L.marker([p.lat, p.lon], { icon: icon("mk-pred", `+${p.minutes} min`), interactive: false }).addTo(layers.path));
  }
}
function drawRisk() {
  layers.risk.clearLayers();
  for (const r of (S.movement && S.movement.places_at_risk) || []) {
    if (r.in_path) L.marker([r.lat, r.lon], { icon: icon("mk-risk path", `${esc(r.name)} · ${r.eta_min} min`), zIndexOffset: 400 }).addTo(layers.risk);
    else L.circleMarker([r.lat, r.lon], { radius: 6, color: "#fff", weight: 2, fillColor: "#f59e0b", fillOpacity: 1 }).bindTooltip(`${esc(r.name)} · ${r.eta_min} min`).addTo(layers.risk);
  }
}
function drawZones() {
  layers.zones.clearLayers(); layers.points.clearLayers();
  const z = S.zones;
  if (!z || !z.hot) return;
  L.circle([z.cold.lat, z.cold.lon], { radius: z.cold.radius_m, color: "#2563eb", weight: 2, dashArray: "8 6", fillOpacity: 0.03 }).addTo(layers.zones);
  L.circle([z.warm.lat, z.warm.lon], { radius: z.warm.radius_m, color: "#d97706", weight: 2, fillColor: "#f59e0b", fillOpacity: 0.12 }).addTo(layers.zones);
  L.circle([z.hot.lat, z.hot.lon], { radius: z.hot.radius_m, color: "#dc2626", weight: 2, fillColor: "#dc2626", fillOpacity: 0.16 }).addTo(layers.zones);
  for (const [k, label] of [["rvp", "RVP"], ["ccp", "CCP"], ["fcp", "Command"]]) {
    if (z[k]) L.marker([z[k].lat, z[k].lon], { icon: icon("mk-pt", label), zIndexOffset: 300 }).bindTooltip(`${label}: ${esc(z[k].near)}`).addTo(layers.points);
  }
}
function drawUnits() {
  const units = (S.deployment && S.deployment.units) || [];
  const seen = new Set();
  for (const u of units) {
    seen.add(u.id);
    const cls = u.type === "arv" ? "mk-arv" : u.type === "ambulance" ? "mk-amb" : "mk-cmd";
    const label = u.type === "command" ? "CMD" : u.id.replace("Ambulance", "AMB");
    let m = unitMarkers[u.id];
    if (!m) {
      const start = S.zones && S.zones.rvp ? [S.zones.rvp.lat, S.zones.rvp.lon] : [u.lat, u.lon];
      m = unitMarkers[u.id] = L.marker(start, { icon: icon(cls, esc(label)), zIndexOffset: 600 }).addTo(layers.units);
      requestAnimationFrame(() => { const el = m.getElement(); if (el) el.classList.add("mk-anim"); requestAnimationFrame(() => m.setLatLng([u.lat, u.lon])); });
    } else m.setLatLng([u.lat, u.lon]);
    m.bindTooltip(`<b>${esc(u.id)}</b><br>${esc(u.task)}`);
  }
  for (const id of Object.keys(unitMarkers)) if (!seen.has(id)) { layers.units.removeLayer(unitMarkers[id]); delete unitMarkers[id]; }
}
function drawAll() { drawZones(); drawRisk(); drawReports(); drawUnits(); }

/* ------------------------------------------------------------------- panels */
function renderThreat() {
  const mv = S.movement;
  const reps = S.reports;
  const cas = reps.reduce((n, r) => n + (Number(r.casualties) || 0), 0);
  $("#tAttackers").textContent = (mv && mv.attackers) || Math.max(0, ...reps.map((r) => Number(r.attackers) || 0)) || "Unknown";
  const weapons = [...new Set(reps.map((r) => r.weapon).filter((w) => w && w !== "unknown"))];
  $("#tWeapons").textContent = weapons.length ? weapons.join(", ") : "Unknown";
  $("#tCasualties").textContent = cas || "None reported";
  $("#tHeading").textContent = mv && mv.heading && mv.heading !== "unknown" ? `${mv.heading[0].toUpperCase()}${mv.heading.slice(1)}${mv.speed_mps ? ` · ${(mv.speed_mps * 3.6).toFixed(0)} km/h` : ""}` : "Unknown";
  const last = reps[reps.length - 1];
  $("#tLast").innerHTML = last ? `Last report <b>${hhmm(last.t * 1000)}</b> — ${esc(nearest(last))}` : "";
  $("#tRisk").innerHTML = ((mv && mv.places_at_risk) || []).map((r) => `<li class="${r.in_path ? "path" : ""}"><span>${esc(r.name)}${r.in_path ? " · in path" : ""}</span><span>${r.eta_min} min</span></li>`).join("");
}
function renderReports() {
  $("#repCount").textContent = S.reports.length ? `${S.reports.length}` : "";
  $("#reports").innerHTML = [...S.reports].map((r, i) => ({ r, i })).reverse().map(({ r, i }) =>
    `<li><span class="rnum">${i + 1}</span><span><span class="rtype">${esc(r.type)}</span> · ${esc(nearest(r))}<br><span class="muted">${esc(r.text)}</span></span><span class="rtime">${hhmm(r.t * 1000)}</span></li>`).join("")
    || `<li class="placeholder">No reports yet. Use “Add report” or “Next exercise report”.</li>`;
}
const TOOL_DOING = { analyse_movement: "Analysing reports", set_zones: "Setting zones", plan_deployment: "Deploying units", lookup_protocol: "Checking procedure", start_triage: "Running triage", log_event: "Logging" };
function renderAgents() {
  const w = S.where;
  if (w) {
    const dev = !w.target || w.target === "edge";
    $("#where").className = `where ${dev ? "device" : "cloud"}`;
    $("#where").innerHTML = `<b>${S.busy ? "Running on" : "Last run on"} ${esc(providerName(w.target))}</b>${w.reason ? ` — ${esc(w.reason)}` : ""}`;
  } else { $("#where").className = "where"; $("#where").textContent = ""; }
  $("#agents").innerHTML = S.order.map((id) => {
    const a = S.agents[id]; const dev = a.target === "edge";
    return `<li><span class="dot ${a.status}"></span><div><div class="ag-n">${esc(a.title)}${a.target ? `<span class="ag-w ${dev ? "device" : "cloud"}">${dev ? "Device" : esc(providerName(a.target))}</span>` : ""}</div><div class="ag-a ${a.tone || ""}">${esc(a.action)}</div></div></li>`;
  }).join("") || (S.busy ? `<li><span class="dot running"></span><div><div class="ag-n">Incident commander</div><div class="ag-a">Choosing advisers…</div></div></li>` : `<li class="placeholder">Runs after each report.</li>`);
  $("#howBtn").hidden = !S.flow;
}
function renderBrief() {
  $("#brief").innerHTML = S.brief ? `<div class="${S.busy ? "cursor" : ""}">${md(S.brief)}</div>` : `<p class="placeholder">${S.busy ? "Advisers are working…" : "Appears after the first report."}</p>`;
  $("#briefState").textContent = S.busy ? "Updating…" : S.brief ? `Updated ${hhmm(Date.now())}` : "";
}
function renderUnits() {
  const units = (S.deployment && S.deployment.units) || [];
  const col = { arv: "#1d4ed8", ambulance: "#15803d", command: "#0f172a" };
  $("#units").innerHTML = units.map((u) => `<li><span class="u-id"><span class="u-sw" style="background:${col[u.type]}"></span>${esc(u.id)}</span><span>${esc(u.task)}</span></li>`).join("")
    || `<li class="placeholder">${S.busy ? "Waiting for the firearms adviser…" : S.reports.length ? "Not planned in the last run." : "No units deployed."}</li>`;
}
function renderAll() {
  const active = !!S.started;
  $("#startPanel").hidden = active; $("#activePanels").hidden = !active;
  $("#incTitle").textContent = active ? "Marauding terrorist attack — Helsinki city centre" : "No incident";
  $("#exTag").hidden = !active;
  $("#addReport").disabled = !active; $("#nextReport").disabled = !active || S.scriptIdx >= SCRIPT.length;
  if (S.sessionId) $("#consoleLink").href = `/#session=${S.sessionId}`;
  renderThreat(); renderReports(); renderAgents(); renderBrief(); renderUnits();
}

/* ------------------------------------------------------------------- agents */
async function ensureSession() {
  if (S.sessionId) return;
  const r = await fetch(`${API}/sessions`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ title: "Marauding terrorist attack — Helsinki city centre (exercise)", agent: "mta", source: "incident-command" }) });
  S.sessionId = (await r.json()).id; save();
}
async function pushContext() {
  await fetch(`${API}/sessions/${S.sessionId}/context`, { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ reports: S.reports, places: PLACES, resources: S.resources, area: "Helsinki city centre" }) });
}
function describe(r, i) {
  return `Report ${i + 1} at ${hhmm(r.t * 1000)} — ${r.type} at ${nearest(r)}: ${r.text}.${r.attackers ? ` Attackers: ${r.attackers}.` : ""}${r.weapon && r.weapon !== "unknown" ? ` Weapon: ${r.weapon}.` : ""}${r.casualties ? ` Casualties: ${r.casualties}.` : ""}`;
}
async function analyse() {
  if (S.busy) { S.queued = true; return; }
  S.busy = true; S.queued = false; S.agents = {}; S.order = []; S.where = null; S.brief = "";
  renderAll();
  try {
    await ensureSession();
    await pushContext();
    const latest = S.reports[S.reports.length - 1];
    const input = S.reports.length === 1
      ? `Marauding terrorist attack declared in Helsinki city centre. ${describe(latest, 0)} ARVs available: ${S.resources.arv}. Ambulances: ${S.resources.ambulance}. Produce the commander's brief.`
      : `New report. ${describe(latest, S.reports.length - 1)} Update the assessment and brief.`;
    const r = await fetch(`${API}/responses`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ session_id: S.sessionId, input, stream: true, source: "incident-command" }) });
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
    toast(`Analysis failed: ${e.message}`);
  } finally {
    S.busy = false;
    for (const a of Object.values(S.agents)) if (a.status === "running") a.status = "done";
    save(); renderAll();
    if (S.queued) analyse();
  }
}
let raf = 0;
const schedule = (fn) => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; fn(); }); };
function onEvent(ev) {
  const a = S.agents[ev.task_id];
  switch (ev.type) {
    case "response.created": S.responseId = ev.response.id; break;
    case "hybridedge.step_start":
      if (ev.task_id === "plan" || ev.task_id === "synth") S.where = { target: ev.target, reason: ev.reason };
      else if (a) { a.status = "running"; a.target = ev.target; if (!a.result) { a.action = "Thinking…"; a.tone = ""; } }
      schedule(renderAgents); break;
    case "hybridedge.plan":
      S.order = ev.plan.tasks.map((t) => t.id);
      for (const t of ev.plan.tasks) S.agents[t.id] = { title: t.title, status: "queued", action: t.task, target: null };
      renderAgents(); break;
    case "hybridedge.tool_call":
      if (a) { a.action = `${TOOL_DOING[ev.name] || ev.name}…`; a.tone = ""; }
      schedule(renderAgents); break;
    case "hybridedge.tool_result": {
      const r = ev.result || {};
      if (ev.name === "analyse_movement" && r.reports) { S.movement = r; drawReports(); drawRisk(); renderThreat(); }
      if (ev.name === "set_zones" && r.hot) {
        S.zones = r; drawZones();
        const c = L.latLng(r.warm.lat, r.warm.lon);
        map.flyToBounds(c.toBounds(r.warm.radius_m * 2.3), { duration: 0.8, paddingTopLeft: [10, 50] });
      }
      if (ev.name === "plan_deployment" && r.units) { S.deployment = r; drawUnits(); renderUnits(); }
      if (a && r.summary) { a.action = r.summary; a.result = true; a.tone = "good"; }
      save(); schedule(renderAgents); break;
    }
    case "hybridedge.failover":
      if (a) { a.action = ev.to === "edge" ? "Cloud lost → this device" : `${providerName(ev.from)} failed → ${providerName(ev.to)}`; a.tone = "warn"; a.target = ev.to; }
      S.where = { target: ev.to, reason: ev.to_reason };
      schedule(renderAgents); break;
    case "hybridedge.upgrade":
      if (a) { a.action = `Back on ${providerName(ev.to)}`; a.tone = "good"; a.target = ev.to; }
      S.where = { target: ev.to, reason: ev.reason };
      schedule(renderAgents); break;
    case "hybridedge.agent_done":
      if (a) { a.status = "done"; if (!a.result) { a.action = "Advice ready"; a.tone = "good"; } }
      schedule(renderAgents); break;
    case "hybridedge.flow":
      S.flow = ev.flow; if (!$("#modal").hidden) renderModal(); break;
    case "response.output_text.delta": S.brief += ev.delta; schedule(renderBrief); break;
    case "hybridedge.output_reset": S.brief = ""; schedule(renderBrief); break;
    case "response.completed": S.responseId = ev.response.id; S.brief = ev.response.output_text || S.brief; break;
    case "response.failed": toast((ev.response.error && ev.response.error.message) || "Analysis failed"); break;
  }
}

/* ------------------------------------------------------------------ reports */
function addReport(r) {
  S.reports.push({ id: `r${S.reports.length + 1}`, ...r });
  save(); drawReports(); renderAll();
  analyse();
}
let adding = false;
function setAdding(on) {
  adding = on;
  $("#addReport").classList.toggle("on", on);
  $("#mapHint").hidden = !on;
  map.getContainer().style.cursor = on ? "crosshair" : "";
}
map.on("click", (e) => {
  if (!adding) return;
  setAdding(false);
  const { lat, lng } = e.latlng;
  const form = L.DomUtil.create("form", "rform");
  form.innerHTML = `<b>New report · ${esc(nearest({ lat, lon: lng }))}</b>
    <label>Type<select name="type"><option>Sighting</option><option>Attack</option><option>Shots fired</option><option>Explosion</option><option>Casualties</option></select></label>
    <div class="row2"><label>Attackers<input name="attackers" type="number" min="0" value="2"></label><label>Casualties<input name="casualties" type="number" min="0" value="0"></label></div>
    <label>Weapon<select name="weapon"><option value="unknown">Unknown</option><option value="knife">Knife</option><option value="firearm">Firearm</option><option value="vehicle">Vehicle</option><option value="explosive">Explosive</option></select></label>
    <label>Details<input name="text" placeholder="What was seen or heard" required></label>
    <button type="submit">Add report</button>`;
  const popup = L.popup({ minWidth: 240 }).setLatLng(e.latlng).setContent(form).openOn(map);
  form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const f = new FormData(form);
    map.closePopup(popup);
    addReport({ type: f.get("type"), lat: +lat.toFixed(6), lon: +lng.toFixed(6), t: Math.round(Date.now() / 1000), attackers: +f.get("attackers") || null, casualties: +f.get("casualties") || 0, weapon: f.get("weapon"), text: String(f.get("text")).trim() });
  });
  setTimeout(() => form.querySelector("[name=text]").focus(), 50);
});

/* -------------------------------------------------------------------- modal */
function renderModal() { $("#modalBody").innerHTML = S.flow ? HybridFlow.render(S.flow) : "<p>No analysis yet.</p>"; }
$("#howBtn").addEventListener("click", () => { renderModal(); $("#modal").hidden = false; });
$("#modalClose").addEventListener("click", () => ($("#modal").hidden = true));
$("#modal").addEventListener("click", (e) => { if (e.target === $("#modal")) $("#modal").hidden = true; });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") { $("#modal").hidden = true; if (adding) setAdding(false); } });

/* ------------------------------------------------------------------- wiring */
$("#startEx").addEventListener("click", () => {
  Object.assign(S, blank(), { started: Date.now(), resources: { arv: Math.max(0, +$("#arvN").value || 0), ambulance: Math.max(0, +$("#ambN").value || 0) } });
  for (const id of Object.keys(unitMarkers)) { layers.units.removeLayer(unitMarkers[id]); delete unitMarkers[id]; }
  save(); drawAll(); renderAll();
});
$("#addReport").addEventListener("click", () => setAdding(!adding));
$("#cancelAdd").addEventListener("click", () => setAdding(false));
$("#nextReport").addEventListener("click", () => {
  const s = SCRIPT[S.scriptIdx];
  if (!s) return;
  S.scriptIdx += 1;
  addReport({ type: s.type, lat: s.lat, lon: s.lon, t: Math.round(Date.now() / 1000), attackers: s.attackers, weapon: s.weapon, casualties: s.casualties, text: s.text });
  map.panTo([s.lat, s.lon], { animate: true });
});
$("#endEx").addEventListener("click", () => {
  if (S.busy) return toast("Wait for the analysis to finish");
  Object.assign(S, blank());
  for (const id of Object.keys(unitMarkers)) { layers.units.removeLayer(unitMarkers[id]); delete unitMarkers[id]; }
  save(); drawAll(); renderAll();
});

renderAll();
drawAll();
pollLink();
