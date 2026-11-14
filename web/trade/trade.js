"use strict";
/*
 * Trusted Trade Agents demo. Left: the buyer's purchasing agent (HybridEdge Agents API, "trade" team).
 * Right: the wholesaler's side of the mock trade interface (/trade/v1): policy, approval queue, orders, audit.
 * Demo keys only; the data is mock data.
 */
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const BUYERS = {
  "hotel-aurora": { key: "demo-aurora-agent-key", net: "Helsinki · fibre and 5G, steady link", ask: "I need 1,000 litres of lactose-free milk by Monday. Find an option that meets our requirements based on price and availability." },
  "lagos-coop": { key: "demo-lagos-agent-key", net: "Lagos · patchy 3G, frequent drops: the agent keeps working on the device", ask: "We need 600 litres of long-life lactose-free milk and 4 sacks of milk powder delivered to Lagos by 30 November. Cheapest option that needs no cold chain." },
};
let BUYER = "hotel-aurora";
const AGENT_KEY_OF = () => BUYERS[BUYER].key;
const APPROVER_KEY = "demo-approver-key";
const TRADE = "/trade/v1";
const S = { sessions: {}, sessionId: null, busy: false, steps: [], byStep: {}, link: null };

function toast(t) { const el = $("#toast"); el.textContent = t; el.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(() => (el.hidden = true), 3500); }
const hhmm = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const eur = (n) => `€${Number(n).toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

function md(src) {
  const lines = esc(src || "").split("\n");
  let html = "", list = null, table = null;
  const inline = (s) => s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>").replace(/`([^`]+)`/g, "$1");
  const closeTable = () => { if (!table) return; const [h, ...rows] = table.head ? table : [null, ...table]; html += `<table>${h ? `<tr>${h.map((c) => `<th>${inline(c)}</th>`).join("")}</tr>` : ""}${rows.map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`).join("")}</table>`; table = null; };
  const close = () => { closeTable(); if (list) { html += `</${list}>`; list = null; } };
  for (const raw of lines) {
    const line = raw.trimEnd(); let m;
    if (/^\s*\|.*\|\s*$/.test(line)) { const cells = line.trim().slice(1, -1).split("|").map((c) => c.trim()); if (cells.every((c) => /^:?-{2,}:?$/.test(c))) { if (table && table.length === 1) table.head = true; continue; } (table ||= []).push(cells); continue; }
    closeTable();
    if ((m = line.match(/^\s*#{1,4}\s+(.*)$/))) { close(); html += `<h4>${inline(m[1])}</h4>`; }
    else if ((m = line.match(/^\s*[-*•]\s+(.*)$/))) { if (list !== "ul") { close(); html += "<ul>"; list = "ul"; } html += `<li>${inline(m[1])}</li>`; }
    else if ((m = line.match(/^\s*\d+[.)]\s+(.*)$/))) { if (list !== "ol") { close(); html += "<ol>"; list = "ol"; } html += `<li>${inline(m[1])}</li>`; }
    else if (!line.trim()) close();
    else { close(); html += `<p>${inline(line)}</p>`; }
  }
  close();
  return html;
}

/* ------------------------------------------------------------ link status */
async function pollLink() {
  try {
    const s = await (await fetch("/v1/status")).json();
    S.link = s;
    $("#link").className = `link ${s.connected ? "" : "device"}`;
    $("#linkText").textContent = s.connected ? `Cloud · ${s.label}` : s.drill ? "Drill · Device only" : "Device only";
    $("#lpText").textContent = s.drill ? "Drill: clouds disconnected. Sourcing also runs on this device." : s.connected ? `Sourcing runs on ${s.label}. Procurement always runs on this device.` : "No cloud reachable. Everything runs on this device.";
    $("#drillBtn").textContent = s.drill ? "End drill" : "Simulate losing the network";
    $("#drillBtn").className = `lp-btn ${s.drill ? "end" : ""}`;
  } catch { $("#link").className = "link down"; $("#linkText").textContent = "Server offline"; }
  setTimeout(pollLink, 2000);
}
$("#link").addEventListener("click", (e) => { e.stopPropagation(); $("#linkPop").hidden = !$("#linkPop").hidden; });
document.addEventListener("click", (e) => { if (!e.target.closest(".link-wrap")) $("#linkPop").hidden = true; });
$("#drillBtn").addEventListener("click", async () => { await fetch("/api/link", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ offline: !(S.link && S.link.drill) }) }); $("#linkPop").hidden = true; });

/* ------------------------------------------------------------ wholesaler side */
async function refreshSeller() {
  try {
    const [pol, orders, audit] = await Promise.all([
      fetch(`${TRADE}/policy`, { headers: { "x-agent-key": AGENT_KEY_OF() } }).then((r) => r.json()),
      fetch(`${TRADE}/approvals`, { headers: { "x-approver-key": APPROVER_KEY } }).then((r) => r.json()),
      fetch(`${TRADE}/audit`, { headers: { "x-approver-key": APPROVER_KEY } }).then((r) => r.json()),
    ]);
    const pct = Math.min(100, (100 * pol.spent_today_eur) / pol.auto_approve_limit_eur);
    $("#policyCard").innerHTML = `<div class="card-h"><h3>Agent authority · ${esc(pol.name)}</h3></div>
      <div class="pol"><div><b>${eur(pol.auto_approve_limit_eur)}</b>Agent may spend per day</div><div><b>${eur(pol.spent_today_eur)}</b>Committed today</div><div><b>${pol.approvers.length}</b>Human approvers</div></div>
      <div class="meter"><i style="width:${pct}%"></i></div>`;
    const ob = await fetch(`/api/trade/outbox?buyer=${BUYER}`).then((r) => r.json());
    $("#outboxCard").hidden = !ob.length;
    $("#outbox").innerHTML = ob.slice(0, 5).map((i) => `<div class="ob"><b>${esc(i.id)}</b><span class="pill ${esc(i.status)}">${i.status === "sent" ? "Sent" : "Waiting for signal"}</span><span>${esc(i.lines.map((l) => `${l.qty} × ${l.sku}`).join(", "))}</span>${i.order_id ? `<span class="muted">→ ${esc(i.order_id)}</span>` : ""}</div>`).join("");
    const pending = orders.filter((o) => o.status === "pending_approval");
    $("#qCount").textContent = pending.length || "";
    $("#qCount").hidden = !pending.length;
    const ord = (o, actions) => `<div class="ord"><div class="ord-h"><b>${esc(o.id)}</b><span class="pill ${esc(o.status)}">${esc(o.status.replace("_", " "))}</span><span class="amt">${eur(o.total_eur)}</span></div>
      <ul class="lines">${o.lines.map((l) => `<li>${esc(l.qty_available)} × ${esc(l.name)} · ${eur(l.unit_price_eur)} · ${esc(l.delivery_date)}</li>`).join("")}</ul>
      ${o.approval_reasons && o.approval_reasons.length && o.status === "pending_approval" ? `<div class="reason">Needs a human: ${esc(o.approval_reasons.join("; "))}</div>` : ""}
      ${o.decided_by ? `<div class="muted" style="font-size:12px;margin-top:4px">${o.status === "approved" ? "Approved" : "Rejected"} by ${esc(o.decided_by)}</div>` : ""}
      ${actions ? `<div class="decide"><button class="yes" data-approve="${esc(o.id)}">Approve</button><button data-reject="${esc(o.id)}">Reject</button></div>` : ""}</div>`;
    $("#queue").innerHTML = pending.map((o) => ord(o, true)).join("") || `<div class="empty">Nothing waiting.</div>`;
    const rest = orders.filter((o) => o.status !== "pending_approval");
    $("#orders").innerHTML = rest.map((o) => ord(o, false)).join("") || `<div class="empty">No orders yet.</div>`;
    $("#audit").innerHTML = audit.slice(0, 14).map((e) => `<li><time>${hhmm(e.ts)}</time><span class="actor a-${esc(e.actor.split(":")[0])}">${esc(e.actor.replace("agent:", "Agent · ").replace("human:", "").replace("policy-engine", "Policy engine"))}</span><span>${esc(e.ref)} · ${esc(e.detail)}</span></li>`).join("") || `<li class="empty">No activity yet.</li>`;
  } catch (e) { /* server restarting */ }
}
document.addEventListener("click", async (e) => {
  const a = e.target.closest("[data-approve]"), r = e.target.closest("[data-reject]");
  if (!a && !r) return;
  const id = (a || r).dataset.approve || (a || r).dataset.reject;
  await fetch(`${TRADE}/approvals/${id}`, { method: "POST", headers: { "content-type": "application/json", "x-approver-key": APPROVER_KEY }, body: JSON.stringify({ approve: !!a, by: "Head chef" }) });
  toast(a ? `${id} approved` : `${id} rejected`);
  refreshSeller();
});
$("#reset").addEventListener("click", async () => { await fetch("/api/trade/reset", { method: "POST" }); $("#summary").hidden = true; S.steps = []; renderSteps(); refreshSeller(); });

/* ------------------------------------------------------------ buyer agent */
const TOOL = { search_products: "Search catalogue", request_quote: "Request quote", get_policy: "Read policy", place_order: "Place order" };
function stepFor(ev) {
  const role = ev.agent;
  let s = S.steps.find((x) => x.role === role);
  if (!s) { s = { role, title: role === "lead" ? "Order summary" : role[0].toUpperCase() + role.slice(1), target: ev.target, reason: ev.reason, tools: [], live: "", done: false }; S.steps.push(s); }
  return s;
}
function renderSteps() {
  if (!S.steps.length) { $("#steps").innerHTML = `<li class="placeholder">${S.busy ? "Starting…" : "Send a request to start."}</li>`; return; }
  $("#steps").innerHTML = S.steps.map((s) => {
    const dev = s.target === "edge";
    return `<li><div class="st-h">${s.done ? `<span class="ok">✓</span>` : `<span class="spin"></span>`}${esc(s.title)}<span class="chip ${dev ? "device" : "cloud"}">${dev ? "This device" : esc((S.link && s.target === S.link.running_on && S.link.label) || s.target)}</span></div>
      ${s.reason ? `<div class="st-why">${esc(s.reason)}</div>` : ""}
      ${s.tools.length ? `<div class="tools">${s.tools.map((t) => `<span class="tool"><b>${esc(TOOL[t.name] || t.name)}</b>${t.note ? ` · ${esc(t.note)}` : ""}</span>`).join("")}</div>` : ""}
      ${!s.done && s.live ? `<div class="st-live">${esc(s.live.slice(-260))}</div>` : ""}</li>`;
  }).join("");
}
function toolNote(name, r) {
  if (!r || r.error) return r && r.error;
  if (name === "search_products") return `${(r.results || []).length} products`;
  if (name === "request_quote" && r.provisional) return `${r.id} · provisional · ${eur(r.total_eur)}`;
  if (name === "request_quote") return `${r.id} · ${eur(r.total_eur)}`;
  if (name === "get_policy") return `limit ${eur(r.auto_approve_limit_eur)}`;
  if (name === "place_order") return r.status === "queued_offline" ? `${r.outbox_id} · saved on device` : `${r.id} · ${r.status === "placed" ? "placed" : "sent to a human"}`;
  return "";
}
async function send(text) {
  if (S.busy) return;
  S.busy = true; S.steps = []; $("#send").disabled = true; $("#summary").hidden = false; $("#sumBody").innerHTML = ""; $("#sumState").textContent = "Working…";
  renderSteps();
  try {
    if (!S.sessions[BUYER]) {
      const title = BUYER === "lagos-coop" ? "Lekki Kitchens Co-op purchasing" : "Hotel Aurora purchasing";
      S.sessions[BUYER] = (await (await fetch("/v1/sessions", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ title, agent: "trade", source: "trade" }) })).json()).id;
      await fetch(`/v1/sessions/${S.sessions[BUYER]}/context`, { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ buyer: BUYER }) });
    }
    S.sessionId = S.sessions[BUYER];
    const r = await fetch("/v1/responses", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ session_id: S.sessionId, input: text, stream: true, source: "trade" }) });
    const reader = r.body.getReader(); const dec = new TextDecoder(); let buf = "", brief = "";
    for (;;) {
      const { value, done } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
        const line = chunk.split("\n").find((l) => l.startsWith("data:")); if (!line) continue;
        const ev = JSON.parse(line.slice(5));
        switch (ev.type) {
          case "hybridedge.step_start": if (ev.agent && ev.agent !== "lead") { const s = stepFor(ev); s.target = ev.target; s.reason = ev.reason; } break;
          case "hybridedge.reasoning": case "hybridedge.token": if (ev.agent && ev.agent !== "lead") { const s = stepFor(ev); s.live += ev.text || ""; } break;
          case "hybridedge.tool_call": if (ev.agent) stepFor(ev).tools.push({ id: ev.id, name: ev.name }); break;
          case "hybridedge.tool_result": { for (const s of S.steps) { const t = s.tools.find((x) => x.id === ev.id); if (t) t.note = toolNote(ev.name, ev.result); } refreshSeller(); break; }
          case "hybridedge.agent_done": { const s = stepFor(ev); s.done = true; break; }
          case "response.output_text.delta": brief += ev.delta; $("#sumBody").innerHTML = md(brief); break;
          case "response.completed": brief = ev.response.output_text || brief; $("#sumBody").innerHTML = md(brief); $("#sumState").textContent = "Done"; break;
          case "response.failed": $("#sumState").textContent = "Failed"; toast((ev.response.error && ev.response.error.message) || "Failed"); break;
        }
        renderSteps();
      }
    }
  } catch (e) { toast(String(e)); }
  S.busy = false; $("#send").disabled = false; refreshSeller();
}
$("#ask").addEventListener("submit", (e) => { e.preventDefault(); send($("#input").value.trim()); });
$("#input").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#ask").requestSubmit(); } });

function setBuyer(b) {
  BUYER = b;
  document.querySelectorAll("[data-buyer]").forEach((x) => x.classList.toggle("on", x.dataset.buyer === b));
  $("#net").textContent = BUYERS[b].net;
  $("#input").value = BUYERS[b].ask;
  S.steps = []; $("#summary").hidden = true; renderSteps(); refreshSeller();
}
$("#buyers").addEventListener("click", (e) => { const b = e.target.closest("[data-buyer]"); if (b && !S.busy) setBuyer(b.dataset.buyer); });
setBuyer("hotel-aurora");
pollLink(); refreshSeller(); setInterval(refreshSeller, 4000); renderSteps();
