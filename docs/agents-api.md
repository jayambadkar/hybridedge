# Agents API

A small Responses-style API on the device (`hybridedge/agents_api.py`). Any app can run
the same checkpointed, failover-aware agents; it never needs to know which provider ran a
step. Interactive reference: http://localhost:8787/docs.

## Endpoints

| Endpoint | Purpose |
|---|---|
| `POST /v1/responses` | Run a task (see body below) |
| `GET /v1/responses/{id}` | Fetch a finished response |
| `POST /v1/sessions` | Create a session: `title`, `agent`, `source` |
| `GET /v1/sessions`, `GET /v1/sessions/{id}` | List or inspect sessions |
| `DELETE /v1/sessions/{id}` | Delete a session with its checkpoints and logs |
| `PUT /v1/sessions/{id}/context`, `GET …/context` | Structured context that tools read (for example map reports) |
| `GET /v1/sessions/{id}/stream` | Live SSE of everything in a session, whichever client started it |
| `GET /v1/sessions/{id}/flow` | The flow graph of each turn |
| `GET /v1/agents` | Agent teams and their roles |
| `GET /v1/status`, `/v1/capability`, `/v1/models` | Where agents run right now, and what that allows |

`POST /v1/responses` body:

| Field | |
|---|---|
| `input` | Text, or a list of messages |
| `agent` | `incident` (default), `police_tactical` or `mta` |
| `stream` | Server-sent events when `true` |
| `session_id` or `previous_response_id` | Continue a session |
| `source` | Your app's name, shown in the console |
| `metadata` | Stored with the response |

## Events

Standard: `response.created`, `response.output_text.delta`, `response.completed`,
`response.failed`.

HybridEdge, prefixed `hybridedge.`:

| Event | Meaning |
|---|---|
| `plan` | Specialists chosen, with tasks; `deferred` lists those waiting for the cloud |
| `step_start` / `step_end` | A model call starts or ends, with its target and the reason |
| `reasoning`, `token` | Live reasoning and output of a step |
| `tool_call`, `tool_result` | A tool ran on the device |
| `failover` | A step lost its provider and moved to the next target |
| `upgrade` | A device step moved back to the cloud |
| `deferred` | A specialist stopped to wait for the cloud |
| `review`, `revise` | Plan review findings and the work sent back |
| `agent_done` | A specialist's report |
| `checkpoint` | A checkpoint was written |
| `flow` | The updated flow graph |

## Example

```bash
SID=$(curl -s localhost:8787/v1/sessions -H 'content-type: application/json' \
  -d '{"title": "Exercise", "agent": "mta", "source": "my-app"}' | jq -r .id)

curl -N localhost:8787/v1/responses -H 'content-type: application/json' -d "{
  \"session_id\": \"$SID\", \"stream\": true,
  \"input\": \"Two attackers with knives at Narinkka Square, moving east. Produce the commander's brief.\"}"
```
