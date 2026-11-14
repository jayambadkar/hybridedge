// Aimo Trade: a wholesaler's buyer console with planning and purchasing agents (mock data, demo keys).
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const eur = (v, d = 0) => (v == null ? "–" : `€${Number(v).toLocaleString("en", { minimumFractionDigits: d, maximumFractionDigits: d })}`);
const TRADE = "/trade/v1", APPROVER = "demo-approver-key";
const json = { "content-type": "application/json" };
const STORES = [["eastgate", "Eastgate Provisions"], ["lagos-coop", "Lekki Kitchens Co-op"], ["hotel-aurora", "Hotel Aurora"]];
const DEVICE_MODEL = "Nemotron 3.5 Bolt E4B";
const S = { offSince: null, lastSync: null, drops: [], page: "home", buyer: localStorage.getItem("trade.buyer") || "eastgate", store: null, link: null, runs: [], steps: [], products: null, q: "" };

const toast = (m) => { const t = $("#toast"); t.textContent = m; t.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(() => (t.hidden = true), 3200); };
const tierOf = (target) => (target === "edge" ? "device" : "cloud");
const placeName = (target) => (target === "edge" ? "This device" : target ? target[0].toUpperCase() + target.slice(1) : "–");

// ---------------------------------------------------------------- data
async function loadStore() {
  S.store = await (await fetch(`/api/trade/store?buyer=${S.buyer}`)).json();
  const low = S.store.stock.items.filter((i) => i.status !== "ok").length, pend = S.store.orders.filter((o) => o.status === "pending_approval").length;
  $("#navLow").hidden = !low; $("#navLow").textContent = low;
  $("#navApprovals").hidden = !pend; $("#navApprovals").textContent = pend;
  renderSignal();
  render();
}
async function pollLink() {
  try {
    const s = await (await fetch("/v1/status")).json();
    const was = S.link && S.link.connected;
    S.link = s;
    $("#netInfo").innerHTML = s.connected
      ? `Agents run on <b>${esc(s.model)}</b> in ${esc(s.label)}. Confidential steps (budgets, approvals) stay on this device.`
      : `No link to the cloud. Agents run on <b>${DEVICE_MODEL}</b> on this laptop. Orders wait in the outbox and send themselves when the link returns.`;
    $("#drill").textContent = s.drill ? "Restore the link" : "Simulate losing the link";
    if (!s.connected && !S.offSince) S.offSince = Date.now();
    if (s.connected) { if (S.offSince) S.drops.push(S.offSince); S.offSince = null; S.lastSync = Date.now(); }
    renderSignal();
    if (was !== undefined && was !== s.connected) { toast(s.connected ? "Link restored. Outbox syncing." : "Link lost. Agents continue on this device."); loadStore(); }
    if (S.page === "inference") renderInference();
  } catch { $("#signal").className = "statusbar sb-down"; $("#sigState").textContent = "Server unreachable"; $("#sigSub").textContent = ""; }
  setTimeout(pollLink, 2000);
}

// ---------------------------------------------------------------- the link, front and centre
// 48 half-hour slots for today. Earlier slots are the store's logged pattern (mock); the last slot is live.
const HISTORY = { eastgate: "111111001111111110000111111111110011111111100111", "lagos-coop": "111111111101111111111111011111111111110111111111", "hotel-aurora": "111111111111111111111111111111111111111111111111" };
function renderSignal() {
  const s = S.link || {}, out = (S.store?.outbox || []).filter((o) => o.status === "waiting_for_signal").length;
  const cls = !S.link ? "sb-wait" : s.connected ? "sb-online" : s.network ? "sb-edge" : "sb-nonet";
  document.body.classList.toggle("offline", !!S.link && !s.connected);
  $("#signal").className = `statusbar ${cls}`;
  $("#sigState").textContent = !S.link ? "Connecting…" : s.connected ? "Connected" : "Offline";
  $("#sigSub").textContent = !S.link ? "" : s.connected ? `· ${s.label}` : s.drill ? "· Clouds disconnected" : s.network ? "· No cloud reachable" : "· No network";
  $("#sigOut").hidden = !out; $("#sigOut").textContent = `${out} order${out === 1 ? "" : "s"} waiting to send`;
}

