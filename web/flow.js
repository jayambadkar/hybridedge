"use strict";
/*
 * HybridFlow — renders the Agents API `flow` object (GET /v1/sessions/{id}/flow or
 * `hybridedge.flow` stream events): how one task moved through the agent loop and
 * where (and why) each part ran.
 *
 *   Prompt → Planning → Reasoning (agent ⇄ tool loops) → Checkpointing → Result
 *
 * Two shapes only, decided by the provider class the task was planned on:
 *   cloud (unconstrained) — agents run in parallel
 *   edge  (constrained)   — agents queue and run one at a time on this device
 *
 *   HybridFlow.render(flow, { compact })  → HTML
 *   HybridFlow.note(flow)                 → one-line routing note, or "" when it ran normally
 *   HybridFlow.summary(flow)              → plain-language sentence of where/why it ran
 */
(function () {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const TOOL = {
    lookup_protocol: "Protocol lookup", start_triage: "START triage", log_event: "Log entry", get_incident_log: "Read log",
    assess_threat: "Threat score", plan_formation: "Formation",
  };
  const time = (ts) => (ts ? new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "");
  const name = (flow, t) => (!t || t === "edge" ? "this device" : (flow.providers && flow.providers[t]) || t);
  const Name = (flow, t) => { const n = name(flow, t); return n[0].toUpperCase() + n.slice(1); };

  function failText(raw) {
    const r = String(raw || "");
    if (/link cut/.test(r)) return "Link dropped mid-answer";
    if (/stalled/.test(r)) return "Stopped responding";
    if (/HTTP \d+/.test(r)) return `Returned an error (${r.match(/HTTP \d+/)[0]})`;
    if (/connection/.test(r)) return "Connection failed";
    return r ? r[0].toUpperCase() + r.slice(1) : "Failed";
  }

  function eventText(flow, e) {
    if (e.kind === "failover") {
      const [why, next] = String(e.reason || "").split(" — ");
      return `${Name(flow, e.from)} → ${name(flow, e.to)}. ${failText(why)}.${next ? ` ${next}.` : ""}`;
    }
    if (e.kind === "upgrade") return `${Name(flow, e.from)} → ${name(flow, e.to)}`;
    if (e.kind === "switch") return `${Name(flow, e.from)} → ${name(flow, e.to)}${e.reason && !OBVIOUS.test(e.reason) ? `. ${e.reason}.` : ""}`;
    return `Started on ${name(flow, e.to)}. ${e.reason || ""}${e.reason ? "." : ""}`;
  }

  // Reasons not worth printing: the primary cloud being used is the normal case.
  const OBVIOUS = /primary cloud and healthy/i;

  function summary(flow) {
    const r = flow.routing || [];
    if (!r.length) {
      const t = flow.planning && flow.planning.target;
      return t ? `Ran on ${name(flow, t)}.` : "";
    }
    const first = r[0];
    if (r.length === 1) return `Ran on ${name(flow, first.to)}. ${first.reason ? `${first.reason}.` : ""}`;
    const last = r[r.length - 1];
    return `Started on ${name(flow, first.to)}, finished on ${name(flow, last.to)}.`;
  }

  /** Short line for the answer footer; empty when the task simply ran on the primary cloud. */
  function note(flow) {
    if (!flow) return "";
    const r = flow.routing || [];
    if (!r.length) return "";
    // Ending up back on the cloud is the normal state: nothing to call out.
    const switches = r.filter((e) => e.kind !== "start" && e.kind !== "upgrade");
    const last = r[r.length - 1];
    if (last.kind === "upgrade") return "";
    if (switches.length) {
      const e = switches[switches.length - 1];
      if (e.kind === "switch" && OBVIOUS.test(e.reason || "")) return ""; // back on the primary cloud: nothing to call out
      return `Switched at ${time(e.ts)} — ${eventText(flow, e)}`;
    }
    const first = r[0];
    if (OBVIOUS.test(first.reason || "")) return "";
    return `Ran on ${name(flow, first.to)}: ${first.reason || ""}`;
  }

  function stages(flow) {
    return `<ol class="hf-stages">${flow.stages.map((s) => {
      const extra = s.id === "checkpointing" && flow.checkpoints.count ? ` <span class="hf-count">${flow.checkpoints.count}</span>` : "";
      return `<li class="hf-${s.status}"><i></i><span>${esc(s.label)}${extra}</span></li>`;
    }).join("")}</ol>`;
  }

  function track(steps) {
    if (!steps.length) return `<span class="hf-wait">Waiting</span>`;
    const items = steps.length > 9 ? [...steps.slice(0, 4), { kind: "more", n: steps.length - 8 }, ...steps.slice(-4)] : steps;
    return items.map((st) => {
      if (st.kind === "more") return `<span class="hf-more">+${st.n}</span>`;
      if (st.kind === "tool") return `<span class="hf-chip hf-tool ${st.status === "running" ? "hf-live" : ""}">${esc(TOOL[st.label] || st.label)}</span>`;
      const cls = st.status === "failed_over" || st.status === "moved" ? "hf-dropped" : st.tier === "cloud" ? "hf-c-cloud" : "hf-c-device";
      const label = st.status === "failed_over" ? "Dropped" : st.status === "moved" ? "Moved" : "Think";
      return `<span class="hf-chip ${cls} ${st.status === "running" ? "hf-live" : ""}">${label}</span>`;
    }).join('<span class="hf-sep">›</span>');
  }

  function where(flow, lane) {
    const ts = [];
    for (const s of lane.steps) if (s.kind === "reason" && s.target && ts[ts.length - 1] !== s.target) ts.push(s.target);
    return ts.map((t) => Name(flow, t)).join(" → ");
  }

  function agents(flow) {
    if (!flow.lanes.length) return "";
    const serial = flow.layout === "serial";
    const head = serial
      ? `<div class="hf-shape"><b>Queued</b> · ${flow.lanes.length} agent${flow.lanes.length > 1 ? "s" : ""}, one at a time on this device</div>`
      : `<div class="hf-shape"><b>Parallel</b> · ${flow.lanes.length} agent${flow.lanes.length > 1 ? "s" : ""} at once</div>`;
    const rows = flow.lanes.map((l, i) => `<div class="hf-agent hf-a-${l.status}">
        <div class="hf-aname">${serial ? `<span class="hf-num">${i + 1}</span>` : ""}${esc(l.title)}</div>
        <div class="hf-track">${track(l.steps)}</div>
        <div class="hf-awhere">${esc(where(flow, l))}</div>
      </div>`).join("");
    return `${head}<div class="hf-agents ${serial ? "hf-serial" : "hf-parallel"}">${rows}</div>`;
  }

  function timeline(flow) {
    const r = flow.routing || [];
    if (!r.length) return "";
    return `<ol class="hf-timeline">${r.map((e) => `<li class="hf-e-${e.kind} ${e.to === "edge" ? "hf-to-edge" : "hf-to-cloud"}">
        <time>${time(e.ts)}</time><span>${esc(eventText(flow, e))}${e.agents && e.agents.length > 1 ? ` <em>(${e.agents.length} agents)</em>` : ""}</span></li>`).join("")}</ol>`;
  }

  function render(flow, opts = {}) {
    if (!flow) return "";
    const cap = flow.capability;
    const sum = summary(flow);
    const showTimeline = !opts.compact || (flow.routing || []).length > 1;
    return `<div class="hf hf-${flow.mode}${opts.compact ? " hf-compact" : ""}">
      <div class="hf-cap"><span class="hf-badge">${esc(cap.label)}</span><span>${esc(cap.model)} · up to ${cap.max_agents} agents${cap.parallel && flow.mode === "cloud" ? " in parallel" : ", one at a time"} · ${esc(cap.context)} context</span></div>
      ${sum ? `<p class="hf-summary">${esc(sum)}</p>` : ""}
      <section><h4>Agent loop</h4>${stages(flow)}${agents(flow)}</section>
      ${showTimeline && (flow.routing || []).length ? `<section><h4>Where it ran</h4>${timeline(flow)}</section>` : ""}
    </div>`;
  }

  window.HybridFlow = { render, summary, note };
})();
