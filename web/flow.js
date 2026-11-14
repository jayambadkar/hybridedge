"use strict";
/*
 * HybridFlow — renders the Agents API `flow` object (GET /v1/sessions/{id}/flow or
 * `hybridedge.flow` stream events) as a visual of where a task is in the agent loop.
 *
 *   Prompt → Planning → Reasoning (agent ⇄ tool loops) → Checkpointing → Result
 *
 * The graph has exactly two shapes, chosen by the class of inference provider:
 *   cloud (unconstrained): agents fan out in parallel lanes between Plan and Result
 *   edge  (constrained):   agents queue on a single track and run one after another
 *
 * Usage: el.innerHTML = HybridFlow.render(flow, { compact: false })
 * Theme with CSS variables (see flow.css): --hf-cloud, --hf-edge, --hf-ink, --hf-muted, --hf-line, --hf-bg.
 */
(function () {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const TOOL_NAMES = {
    lookup_protocol: "Protocol lookup", start_triage: "START triage", log_event: "Log event", get_incident_log: "Read log",
    assess_threat: "Threat score", plan_formation: "Formation",
  };

  function stage(s, flow) {
    const extra = s.id === "checkpointing"
      ? `<span class="hf-sub">${flow.checkpoints.count ? `${flow.checkpoints.count} saved` : s.status === "done" ? "All saved" : "Every step"}</span>`
      : s.id === "planning" && flow.planning.tier ? `<span class="hf-sub">${flow.planning.tier === "cloud" ? "Cloud" : "Device"}</span>`
      : s.id === "result" && flow.result.tier ? `<span class="hf-sub">${flow.result.tier === "cloud" ? "Cloud" : "Device"}</span>`
      : s.id === "reasoning" && flow.lanes.length ? `<span class="hf-sub">${flow.lanes.length} agent${flow.lanes.length > 1 ? "s" : ""}</span>` : "";
    return `<li class="hf-stage hf-${s.status}"><span class="hf-dot"></span><span class="hf-lbl">${esc(s.label)}</span>${extra}</li>`;
  }

  function step(st) {
    const cls = `hf-step hf-${st.kind} hf-t-${st.tier} hf-s-${st.status}`;
    if (st.kind === "tool") return `<span class="${cls}" title="Tool runs on this device">${esc(TOOL_NAMES[st.label] || st.label)}</span>`;
    const title = st.status === "failed_over" ? "Provider dropped — resumed elsewhere from checkpoint" : st.tier === "cloud" ? "Reasoning on cloud" : "Reasoning on this device";
    return `<span class="${cls}" title="${title}"></span>`;
  }

  function lane(l, serial, idx) {
    const steps = l.steps.length ? l.steps.map(step).join('<span class="hf-arrow"></span>') : `<span class="hf-wait">${serial && idx > 0 ? "Waiting for device" : "Queued"}</span>`;
    const loops = l.loops > 1 ? `<span class="hf-loop" title="Reasoning loops">↻ ${l.loops}</span>` : "";
    return `<div class="hf-lane hf-l-${l.status}"><div class="hf-lane-h"><span class="hf-lane-t">${esc(l.title)}</span>${loops}</div><div class="hf-steps">${steps}</div></div>`;
  }

  function render(flow, opts = {}) {
    if (!flow) return "";
    const cap = flow.capability;
    const serial = flow.layout === "serial";
    const lanes = flow.lanes.map((l, i) => lane(l, serial, i)).join(serial ? '<span class="hf-next"></span>' : "");
    const failovers = flow.failovers && flow.failovers.length
      ? `<div class="hf-note">↪ ${flow.failovers.length === 1 ? "1 step" : `${flow.failovers.length} steps`} switched provider and resumed from a checkpoint</div>` : "";
    return `<div class="hf hf-${flow.mode} ${opts.compact ? "hf-compact" : ""}">
      <div class="hf-cap"><span class="hf-badge">${esc(cap.label)}</span><span class="hf-capd">${esc(cap.model)} · ${esc(cap.summary)}</span></div>
      <ol class="hf-stages">${flow.stages.map((s) => stage(s, flow)).join("")}</ol>
      ${flow.lanes.length ? `<div class="hf-body ${serial ? "hf-serial" : "hf-parallel"}">
        <div class="hf-node">Plan</div>
        <div class="hf-lanes">${lanes}</div>
        <div class="hf-node">Result</div>
      </div>` : ""}
      ${serial && flow.lanes.length ? `<div class="hf-note hf-why">Constrained: a small on-device model runs the agents one at a time with less context, so plans are narrower and slower.</div>` : ""}
      ${failovers}
      <div class="hf-legend"><span><i class="hf-k hf-t-cloud"></i>Cloud reasoning</span><span><i class="hf-k hf-t-edge"></i>Device reasoning</span><span><i class="hf-k hf-tool"></i>Tool (on device)</span></div>
    </div>`;
  }

  window.HybridFlow = { render };
})();
