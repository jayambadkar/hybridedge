"""Store operations for the Aimo Trade console (web/trade/): stock, sales, supply routes and margins,
plus the planning agent team that turns them into a weekly operating plan.

The default store is a distributor in a region where conflict has cut the fixed network: the link is
a shared satellite terminal that drops for hours, roads close at short notice, and the store still
has to buy well and price competitively. All data is MOCK data for the Junction 2026 prototype.
"""
from __future__ import annotations

import datetime as dt
import random
from typing import Any

from fastapi import APIRouter, HTTPException

from . import trade

trade.BUYERS.setdefault("eastgate", {
    "name": "Eastgate Provisions (field distribution, 3 depots)",
    "agent_key": "demo-eastgate-agent-key",
    "export": True,
    "policy": {
        "auto_approve_limit_eur": 2500,
        "categories": ["dairy", "dairy-alternatives", "dry-goods", "eggs", "produce", "frozen"],
        "max_units_per_line": 4000,
        "human_approval_for": ["over the daily limit", "a product outside the allowed categories", "a delivery later than requested", "anything that needs a cold chain"],
    },
    "approvers": ["Depot manager"],
})

STORES: dict[str, dict[str, Any]] = {
    "eastgate": {
        "name": "Eastgate Provisions", "place": "Eastern region · 3 depots", "currency": "EUR",
        "link": "Shared satellite terminal, 1–3 drops a day", "customers": "Shops, field kitchens, a hospital canteen",
        "stock": {"AIMO-1005": 420, "AIMO-1006": 35, "AIMO-1070": 6, "AIMO-3001": 14, "AIMO-3002": 22, "AIMO-4001": 9, "AIMO-5001": 30, "AIMO-2001": 4, "AIMO-1050": 140},
        "daily": {"AIMO-1005": 95, "AIMO-1006": 9, "AIMO-1070": 1.6, "AIMO-3001": 3.1, "AIMO-3002": 2.2, "AIMO-4001": 1.1, "AIMO-5001": 6.5, "AIMO-2001": 1.4, "AIMO-1050": 18},
        "sell_markup": {"AIMO-1005": 1.32, "AIMO-1006": 1.30, "AIMO-1070": 1.38, "AIMO-3001": 1.28, "AIMO-3002": 1.45, "AIMO-4001": 1.35, "AIMO-5001": 1.25, "AIMO-2001": 1.30, "AIMO-1050": 1.33},
        "market": {"AIMO-1005": 1.69, "AIMO-1006": 19.90, "AIMO-1070": 139.00, "AIMO-3001": 26.00, "AIMO-3002": 24.00, "AIMO-4001": 17.50, "AIMO-5001": 13.00, "AIMO-2001": 52.00, "AIMO-1050": 4.50},
        "trend": 1.18,  # demand rising: displaced families arriving in the depot towns
        "routes": [
            {"id": "R1", "name": "Northern road via the border crossing", "status": "open", "transit_days": 3, "risk": "low", "note": "Crossing open 06–20. Queues of 6–10 h on Mondays.", "updated": "2 h ago"},
            {"id": "R2", "name": "Eastern highway, direct", "status": "closed", "transit_days": 2, "risk": "high", "note": "Bridge damaged. Closed until further notice.", "updated": "40 min ago"},
            {"id": "R3", "name": "Rail freight to the regional hub, then trucks", "status": "disrupted", "transit_days": 5, "risk": "medium", "note": "Runs every other day. No reefer wagons: no cold chain.", "updated": "5 h ago"},
        ],
    },
    "lagos-coop": {
        "name": "Lekki Kitchens Co-op", "place": "Lagos · 6 kitchens", "currency": "EUR",
        "link": "Patchy 3G, frequent drops", "customers": "Restaurants and school kitchens",
        "stock": {"AIMO-1005": 300, "AIMO-1070": 3, "AIMO-3001": 8, "AIMO-3002": 10},
        "daily": {"AIMO-1005": 60, "AIMO-1070": 0.8, "AIMO-3001": 1.5, "AIMO-3002": 1.2},
        "sell_markup": {"AIMO-1005": 1.4, "AIMO-1070": 1.35, "AIMO-3001": 1.3, "AIMO-3002": 1.5},
        "market": {"AIMO-1005": 1.95, "AIMO-1070": 128.0, "AIMO-3001": 29.0, "AIMO-3002": 23.0},
        "trend": 1.06,
        "routes": [
            {"id": "R1", "name": "Sea freight to Apapa port", "status": "open", "transit_days": 9, "risk": "low", "note": "Weekly sailing on Thursdays.", "updated": "1 day ago"},
            {"id": "R2", "name": "Air freight to Lagos airport", "status": "open", "transit_days": 2, "risk": "low", "note": "About 4x the cost of sea freight.", "updated": "1 day ago"},
        ],
    },
    "hotel-aurora": {
        "name": "Hotel Aurora", "place": "Helsinki · 4 kitchens", "currency": "EUR",
        "link": "Fibre and 5G", "customers": "Hotel restaurants and room service",
        "stock": {"AIMO-1001": 260, "AIMO-1030": 30, "AIMO-1050": 80, "AIMO-2001": 3, "AIMO-3002": 12},
        "daily": {"AIMO-1001": 70, "AIMO-1030": 6, "AIMO-1050": 9, "AIMO-2001": 1.1, "AIMO-3002": 1.8},
        "sell_markup": {"AIMO-1001": 2.6, "AIMO-1030": 2.4, "AIMO-1050": 2.2, "AIMO-2001": 2.0, "AIMO-3002": 3.0},
        "market": {},
        "trend": 0.97,
        "routes": [{"id": "R1", "name": "Wholesaler van, next day", "status": "open", "transit_days": 1, "risk": "low", "note": "Daily delivery before 07:00.", "updated": "today"}],
    },
}


