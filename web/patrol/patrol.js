"use strict";
/*
 * Patrol — a police tactical-response demo built ONLY on the HybridEdge Agents API (/v1).
 * It knows nothing about clouds, providers, checkpoints or LangGraph: it sends a Responses
 * request with agent "police_tactical", follows the stream, and renders tool results
 * (threat score, formation) plus the abstract `flow` the API provides.
 */
const API = "/v1";
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const S = {
  type: "Barricaded suspect",
  weapon: "firearm",
  flags: { barricaded: true, hostages: false, violent: true, building: false },
  subjects: 1,
  officers: 9,
  sessionId: null,
  responseId: null,
  busy: false,
  threat: null,
  formation: null,
  orders: "",
  flow: null,
  link: null,
};
try { Object.assign(S, JSON.parse(localStorage.getItem("patrol.state") || "{}"), { busy: false }); } catch {}
const save = () => { try { localStorage.setItem("patrol.state", JSON.stringify({ ...S, busy: false, link: null })); } catch {} };

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
  clearTimeout(toastT); toastT = setTimeout(() => (t.hidden = true), 3200);
}

/* --------------------------------------------------------------------- link */
async function pollLink() {
  try {
    const s = await (await fetch(`${API}/status`)).json();
    const prev = S.link;
    S.link = s;
    const el = $("#link");
    el.className = `link ${s.connected ? "" : "edge"}`;
    $("#linkText").textContent = s.connected
      ? `Cloud link · ${s.label} · ${s.latency_ms ?? "–"} ms · up to ${s.max_agents} agents`
      : `${s.network ? "No cloud reachable" : "No network"} · running on this device · up to ${s.max_agents} agents`;
    if (prev && prev.connected !== s.connected) toast(s.connected ? `Cloud link restored (${s.label})` : "Link lost — planning continues on this device", s.connected);
  } catch {
    $("#link").className = "link down";
    $("#linkText").textContent = "HybridEdge Agents API unreachable";
  }
  setTimeout(pollLink, 1500);
}

/* -------------------------------------------------------------------- brief */
function composeInput() {
  const f = S.flags;
  const intel = [
    `weapon: ${S.weapon}`,
    f.barricaded ? "subject barricaded" : null,
    f.hostages ? "hostages or vulnerable persons present" : "no known hostages",
    f.violent ? "history of violence" : null,
    f.building ? "subject inside a building" : null,
    `${S.subjects} subject${S.subjects > 1 ? "s" : ""}`,
  ].filter(Boolean).join("; ");
  const notes = $("#notes").value.trim();
  return `Incident: ${S.type}. Location: ${$("#location").value.trim()}. Intel: ${intel}. Officers available: ${S.officers}.${notes ? ` Notes: ${notes}.` : ""} Produce the tactical plan.`;
}

function renderBrief() {
  document.querySelectorAll("#types .chip").forEach((c) => c.classList.toggle("on", c.dataset.type === S.type));
  document.querySelectorAll("#weapon button").forEach((b) => b.classList.toggle("on", b.dataset.v === S.weapon));
  document.querySelectorAll(".tog").forEach((t) => t.classList.toggle("on", !!S.flags[t.dataset.k]));
  $("#subjects").textContent = S.subjects;
  $("#officers").textContent = S.officers;
}

/* --------------------------------------------------------------------- plan */
const COLORS = { inner: "#3b82f6", outer: "#94a3b8", overwatch: "#a855f7", staging: "#22c55e", cp: "#38bdf8", contact: "#f97316" };
const SHORT = { cp: "CP", inner_north: "N", inner_east: "E", inner_south: "S", inner_west: "W", overwatch: "OW", arrest: "AT", contact: "C", cover: "CV", rear: "R", outer_a: "TA", outer_b: "TB", outer_c: "PC", ems: "EMS" };
const colorOf = (p) => p.id === "cp" ? COLORS.cp : (p.id === "contact" || p.id === "cover") ? COLORS.contact : COLORS[p.ring] || COLORS.outer;

