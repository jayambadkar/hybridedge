// Equal Care: cancer pathway by wellbeing services county. Data: /api/pathway (public aggregate data only).
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const API = "/v1";
const COLORS = { detection: "var(--detection)", primary: "var(--primary)", referral: "var(--referral)" };
const STATUS = { behind: "Behind", typical: "Typical", ahead: "Ahead", not_measured: "Not measured", no_data: "Not reported" };
const S = { overview: null, geo: null, sources: null, code: null, county: null, busy: false, agents: {}, order: [], brief: "" };

const num = (v, unit = "%") => (v == null ? "–" : `${Number(v).toLocaleString("en", { maximumFractionDigits: 1 })}${unit === "%" ? " %" : ""}`);
const chip = (id) => (id ? `<span class="src" data-src="${esc(id)}">${esc(id)}</span>` : "");
const get = async (u) => (await fetch(u)).json();

function toast(msg) { const t = $("#toast"); t.textContent = msg; t.hidden = false; setTimeout(() => (t.hidden = true), 3500); }

// ---------------------------------------------------------------- map
function drawMap() {
  const W = 300, H = 400, k = Math.cos((65 * Math.PI) / 180);
  let x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9;
  const polys = (g) => (g.type === "Polygon" ? [g.coordinates] : g.coordinates);
  for (const f of S.geo) for (const p of polys(f.geometry)) for (const [x, y] of p[0]) { x0 = Math.min(x0, x * k); x1 = Math.max(x1, x * k); y0 = Math.min(y0, y); y1 = Math.max(y1, y); }
  const sc = Math.min((W - 12) / (x1 - x0), (H - 12) / (y1 - y0)), ox = (W - (x1 - x0) * sc) / 2;
  const pt = ([x, y]) => `${(ox + (x * k - x0) * sc).toFixed(1)},${(6 + (y1 - y) * sc).toFixed(1)}`;
  const by = Object.fromEntries(S.overview.counties.map((c) => [c.code, c]));
  $("#map").setAttribute("viewBox", `0 0 ${W} ${H}`);
  $("#map").innerHTML = S.geo.map((f) => {
    const d = polys(f.geometry).map((p) => p.map((ring) => `M${ring.map(pt).join("L")}Z`).join("")).join("");
    const c = by[f.code];
    return `<path d="${d}" data-code="${f.code}" fill="${COLORS[c?.gap_stage] || "var(--none)"}"></path>`;
  }).join("");
  const n = S.overview.gap_counts;
  $("#legend").innerHTML = [["detection", "Early detection"], ["primary", "Primary care"], ["referral", "Referral"]]
    .map(([k2, l]) => `<span><i style="background:${COLORS[k2]}"></i>${l} (${n[k2]})</span>`).join("") + `<span><i style="background:var(--none)"></i>No stage stands out</span>`;
  const tip = $("#tip"), map = $("#map");
  map.addEventListener("mousemove", (e) => {
    const c = by[e.target.dataset?.code];
    if (!c) return (tip.hidden = true);
    tip.innerHTML = `<b>${esc(c.name)}</b>${c.gap_stage ? `Gap opens at ${STAGE[c.gap_stage].toLowerCase()}` : "No stage stands out"}`;
    const r = map.getBoundingClientRect();
    tip.style.left = `${Math.min(e.clientX - r.left + 12, r.width - 150)}px`; tip.style.top = `${e.clientY - r.top + 12}px`; tip.hidden = false;
  });
  map.addEventListener("mouseleave", () => (tip.hidden = true));
  map.addEventListener("click", (e) => e.target.dataset?.code && select(e.target.dataset.code));
}
let STAGE = {};

