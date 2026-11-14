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
a cloud is up and `EDGE_MAX_AGENTS` (2) on the device. Each LLM step picks the best target
*at that moment*. A workflow planned in the cloud keeps going on the laptop if the link drops
(the on-device model is limited to one generation at a time), and speeds back up as soon as
a cloud returns.

**Failover** (`hybridedge/router.py`): health probes every 3 s; mid-stream cuts are detected
instantly (cut event), and stalls through first-token and between-token timeouts. A failed
step is retried on the next target (Verda → Modal → device) from the same checkpointed
state. Modal is a **cold standby**: it isn't probed while Verda is healthy, so it scales to
zero and costs nothing until it's needed.

**UI** (`web/`): plain HTML, CSS and JS with no build step and no external assets, so it
loads offline. It has a status pill with the failover order and drill toggles (simulate an
outage at one provider, or disconnect all clouds), a degraded-mode banner, live per-agent
progress, inline failover notes, and a checkpoints / incident-log inspector.

## Cloud deployments

- **Verda**: serverless container `hybridedge-nemotron`, image `vllm/vllm-openai:v0.27.1`,
  1× H100 SXM5, about $5.77/h while running. Pause it in the Verda console when you're not
  using it.
- **Modal**: `uv run modal deploy deploy/modal_app.py` (instructions are in the file).
  Scales to zero after 10 idle minutes.

The on-device model: `ollama pull nemotron-3.5-bolt:e4b && ollama create hybridedge-bolt -f Modelfile`.

> The protocol cards are generic demo references. Local SOPs and medical direction take precedence.