function renderMap() {
  const f = S.formation;
  if (!f) { $("#map").innerHTML = `<div class="placeholder">${S.busy ? "Formation planner is working…" : "No formation yet."}</div>`; $("#assign").innerHTML = ""; $("#requests").innerHTML = ""; $("#fsum").textContent = ""; return; }
  const C = 180, R = { inner: 78, overwatch: 108, outer: 140, staging: 166 };
  const pt = (ring, b) => { const r = R[ring] ?? 140, a = (b * Math.PI) / 180; return [C + r * Math.sin(a), C - r * Math.cos(a)]; };
  const dots = f.positions.map((p, i) => {
    const [x, y] = pt(p.ring, p.bearing);
    const col = colorOf(p);
    const label = SHORT[p.id] || p.label.slice(0, 3);
    return `<g class="pos" style="animation-delay:${i * 70}ms;transform-origin:${x}px ${y}px">
      <circle cx="${x}" cy="${y}" r="15" fill="${col}" fill-opacity=".18" stroke="${col}" stroke-width="1.5" ${p.officers ? "" : 'stroke-dasharray="3 3"'}/>
      <text x="${x}" y="${y + 4}" text-anchor="middle" font-size="11" font-weight="700" fill="${col}">${esc(label)}</text>
      ${p.officers ? `<circle cx="${x + 12}" cy="${y - 12}" r="7.5" fill="#0e1729" stroke="${col}"/><text x="${x + 12}" y="${y - 9}" text-anchor="middle" font-size="9.5" font-weight="700" fill="#e7eefb">${p.officers}</text>` : ""}
    </g>`;
  }).join("");
  $("#map").innerHTML = `<svg viewBox="0 0 360 360" role="img" aria-label="Formation diagram">
    <circle cx="${C}" cy="${C}" r="${R.outer}" fill="none" stroke="#64748b" stroke-opacity=".5" stroke-dasharray="4 6"/>
    <circle cx="${C}" cy="${C}" r="${R.inner}" fill="#ef4444" fill-opacity=".05" stroke="#3b82f6" stroke-opacity=".7"/>
    <text x="${C - R.inner * 0.72}" y="${C + R.inner * 0.72 + 14}" text-anchor="middle" font-size="9.5" fill="#8b9bb8">INNER · ${f.standoff_m} m</text>
    <text x="${C}" y="${C - R.outer - 6}" text-anchor="middle" font-size="9.5" fill="#8b9bb8">OUTER · ${f.outer_m} m</text>
    <circle cx="${C}" cy="${C}" r="20" fill="#ef4444" fill-opacity=".15"><animate attributeName="r" values="14;26;14" dur="2s" repeatCount="indefinite"/></circle>
    <circle cx="${C}" cy="${C}" r="9" fill="#ef4444"/>
    <text x="${C}" y="${C + 26}" text-anchor="middle" font-size="9.5" font-weight="700" fill="#fca5a5">SUBJECT</text>
    ${dots}
  </svg>`;
  $("#fsum").textContent = `${f.officers_assigned} of ${f.officers_available} officers · ${f.posture}`;
  $("#assign").innerHTML = f.positions.map((p) => `<li><span class="sw" style="background:${colorOf(p)}"></span><span><b>${esc(SHORT[p.id] || "")}</b> ${esc(p.label)}<br><span style="color:var(--muted);font-size:12px">${esc(p.note)}</span></span><span class="n">${p.officers ? `${p.officers} officer${p.officers > 1 ? "s" : ""}` : "Request"}</span></li>`).join("");
  $("#requests").innerHTML = f.requests.map((r) => `<span class="req">${esc(r)}</span>`).join("");
}

function renderThreat() {
  const t = S.threat || (S.formation && { intel_level: S.formation.intel_level, label: ["", "LOW", "GUARDED", "ELEVATED", "CRITICAL"][S.formation.intel_level], posture: S.formation.posture, standoff_m: S.formation.standoff_m, factors: [] });
  const el = $("#threat");
  if (!t) { el.removeAttribute("data-l"); $("#lvl").textContent = "–"; $("#tlabel").textContent = S.busy ? "Assessing threat…" : "No assessment yet"; $("#tposture").textContent = ""; $("#tfactors").textContent = ""; return; }
  el.dataset.l = t.intel_level;
  $("#lvl").textContent = t.intel_level;
  $("#tlabel").textContent = `INTEL LEVEL ${t.intel_level} · ${t.label}`;
  $("#tposture").textContent = `${t.posture} · stand-off ${t.standoff_m} m`;
  $("#tfactors").textContent = (t.factors || []).join(" · ");
}