// ---------------------------------------------------------------- county
function strip(i) {
  // every county as a faint tick, this county as a dot, Finland as a line, the target as a dashed line
  const all = S.overview.counties.map((c) => c.values[i.key]).filter((v) => v != null);
  const lo = Math.min(...all, i.target ?? Infinity), hi = Math.max(...all, i.target ?? -Infinity), span = hi - lo || 1;
  const x = (v) => (4 + ((v - lo) / span) * 192).toFixed(1);
  return `<svg class="strip" viewBox="0 0 200 18" preserveAspectRatio="none" aria-hidden="true">
    <line x1="4" x2="196" y1="9" y2="9" stroke="#e2e8f0" stroke-width="2"/>
    ${all.map((v) => `<line x1="${x(v)}" x2="${x(v)}" y1="6" y2="12" stroke="#94a3b8" stroke-width="1"/>`).join("")}
    ${i.national != null ? `<line x1="${x(i.national)}" x2="${x(i.national)}" y1="2" y2="16" stroke="#0f172a" stroke-width="1.5"/>` : ""}
    ${i.target ? `<line x1="${x(i.target)}" x2="${x(i.target)}" y1="1" y2="17" stroke="#15803d" stroke-width="1.5" stroke-dasharray="2 2"/>` : ""}
    <circle cx="${x(i.value)}" cy="9" r="4.5" fill="${i.worse_by_sd > 0.5 ? "#b91c1c" : "#0f766e"}" stroke="#fff" stroke-width="1.5"/></svg>`;
}
function indHtml(i) {
  if (i.missing) return `<div class="ind"><div class="ind-l">${esc(i.label)}</div><div class="note muted">Not reported by this county ${chip(i.source)}</div></div>`;
  const d = i.vs_national, good = d != null && d !== 0 && (d > 0) === i.higher_is_better;
  return `<div class="ind"><div class="ind-l">${esc(i.label)}</div>
    <div class="ind-v"><b>${num(i.value, i.unit)}</b>${d != null ? `<span class="d ${d === 0 ? "" : good ? "good" : "bad"}">${d > 0 ? "+" : ""}${d} vs Finland</span>` : ""}</div>
    ${strip(i)}
    <div class="ind-f"><span>${i.target ? `Target ${i.target} %` : `Finland ${num(i.national, i.unit)}`} · rank ${i.rank}/${i.counties}</span><span>${i.year}${chip(i.source)}</span></div></div>`;
}
function renderCounty() {
  const c = S.county;
  $("#countyMeta").innerHTML = `${c.population ? `${c.population.toLocaleString("en")} residents` : ""}${c.small ? `<span class="chip">Small county: figures vary year to year</span>` : ""}`;
  $("#headline").textContent = c.headline.text;
  $("#unmeasured").textContent = c.headline.unmeasured;
  $("#path").innerHTML = c.stages.map((s) => `
    <div class="stage ${s.id === c.gap_stage ? "gap" : ""} ${s.status === "not_measured" ? "unmeasured" : ""}">
      ${s.id === c.gap_stage ? `<div class="gap-tag">The gap opens here</div>` : ""}
      <div class="stage-h"><span class="stage-n">${esc(s.name)}</span><span class="pill ${s.status}">${STATUS[s.status]}</span></div>
      <div class="stage-q">${esc(s.question)}</div>
      ${s.note ? `<div class="note">${esc(s.note)} ${chip("STRATEGY")}</div>` : s.indicators.map(indHtml).join("")}
      ${s.indicators.some((i) => i.proxy) ? `<div class="note muted">All patients, not only cancer.</div>` : ""}
    </div>`).join("");
  const w = c.warnings;
  $("#warnings").hidden = !w.length;
  $("#warnings").innerHTML = w.map((x) => `Early warning: ${esc(x.label.toLowerCase())} has moved ${Math.abs(x.delta)} points the wrong way since ${x.since}. ${chip(x.source)}`).join("<br>");
  $("#props").innerHTML = c.proposals.map((p, n) => {
    const k = p.kpi, src = S.sources.sources;
    return `<details class="prop ${n > 2 ? "later" : ""}" ${n === 0 ? "open" : ""}>
      <summary><span class="prop-n">${p.priority}</span>
        <div><div class="prop-t">${esc(p.title)} <span class="st" style="background:${COLORS[p.stage] || "#475569"}">${esc(STAGE[p.stage])}</span></div>
          <div class="prop-w">${esc(p.why)}</div>
          <div class="prop-m"><span><b>Owner</b> ${esc(p.owner)}</span><span><b>First 90 days</b> ${esc(p.first_90_days)}</span></div></div>
        <div class="kpi">KPI<b>${k.now != null ? `${num(k.now, k.unit)} → ` : "→ "}${num(k.target, k.unit)}${k.unit !== "%" ? ` ${esc(k.unit)}` : ""}</b>${p.size.text ? esc(p.size.text.split(" a year")[0]) + " a year" : ""}</div>
      </summary>
      <div class="prop-b"><div><h4>Steps</h4><ol>${p.steps.map((s) => `<li>${esc(s)}</li>`).join("")}</ol>
          <h4 style="margin-top:10px">KPI</h4><p>${esc(k.indicator)}. ${esc(k.review)} ${chip(k.source)}</p></div>
        <div><h4>Evidence</h4>${p.evidence.map((e) => `<div class="ev">${esc(src[e].used_for)} ${chip(e)}</div>`).join("")}
          <h4>National strategy</h4><p>Measure ${esc(p.strategy.measure)}, ${esc(p.strategy.page)}: ${esc(p.strategy.text)} ${chip(p.strategy.source)}</p>
          ${p.assumption ? `<div class="assume"><b>Assumption.</b> ${esc(p.assumption)}</div>` : ""}</div></div></details>`;
  }).join("");
  const saved = JSON.parse(localStorage.getItem(`pathway.brief.${c.code}`) || "null");
  S.brief = saved?.text || ""; S.agents = {}; S.order = [];
  renderBrief(saved?.check); renderAgents();
}
async function select(code) {
  if (S.busy) return toast("The analysts are still writing. One moment.");
  S.code = code; history.replaceState(null, "", `#${code}`);
  $("#countySel").value = code;
  document.querySelectorAll("#map path").forEach((p) => p.classList.toggle("sel", p.dataset.code === code));
  S.county = await get(`/api/pathway/county/${code}`);
  renderCounty();
}