// ---------------------------------------------------------------- agent runs
const PRESETS = [
  ["plan", "Plan this week's operations", "Make this week's operating plan: what to restock, which routes to use, and any price changes."],
  ["plan", "Analyse margins vs local market", "Analyse our prices and margins against the local market and recommend changes."],
  ["plan", "Route risk and what it means for stock", "Assess the supply routes this week and what we should stock differently because of them."],
  ["order", "Restock long-life milk", "We need 1,200 litres of long-life lactose-free milk delivered as soon as possible."],
  ["order", "Restock milk powder", "Order 20 sacks of whole milk powder, delivery within 12 days."],
];
async function startRun(kind, text) {
  const run = { id: Date.now(), kind, text, title: kind === "plan" ? "Operations plan" : "Purchase", lanes: {}, order: [], stage: "plan", review: null, out: "", done: false, t0: Date.now(), events: 0 };
  S.runs.unshift(run); go("agents");
  try {
    const sid = (await (await fetch("/v1/sessions", { method: "POST", headers: json, body: JSON.stringify({ title: `${S.store.name}: ${text.slice(0, 60)}`, agent: kind === "plan" ? "trade_ops" : "trade", source: "aimo-trade" }) })).json()).id;
    await fetch(`/v1/sessions/${sid}/context`, { method: "PUT", headers: json, body: JSON.stringify({ buyer: S.buyer }) });
    const r = await fetch("/v1/responses", { method: "POST", headers: json, body: JSON.stringify({ session_id: sid, input: text, stream: true, source: "aimo-trade" }) });
    const reader = r.body.getReader(), dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const line = buf.slice(0, i).split("\n").find((l) => l.startsWith("data: "));
        buf = buf.slice(i + 2);
        if (line) onEvent(run, JSON.parse(line.slice(6)));
      }
    }
  } catch (e) { toast(`Agent task failed: ${e.message}`); }
  run.done = true; run.stage = "done"; run.ms = Date.now() - run.t0;
  for (const l of Object.values(run.lanes)) if (l.status === "running") l.status = "done";
  loadStore(); paint();
}
function lane(run, id, title) {
  if (!run.lanes[id]) { run.lanes[id] = { title: title || id, status: "queued", events: [], live: "", where: null }; run.order.push(id); }
  return run.lanes[id];
}
const TOOL_LABEL = { stock_position: "Reading stock and days of cover", sales_trend: "Reading sales trend", route_status: "Checking supply routes", margin_report: "Comparing prices with the local market", search_products: "Searching the wholesaler's catalogue", request_quote: "Requesting a fixed-price quote", get_policy: "Reading the buying policy", place_order: "Placing the order" };
function onEvent(run, ev) {
  const id = ev.task_id;
  switch (ev.type) {
    case "hybridedge.plan":
      for (const t of ev.plan.tasks) lane(run, t.id, t.title);
      for (const t of ev.plan.deferred || []) Object.assign(lane(run, t.id, t.title), { status: "waiting", where: null, events: [{ t: "Waits for the cloud: the device runs the core team" }] });
      run.stage = "work"; break;
    case "hybridedge.step_start": {
      const step = { run: run.id, runTitle: run.title, agent: id === "plan" ? "Planner" : id === "synth" ? "Lead" : id === "review" ? "Reviewer" : run.lanes[id]?.title || id, target: ev.target, model: ev.model, reason: ev.reason, t: Date.now(), step_id: ev.step_id, tools: [] };
      S.steps.unshift(step);
      if (id === "review") { run.stage = "review"; Object.assign(lane(run, "review", "Plan review"), { status: "running", where: ev.target }); }
      else if (id === "synth") run.stage = "synth";
      else if (run.lanes[id]) Object.assign(run.lanes[id], { status: "running", where: ev.target });
      break;
    }
    case "hybridedge.step_end": {
      const st = S.steps.find((s) => s.step_id === ev.step_id);
      if (st && ev.meta) Object.assign(st, { ms: ev.meta.ms, ttft: ev.meta.ttft_ms, target: ev.meta.target || st.target, model: ev.meta.model || st.model });
      break;
    }
    case "hybridedge.tool_call": {
      const st = S.steps.find((s) => s.step_id === ev.step_id) || S.steps.find((s) => s.run === run.id);
      if (st) st.tools.push(ev.name);
      if (run.lanes[id]) run.lanes[id].events.push({ t: TOOL_LABEL[ev.name] || ev.name, tool: ev.name });
      break;
    }
    case "hybridedge.tool_result": {
      const r = ev.result || {}, l = run.lanes[id];
      if (l && l.events.length) {
        const e = l.events[l.events.length - 1];
        e.r = r.status === "queued_offline" ? `Saved on this device (${r.outbox_id}), sends when the link returns`
          : r.status === "pending_approval" ? `Waiting for a human: ${(r.approval_reasons || []).join(", ")}`
          : r.status === "placed" ? `Order ${r.id} placed, ${eur(r.total_eur, 2)}`
          : r.total_eur != null ? `Quote ${r.id}: ${eur(r.total_eur, 2)}` : r.results ? `${r.results.length} products found` : r.items ? `${r.items.length} lines` : r.routes ? `${r.routes.filter((x) => x.status !== "closed").length} of ${r.routes.length} routes usable` : "";
      }
      break;
    }
    case "hybridedge.token": if (run.lanes[id] && id !== "synth") run.lanes[id].live = (run.lanes[id].live + ev.text).slice(-400); break;
    case "hybridedge.failover": {
      const l = run.lanes[id]; if (l) { l.where = ev.to; l.events.push({ t: ev.to === "edge" ? "Link lost: continued on this device from the last checkpoint" : `${placeName(ev.from)} failed: moved to ${placeName(ev.to)}`, warn: true }); }
      S.steps.unshift({ run: run.id, runTitle: run.title, agent: l?.title || id, event: "failover", target: ev.to, from: ev.from, reason: ev.to_reason || ev.reason, t: Date.now(), tools: [] });
      break;
    }
    case "hybridedge.upgrade": { const l = run.lanes[id]; if (l) { l.where = ev.to; l.events.push({ t: `Link back: moved up to ${placeName(ev.to)}` }); } break; }
    case "hybridedge.deferred": if (run.lanes[id]) Object.assign(run.lanes[id], { status: "waiting", events: [...run.lanes[id].events, { t: "Deferred: runs on the cloud when the link returns", warn: true }] }); break;
    case "hybridedge.revise":
      for (const t of ev.tasks) {
        const l = lane(run, t.id, `${t.title}${t.next ? "" : t.deferred ? "" : " · revision"}`);
        l.status = "queued"; l.events.push({ t: t.next ? "Next step: acting on the sourcing report" : t.deferred ? "Cloud is back: running now" : "Sent back by the reviewer to fix an issue", warn: !t.next && !t.deferred });
      }
      run.stage = "work"; break;
    case "hybridedge.review": run.review = ev.review; if (run.lanes.review) run.lanes.review.status = "done"; break;
    case "hybridedge.agent_done": if (run.lanes[id] && !ev.result?.deferred) run.lanes[id].status = "done"; break;
    case "response.output_text.delta": run.out += ev.delta; break;
    case "response.completed": run.out = ev.response.output_text || run.out; break;
    case "response.failed": toast("Agent task failed"); break;
  }
  paint();
}
let raf = 0;
const paint = () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; $("#navRun").hidden = !S.runs.some((r) => !r.done); if (S.page === "agents") renderAgents(); if (S.page === "inference") renderInference(); }); };

