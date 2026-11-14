"""Trusted Trade Agents: a mock wholesaler (Valio Aimo-style) interface for external purchasing agents.

The wholesaler exposes a small, open, scoped interface (OpenAPI under /trade/v1, plus an
agent card at /.well-known/agent-card.json):

    search catalogue ─▶ request a fixed-price quote ─▶ place the order
                                                         │
                         policy engine (server side) ────┤── within authority → placed
                                                         └── otherwise        → waits for a human

* Agents only ever see public product fields; internal fields (cost, margin, supplier terms)
  never leave this module.
* Each agent key is bound to one buyer and can only see that buyer's quotes and orders.
* The buyer's policy is enforced here, not trusted to the agent: an agent cannot approve its
  own order, and splitting an order to dodge the limit does not work (same-day orders add up).
* Every action is written to an append-only audit trail with who did it and why.

All data is MOCK data for the Junction 2026 prototype - not real Valio Aimo products or prices.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

DATA = Path(__file__).resolve().parents[1] / "data" / "trade.json"

# ------------------------------------------------------------------ catalogue (mock)
# cost_eur / margin are internal: they exist to show what the interface does NOT expose.
CATALOG: list[dict[str, Any]] = [
    {"sku": "AIMO-1001", "name": "Lactose-free milk drink 1.5% 1 L carton", "category": "dairy", "unit": "carton", "litres": 1, "price_eur": 1.29, "stock": 1800, "lead_days": 1, "labels": ["lactose-free", "finnish-origin"], "cost_eur": 0.97},
    {"sku": "AIMO-1002", "name": "Lactose-free milk drink 1.5% 10 L bag-in-box", "category": "dairy", "unit": "box", "litres": 10, "price_eur": 10.90, "stock": 45, "lead_days": 1, "labels": ["lactose-free", "finnish-origin", "less-packaging"], "cost_eur": 8.40},
    {"sku": "AIMO-1003", "name": "Organic lactose-free milk drink 1.5% 1 L carton", "category": "dairy", "unit": "carton", "litres": 1, "price_eur": 1.79, "stock": 600, "lead_days": 2, "labels": ["lactose-free", "organic", "finnish-origin"], "cost_eur": 1.31},
    {"sku": "AIMO-1004", "name": "Lactose-free skimmed milk drink 1 L carton", "category": "dairy", "unit": "carton", "litres": 1, "price_eur": 1.19, "stock": 900, "lead_days": 1, "labels": ["lactose-free", "finnish-origin"], "cost_eur": 0.88},
    {"sku": "AIMO-1005", "name": "Lactose-free UHT milk 1.5% 1 L, long-life", "category": "dairy", "unit": "carton", "litres": 1, "price_eur": 1.39, "stock": 3000, "lead_days": 1, "export_lead_days": 9, "labels": ["lactose-free", "long-life", "no-cold-chain"], "cost_eur": 1.02},
    {"sku": "AIMO-1006", "name": "Lactose-free UHT milk 1.5% 12 x 1 L tray, long-life", "category": "dairy", "unit": "tray", "litres": 12, "price_eur": 15.60, "stock": 220, "lead_days": 1, "export_lead_days": 9, "labels": ["lactose-free", "long-life", "no-cold-chain"], "cost_eur": 11.40},
    {"sku": "AIMO-1070", "name": "Whole milk powder 25 kg sack", "category": "dairy", "unit": "sack", "kg": 25, "price_eur": 92.00, "stock": 140, "lead_days": 2, "export_lead_days": 10, "labels": ["long-life", "no-cold-chain"], "cost_eur": 71.00},
    {"sku": "AIMO-1010", "name": "Semi-skimmed milk 1.5% 1 L carton", "category": "dairy", "unit": "carton", "litres": 1, "price_eur": 0.99, "stock": 5000, "lead_days": 1, "labels": ["finnish-origin"], "cost_eur": 0.71},
    {"sku": "AIMO-1011", "name": "Semi-skimmed milk 1.5% 10 L bag-in-box", "category": "dairy", "unit": "box", "litres": 10, "price_eur": 8.70, "stock": 300, "lead_days": 1, "labels": ["finnish-origin", "less-packaging"], "cost_eur": 6.30},
    {"sku": "AIMO-1020", "name": "Oat drink barista 1 L", "category": "dairy-alternatives", "unit": "carton", "litres": 1, "price_eur": 1.49, "stock": 2400, "lead_days": 1, "labels": ["lactose-free", "plant-based", "vegan"], "cost_eur": 1.02},
    {"sku": "AIMO-1030", "name": "Lactose-free whipping cream 35% 1 L", "category": "dairy", "unit": "carton", "litres": 1, "price_eur": 4.20, "stock": 400, "lead_days": 1, "labels": ["lactose-free", "finnish-origin"], "cost_eur": 3.05},
    {"sku": "AIMO-1040", "name": "Lactose-free natural yoghurt 5 kg", "category": "dairy", "unit": "bucket", "kg": 5, "price_eur": 13.50, "stock": 120, "lead_days": 1, "labels": ["lactose-free", "finnish-origin"], "cost_eur": 9.90},
    {"sku": "AIMO-1050", "name": "Butter 82% 500 g", "category": "dairy", "unit": "pack", "kg": 0.5, "price_eur": 3.60, "stock": 1500, "lead_days": 1, "labels": ["finnish-origin"], "cost_eur": 2.70},
    {"sku": "AIMO-1060", "name": "Edam cheese slices 1 kg", "category": "dairy", "unit": "pack", "kg": 1, "price_eur": 9.80, "stock": 350, "lead_days": 2, "labels": ["lactose-free", "finnish-origin"], "cost_eur": 7.10},
    {"sku": "AIMO-2001", "name": "Free-range eggs M 180 pcs", "category": "eggs", "unit": "tray", "price_eur": 39.00, "stock": 80, "lead_days": 2, "labels": ["finnish-origin", "free-range"], "cost_eur": 30.50},
    {"sku": "AIMO-3001", "name": "Wheat flour 25 kg", "category": "dry-goods", "unit": "sack", "kg": 25, "price_eur": 21.40, "stock": 90, "lead_days": 2, "labels": [], "cost_eur": 15.90},
    {"sku": "AIMO-3002", "name": "Medium-roast coffee 1 kg", "category": "dry-goods", "unit": "pack", "kg": 1, "price_eur": 14.90, "stock": 400, "lead_days": 1, "labels": ["fairtrade"], "cost_eur": 10.80},
    {"sku": "AIMO-4001", "name": "Frozen bilberries 2 kg", "category": "frozen", "unit": "bag", "kg": 2, "price_eur": 12.60, "stock": 220, "lead_days": 2, "labels": ["finnish-origin", "wild"], "cost_eur": 9.10},
    {"sku": "AIMO-5001", "name": "Potatoes, firm 15 kg", "category": "produce", "unit": "sack", "kg": 15, "price_eur": 11.20, "stock": 150, "lead_days": 1, "labels": ["finnish-origin"], "cost_eur": 7.80},
]
PUBLIC = ("sku", "name", "category", "unit", "litres", "kg", "price_eur", "lead_days", "labels")
BY_SKU = {p["sku"]: p for p in CATALOG}

BUYERS: dict[str, dict[str, Any]] = {
    "hotel-aurora": {
        "name": "Hotel Aurora (chain, 4 kitchens)",
        "agent_key": "demo-aurora-agent-key",
        "policy": {
            "auto_approve_limit_eur": 1000,  # per day, all of this buyer's agent orders together
            "categories": ["dairy", "dairy-alternatives", "eggs", "dry-goods", "produce", "frozen"],
            "max_units_per_line": 2000,
            "human_approval_for": ["over the daily limit", "a product outside the allowed categories", "a delivery later than requested"],
        },
        "approvers": ["Head chef", "Purchasing manager"],
    },
    "lagos-coop": {
        "name": "Lekki Kitchens Co-op, Lagos (export)",
        "agent_key": "demo-lagos-agent-key",
        "export": True,
        "policy": {
            "auto_approve_limit_eur": 1500,
            "categories": ["dairy", "dry-goods"],
            "max_units_per_line": 3000,
            "human_approval_for": ["over the daily limit", "a product outside the allowed categories", "a delivery later than requested", "anything that needs a cold chain"],
        },
        "approvers": ["Co-op buyer"],
    },
}
CATALOG_SYNCED = time.time()
APPROVER_KEY = "demo-approver-key"

STATE: dict[str, Any] = {"quotes": {}, "orders": {}, "audit": [], "outbox": []}


def _load() -> None:
    if DATA.exists():
        try:
            STATE.update(json.loads(DATA.read_text()))
        except Exception:
            pass


def _save() -> None:
    DATA.parent.mkdir(exist_ok=True)
    DATA.write_text(json.dumps(STATE, indent=1))


_load()


def _audit(actor: str, action: str, ref: str, detail: str, buyer: str, **extra) -> dict:
    e = {"ts": time.time(), "actor": actor, "action": action, "ref": ref, "detail": detail, "buyer": buyer, **extra}
    STATE["audit"].append(e)
    return e


def public(p: dict) -> dict:
    out = {k: p[k] for k in PUBLIC if k in p}
    out["in_stock"] = p["stock"] > 0
    if p.get("litres"):
        out["price_per_litre_eur"] = round(p["price_eur"] / p["litres"], 3)
    return out


# ------------------------------------------------------------------ dates
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def today() -> dt.date:
    return dt.date.today()


def lead(p: dict, buyer: str | None) -> int:
    return p.get("export_lead_days", p["lead_days"] + 8) if buyer and BUYERS.get(buyer, {}).get("export") else p["lead_days"]


def delivery_date(lead_days: int) -> dt.date:
    d = today() + dt.timedelta(days=lead_days)
    return d + dt.timedelta(days=1) if d.weekday() == 6 else d  # no Sunday deliveries


def parse_date(s: str | None) -> dt.date | None:
    if not s:
        return None
    s = str(s).strip().lower()
    try:
        return dt.date.fromisoformat(s[:10])
    except ValueError:
        pass
    for i, w in enumerate(WEEKDAYS):
        if w in s or s[:3] == w[:3]:
            ahead = (i - today().weekday()) % 7 or 7
            return today() + dt.timedelta(days=ahead)
    if "tomorrow" in s:
        return today() + dt.timedelta(days=1)
    return None


# ------------------------------------------------------------------ operations
def search_products(query: str = "", category: str | None = None, labels: list[str] | None = None, limit: int = 8, buyer: str | None = None) -> dict:
    words = [w for w in re.findall(r"[a-z0-9-]+", (query or "").lower()) if len(w) > 1]
    want = {l.lower() for l in (labels or [])}
    hits = []
    for p in CATALOG:
        text = (p["name"] + " " + p["category"] + " " + " ".join(p["labels"])).lower()
        score = sum(1 for w in words if w in text)
        if words and score == 0:
            continue
        if category and p["category"] != category:
            continue
        if want and not want <= set(p["labels"]):
            continue
        hits.append((score, p))
    hits.sort(key=lambda x: (-x[0], x[1]["price_eur"]))
    return {"results": [public(p) | {"delivery_date": delivery_date(lead(p, buyer)).isoformat()} for _, p in hits[: max(1, min(int(limit or 8), 20))]],
            "note": "Prices are fixed list prices in EUR excl. VAT. Availability is confirmed by a quote."}


def request_quote(buyer: str, lines: list[dict], deliver_by: str | None, actor: str, thread_id: str | None = None) -> dict:
    by = parse_date(deliver_by)
    # Idempotent: the same request while a quote is still open returns that quote.
    key = sorted((str(l.get("sku", "")).upper(), int(float(l.get("qty", 0) or 0))) for l in lines or [])
    for q in STATE["quotes"].values():
        if (q["buyer"] == buyer and q["status"] == "open" and time.time() < q["valid_until"] and q.get("deliver_by") == (by.isoformat() if by else None)
                and sorted((l.get("sku"), l.get("qty_requested")) for l in q["lines"]) == key):
            return {k: v for k, v in q.items() if k != "thread_id"} | {"valid_minutes": 30}
    qlines, total = [], 0.0
    for ln in lines or []:
        p = BY_SKU.get(str(ln.get("sku", "")).upper())
        if not p:
            qlines.append({"sku": ln.get("sku"), "error": "unknown sku"})
            continue
        qty = max(0, int(float(ln.get("qty", 0) or 0)))
        avail = min(qty, p["stock"])
        dd = delivery_date(lead(p, buyer))
        line_total = round(avail * p["price_eur"], 2)
        total += line_total
        qlines.append({"sku": p["sku"], "name": p["name"], "unit": p["unit"], "qty_requested": qty, "qty_available": avail,
                       "unit_price_eur": p["price_eur"], "line_total_eur": line_total, "delivery_date": dd.isoformat(),
                       "meets_deadline": by is None or dd <= by, "litres": (p.get("litres") or 0) * avail or None, "labels": p["labels"]})
    qid = "Q-" + uuid.uuid4().hex[:6].upper()
    q = {"id": qid, "buyer": buyer, "lines": qlines, "total_eur": round(total, 2), "deliver_by": by.isoformat() if by else None,
         "valid_until": time.time() + 1800, "created": time.time(), "thread_id": thread_id, "status": "open"}
    STATE["quotes"][qid] = q
    _audit(actor, "quote", qid, f"Quote for {len(qlines)} line(s), total €{q['total_eur']:.2f}", buyer)
    _save()
    return {k: v for k, v in q.items() if k not in ("thread_id",)} | {"valid_minutes": 30}


def _spent_today(buyer: str) -> float:
    start = dt.datetime.combine(today(), dt.time()).timestamp()
    return sum(o["total_eur"] for o in STATE["orders"].values()
               if o["buyer"] == buyer and o["created"] >= start and o["status"] in ("placed", "approved", "pending_approval"))


def evaluate_policy(buyer: str, q: dict) -> list[str]:
    pol = BUYERS[buyer]["policy"]
    reasons = []
    spent = _spent_today(buyer)
    if spent + q["total_eur"] > pol["auto_approve_limit_eur"]:
        reasons.append(f"€{q['total_eur']:.2f} takes today's agent orders to €{spent + q['total_eur']:.2f}, over the €{pol['auto_approve_limit_eur']} daily limit")
    for ln in q["lines"]:
        p = BY_SKU.get(ln.get("sku") or "")
        if p and p["category"] not in pol["categories"]:
            reasons.append(f"{p['name']} is outside the allowed categories")
        if ln.get("qty_available", 0) > pol["max_units_per_line"]:
            reasons.append(f"{ln['qty_available']} units of {ln['sku']} is over the {pol['max_units_per_line']}-unit line limit")
        if p and BUYERS[buyer].get("export") and "no-cold-chain" not in p["labels"]:
            reasons.append(f"{p['name']} needs a cold chain, which this export route cannot guarantee")
        if not ln.get("meets_deadline", True):
            reasons.append(f"{ln['sku']} arrives {ln['delivery_date']}, after the requested {q['deliver_by']}")
    return reasons


def place_order(buyer: str, quote_id: str, note: str, actor: str) -> dict:
    q = STATE["quotes"].get(str(quote_id).strip().upper())
    if not q or q["buyer"] != buyer:
        return {"error": "unknown quote for this buyer"}
    if q["status"] != "open":
        o = next((o for o in STATE["orders"].values() if o["quote_id"] == q["id"]), None)
        return {"error": "quote already used", "order": o["id"] if o else None, "status": o["status"] if o else None}
    if time.time() > q["valid_until"]:
        return {"error": "quote expired, request a new one"}
    lines = [l for l in q["lines"] if l.get("qty_available")]
    if not lines:
        return {"error": "nothing available on this quote"}
    reasons = evaluate_policy(buyer, q)
    oid = "PO-" + uuid.uuid4().hex[:6].upper()
    status = "pending_approval" if reasons else "placed"
    o = {"id": oid, "buyer": buyer, "quote_id": q["id"], "lines": lines, "total_eur": q["total_eur"], "deliver_by": q["deliver_by"],
         "status": status, "approval_reasons": reasons, "note": (note or "")[:500], "created": time.time(), "placed_by": actor,
         "thread_id": q.get("thread_id"), "decided_by": None}
    q["status"] = "used"
    STATE["orders"][oid] = o
    _audit(actor, "order", oid, f"Order for €{o['total_eur']:.2f} from quote {q['id']}" + (f". Agent's reason: {o['note']}" if o["note"] else ""), buyer)
    if reasons:
        _audit("policy-engine", "needs_approval", oid, "; ".join(reasons), buyer)
    else:
        _audit("policy-engine", "auto_approved", oid, f"Within the €{BUYERS[buyer]['policy']['auto_approve_limit_eur']} daily limit and policy", buyer)
    _save()
    return order_view(o) | {"message": "Placed." if status == "placed" else "Sent to a human approver: " + "; ".join(reasons)}


def decide(order_id: str, approve: bool, by: str, note: str = "") -> dict:
    o = STATE["orders"].get(order_id)
    if not o:
        raise KeyError(order_id)
    if o["status"] != "pending_approval":
        return order_view(o)
    o["status"] = "approved" if approve else "rejected"
    o["decided_by"] = by
    _audit(f"human:{by}", "approved" if approve else "rejected", order_id, note or ("Approved" if approve else "Rejected"), o["buyer"])
    _save()
    return order_view(o)


def order_view(o: dict) -> dict:
    return {k: v for k, v in o.items() if k != "thread_id"} | {"audit": [e for e in STATE["audit"] if e["ref"] in (o["id"], o["quote_id"])]}


def policy_view(buyer: str) -> dict:
    b = BUYERS[buyer]
    return {"buyer": buyer, "name": b["name"], **b["policy"], "spent_today_eur": round(_spent_today(buyer), 2), "approvers": b["approvers"]}


# ------------------------------------------------------------------ tools for the buyer's agent team
# Flat string arguments: small on-device models call these reliably (nested arrays/objects they do not).
TOOL_SPECS = [
    {"type": "function", "function": {"name": "search_products", "description": "Search the wholesaler's catalogue. Returns public product data: SKU, name, unit, fixed price, price per litre, labels, earliest delivery date.", "parameters": {"type": "object", "properties": {
        "query": {"type": "string", "description": "Free-text search, e.g. 'lactose-free milk'"},
        "label": {"type": "string", "description": "Optional required label, e.g. 'lactose-free' or 'long-life'"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "request_quote", "description": "Request a fixed-price quote with confirmed availability and delivery dates.", "parameters": {"type": "object", "properties": {
        "items": {"type": "string", "description": "SKU and number of units, comma-separated, e.g. 'AIMO-1002:45, AIMO-1004:550'"},
        "deliver_by": {"type": "string", "description": "Latest delivery date, ISO date or weekday name"}}, "required": ["items"]}}},
    {"type": "function", "function": {"name": "get_policy", "description": "Read this buyer's purchasing policy: what the agent may order on its own and what needs human approval.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "place_order", "description": "Place an order from a quote. The wholesaler applies the buyer's policy: within authority it is placed, otherwise it waits for a human approver.", "parameters": {"type": "object", "properties": {
        "quote_id": {"type": "string"}, "reason": {"type": "string", "description": "One sentence: why this option was chosen"}}, "required": ["quote_id", "reason"]}}},
]


def _items(args: dict) -> list[dict]:
    if isinstance(args.get("lines"), list):
        return args["lines"]
    out = []
    for part in re.split(r"[,;\n]+", str(args.get("items", ""))):
        m = re.search(r"([A-Za-z]+-\d+)\s*[:x×* ]\s*(\d+)", part)
        if m:
            out.append({"sku": m.group(1).upper(), "qty": int(m.group(2))})
    return out


NAMES = {t["function"]["name"] for t in TOOL_SPECS}


def link_up() -> bool:
    from .router import router
    return router.net_up and not router.offline


LAST: dict[str, dict] = {}  # per session: the latest quote and order, for the order summary


def run(name: str, args: dict, ctx: dict, thread_id: str) -> dict:
    """The buyer agent's tools. With no connection to the wholesaler they degrade instead of failing:
    search uses the catalogue cached on the device, quotes are provisional, and orders go to an
    outbox on the device that is sent automatically when the link returns."""
    buyer = (ctx or {}).get("buyer") or "hotel-aurora"
    actor = f"agent:{buyer}"
    online = link_up()
    last = LAST.setdefault(thread_id, {})
    if name == "search_products":
        labels = args.get("labels") or ([args["label"]] if args.get("label") else None)
        r = search_products(str(args.get("query", "")), args.get("category"), labels, buyer=buyer)
        if not online:
            r["source"] = f"Catalogue cached on this device, synced {time.strftime('%H:%M', time.localtime(CATALOG_SYNCED))}. Prices are confirmed when the order is sent."
        return r
    if name == "request_quote":
        lines = _items(args)
        deliver_by = args.get("deliver_by") or (ctx or {}).get("deliver_by")
        r = request_quote(buyer, lines, deliver_by, actor, thread_id) if online else _provisional(buyer, lines, deliver_by)
        last.setdefault("quotes", {})[r["id"]] = r
        last.update(quote=r, order=None, buyer=buyer)
        return r
    if name == "get_policy":
        return policy_view(buyer)
    if name == "place_order":
        qid = _qid(str(args.get("quote_id", ""))) or last.get("recommended") or ""
        reason = str(args.get("reason", ""))
        if qid in last.get("quotes", {}):
            last["quote"] = last["quotes"][qid]
        r = _queue(buyer, qid, reason, actor) if (not online or qid.startswith("PQ-")) else place_order(buyer, qid, reason, actor)
        if "error" not in r:
            last.update(order=r, reason=reason, buyer=buyer)
        return r
    return {"error": f"unknown tool {name}. Available tools: get_policy, place_order(quote_id, reason)."}


def _qid(text: str) -> str | None:
    m = re.search(r"\b(PQ|Q)[-\u2010\u2011\u2012\u2013]([0-9A-Fa-f]{6})\b", text or "")
    return f"{m.group(1).upper()}-{m.group(2).upper()}" if m else None


def recommend(thread_id: str, report: str) -> str | None:
    """Which quote did sourcing recommend? Read it from its report; fall back to the latest quote."""
    last = LAST.setdefault(thread_id, {})
    quotes = last.get("quotes", {})
    i = (report or "").lower().find("recommend")
    qid = _qid(report[i:]) if i >= 0 else None
    if qid not in quotes:
        ids = [f"{a.upper()}-{b.upper()}" for a, b in re.findall(r"\b(PQ|Q)[-\u2010\u2011\u2012\u2013]([0-9A-Fa-f]{6})\b", report or "")]
        qid = next((x for x in reversed(ids) if x in quotes), None) or (last.get("quote") or {}).get("id")
    if qid in quotes:
        last["quote"] = quotes[qid]
    last["recommended"] = qid
    return qid


def summary(thread_id: str) -> str | None:
    """Order summary built from what the tools actually returned - never from model text."""
    last = LAST.get(thread_id) or {}
    q, o = last.get("quote"), last.get("order")
    if not q:
        return None
    rows = []
    for l in q.get("lines", []):
        if l.get("error"):
            continue
        qty = l.get("qty_available", l.get("qty", 0))
        rows.append(f"| {l['sku']} | {l['name']} | {qty} | €{l['unit_price_eur']:.2f} | €{qty * l['unit_price_eur']:.2f} | {l['delivery_date']} |")
    table = "| SKU | Product | Units | Unit price | Line total | Delivery |\n|---|---|---|---|---|---|\n" + "\n".join(rows)
    total = f"€{q['total_eur']:,.2f}"
    if not o:
        head = f"**Quote {q['id']} ready, no order placed yet** — {total}"
        tail = ""
    elif o.get("status") == "queued_offline":
        head = f"**Saved on this device ({o['outbox_id']})** — {total}. No connection to the wholesaler right now."
        tail = "**What happens next** — the order is sent automatically when the signal returns. The wholesaler then confirms stock and price and applies your purchasing policy."
    elif o.get("status") == "pending_approval":
        head = f"**Order {o['id']} is waiting for your approval** — {total}"
        tail = "**Needs you** — " + "; ".join(o.get("approval_reasons") or []) + ". It is in the approval queue."
    else:
        head = f"**Order {o['id']} placed** — {total}"
        tail = "**Approval** — not needed: within the agent's authority."
    why = f"**Why this option** — {last['reason']}" if last.get("reason") else ""
    return "\n\n".join(x for x in (head, table, why, tail) if x)


PROVISIONAL: dict[str, dict] = {}


def _provisional(buyer: str, lines: list[dict], deliver_by: str | None) -> dict:
    by = parse_date(deliver_by)
    out, total = [], 0.0
    for ln in lines:
        p = BY_SKU.get(str(ln.get("sku", "")).upper())
        if not p:
            continue
        qty = max(0, int(float(ln.get("qty", 0) or 0)))
        dd = delivery_date(lead(p, buyer))
        total += qty * p["price_eur"]
        out.append({"sku": p["sku"], "name": p["name"], "qty": qty, "unit_price_eur": p["price_eur"], "delivery_date": dd.isoformat(), "meets_deadline": by is None or dd <= by})
    qid = "PQ-" + uuid.uuid4().hex[:6].upper()
    PROVISIONAL[qid] = {"lines": [{"sku": l["sku"], "qty": l["qty"]} for l in out], "deliver_by": deliver_by}
    return {"id": qid, "provisional": True, "lines": out, "total_eur": round(total, 2),
            "note": "No connection: provisional quote from the cached catalogue. Stock and price are confirmed when the order is sent."}


def _queue(buyer: str, qid: str, reason: str, actor: str) -> dict:
    src = PROVISIONAL.get(qid) or ({"lines": [{"sku": l["sku"], "qty": l["qty_requested"]} for l in STATE["quotes"][qid]["lines"] if l.get("sku")],
                                    "deliver_by": STATE["quotes"][qid]["deliver_by"]} if qid in STATE["quotes"] else None)
    if not src:
        return {"error": "unknown quote"}
    item = {"id": "OUT-" + uuid.uuid4().hex[:6].upper(), "buyer": buyer, "lines": src["lines"], "deliver_by": src["deliver_by"],
            "reason": reason, "created": time.time(), "actor": actor, "status": "waiting_for_signal"}
    STATE["outbox"].append(item)
    _save()
    return {"status": "queued_offline", "outbox_id": item["id"],
            "message": "No connection to the wholesaler. The order is saved on this device and will be sent automatically when the signal returns; the wholesaler then confirms stock and applies the buyer's policy."}


def flush_outbox() -> list[dict]:
    """Send orders saved while offline. Called when the link comes back."""
    sent = []
    for item in [i for i in STATE["outbox"] if i["status"] == "waiting_for_signal"]:
        q = request_quote(item["buyer"], item["lines"], item["deliver_by"], item["actor"])
        o = place_order(item["buyer"], q["id"], (item["reason"] + " (sent from the offline outbox)").strip(), item["actor"])
        item.update(status="sent", order_id=o.get("id"), sent_at=time.time())
        _audit("device-outbox", "synced", o.get("id") or item["id"], f"Saved offline at {time.strftime('%H:%M:%S', time.localtime(item['created']))}, sent when the signal returned", item["buyer"])
        sent.append(o)
    if sent:
        _save()
    return sent


# ------------------------------------------------------------------ HTTP interface for external agents
api = APIRouter(prefix="/trade/v1", tags=["trade"])


def _buyer(key: str | None) -> str:
    for bid, b in BUYERS.items():
        if key and key == b["agent_key"]:
            return bid
    raise HTTPException(401, "Unknown agent key")


class QuoteLine(BaseModel):
    sku: str
    qty: int = Field(gt=0)


class QuoteBody(BaseModel):
    lines: list[QuoteLine]
    deliver_by: str | None = None


class OrderBody(BaseModel):
    quote_id: str
    reason: str = ""


class Decision(BaseModel):
    approve: bool
    by: str = "Purchasing manager"
    note: str = ""


@api.get("/catalog/search", summary="Search products (public fields only)")
async def http_search(q: str = "", category: str | None = None, label: str | None = None, x_agent_key: str | None = Header(None)):
    return search_products(q, category, [label] if label else None, buyer=_buyer(x_agent_key))


@api.post("/quotes", summary="Fixed-price quote with confirmed availability")
async def http_quote(body: QuoteBody, x_agent_key: str | None = Header(None)):
    b = _buyer(x_agent_key)
    return request_quote(b, [l.model_dump() for l in body.lines], body.deliver_by, f"agent:{b}")


@api.post("/orders", summary="Place an order; the buyer's policy decides if a human must approve")
async def http_order(body: OrderBody, x_agent_key: str | None = Header(None)):
    b = _buyer(x_agent_key)
    r = place_order(b, body.quote_id, body.reason, f"agent:{b}")
    if "error" in r:
        raise HTTPException(409, r["error"])
    return r


@api.get("/orders", summary="This buyer's orders")
async def http_orders(x_agent_key: str | None = Header(None)):
    b = _buyer(x_agent_key)
    return [order_view(o) for o in STATE["orders"].values() if o["buyer"] == b][::-1]


@api.get("/orders/{oid}", summary="One order with its audit trail")
async def http_order_get(oid: str, x_agent_key: str | None = Header(None)):
    b = _buyer(x_agent_key)
    o = STATE["orders"].get(oid)
    if not o or o["buyer"] != b:
        raise HTTPException(404, "Unknown order")
    return order_view(o)


@api.get("/policy", summary="What this agent may decide on its own")
async def http_policy(x_agent_key: str | None = Header(None)):
    return policy_view(_buyer(x_agent_key))


# Human side (the buyer's approvers). Agents cannot call these: they need an approver key.
@api.get("/approvals", summary="Orders waiting for a human (approver key)")
async def http_approvals(x_approver_key: str | None = Header(None)):
    if x_approver_key != APPROVER_KEY:
        raise HTTPException(403, "Approver key required")
    return [order_view(o) for o in STATE["orders"].values()][::-1]


@api.post("/approvals/{oid}", summary="Approve or reject an order (approver key)")
async def http_decide(oid: str, body: Decision, x_approver_key: str | None = Header(None)):
    if x_approver_key != APPROVER_KEY:
        raise HTTPException(403, "Approver key required")
    try:
        return decide(oid, body.approve, body.by, body.note)
    except KeyError:
        raise HTTPException(404, "Unknown order")


@api.get("/audit", summary="Append-only audit trail (approver key)")
async def http_audit(x_approver_key: str | None = Header(None)):
    if x_approver_key != APPROVER_KEY:
        raise HTTPException(403, "Approver key required")
    return STATE["audit"][-200:][::-1]


AGENT_CARD = {
    "name": "Valio Aimo trade interface (mock)",
    "description": "Mock wholesaler interface for external purchasing agents: catalogue search, fixed-price quotes, orders with buyer-policy approval. Junction 2026 prototype; not real data.",
    "version": "0.1.0",
    "openapi": "/openapi.json",
    "auth": {"agents": "X-Agent-Key header, bound to one buyer", "humans": "X-Approver-Key header for approvals"},
    "skills": [
        {"id": "search", "endpoint": "GET /trade/v1/catalog/search", "description": "Product, price and delivery lookup"},
        {"id": "quote", "endpoint": "POST /trade/v1/quotes", "description": "Fixed-price quote with confirmed availability, valid 30 minutes"},
        {"id": "order", "endpoint": "POST /trade/v1/orders", "description": "Order from a quote; over-authority orders wait for a human"},
        {"id": "policy", "endpoint": "GET /trade/v1/policy", "description": "The agent's own authority"},
    ],
    "guarantees": ["Only public product fields are exposed", "Policy is enforced by the wholesaler, not the agent",
                   "Same-day orders count together against the limit", "Every action is in an audit trail"],
}


def reset() -> None:
    STATE.update({"quotes": {}, "orders": {}, "audit": [], "outbox": []})
    _save()
