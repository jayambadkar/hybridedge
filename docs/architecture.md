# Architecture

## Workflow

`hybridedge/graph.py` defines one LangGraph `StateGraph`:

```
plan ─Send×N─▶ specialist ─▶ review ─┬─▶ synthesize ─▶ END
                (subgraph)            └─▶ specialist (revisions, deferred work)
```

- **plan**: the lead picks specialists and gives each a sub-task. In the cloud this is one
  reasoning call. On the device it is a short call with no reasoning (about 3 to 5 s) that
  ranks the specialists. The core team runs; the rest are recorded as deferred.
- **specialist**: a subgraph with an `agent ⇄ tools` loop, up to six steps. Tools run on the
  device, so they work offline.
- **review**: deterministic consistency checks (`check_plan`) and, in the cloud, an LLM
  reviewer. High-severity issues go back to their owner for one revision round, or add a
  missing specialist. On the device only the deterministic checks run.
- **synthesize**: the lead merges the reports into a brief and says which specialists are
  still waiting for the cloud.

Agent teams are profiles: `incident` (multi-agency), `police_tactical`, and `mta`
(marauding terrorist attack). A profile is a roster of roles, their tools, prompts and a
core team.

## Checkpointing

`AsyncSqliteSaver` with `durability="sync"`: each super-step of the parent graph and of every
specialist subgraph is on disk before the next starts.

- Parallel specialists that finished are stored as pending writes. A resumed run only
  executes unfinished ones; an unfinished specialist resumes from its own last checkpoint.
- Runs are decoupled from HTTP. Any number of clients can subscribe to a run, drop and
  re-attach.
- **Re-run on cloud** forks the thread from the checkpoint after the user's message, so an
  answer written on the device can be redone once the cloud is back.

## Routing (`hybridedge/router.py`)

Each LLM step calls `pick_reason()`, which returns a target and a reason:

1. The highest-priority healthy cloud with free capacity.
2. If it is full, the next cloud. A cold standby (Modal) wakes only when the primary is
   down or full.
3. Otherwise, the device, one generation at a time.

Signals:

- **Network**: an OS route lookup every 0.5 s (no packets sent). Losing the route cuts all
  cloud streams immediately.
- **Provider health**: probed every 2 s; a refused connection marks the provider down at once.
- **Stream guards**: 30 s to the first token, 20 s maximum stall.
- **On the device**: the router keeps probing the clouds every second. When one returns, the
  running device step is cancelled and restarted there (`upgrade`).

## Scaling down

When the link drops in the middle of a run, specialists outside the core team raise
`Deferred` instead of queueing on the device. The core team finishes locally and the brief
says what is outstanding. When the cloud returns before the review, the deferred
specialists run there.

## Flow model (`hybridedge/flow.py`)

Every task is described as `Prompt → Planning → Reasoning → Checkpointing → Result`, with one
lane per specialist and a routing timeline. It is streamed as `hybridedge.flow` events,
stored per turn, and drawn by `web/flow.js` ("How it ran").