// ---------------------------------------------------------------- markdown (tables, lists, bold, ORDER lines)
function md(src, run) {
  let html = "", list = null, table = null;
  const inline = (s) => s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  const flush = () => { if (table) { const [h, ...rows] = table; html += `<table><thead><tr>${h.map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`).join("")}</tbody></table>`; table = null; } if (list) { html += `</${list}>`; list = null; } };
  for (const raw of esc(src || "").split("\n")) {
    const line = raw.trim(); let m;
    if (/^\|.*\|$/.test(line)) { const cells = line.slice(1, -1).split("|").map((c) => c.trim()); if (cells.every((c) => /^:?-{2,}:?$/.test(c))) continue; if (list) { html += `</${list}>`; list = null; } (table ||= []).push(cells); continue; }
    if (table) flush();
    if ((m = line.match(/^(?:[-*]\s*)?ORDER:\s*(.+)$/i))) { flush(); html += `<div class="order-line"><span>${inline(m[1])}</span>${run ? `<button class="btn dark sm" data-order="${m[1]}">Send to purchasing agent</button>` : ""}</div>`; }
    else if ((m = line.match(/^#{1,4}\s+(.*)$/))) { flush(); html += `<h4>${inline(m[1])}</h4>`; }
    else if ((m = line.match(/^[-*•]\s+(.*)$/))) { if (list !== "ul") { flush(); html += "<ul>"; list = "ul"; } html += `<li>${inline(m[1])}</li>`; }
    else if ((m = line.match(/^\d+[.)]\s+(.*)$/))) { if (list !== "ol") { flush(); html += "<ol>"; list = "ol"; } html += `<li>${inline(m[1])}</li>`; }
    else if (!line) flush();
    else { flush(); html += `<p>${inline(line)}</p>`; }
  }
  flush();
  return html;
}

// ---------------------------------------------------------------- pages
function go(p) { S.page = p; document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("on", a.dataset.p === p)); location.hash = p; render(); }
function render() {
  if (!S.store) return;
  ({ home: renderHome, orders: renderOrders, products: renderProducts, inventory: renderInventory, routes: renderRoutes, agents: renderAgents, inference: renderInference })[S.page]();
}

function chart(series) {
  const W = 800, H = 220, P = 28, vals = series.map((d) => d.revenue_eur), max = Math.max(...vals) * 1.12;
  const x = (i) => P + (i * (W - P * 2)) / (series.length - 1), y = (v) => H - 24 - (v / max) * (H - 44);
  const line = series.map((d, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(d.revenue_eur).toFixed(1)}`).join("");
  const ticks = [0, 0.5, 1].map((f) => max * f / 1.12);
  return `<div class="chart" id="chart"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    ${ticks.map((t) => `<line x1="${P}" x2="${W - P}" y1="${y(t)}" y2="${y(t)}" stroke="#ebebeb"/><text x="${W - P}" y="${y(t) - 4}" text-anchor="end" font-size="10" fill="#8a8a8a">${eur(t)}</text>`).join("")}
    <path d="${line}L${x(series.length - 1)},${H - 24}L${x(0)},${H - 24}Z" fill="rgba(118,185,0,.10)"/>
    <path d="${line}" fill="none" stroke="#5c9400" stroke-width="2" vector-effect="non-scaling-stroke"/>
    ${series.map((d, i) => (i % 7 === 0 ? `<text x="${x(i)}" y="${H - 6}" font-size="10" fill="#8a8a8a" text-anchor="middle">${d.date.slice(5)}</text>` : "")).join("")}
    <circle id="cdot" r="4" fill="#5c9400" stroke="#fff" stroke-width="2" visibility="hidden"/></svg><div class="ctip" id="ctip" hidden></div></div>`;
}
function bindChart(series) {
  const el = $("#chart"); if (!el) return;
  const svg = $("svg", el), tip = $("#ctip"), dot = $("#cdot"), W = 800, P = 28, max = Math.max(...series.map((d) => d.revenue_eur)) * 1.12;
  el.addEventListener("mousemove", (e) => {
    const r = svg.getBoundingClientRect(), fx = ((e.clientX - r.left) / r.width) * W;
    const i = Math.max(0, Math.min(series.length - 1, Math.round(((fx - P) / (W - P * 2)) * (series.length - 1)))), d = series[i];
    const cx = P + (i * (W - P * 2)) / (series.length - 1), cy = 220 - 24 - (d.revenue_eur / max) * (220 - 44);
    dot.setAttribute("cx", cx); dot.setAttribute("cy", cy); dot.setAttribute("visibility", "visible");
    tip.hidden = false; tip.style.left = `${(cx / W) * r.width}px`; tip.style.top = `${(cy / 220) * r.height}px`;
    tip.innerHTML = `<b>${d.date}</b> · ${eur(d.revenue_eur)} · ${d.orders} orders`;
  });
  el.addEventListener("mouseleave", () => { tip.hidden = true; dot.setAttribute("visibility", "hidden"); });
}