// ---------------------------------------------------------------- brief (agent team)
function md(src) {
  let html = "", list = null;
  const inline = (s) => s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>").replace(/\[((?:SOTKA-\d+|[A-Z]+\d{0,4})(?:,\s*(?:SOTKA-\d+|[A-Z]+\d{0,4}))*)\]/g, (_, ids) => ids.split(/,\s*/).map((id) => (S.sources.sources[id] ? chip(id) : `[${id}]`)).join(""));
  const close = () => { if (list) { html += `</${list}>`; list = null; } };
  for (const raw of esc((src || "").replace(/\u2011/g, "-")).split("\n")) {
    const line = raw.trimEnd(); let m;
    if ((m = line.match(/^\s*#{1,4}\s+(.*)$/))) { close(); html += `<h4>${inline(m[1])}</h4>`; }
    else if ((m = line.match(/^\s*[-*•]\s+(.*)$/))) { if (list !== "ul") { close(); html += "<ul>"; list = "ul"; } html += `<li>${inline(m[1])}</li>`; }
    else if ((m = line.match(/^\s*\d+[.)]\s+(.*)$/))) { if (list !== "ol") { close(); html += "<ol>"; list = "ol"; } html += `<li>${inline(m[1])}</li>`; }
    else if (!line.trim()) close();
    else { close(); html += `<p>${inline(line)}</p>`; }
  }
  close();
  return html;
}
function renderBrief(check) {
  $("#brief").innerHTML = S.brief ? md(S.brief) : `<p class="muted">A two-minute brief for the county's director of health services.</p>`;
  const el = $("#check");
  el.hidden = !check;
  if (check) {
    const ok = !check.unmatched.length;
    el.className = `check ${ok ? "" : "bad"}`;
    el.textContent = ok ? `✓ ${check.matched} of ${check.checked} figures match the source data` : `${check.matched} of ${check.checked} figures match. Not in the data: ${check.unmatched.join(", ")}`;
  }
  $("#briefBtn").textContent = S.busy ? "Writing…" : S.brief ? "Write again" : "Write the brief";
  $("#briefBtn").disabled = S.busy;
}
function renderAgents() {
  $("#agents").innerHTML = S.order.map((id) => { const a = S.agents[id]; return `<span class="ag ${a.status}"><i></i>${esc(a.title)}${a.where ? ` <small>${esc(a.where)}</small>` : ""}</span>`; }).join("");
}
function onEvent(ev) {
  const a = S.agents[ev.task_id];
  const where = (t) => (t === "edge" ? "this device" : t || "");
  switch (ev.type) {
    case "hybridedge.plan":
      for (const t of ev.plan.tasks) { S.agents[t.id] = { title: t.title, status: "queued" }; S.order.push(t.id); }
      for (const t of ev.plan.deferred || []) { S.agents[t.id] = { title: t.title, status: "deferred", where: "waits for cloud" }; S.order.push(t.id); }
      break;
    case "hybridedge.step_start":
      if (ev.task_id === "synth" && !S.agents.synth) { S.agents.synth = { title: "Lead analyst", status: "running" }; S.order.push("synth"); }
      if (S.agents[ev.task_id]) Object.assign(S.agents[ev.task_id], { status: "running", where: where(ev.target) });
      break;
    case "hybridedge.failover": if (a) a.where = where(ev.to); break;
    case "hybridedge.deferred": if (a) Object.assign(a, { status: "deferred", where: "waits for cloud" }); break;
    case "hybridedge.revise": for (const t of ev.tasks) if (S.agents[t.id]) S.agents[t.id].status = "queued"; break;
    case "hybridedge.agent_done": if (a && !(ev.result && ev.result.deferred)) a.status = "done"; break;
    case "response.output_text.delta": S.brief += ev.delta; renderBrief(); break;
    case "response.completed": S.brief = ev.response.output_text || S.brief; if (S.agents.synth) S.agents.synth.status = "done"; break;
    case "response.failed": toast((ev.response.error && ev.response.error.message) || "The brief failed"); break;
  }
  renderAgents();
}
async function writeBrief() {
  if (S.busy) return;
  const c = S.county;
  S.busy = true; S.brief = ""; S.agents = {}; S.order = []; renderBrief(); renderAgents();
  let check = null;
  try {
    const json = { "content-type": "application/json" };
    const sid = (await (await fetch(`${API}/sessions`, { method: "POST", headers: json, body: JSON.stringify({ title: `Cancer pathway brief: ${c.name}`, agent: "pathway", source: "equal-care" }) })).json()).id;
    await fetch(`${API}/sessions/${sid}/context`, { method: "PUT", headers: json, body: JSON.stringify({ county: c.code, county_name: c.name }) });
    const r = await fetch(`${API}/responses`, { method: "POST", headers: json, body: JSON.stringify({ session_id: sid, stream: true, source: "equal-care", input: `Write the county brief on the cancer care pathway for ${c.name} (county code ${c.code}).\nKey finding computed from the data: ${c.headline.text}\nPriority order of the proposals: ${c.proposals.slice(0, 3).map((p, n) => `${n + 1}. ${p.title}`).join("; ")}.\nKeep this finding and this order.` }) });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    const reader = r.body.getReader(), dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const data = buf.slice(0, i).split("\n").find((l) => l.startsWith("data: "));
        buf = buf.slice(i + 2);
        if (data) onEvent(JSON.parse(data.slice(6)));
      }
    }
    if (S.brief) {
      check = await (await fetch("/api/pathway/verify", { method: "POST", headers: json, body: JSON.stringify({ county: c.code, text: S.brief }) })).json();
      localStorage.setItem(`pathway.brief.${c.code}`, JSON.stringify({ text: S.brief, check }));
    }
  } catch (e) { toast(`The brief failed: ${e.message}`); }
  S.busy = false; renderBrief(check);
}

