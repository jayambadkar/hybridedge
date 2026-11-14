# HybridEdgeLLM

Hybrid cloud/edge multi-agent system for mobile, mission-critical work (police, fire, EMS).
Agents run on powerful cloud GPUs when there is a link and keep working on the laptop when
there isn't. Every agent step is checkpointed on the device, so nothing is lost on disconnect,
crash, or provider failover.

```
                 ┌───────────── this laptop (source of truth) ──────────────┐
 Web UI  ──SSE── │ FastAPI edge server                                      │
 (localhost)     │  LangGraph workflow ── SQLite checkpoints (every step)   │
                 │  Router: Verda ─▶ Modal ─▶ on-device                      │
                 └─────┬───────────────┬────────────────────┬────────────────┘
                       │               │                    │
          Verda H100 (vLLM)   Modal H100 (vLLM, cold    Ollama on M3
          Nemotron 3.5         standby, scales to 0)     Nemotron 3.5 Bolt E4B
          Lightning 30B-A3B    same model                (tool use, 16k ctx)
```

## Run

```bash
./start.sh            # starts Ollama if needed, then http://localhost:8787
```

Config lives in `.env` (see `.env.example`). Keys stay local; `.env` is gitignored.

## How it works

**Multi-agent workflow** (`hybridedge/graph.py`):
`plan → specialists in parallel (Send fan-out) → synthesize`. Each specialist (Medical,
Fire & Hazmat, Scene Safety, Command & Comms, or a generalist) is its own LangGraph
**subgraph** with an `agent ⇄ tools` loop. Tools run on the device, so they work offline:
protocol lookup, deterministic START triage, and an incident log.

**Checkpointing**: `AsyncSqliteSaver` with `durability="sync"`, so every workflow step and
every subgraph step is written to disk before the next one starts.
- A specialist that already finished is kept (pending writes). After a crash, **Resume**
  re-runs only the unfinished specialists. Tested with `kill -9` partway through a workflow.
- Runs are separate from HTTP connections. If the browser reloads, it re-attaches to the
  run that is still going.
- **Re-run on cloud** forks the thread from the checkpoint taken after the user's message
  (LangGraph time travel), so an answer produced on the device can be upgraded once a cloud
  is back.

**Scaling up and down**: the planner can fan out to `CLOUD_MAX_AGENTS` (4) specialists when
a cloud is up and `EDGE_MAX_AGENTS` (2) on the device. Routing is decided **per LLM step**, not
per task:
- Steps go to the highest-priority healthy cloud that has free capacity (`<NAME>_CONCURRENCY`,
  default 32 concurrent generations).
- When that cloud is full, the overflow goes to the next cloud. A cold standby (Modal) is woken
  only when the primary is down *or* full, so it costs nothing during normal load.
- With no usable cloud, steps run on this device, one generation at a time.

So a single task's agents can be spread across Verda, Modal and the laptop, and every step
picks the best target at the moment it runs.

**Failover** (`hybridedge/router.py`):
- **Device connectivity** is checked every 0.5 s with an OS route lookup that sends no packets.
  Turning off Wi-Fi cuts in-flight cloud streams immediately and routes work to the device.
  When the network returns, the providers are re-probed straight away.
- **Provider health** is probed every 2 s; a refused connection marks a provider down at once.
  A stall is caught by the first-token (30 s) and between-token (10 s) timeouts.
- **A failed step** is retried on the next target from the same checkpointed state.

**UI** (`web/`): plain HTML, CSS and JS with no build step and no external assets, so it
loads offline.
- **Status bar**: a full-width banner showing Connected / Reconnecting / Offline / No network.
- **Status menu**: a read-only view of where agents are running.
- **Settings**: provider setup, live load and health, plus a labelled **Test mode** with
  outage drills.
- **Agent progress**: live per-agent progress with inline failover notes.
- **Collapsible panels**: the sidebar, the workflow cards, and separate Incident log and
  Checkpoints sections.

Routing checks: `PYTHONPATH=. uv run python tests/check_routing.py`.

## On-device Agents API (`/v1`)

Any app on the device can use the same checkpointed, failover-aware agents through a small
Responses-style API (`hybridedge/agents_api.py`). Every session is an ordinary incident,
so **the console shows sessions from other apps live**, marked with the app's name.

| Endpoint | Purpose |
|---|---|
| `POST /v1/responses` | Run a task. Body: `input`, `agent` (`incident` \| `police_tactical`), `stream`, `previous_response_id` or `session_id`, `source`, `metadata` |
| `GET /v1/responses/{id}` | Fetch a response |
| `POST /v1/sessions`, `GET /v1/sessions[/{id}]` | Create, list or inspect sessions |
| `GET /v1/sessions/{id}/stream` | Live SSE of everything in a session, whichever client started it |
| `GET /v1/sessions/{id}/flow` | The task's **flow** graph (see below) |
| `GET /v1/agents`, `/v1/status`, `/v1/capability`, `/v1/models` | Catalogue and live capability |

Streaming uses Responses-API events (`response.created`, `response.output_text.delta`,
`response.completed`, `response.failed`) plus `hybridedge.*` events: `plan`, `step_start`,
`tool_call`, `tool_result`, `failover`, `checkpoint`, `agent_done` and `flow`.

```bash
curl -N localhost:8787/v1/responses -H 'content-type: application/json' -d '{
  "agent": "police_tactical", "stream": true, "source": "my-app",
  "input": "Barricaded suspect, firearm reported, 9 officers available. Plan the response."}'
```

Agent teams (profiles) live in `hybridedge/graph.py`. Adding a new domain means adding a roster
of roles plus any tools; the planning, checkpointing, failover and flow logic is shared.

**Flow model** (`hybridedge/flow.py`, rendered by `web/flow.js`): every task goes through
`Prompt → Planning → Reasoning (agent ⇄ tool loops) → Checkpointing → Result`. The graph has
exactly two shapes, decided only by the class of inference provider:
- **Cloud (unconstrained)**: up to 4 agents fan out in parallel lanes.
- **On-device (constrained)**: up to 2 agents queue on one track and run one at a time.

Each step is coloured by where it ran, so a mixed run is visible at a glance.

## Patrol demo (`/command/`)

A mobile police tactical-response app built **only** on `/v1`. It has no knowledge of clouds,
providers or checkpoints. Pick the incident, intel and officer count, and it streams back:
- a threat level (from the deterministic `assess_threat` tool)
- an animated formation map (from the `plan_formation` tool)
- the live agent flow
- tactical orders

Intel updates continue the same session via `previous_response_id`. Use **View on console ↗**
to open the same session in the console. Open it at http://localhost:8787/command/.

## Cloud deployments

- **Verda**: serverless container `hybridedge-nemotron`, image `vllm/vllm-openai:v0.27.1`,
  1× H100 SXM5, about $5.77/h while running. Pause it in the Verda console when you're not
  using it.
- **Modal**: `uv run modal deploy deploy/modal_app.py` (instructions are in the file).
  Scales to zero after 10 idle minutes.

The on-device model: `ollama pull nemotron-3.5-bolt:e4b && ollama create hybridedge-bolt -f Modelfile`.

> The protocol cards are generic demo references. Local SOPs and medical direction take precedence.