function renderHome() {
  const s = S.store, t = s.trend, low = s.stock.items.filter((i) => i.status !== "ok"), pend = s.orders.filter((o) => o.status === "pending_approval");
  const out = s.outbox.filter((o) => o.status === "waiting_for_signal"), closed = s.routes.routes.filter((r) => r.status !== "open");
  const todos = [
    ...pend.map((o) => ({ ic: "!", t: `Approve order ${o.id} · ${eur(o.total_eur, 2)}`, s: (o.approval_reasons || []).join(", "), btn: `<button class="btn sm" data-approve="${o.id}">Review</button>` })),
    ...(low.length ? [{ ic: "↓", t: `${low.length} products running low`, s: low.slice(0, 3).map((i) => `${i.name.split(" ").slice(0, 3).join(" ")} (${i.days_of_cover} days)`).join(" · "), btn: `<button class="btn sm" data-preset="0">Plan restock</button>` }] : []),
    ...out.map((o) => ({ ic: "⇡", t: `Order ${o.id} saved on this device`, s: "Sends automatically when the link returns", btn: "" })),
    ...closed.map((r) => ({ ic: "⨯", t: `${r.name}: ${r.status}`, s: r.note, btn: `<button class="btn sm" data-go="routes">View</button>` })),
  ];
  $("#page").innerHTML = `
    <div class="head"><h1>Good ${new Date().getHours() < 12 ? "morning" : "afternoon"}</h1><div class="r"><button class="btn dark" data-preset="0">Plan this week with agents</button></div></div>
    <div class="grid g4">
      <div class="card kpi"><div class="l">Revenue, last 7 days</div><div class="v num">${eur(t.revenue_last_7_days_eur)}</div><div class="d ${t.change_pct >= 0 ? "up" : "down"}">${t.change_pct >= 0 ? "↑" : "↓"} ${Math.abs(t.change_pct)} % vs previous week</div></div>
      <div class="card kpi"><div class="l">Orders, last 7 days</div><div class="v num">${t.orders_last_7_days}</div><div class="d muted">${esc(s.customers)}</div></div>
      <div class="card kpi"><div class="l">Products running low</div><div class="v num">${low.length}</div><div class="d ${low.length ? "down" : "muted"}">${low.length ? `Shortest cover ${low[0].days_of_cover} days` : "All stocked"}</div></div>
      <div class="card kpi"><div class="l">Supply routes open</div><div class="v num">${s.routes.routes.filter((r) => r.status === "open").length} / ${s.routes.routes.length}</div><div class="d ${closed.length ? "down" : "muted"}">${closed.length ? `${closed.length} disrupted or closed` : "All open"}</div></div>
    </div>
    <div class="grid g21">
      <div class="card"><div class="card-h"><h2>Revenue</h2><span class="muted">Last 28 days</span></div>${chart(s.sales)}</div>
      <div class="card"><div class="card-h"><h2>Things to do</h2></div>${todos.length ? todos.map((x) => `<div class="todo"><div class="ic">${x.ic}</div><div><div class="t">${esc(x.t)}</div><div class="s">${esc(x.s)}</div></div>${x.btn}</div>`).join("") : `<div class="empty">Nothing needs you.</div>`}</div>
    </div>
    <div class="card"><div class="card-h"><h2>Ask the agents</h2><span class="muted">${esc(t.demand_note)}</span></div>
      <div class="presets">${PRESETS.map((p, i) => `<button class="preset" data-preset="${i}">${esc(p[1])}</button>`).join("")}</div></div>`;
  bindChart(s.sales);
}