// ---------------------------------------------------------------- sources drawer
function renderDrawer() {
  const s = S.sources;
  $("#drawerBody").innerHTML = `<h3>What the data cannot show</h3><ul>${s.limitations.map((l) => `<li>${esc(l)}</li>`).join("")}</ul>
    <h3>Method</h3><ul>${s.method.map((l) => `<li>${esc(l)}</li>`).join("")}</ul>
    <h3>Sources</h3>${Object.entries(s.sources).map(([id, x]) => `<div class="source" id="src-${id}"><div class="id">${id}</div><div class="t">${esc(x.title)}</div><div class="p">${esc(x.publisher)}</div><div class="u">Used for: ${esc(x.used_for)}</div><a href="${esc(x.url)}" target="_blank" rel="noopener">Open source</a></div>`).join("")}
    <p class="muted">Data snapshot built ${esc(s.built)} with scripts/build_pathway_data.py. Public aggregate data only: no patient-level data.</p>`;
}
function openDrawer(id) {
  $("#drawer").hidden = $("#scrim").hidden = false;
  document.querySelectorAll(".source.hit").forEach((e) => e.classList.remove("hit"));
  const el = id && document.getElementById(`src-${id}`);
  if (el) { el.classList.add("hit"); el.scrollIntoView({ block: "center" }); }
}
const closeDrawer = () => ($("#drawer").hidden = $("#scrim").hidden = true);

async function pollLink() {
  try {
    const s = await get(`${API}/status`);
    $("#link").className = `link ${s.connected ? "" : "device"}`;
    $("#linkText").textContent = s.connected ? `Analysts on ${s.label}` : "Analysts on this device";
  } catch { $("#link").className = "link down"; $("#linkText").textContent = "Analysts offline"; }
  setTimeout(pollLink, 3000);
}

async function init() {
  [S.overview, S.geo, S.sources] = await Promise.all([get("/api/pathway/overview"), get("/api/pathway/geo"), get("/api/pathway/sources")]);
  STAGE = Object.fromEntries(S.overview.stages.map((s) => [s.id, s.name]));
  $("#built").textContent = `Data as of ${S.overview.built}`;
  $("#countySel").innerHTML = S.overview.counties.map((c) => `<option value="${c.code}">${esc(c.name)}</option>`).join("");
  $("#facts").innerHTML = S.overview.findings.map((f) => `<li>${esc(f.text)}${chip(f.source)}</li>`).join("");
  drawMap(); renderDrawer(); pollLink();
  $("#countySel").addEventListener("change", (e) => select(e.target.value));
  $("#briefBtn").addEventListener("click", writeBrief);
  $("#printBtn").addEventListener("click", () => { document.querySelectorAll(".prop").forEach((p) => (p.open = true)); print(); });
  $("#sourcesBtn").addEventListener("click", () => openDrawer());
  $("#drawerClose").addEventListener("click", closeDrawer);
  $("#scrim").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => e.key === "Escape" && closeDrawer());
  document.addEventListener("click", (e) => { const s = e.target.closest(".src"); if (s) { e.preventDefault(); openDrawer(s.dataset.src); } });
  const hash = location.hash.slice(1);
  select(S.overview.counties.some((c) => c.code === hash) ? hash : "14");
}
init();