def _store(buyer: str) -> dict:
    if buyer not in STORES:
        raise KeyError(buyer)
    return STORES[buyer]


def _product(sku: str) -> dict:
    return trade.BY_SKU[sku]


def sales_series(buyer: str, days: int = 28) -> list[dict]:
    s = _store(buyer)
    rng = random.Random(buyer)
    base = sum(s["daily"][k] * _product(k)["price_eur"] * s["sell_markup"][k] for k in s["daily"])
    out, today = [], dt.date.today()
    for i in range(days):
        d = today - dt.timedelta(days=days - 1 - i)
        growth = s["trend"] ** (i / 7)  # weekly trend, compounding
        weekday = 0.8 if d.weekday() == 6 else 1.0
        rev = base * growth * weekday * rng.uniform(0.85, 1.15)
        out.append({"date": d.isoformat(), "revenue_eur": round(rev, 2), "orders": max(1, round(rev / 180 * rng.uniform(0.8, 1.2)))})
    return out


def stock_position(buyer: str) -> dict:
    s = _store(buyer)
    lead = max(1, min(r["transit_days"] for r in s["routes"] if r["status"] != "closed"))
    rows = []
    for sku, qty in s["stock"].items():
        p = _product(sku)
        daily = s["daily"][sku] * s["trend"]
        cover = round(qty / daily, 1) if daily else None
        reorder = cover is not None and cover < lead + 5
        rows.append({"sku": sku, "name": p["name"], "on_hand": qty, "unit": p["unit"], "sold_per_day": round(daily, 1), "days_of_cover": cover,
                     "status": "out soon" if cover is not None and cover < lead else "reorder" if reorder else "ok",
                     "suggested_units": max(0, round(daily * (lead + 14) - qty)) if reorder else 0, "cold_chain": "no-cold-chain" not in p["labels"] and p["category"] in ("dairy", "eggs", "frozen") and "long-life" not in p["labels"]})
    rows.sort(key=lambda r: r["days_of_cover"] if r["days_of_cover"] is not None else 999)
    return {"store": s["name"], "shortest_open_route_days": lead, "items": rows,
            "rule": "Reorder when days of cover < shortest open route + 5 days of buffer. Suggested quantity covers transit plus 14 days."}


def sales_trend(buyer: str) -> dict:
    ser = sales_series(buyer)
    last, prev = ser[-7:], ser[-14:-7]
    lw, pw = sum(d["revenue_eur"] for d in last), sum(d["revenue_eur"] for d in prev)
    s = _store(buyer)
    movers = sorted(s["daily"], key=lambda k: s["daily"][k] * _product(k)["price_eur"], reverse=True)[:3]
    return {"store": s["name"], "revenue_last_7_days_eur": round(lw), "revenue_previous_7_days_eur": round(pw), "change_pct": round((lw / pw - 1) * 100, 1),
            "orders_last_7_days": sum(d["orders"] for d in last), "top_products": [_product(k)["name"] for k in movers],
            "demand_note": {"eastgate": "Demand is rising: families displaced from the east are arriving in the depot towns.",
                            "lagos-coop": "Steady growth from new school-kitchen contracts.", "hotel-aurora": "Seasonal dip after the conference season."}[buyer]}


def route_status(buyer: str) -> dict:
    s = _store(buyer)
    open_ = [r for r in s["routes"] if r["status"] != "closed"]
    best = min(open_, key=lambda r: (r["risk"] != "low", r["transit_days"])) if open_ else None
    return {"store": s["name"], "routes": s["routes"], "recommended": best and best["id"],
            "cold_chain_possible": any(r["status"] == "open" and "No cold chain" not in r["note"] for r in s["routes"]),
            "advice": "Prefer long-life products when the only open routes cannot keep a cold chain."}


def margin_report(buyer: str) -> dict:
    s = _store(buyer)
    rows = []
    for sku, mk in s["sell_markup"].items():
        p = _product(sku)
        sell = round(p["price_eur"] * mk, 2)
        mkt = s["market"].get(sku)
        rows.append({"sku": sku, "name": p["name"], "buy_eur": p["price_eur"], "sell_eur": sell, "margin_pct": round((1 - p["price_eur"] / sell) * 100, 1),
                     "local_market_eur": mkt, "vs_market_pct": round((sell / mkt - 1) * 100, 1) if mkt else None})
    return {"store": s["name"], "items": rows,
            "note": "Local market prices are the last prices seen at the two nearest competitors. Stay at or below them on staples to keep customers."}