const STATUS = { placed: ["Placed", "ok"], pending_approval: ["Needs approval", "warn"], rejected: ["Rejected", "bad"], approved: ["Approved", "ok"], waiting_for_signal: ["Waiting for signal", "warn"], sent: ["Sent", "ok"] };
const badge = (st) => { const [l, c] = STATUS[st] || [st, ""]; return `<span class="badge ${c}">${esc(l)}</span>`; };
function renderOrders() {
  const s = S.store;
  const rows = [...s.outbox.filter((o) => o.status === "waiting_for_signal").map((o) => ({ id: o.id, created: o.created, status: o.status, lines: o.lines.map((l) => `${l.sku} × ${l.qty}`).join(", "), total: "Priced on send", by: o.actor, offline: true })),
    ...s.orders.map((o) => ({ id: o.id, created: o.created, status: o.status, lines: o.lines.map((l) => `${l.name || l.sku} × ${l.qty_available ?? l.qty}`).join(", "), total: eur(o.total_eur, 2), by: o.placed_by, reasons: o.approval_reasons, deliver: o.deliver_by }))];
  $("#page").innerHTML = `<div class="head"><h1>Orders</h1><div class="r"><button class="btn dark" data-preset="3">New order with an agent</button></div></div>
    <div class="card flush"><table><thead><tr><th>Order</th><th>Date</th><th>Placed by</th><th>Status</th><th>Items</th><th class="r">Total</th><th></th></tr></thead><tbody>
    ${rows.length ? rows.map((o) => `<tr><td><b>${esc(o.id)}</b>${o.offline ? ` <span class="badge device">Outbox</span>` : ""}</td><td class="muted">${new Date(o.created * 1000).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}</td>
      <td>${esc((o.by || "").replace("agent:", "Agent · ").replace("human:", ""))}</td><td>${badge(o.status)}${o.reasons && o.reasons.length && o.status === "pending_approval" ? `<div class="muted" style="font-size:12px;margin-top:3px">${esc(o.reasons.join(", "))}</div>` : ""}</td>
      <td style="max-width:320px">${esc(o.lines)}</td><td class="r num">${esc(o.total)}</td>
      <td class="r">${o.status === "pending_approval" ? `<button class="btn sm" data-decide="${o.id}" data-ok="1">Approve</button> <button class="btn sm" data-decide="${o.id}" data-ok="">Reject</button>` : ""}</td></tr>`).join("") : `<tr><td colspan="7" class="empty">No orders yet. Ask an agent to restock.</td></tr>`}
    </tbody></table></div>
    <div class="card"><div class="card-h"><h2>Buying policy</h2><span class="muted">Enforced by the wholesaler, not by the agent</span></div>
      <div class="grid g3"><div><div class="muted">Agent may spend per day</div><b class="num">${eur(s.policy.auto_approve_limit_eur)}</b> <span class="muted">(${eur(s.policy.spent_today_eur, 2)} used today)</span></div>
      <div><div class="muted">Needs a human for</div>${esc(s.policy.human_approval_for.join(", "))}</div><div><div class="muted">Approvers</div>${esc(s.policy.approvers.join(", "))}</div></div></div>`;
}
async function renderProducts() {
  $("#page").innerHTML = `<div class="head"><h1>Products</h1><span class="muted">Wholesaler catalogue · prices excl. VAT</span></div><div class="card flush" id="plist"><div class="empty">Loading…</div></div>`;
  const r = await (await fetch(`${TRADE}/catalog/search?q=${encodeURIComponent(S.q)}`, { headers: { "x-agent-key": S.store.agent_key } })).json();
  const items = r.results || r;
  $("#plist").innerHTML = `<table><thead><tr><th>Product</th><th>SKU</th><th>Category</th><th>Labels</th><th class="r">Price</th><th class="r">Per litre</th><th>Earliest delivery</th></tr></thead><tbody>
    ${items.map((p) => `<tr><td><b>${esc(p.name)}</b></td><td class="muted num">${esc(p.sku)}</td><td>${esc(p.category)}</td><td>${(p.labels || []).map((l) => `<span class="badge">${esc(l)}</span>`).join(" ")}</td><td class="r num">${eur(p.price_eur, 2)}</td><td class="r num muted">${p.price_per_litre_eur ? eur(p.price_per_litre_eur, 2) : ""}</td><td class="num">${esc(p.delivery_date || "")}</td></tr>`).join("")}</tbody></table>`;
}
function renderInventory() {
  const st = S.store.stock, maxc = Math.max(...st.items.map((i) => i.days_of_cover || 0), 1);
  $("#page").innerHTML = `<div class="head"><h1>Inventory</h1><div class="r"><button class="btn dark" data-preset="0">Plan restock with agents</button></div></div>
    <div class="card flush"><table><thead><tr><th>Product</th><th class="r">On hand</th><th class="r">Sold per day</th><th style="width:28%">Days of cover</th><th>Status</th><th class="r">Suggested order</th></tr></thead><tbody>
    ${st.items.map((i) => `<tr><td><b>${esc(i.name)}</b>${i.cold_chain ? ` <span class="badge warn">Cold chain</span>` : ""}</td><td class="r num">${i.on_hand} ${esc(i.unit)}</td><td class="r num">${i.sold_per_day}</td>
      <td><div class="cover"><i class="${i.status === "out soon" ? "lo" : i.status === "reorder" ? "mid" : ""}" style="width:${Math.max(2, (i.days_of_cover / maxc) * 100)}%"></i><span class="num">${i.days_of_cover} d</span></div></td>
      <td>${i.status === "ok" ? `<span class="badge ok">OK</span>` : i.status === "reorder" ? `<span class="badge warn">Reorder</span>` : `<span class="badge bad">Out soon</span>`}</td><td class="r num">${i.suggested_units ? `${i.suggested_units} ${esc(i.unit)}` : ""}</td></tr>`).join("")}
    </tbody></table></div><p class="muted">${esc(st.rule)}</p>`;
}
function renderRoutes() {
  const r = S.store.routes;
  $("#page").innerHTML = `<div class="head"><h1>Supply routes</h1><span class="muted">Updated by the depot team over the radio net and the satellite link</span><div class="r"><button class="btn dark" data-preset="2">Assess route risk with agents</button></div></div>
    <div class="grid g3">${r.routes.map((x) => `<div class="card"><div class="card-h"><h2>${esc(x.name)}</h2></div>
      <div style="display:flex;gap:6px;margin-bottom:10px">${x.status === "open" ? `<span class="badge ok">Open</span>` : x.status === "closed" ? `<span class="badge bad">Closed</span>` : `<span class="badge warn">Disrupted</span>`}<span class="badge">${x.transit_days} days</span><span class="badge ${x.risk === "low" ? "" : x.risk === "high" ? "bad" : "warn"}">${esc(x.risk)} risk</span>${r.recommended === x.id ? `<span class="badge cloud">Recommended</span>` : ""}</div>
      <div>${esc(x.note)}</div><div class="muted" style="margin-top:8px;font-size:12px">Updated ${esc(x.updated)}</div></div>`).join("")}</div>
    <div class="card"><b>Cold chain:</b> ${r.cold_chain_possible ? "possible on at least one open route." : "no open route can keep a cold chain this week. Buy long-life products."}</div>`;
}