function renderFlow() {
  $("#flow").innerHTML = S.flow ? HybridFlow.render(S.flow, { compact: true }) : `<div class="placeholder">${S.busy ? "Waiting for the planner…" : "No run yet."}</div>`;
}

function renderOrders() {
  $("#orders").innerHTML = S.orders ? `<div class="${S.busy ? "cursor" : ""}">${md(S.orders)}</div>` : `<div class="placeholder">${S.busy ? "Specialists are working — orders follow." : "Orders appear here."}</div>`;
}

function renderPlan() {
  $("#ptitle").textContent = `${S.type} — ${$("#location").value.trim()}`;
  if (S.sessionId) $("#consoleLink").href = `/#session=${S.sessionId}`;
  $("#consoleLink").hidden = !S.sessionId;
  renderThreat(); renderMap(); renderFlow(); renderOrders();
  $("#updateBtn").disabled = S.busy;
}

function show(screen) {
  $("#brief").hidden = screen !== "brief";
  $("#plan").hidden = screen !== "plan";
  $(".update").hidden = screen !== "plan";
  window.scrollTo({ top: 0 });
}

/* ------------------------------------------------------------------- stream */
let raf = 0;
const schedule = (fn) => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; fn(); }); };

async function run(body) {
  S.busy = true; save(); renderPlan();
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
    S.busy = false; save(); renderPlan();
  }
}

function onEvent(ev) {
  switch (ev.type) {
    case "response.created":
      S.sessionId = ev.response.session_id; S.responseId = ev.response.id; save(); renderPlan(); break;
    case "hybridedge.flow":
      S.flow = ev.flow; schedule(renderFlow); break;
    case "hybridedge.tool_result":
      if (ev.name === "assess_threat" && ev.result && ev.result.intel_level) { S.threat = ev.result; renderThreat(); }
      if (ev.name === "plan_formation" && ev.result && ev.result.positions) { S.formation = ev.result; renderMap(); renderThreat(); }
      break;
    case "hybridedge.failover":
      toast(ev.to === "edge" ? "Link lost — continuing on this device from checkpoint" : `Switched to ${ev.to} — resumed from checkpoint`); break;
    case "response.output_text.delta":
      S.orders += ev.delta; schedule(renderOrders); break;
    case "hybridedge.output_reset":
      S.orders = ""; schedule(renderOrders); break;
    case "response.completed":
      S.responseId = ev.response.id; S.orders = ev.response.output_text || S.orders; save(); break;
    case "response.failed":
      toast((ev.response.error && ev.response.error.message) || "Plan failed — state is saved; try again"); break;
  }
}

/* ------------------------------------------------------------------- wiring */
$("#types").addEventListener("click", (e) => { const c = e.target.closest("[data-type]"); if (c) { S.type = c.dataset.type; renderBrief(); } });
$("#weapon").addEventListener("click", (e) => { const b = e.target.closest("[data-v]"); if (b) { S.weapon = b.dataset.v; renderBrief(); } });
document.querySelectorAll(".tog").forEach((t) => t.addEventListener("click", () => { S.flags[t.dataset.k] = !S.flags[t.dataset.k]; renderBrief(); }));
document.querySelectorAll("[data-step]").forEach((b) => b.addEventListener("click", () => {
  const k = b.dataset.step, lim = k === "officers" ? [1, 40] : [1, 9];
  S[k] = Math.max(lim[0], Math.min(lim[1], S[k] + Number(b.dataset.d))); renderBrief();
}));
$("#go").addEventListener("click", () => {
  Object.assign(S, { sessionId: null, responseId: null, threat: null, formation: null, orders: "", flow: null });
  show("plan");
  run({ input: composeInput(), title: `${S.type} — ${$("#location").value.trim()}` });
});
$("#back").addEventListener("click", () => { if (!S.busy) { show("brief"); } });
$("#update").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = $("#updateText").value.trim();
  if (!text || S.busy || !S.responseId) return;
  $("#updateText").value = "";
  S.orders = ""; S.flow = null;
  run({ input: `Intel update: ${text}. Re-assess and update the plan.`, previous_response_id: S.responseId });
});

renderBrief();
pollLink();
if (S.sessionId && S.orders) { show("plan"); renderPlan(); } else show("brief");