FUNCS = {"stock_position": stock_position, "sales_trend": sales_trend, "route_status": route_status, "margin_report": margin_report}
_NOARGS = {"type": "object", "properties": {}}
TOOL_SPECS = [
    {"type": "function", "function": {"name": "stock_position", "description": "Stock on hand, sales per day and days of cover for every product, with reorder suggestions.", "parameters": _NOARGS}},
    {"type": "function", "function": {"name": "sales_trend", "description": "Revenue and orders this week vs last week, top products and the demand note.", "parameters": _NOARGS}},
    {"type": "function", "function": {"name": "route_status", "description": "Supply routes: open, disrupted or closed, transit days, risk and cold-chain limits.", "parameters": _NOARGS}},
    {"type": "function", "function": {"name": "margin_report", "description": "Buy price, sell price, margin and local competitor price per product.", "parameters": _NOARGS}},
]
NAMES = set(FUNCS)


def run(name: str, args: dict, ctx: dict) -> dict:
    buyer = (ctx or {}).get("buyer") or "eastgate"
    return FUNCS[name](buyer if buyer in STORES else "eastgate")


ROLES: dict[str, dict[str, Any]] = {
    "demand": {
        "title": "Demand and stock",
        "brief": "Works out what will run out and when. Call stock_position, then sales_trend.",
        "tools": ["stock_position", "sales_trend"], "must": "stock_position",
        "report": "### Demand\nOne or two sentences with the weekly change.\n### Running low\nTable: product | on hand | days of cover | suggested units.",
    },
    "supply": {
        "title": "Supply routes",
        "brief": "Works out how goods can reach the depots this week. Call route_status. If the open routes have no cold chain, prefer long-life products (call search_products with label long-life).",
        "tools": ["route_status", "search_products"], "must": "route_status",
        "report": "### Routes\nTable: route | status | transit days | risk.\n### Recommendation\nWhich route to use and what it means for what we can buy.",
    },
    "pricing": {
        "title": "Pricing and margin",
        "brief": "Checks that prices stay competitive and margins healthy. Call margin_report.",
        "tools": ["margin_report"], "must": "margin_report",
        "report": "### Prices\nTable: product | our price | local market | margin.\n### Changes\nBullets: price changes to make and why.",
    },
}

SYNTH = """You are the operations lead of a distributor. Write THIS WEEK'S OPERATING PLAN for the owner from the reports below.
Use exactly these short sections:
**Summary** — two sentences: the situation and the main decision.
**Plan for the next 7 days** — table: action | why | owner (Purchasing agent, Depot manager, Pricing).
**Risks** — up to three bullets.
**Orders to place** — one line per order, each starting with "ORDER:" followed by a plain request a purchasing agent can act on,
for example "ORDER: 1,200 L of long-life lactose-free milk delivered within 10 days".
Use the figures from the reports exactly. Do not invent products, prices or routes. Current local time: {now}."""

REVIEW = """You review a distributor's weekly plan. Read the specialists' reports and find real problems only: an order that needs a cold chain
when no open route has one, quantities that disagree with the stock figures, a price change that would put us above the local market,
a closed route being relied on. Ignore style.
Respond with JSON only:
{"verdict": "<one sentence>", "issues": [{"severity": "high|medium|low", "owner": "<specialist role id>", "issue": "<what is wrong>", "fix": "<what to change>"}]}
Specialist role ids: {roles}."""

PROFILE = {
    "label": "Operations planning team",
    "lead": "operations lead of a food distributor",
    "roles": ROLES,
    "plan_extra": "",
    "fixed": [
        {"role": "demand", "task": "Find what will run out and how much to reorder."},
        {"role": "supply", "task": "Find which supply routes are usable this week and what that means for what we buy."},
        {"role": "pricing", "task": "Check our prices against the local market and margins."},
    ],
    "fallback": [
        {"role": "demand", "task": "Find what will run out and how much to reorder."},
        {"role": "supply", "task": "Find which supply routes are usable this week and what that means for what we buy."},
    ],
    "synth": SYNTH,
    "review": True,
    "review_prompt": REVIEW,
}


api = APIRouter(prefix="/api/trade", tags=["trade"])


@api.get("/store")
async def http_store(buyer: str = "eastgate"):
    try:
        s = _store(buyer)
    except KeyError:
        raise HTTPException(404, "unknown store")
    orders = [trade.order_view(o) for o in trade.STATE["orders"].values() if o["buyer"] == buyer][::-1]
    return {"buyer": buyer, **{k: s[k] for k in ("name", "place", "link", "customers", "currency")}, "sales": sales_series(buyer), "trend": sales_trend(buyer),
            "stock": stock_position(buyer), "routes": route_status(buyer), "margins": margin_report(buyer), "orders": orders,
            "outbox": [i for i in trade.STATE["outbox"] if i["buyer"] == buyer][::-1], "policy": trade.policy_view(buyer),
            "agent_key": trade.BUYERS[buyer]["agent_key"]}