function renderAgents() {
  const runs = S.runs;
  const stage = (run) => { const order = ["plan", "work", "review", "synth", "done"], names = ["Plan", "Specialists", "Review", "Write-up", "Done"], at = order.indexOf(run.stage); return `<div class="stages">${names.map((n, i) => `${i ? "<em>→</em>" : ""}<span class="${i < at ? "done" : i === at ? (run.done ? "done" : "on") : ""}">${n}</span>`).join("")}</div>`; };
  const where = (t) => (t ? `<span class="badge ${tierOf(t)}">${esc(placeName(t))}</span>` : "");
  $("#page").innerHTML = `<div class="head"><h1>Agent tasks</h1><span class="muted">Planning agents analyse and plan. Purchasing agents quote and order through the wholesaler's interface.</span></div>
    <div class="card"><form class="compose" id="compose"><textarea id="task" placeholder="Ask for a plan, an analysis or an order, e.g. “Plan this week's restock around the closed highway”"></textarea>
      <button class="btn dark" data-kind="plan">Plan</button><button class="btn" data-kind="order">Order</button></form>
      <div class="presets">${PRESETS.map((p, i) => `<button class="preset" data-preset="${i}">${p[0] === "order" ? "Order · " : ""}${esc(p[1])}</button>`).join("")}</div></div>
    <div class="runs">${runs.length ? runs.map((run) => `<div class="card">
      <div class="run-h"><span class="t">${esc(run.title)}</span><span class="muted">“${esc(run.text)}”</span><span class="r" style="margin-left:auto">${run.done ? `<span class="badge ok">Done in ${(run.ms / 1000).toFixed(0)} s</span>` : `<span class="badge cloud">Working…</span>`}</span></div>
      ${stage(run)}
      <div class="lanes">${run.order.map((id) => { const l = run.lanes[id]; return `<div class="lane ${l.status === "running" ? "running" : ""}"><div class="lane-h"><span class="dot ${l.status === "running" ? "run" : l.status === "done" ? "done" : l.status === "waiting" ? "wait" : ""}"></span><b>${esc(l.title)}</b>${where(l.where)}</div>
        ${l.events.map((e) => `<div class="ev"><span>${e.warn ? "⚠" : "›"}</span><div>${esc(e.t)}${e.r ? `<div style="color:#303030">${esc(e.r)}</div>` : ""}</div></div>`).join("")}
        ${l.status === "running" && l.live ? `<div class="live">${esc(l.live)}</div>` : ""}</div>`; }).join("")}</div>
      ${run.review ? `<div class="review"><b>Reviewer:</b> ${esc(run.review.verdict || "")}${(run.review.issues || []).length ? `<ul>${run.review.issues.map((i) => `<li><span class="badge ${i.severity === "high" ? "bad" : "warn"}">${esc(i.severity)}</span> ${esc(i.issue)}</li>`).join("")}</ul>` : ""}</div>` : ""}
      ${run.out ? `<div class="out md">${md(run.out, run)}</div>` : ""}</div>`).join("") : `<div class="card empty">No agent tasks yet. Pick one above.</div>`}</div>`;
  $("#compose").onsubmit = (e) => { e.preventDefault(); const v = $("#task").value.trim(); if (v) startRun(e.submitter?.dataset.kind || "plan", v); };
}

