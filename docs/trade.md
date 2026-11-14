# Trusted Trade Agents

Our entry for Valio Aimo's challenge at Junction 2026: how a food wholesaler can let its customers'
AI purchasing agents buy from it, safely, including where the connection is poor.

Open http://localhost:8787/trade/ after `./start.sh`. All products, prices and buyers are mock data.

The console is a buyer's admin for a distributor where conflict has cut the fixed network
(Eastgate Provisions: three depots, a shared satellite link, roads that close at short notice).
It has Home, Orders, Products, Inventory, Supply routes, **Agent tasks** and **Inference** pages.

- **Planning agents** (Demand and stock, Supply routes, Pricing and margin) analyse the store,
  a reviewer sends weak work back for revision, and the lead writes the week's operating plan.
  Each "ORDER:" line in the plan hands off to the purchasing agents with one click.
- **Inference** shows every model step: where it ran (Verda, Modal or this device), first-token
  latency, tools called, failovers, and why the router chose that place.

## Two sides

**The wholesaler's interface** (`/trade/v1`, OpenAPI at `/docs`, agent card at
`/.well-known/agent-card.json`) is small, open and scoped:

| Endpoint | What it does |
|---|---|
| `GET /catalog/search` | Search products. Public fields only: cost and margin never leave the server. |
| `POST /quotes` | Fixed-price quote with confirmed availability. |
| `POST /orders` | Place an order. The buyer's policy decides whether a human must approve. |
| `GET /orders`, `GET /orders/{id}` | This buyer's orders, each with its audit trail. |
| `GET /policy` | What this agent may decide on its own. |
| `GET /approvals`, `POST /approvals/{id}` | The human approval queue (approver key). |
| `GET /audit` | Append-only audit trail (approver key). |

- Each agent key is bound to one buyer and sees only that buyer's quotes and orders.
- The buyer's policy is enforced by the wholesaler, not trusted to the agent. An agent cannot
  approve its own order, and splitting an order to stay under the limit does not work, because
  same-day orders add up.
- Every action is logged with who did it and why.

**The buyer's purchasing agents** run on HybridEdge:

| Agent | Runs on | Why |
|---|---|---|
| Sourcing | Nemotron 3.5 Lightning 30B in the cloud (Verda, Modal as standby) | Searches, compares price per litre, stock and delivery, and requests a quote. |
| Procurement | Nemotron 3.5 Bolt E4B on the laptop, always | Holds the budget and approval rules, so they never leave the device. Places the order. |

## Low connectivity

When the link drops, the sourcing agent moves to the laptop and works from a cached catalogue.
Quotes are provisional, and the order is saved in an outbox on the device. When the link returns
the outbox sends the order, and the wholesaler prices it, applies the policy and logs it as usual.

## Demo

0. **Eastgate.** On Home, click **Plan this week with agents**. Watch the specialists, the review
   loop and the plan on **Agent tasks**, then send one of its orders. Open **Inference** to show
   where each step ran. Then simulate losing the link and run it again: it plans on the laptop.

1. **Helsinki.** Ask for 1,000 L of lactose-free milk by Monday. The agents quote €1,145, which is
   over the buyer's €1,000 limit, so the order waits for a human. Click **Approve**.
2. **Lagos.** Pick the Lagos buyer, open the status pill (top right) and choose **Simulate losing
   the network**. Send the order: it is saved on the device. End the drill and it syncs.
3. **Reset demo** (bottom right) clears everything between runs.
