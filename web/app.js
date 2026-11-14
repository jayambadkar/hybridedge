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
  tab: "checkpoints",
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
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return new Date(ts * 1000).toLocaleDateString();
}
const clock = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

function md(src) {
  const lines = esc(src || "").split("\n");
  let html = "", list = null;
  const inline = (s) => s
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  const close = () => { if (list) { html += `</${list}>`; list = null; } };
  for (const raw of lines) {
    const line = raw.trimEnd();
    let m;
    if ((m = line.match(/^\s*#{1,4}\s+(.*)$/))) { close(); html += `<h3>${inline(m[1])}</h3>`; }
    else if ((m = line.match(/^\s*[-*•]\s+(.*)$/))) { if (list !== "ul") { close(); html += "<ul>"; list = "ul"; } html += `<li>${inline(m[1])}</li>`; }
    else if ((m = line.match(/^\s*\d+[.)]\s+(.*)$/))) { if (list !== "ol") { close(); html += "<ol>"; list = "ol"; } html += `<li>${inline(m[1])}</li>`; }
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
async function pollStatus() {
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
  setTimeout(pollStatus, 1500);
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

  const b = $("#banner");
  if (s && s.active === "edge") {
    b.hidden = false;
    b.innerHTML = s.edge.ok
      ? `<b>On-device mode.</b><span class="muted">${s.offline ? "Link disabled." : "No cloud reachable."} Smaller model, up to ${s.budget.max_agents} agents — workflows continue and stay checkpointed.</span>`
      : `<b>No model available.</b><span class="muted">Cloud unreachable and the local model (Ollama) is not running.</span>`;
  } else b.hidden = true;

  // sidebar compute
  if (s) {
    const rows = s.providers.map((p) => {
      const cls = p.usable ? "ok" : p.idle ? "idle" : p.configured ? "bad" : "idle";
      const r = s.offline ? "offline" : p.usable ? `${p.latency_ms ?? "–"} ms` : p.disabled ? "outage" : p.idle ? "standby" : p.configured ? "down" : "not set";
      return `<div class="row"><span class="sdot ${cls}"></span><b>${esc(p.label)}</b><span class="muted">${esc(p.hardware)}</span><span class="r">${r}</span></div>`;
    });
    rows.push(`<div class="row"><span class="sdot ${s.edge.ok ? "edge" : "bad"}"></span><b>This device</b><span class="muted">Bolt E4B</span><span class="r">${s.edge.ok ? "ready" : "down"}</span></div>`);
    $("#compute").innerHTML = `<div class="cap">Compute</div>${rows.join("")}`;
  }
  if (!$("#linkPop").hidden) renderPopover();
  $("#send").disabled = state.sending || !$("#input").value.trim();
}

function renderPopover() {
  const s = state.status;
  if (!s) return;
  const pop = $("#linkPop");
  const provs = s.providers.map((p, i) => {
    const d = !p.configured ? "Not configured" : p.disabled ? "Simulated outage" : p.usable ? `${esc(p.model)} · ${p.latency_ms} ms` : p.idle ? "Cold standby — wakes on failover" : `Unreachable · ${esc(p.last_error || "")}`;
    const dot = p.usable ? "ok" : p.idle ? "idle" : "bad";
    return `<div class="prov"><span class="order">${i + 1}</span><span class="sdot ${dot}"></span><div class="grow"><div class="n">${esc(p.label)} <span class="muted">· ${esc(p.hardware)}</span></div><div class="d">${d}</div></div>
      <button class="switch warn ${p.disabled ? "on" : ""}" data-outage="${esc(p.name)}" title="Simulate outage" aria-label="Simulate ${esc(p.label)} outage"></button></div>`;
  }).join("");
  pop.innerHTML = `<div class="pop-h">Failover order</div>${provs}
    <div class="prov"><span class="order">${s.providers.length + 1}</span><span class="sdot ${s.edge.ok ? "edge" : "bad"}"></span><div class="grow"><div class="n">This device</div><div class="d">${esc(s.edge.model)} · always available</div></div></div>
    <div class="pop-sep"></div>
    <div class="prov"><div class="grow"><div class="n">Disconnect all clouds</div><div class="d">Simulate losing the network</div></div><button class="switch warn ${s.offline ? "on" : ""}" data-offline aria-label="Disconnect all clouds"></button></div>
    <div class="pop-note">Toggles are for drills. Switching mid-answer fails over instantly from the last checkpoint.</div>`;
}

async function setLink(body) {
  state.status = await api("/api/link", { method: "POST", body: JSON.stringify(body) });
  const prev = state.prevActive;
  state.prevActive = state.status.active;
  if (prev && prev !== state.status.active) onLinkChange(prev, state.status.active);
  renderStatus();
}

/* ------------------------------------------------------------------ threads */
async function loadThreads() {
  state.threads = await api("/api/threads");
  renderThreads();
}
function renderThreads() {
  $("#threads").innerHTML = state.threads.map((t) =>
    `<button class="thread ${t.id === state.threadId ? "active" : ""}" data-thread="${t.id}"><span class="t">${esc(t.title)}</span><span class="s">${ago(t.updated_at)}</span></button>`
  ).join("") || `<div class="muted" style="padding:8px 10px;font-size:12.5px">No incidents yet.</div>`;
}

async function openThread(id, { attach = true } = {}) {
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
  const chip = targets.length ? targets.map((t) => tag(t)).join("") : a.target ? tag(a.target) : `<span class="tag plain">queued</span>`;
  const sub = a.status === "running" && a.preview ? `<div class="agent-preview">${esc(a.preview)}</div>` : `<div class="agent-task">${esc(a.task)}</div>`;
  const notes = (a.failovers || []).map(noteHTML).join("");
  const tools = (a.tools || []).length ? `<div class="tools">${a.tools.map((t) => `<span class="tool">${esc(t)}</span>`).join("")}</div>` : "";
  const body = expanded ? `<div class="agent-body">${notes}${tools}<div class="md">${a.output ? md(a.output) : `<span class="muted">${a.status === "done" ? "No output." : "Working…"}</span>`}</div></div>` : "";
  return `<div class="agent"><button class="agent-row" data-agent="${esc(a.id)}">${st}<div style="min-width:0"><div class="agent-name">${esc(a.title)}</div>${sub}</div><div class="agent-meta">${a.steps ? `<span class="muted" style="font-size:12px">${a.steps} step${a.steps > 1 ? "s" : ""}</span>` : ""}${chip}</div></button>${!expanded && notes ? `<div class="agent-body" style="padding-bottom:8px">${notes}</div>` : ""}${body}</div>`;
}

const expanded = new Set();
const noteHTML = (f) => f.generic
  ? `<div class="note">↪ Switched provider mid-task — resumed from checkpoint</div>`
  : `<div class="note">↪ ${esc(targetName(f.from))} dropped (${esc(f.reason)}) — resumed on ${esc(targetName(f.to))} from checkpoint</div>`;

function workflowHTML(plan, agents, running) {
  if (!plan) return "";
  const n = plan.tasks.length;
  const head = `<div class="wf-h"><b>${n === 1 ? "1 agent" : `${n} agents in parallel`}</b><span>· planned on ${esc(targetName(plan.planned_on))}</span><span class="r">${running ? "running" : "complete"}</span></div>`;
  return `<div class="wf">${head}${plan.tasks.map((t) => agentHTML(agents[t.id] || { id: t.id, title: t.title, task: t.task, status: "queued" }, expanded.has(t.id))).join("")}</div>`;
}

function answerHTML(text, meta, { streaming = false, isLast = false } = {}) {
  const degraded = meta?.target === "edge" || (meta?.workflow?.results || []).some((r) => r.targets.includes("edge"));
  const fail = meta?.failover?.length || (meta?.workflow?.results || []).some((r) => r.failover);
  const bits = [];
  if (meta?.target) bits.push(tag(meta.target));
  if (meta?.model) bits.push(`<span>${esc(meta.model)}</span>`);
  if (meta?.ms) bits.push(`<span>${(meta.ms / 1000).toFixed(1)}s</span>`);
  if (fail) bits.push(`<span style="color:var(--edge)">switched provider mid-run</span>`);
  if (isLast && degraded && state.status?.active && state.status.active !== "edge" && !state.sending)
    bits.push(`<button class="btn-sm primary" data-rerun>${ICON.redo}Re-run on ${esc(targetName(state.status.active))}</button>`);
  return `<div class="answer ${degraded ? "degraded" : ""}"><div class="md ${streaming ? "cursor" : ""}">${md(text)}</div>${bits.length && !streaming ? `<div class="answer-meta">${bits.join("")}</div>` : ""}</div>`;
}

function agentsFromResults(wf) {
  const out = {};
  for (const r of wf.results) out[r.task_id] = { id: r.task_id, title: r.title, task: r.task, status: "done", output: r.output, targets: r.targets, steps: r.steps, tools: r.tools, failovers: r.failover ? [{ generic: true }] : [] };
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
        const multi = wf && wf.plan.tasks.length > 1;
        parts.push(`<div class="turn">${multi ? workflowHTML(wf.plan, agentsFromResults(wf), false) : ""}${answerHTML(m.content, m.meta, { isLast: i === lastAssistant && !state.live })}</div>`);
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
  if (!L.plan) parts.push(`<div class="thinking"><span class="st"><span class="spin"></span></span>${L.planning ? `Planning on ${esc(targetName(L.planning))}…` : "Starting…"}</div>`);
  if (L.plan && L.plan.tasks.length > 1) parts.push(workflowHTML(L.plan, L.agents, !L.final));
  if (L.plan && L.plan.tasks.length === 1) {
    const a = Object.values(L.agents)[0];
    if (a && !L.final) {
      const notes = (a.failovers || []).map(noteHTML).join("");
      const tools = (a.tools || []).length ? `<div class="answer-meta" style="margin:0 0 8px">${a.tools.map((t) => `<span class="tool">${esc(t)}</span>`).join("")}</div>` : "";
      parts.push(`${notes}${tools}${a.output ? answerHTML(a.output, null, { streaming: true }) : `<div class="thinking"><span class="st"><span class="spin"></span></span>Working on ${esc(targetName(a.target))}…</div>`}`);
    }
  }
  if (L.synthNotes) parts.push(L.synthNotes);
  if (L.final) parts.push(answerHTML(L.final.text, L.final.done ? L.final.meta : null, { streaming: !L.final.done }));
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
    case "token":
      if (ev.task_id === "synth") { L.final ||= { text: "", done: false }; L.final.text += ev.text; }
      else if (L.agents[agentId]) { const a = L.agents[agentId]; a.output += ev.text; a.preview = a.output.replace(/\s+/g, " ").slice(-90); }
      break;
    case "failover": {
      if (ev.task_id === "synth") { L.final = null; L.synthNotes = `<div class="note">↪ ${esc(targetName(ev.from))} dropped mid-answer — continuing on ${esc(targetName(ev.to))} from checkpoint</div>`; }
      else if (ev.task_id !== "plan") agentFor().failovers.push({ from: ev.from, to: ev.to, reason: ev.reason });
      break;
    }
    case "step_end":
      if (ev.task_id !== "plan" && ev.task_id !== "synth") { const a = agentFor(); a.steps += 1; a.targets.push(ev.meta.target); }
      if (ev.task_id === "synth" && L.final) { L.final.done = true; L.final.meta = ev.meta; }
      break;
    case "tool_call": { const a = agentFor(); a.tools.push(ev.name); a.output = ""; break; }
    case "plan":
      L.plan = ev.plan;
      for (const t of ev.plan.tasks) L.agents[t.id] = { id: t.id, title: t.title, task: t.task, status: "queued", steps: 0, tools: [], failovers: [], targets: [], output: "", preview: "" };
      break;
    case "agent_done": { const a = agentFor(); a.status = "done"; a.output = ev.result.output; a.targets = ev.result.targets; break; }
    case "final": L.final = { text: ev.content, done: true, meta: ev.meta }; break;
    case "checkpoint": L.checkpoints += 1; bumpCheckpoints(ev); break;
    case "error": L.error = ev.message; break;
  }
  schedule();
}

function bumpCheckpoints(ev) {
  if (!state.thread) return;
  state.thread.checkpoints.unshift({ checkpoint_id: ev.checkpoint_id, ns: ev.ns, step: ev.step, source: ev.source, ts: new Date(ev.ts * 1000).toISOString(), fresh: true, agent: ev.agent });
  if (state.tab === "checkpoints" && !$("#inspector").hidden) renderInspector();
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
function renderInspector() {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === state.tab));
  const T = state.thread;
  const body = $("#tabBody");
  if (!T) { body.innerHTML = `<div class="muted">Open an incident.</div>`; return; }
  if (state.tab === "checkpoints") {
    const cps = T.checkpoints;
    const sub = cps.filter((c) => c.ns).length;
    const list = cps.slice(0, 120).map((c) => {
      const who = c.ns ? (c.agent && c.agent !== "lead" ? c.agent.replace("_", " ") : "agent subgraph") : "workflow";
      const t = c.ts ? new Date(c.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "";
      return `<div class="cp ${c.ns ? "sub" : ""} ${c.fresh ? "cp-fresh" : ""}"><div class="line"><i></i></div><div><div class="w">${esc(who)} · step ${c.step ?? "–"}</div><div class="x">${esc(c.checkpoint_id.slice(-12))} · ${esc(c.source || "")}</div></div><div class="x">${t}</div></div>`;
    }).join("");
    body.innerHTML = `${T.next?.length && !T.running ? `<div class="resume">Run interrupted.<button class="btn-sm" data-resume style="margin-left:auto">${ICON.play}Resume</button></div>` : ""}
      <div class="stat"><div><div class="v">${cps.length}</div><div class="k">checkpoints</div></div><div><div class="v">${sub}</div><div class="k">in agent subgraphs</div></div></div>
      ${list || `<div class="muted">No checkpoints yet.</div>`}
      <div class="muted" style="font-size:11.5px;margin-top:12px">Stored in SQLite on this device. Every workflow and agent step is persisted before the next one starts.</div>`;
  } else {
    const evs = T.events;
    body.innerHTML = evs.length ? [...evs].reverse().map((e) => `<div class="ev ${esc(e.priority)}"><div class="h"><b>${esc(e.priority)}</b><span>${clock(e.ts)}</span><span style="margin-left:auto">${e.tier === "edge" ? "device" : "cloud"}</span></div>${esc(e.text)}</div>`).join("")
      : `<div class="muted">Agents add entries here as they work (triage results, hazards, decisions).</div>`;
  }
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
$("#menuBtn").addEventListener("click", () => $("#sidebar").classList.toggle("open"));
$("#threads").addEventListener("click", (e) => {
  const b = e.target.closest("[data-thread]");
  if (b && !state.sending) openThread(b.dataset.thread);
});
$("#feed").addEventListener("click", (e) => {
  const chip = e.target.closest("[data-chip]");
  if (chip) return send(chip.dataset.chip);
  const ag = e.target.closest("[data-agent]");
  if (ag) { const id = ag.dataset.agent; expanded.has(id) ? expanded.delete(id) : expanded.add(id); return renderFeed(); }
  if (e.target.closest("[data-rerun]")) return rerun();
  if (e.target.closest("[data-resume]")) return resume();
});
$("#tabBody").addEventListener("click", (e) => { if (e.target.closest("[data-resume]")) resume(); });
$("#linkPill").addEventListener("click", (e) => {
  e.stopPropagation();
  const pop = $("#linkPop");
  pop.hidden = !pop.hidden;
  $("#linkPill").setAttribute("aria-expanded", String(!pop.hidden));
  if (!pop.hidden) renderPopover();
});
$("#linkPop").addEventListener("click", (e) => {
  e.stopPropagation();
  const o = e.target.closest("[data-outage]");
  if (o) return setLink({ provider: o.dataset.outage, disabled: !o.classList.contains("on") });
  const off = e.target.closest("[data-offline]");
  if (off) return setLink({ offline: !off.classList.contains("on") });
});
document.addEventListener("click", () => { $("#linkPop").hidden = true; $("#linkPill").setAttribute("aria-expanded", "false"); });
$("#inspectorBtn").addEventListener("click", () => {
  const ins = $("#inspector");
  ins.hidden = !ins.hidden;
  $("#inspectorBtn").classList.toggle("on", !ins.hidden);
  localStorage.setItem("he.inspector", ins.hidden ? "0" : "1");
  renderInspector();
});
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => { state.tab = t.dataset.tab; renderInspector(); }));

(async function boot() {
  try { if (localStorage.getItem("he.inspector") === "1") { $("#inspector").hidden = false; $("#inspectorBtn").classList.add("on"); } } catch {}
  pollStatus();
  await loadThreads().catch(() => {});
  let last = null;
  try { last = localStorage.getItem("he.thread"); } catch {}
  if (last && state.threads.some((t) => t.id === last)) await openThread(last);
  else renderFeed();
  setInterval(renderThreads, 60000);
})();