function renderInference() {
  const s = S.link || {}, steps = S.steps.filter((x) => !x.event), cloud = steps.filter((x) => x.target !== "edge").length, dev = steps.length - cloud, fails = S.steps.filter((x) => x.event === "failover").length;
  const avg = (arr) => (arr.length ? Math.round(arr.reduce((a, b) => a + b, 0) / arr.length) : null);
  const ms = (t) => avg(steps.filter((x) => (t === "edge" ? x.target === "edge" : x.target !== "edge") && x.ttft).map((x) => x.ttft));
  const live = s.connected ? s.running_on : "edge";
  $("#page").innerHTML = `<div class="head"><h1>Inference</h1><span class="muted">Where each agent step ran, and why</span></div>
    <div class="card"><div class="where">
      <div class="tier ${live === "verda" ? "live" : ""}"><div class="h"><i style="background:var(--cloud)"></i>Verda · H100</div><div class="m">Nemotron 3.5 Lightning 30B-A3B · vLLM · primary cloud</div><div class="n">${s.connected && s.running_on === "verda" ? `${s.latency_ms} ms` : "–"}</div><div class="muted">Planning, analysis, review and write-up when the link is up</div></div>
      <div class="tier ${live === "modal" ? "live" : ""}"><div class="h"><i style="background:#6366f1"></i>Modal · H100</div><div class="m">Nemotron 3.5 Lightning 30B-A3B · vLLM · standby</div><div class="n">${s.connected && s.running_on === "modal" ? `${s.latency_ms} ms` : "Standby"}</div><div class="muted">Takes over the moment Verda fails or is overloaded</div></div>
      <div class="tier ${live === "edge" ? "live" : ""}"><div class="h"><i style="background:var(--device)"></i>This device · M3</div><div class="m">${DEVICE_MODEL} · Ollama · always available</div><div class="n">${s.connected ? "Ready" : "Running"}</div><div class="muted">Budgets and approvals always; everything else when the link drops</div></div></div>
      <div class="split">${steps.length ? `<i style="width:${(cloud / steps.length) * 100}%;background:var(--cloud)"></i><i style="width:${(dev / steps.length) * 100}%;background:var(--device)"></i>` : ""}</div>
      <div class="muted">${steps.length} model steps this session · ${cloud} in the cloud · ${dev} on this device · ${fails} failover${fails === 1 ? "" : "s"} · first token ${ms("cloud") ?? "–"} ms cloud / ${ms("edge") ?? "–"} ms device</div></div>
    <div class="card flush"><table><thead><tr><th>Time</th><th>Task</th><th>Agent</th><th>Ran on</th><th>Model</th><th class="r">First token</th><th class="r">Step</th><th>Tools</th><th>Why there</th></tr></thead><tbody>
      ${S.steps.length ? S.steps.slice(0, 80).map((x) => x.event ? `<tr style="background:#fff8ef"><td class="muted num">${new Date(x.t).toLocaleTimeString("en-GB")}</td><td>${esc(x.runTitle)}</td><td>${esc(x.agent)}</td><td colspan="5"><b>Failover</b> ${esc(placeName(x.from))} → ${esc(placeName(x.target))}, resumed from the last checkpoint</td><td class="why">${esc(x.reason || "")}</td></tr>`
        : `<tr><td class="muted num">${new Date(x.t).toLocaleTimeString("en-GB")}</td><td>${esc(x.runTitle)}</td><td>${esc(x.agent)}</td><td><span class="badge ${tierOf(x.target)}">${esc(placeName(x.target))}</span></td><td class="muted">${esc(x.model || "")}</td><td class="r num">${x.ttft != null ? `${x.ttft} ms` : "–"}</td><td class="r num">${x.ms != null ? `${(x.ms / 1000).toFixed(1)} s` : "…"}</td><td>${x.tools.map((t) => `<code style="font-size:11px">${esc(t)}</code>`).join(" ")}</td><td class="why">${esc(x.reason || "")}</td></tr>`).join("")
        : `<tr><td colspan="9" class="empty">Run an agent task to see each step here.</td></tr>`}</tbody></table></div>`;
}

// ---------------------------------------------------------------- events
document.addEventListener("click", async (e) => {
  const t = e.target.closest("[data-p],[data-preset],[data-go],[data-order],[data-decide],[data-approve]");
  if (!t) { if (!e.target.closest("#signal")) $("#netPop").hidden = true; return; }
  if (t.dataset.p) go(t.dataset.p);
  else if (t.dataset.go) go(t.dataset.go);
  else if (t.dataset.approve) go("orders");
  else if (t.dataset.preset) { const p = PRESETS[+t.dataset.preset]; startRun(p[0], p[2]); }
  else if (t.dataset.order) { t.disabled = true; t.textContent = "Sent"; startRun("order", t.dataset.order); }
  else if (t.dataset.decide) {
    await fetch(`${TRADE}/approvals/${t.dataset.decide}`, { method: "POST", headers: { ...json, "x-approver-key": APPROVER }, body: JSON.stringify({ approve: !!t.dataset.ok, by: "Depot manager" }) });
    toast(t.dataset.ok ? "Approved. The wholesaler will deliver." : "Rejected."); loadStore();
  }
});
$("#signal").addEventListener("click", (e) => { if (!e.target.closest(".pop")) $("#netPop").hidden = !$("#netPop").hidden; });
$("#drill").addEventListener("click", async () => { await fetch("/api/link", { method: "POST", headers: json, body: JSON.stringify({ offline: !(S.link && S.link.drill) }) }); $("#netPop").hidden = true; });
$("#store").innerHTML = STORES.map(([k, n]) => `<option value="${k}">${n}</option>`).join("");
$("#store").value = S.buyer;
$("#store").addEventListener("change", (e) => { S.buyer = e.target.value; localStorage.setItem("trade.buyer", S.buyer); S.products = null; loadStore(); });
$("#q").addEventListener("keydown", (e) => { if (e.key === "Enter") { S.q = e.target.value.trim(); go("products"); } });
$("#reset").addEventListener("click", async () => { await fetch("/api/trade/reset", { method: "POST" }); S.runs = []; S.steps = []; toast("Demo reset"); loadStore(); });

const start = location.hash.slice(1);
if (["home", "orders", "products", "inventory", "routes", "agents", "inference"].includes(start)) S.page = start;
document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("on", a.dataset.p === S.page));
loadStore(); pollLink();
