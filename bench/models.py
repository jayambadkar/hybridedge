"""Model benchmarks: GSM8K (maths reasoning) and BFCL v3 (function calling), on the cloud and on the device.

Each tier runs in the configuration HybridEdge uses in production:
  cloud  = Nemotron 3.5 Lightning 30B-A3B on Verda (vLLM, reasoning on)
  device = Nemotron 3.5 Bolt E4B on this laptop (Ollama, reasoning off)

    uv run python bench/models.py --tier cloud --gsm8k 100 --bfcl 50
    uv run python bench/models.py --tier device --gsm8k 40 --bfcl 25
Results are merged into bench/results.json.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import string
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from openai import AsyncOpenAI  # noqa: E402

from hybridedge.config import settings  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "datasets" / "bench"
OUT = Path(__file__).resolve().parent / "results.json"


def client(tier: str):
    if tier == "cloud":
        p = settings.providers[0]
        return AsyncOpenAI(base_url=p.base_url, api_key=p.api_key, timeout=300), p.model, 16
    return AsyncOpenAI(base_url=settings.edge_base_url, api_key="ollama", timeout=300), settings.edge_model, 1


def req_kwargs(tier: str, max_tokens: int) -> dict:
    if tier == "cloud":
        return {"max_tokens": max_tokens + 4096, "extra_body": {"chat_template_kwargs": {"enable_thinking": True}}}
    return {"max_tokens": max_tokens, "reasoning_effort": "none"}


# ---------------------------------------------------------------- GSM8K
def _num(s: str) -> float | None:
    s = s.replace(",", "").replace("$", "").strip().rstrip(".")
    try:
        return float(s)
    except ValueError:
        return None


def gsm8k_extract(text: str) -> float | None:
    m = re.findall(r"Answer:\s*\**\s*(-?[\d,]*\.?\d+)", text)
    if not m:
        m = re.findall(r"\\boxed\{(-?[\d,]*\.?\d+)\}", text) or re.findall(r"(-?[\d,]*\.?\d+)", text)
    return _num(m[-1]) if m else None


async def run_gsm8k(tier: str, n: int) -> dict:
    rows = [json.loads(l) for l in (DATA / "gsm8k_test.jsonl").read_text().splitlines()[:n]]
    c, model, conc = client(tier)
    sem = asyncio.Semaphore(conc)
    t0 = time.time()

    async def one(r):
        gold = _num(r["answer"].split("####")[-1])
        async with sem:
            t = time.time()
            try:
                resp = await c.chat.completions.create(model=model, messages=[
                    {"role": "system", "content": "Solve the maths word problem. Show brief working, then finish with a final line 'Answer: <number>'."},
                    {"role": "user", "content": r["question"]}], temperature=0, **req_kwargs(tier, 512))
                text = resp.choices[0].message.content or ""
                toks = resp.usage.completion_tokens if resp.usage else 0
            except Exception as e:  # count as wrong
                text, toks = f"ERROR {e}", 0
        pred = gsm8k_extract(text)
        return {"ok": pred is not None and gold is not None and abs(pred - gold) < 1e-6, "s": time.time() - t, "tokens": toks}

    res = await asyncio.gather(*(one(r) for r in rows))
    return _summary(res, time.time() - t0)


# ---------------------------------------------------------------- BFCL (AST match, as in the official checker)
TYPE_MAP = {"dict": "object", "float": "number", "tuple": "array", "any": "string", "integer": "integer", "string": "string", "boolean": "boolean", "array": "array"}


def _schema(p: dict) -> dict:
    p = dict(p)
    p["type"] = TYPE_MAP.get(p.get("type", "string"), "string")
    if "properties" in p:
        p["properties"] = {k: _schema(v) for k, v in p["properties"].items()}
    if "items" in p:
        p["items"] = _schema(p["items"])
    p.pop("optional", None)
    if p["type"] != "object":
        p.pop("required", None)
    return p


def _tools(funcs: list[dict]) -> tuple[list[dict], dict]:
    tools, names = [], {}
    for f in funcs:
        safe = re.sub(r"[^a-zA-Z0-9_-]", "_", f["name"])
        names[safe] = f["name"]
        tools.append({"type": "function", "function": {"name": safe, "description": f["description"], "parameters": _schema(f["parameters"])}})
    return tools, names


def _norm_str(s: str) -> str:
    return re.sub(rf"[{re.escape(string.punctuation)}\s]", "", s.lower())


def _val_ok(v, allowed: list) -> bool:
    for a in allowed:
        if a == "" and v in (None, ""):
            return True
        if isinstance(a, bool) or isinstance(v, bool):
            if a is v:
                return True
            continue
        if isinstance(a, (int, float)) and isinstance(v, (int, float)) and abs(float(a) - float(v)) < 1e-9:
            return True
        if isinstance(a, str) and isinstance(v, str) and _norm_str(a) == _norm_str(v):
            return True
        if isinstance(a, list) and isinstance(v, list) and len(a) == len(v) and all(_val_ok(x, [y]) or x == y for x, y in zip(v, a)):
            return True
        if isinstance(a, dict) and isinstance(v, dict):
            if all(_val_ok(v.get(k), av if isinstance(av, list) else [av]) for k, av in a.items()):
                return True
        if a == v:
            return True
    return False


def _call_ok(call: dict, gt: dict) -> bool:
    (gname, gparams), = gt.items()
    if call["name"] != gname:
        return False
    args = call["args"]
    for k in args:
        if k not in gparams:
            return False
    for k, allowed in gparams.items():
        if k not in args:
            if "" not in allowed:
                return False
        elif not _val_ok(args[k], allowed):
            return False
    return True


def bfcl_correct(calls: list[dict], gts: list[dict]) -> bool:
    if len(calls) != len(gts):
        return False
    left = list(gts)
    for c in calls:
        hit = next((g for g in left if _call_ok(c, g)), None)
        if hit is None:
            return False
        left.remove(hit)
    return True


async def run_bfcl(tier: str, cat: str, n: int) -> dict:
    qs = [json.loads(l) for l in (DATA / f"BFCL_v3_{cat}.json").read_text().splitlines()[:n]]
    ans = {a["id"]: a["ground_truth"] for a in map(json.loads, (DATA / f"ans_BFCL_v3_{cat}.json").read_text().splitlines())}
    c, model, conc = client(tier)
    sem = asyncio.Semaphore(conc)
    t0 = time.time()

    async def one(q):
        tools, names = _tools(q["function"])
        async with sem:
            t = time.time()
            try:
                resp = await c.chat.completions.create(model=model, messages=q["question"][0], tools=tools, tool_choice="auto", temperature=0, **req_kwargs(tier, 512))
                m = resp.choices[0].message
                calls = []
                for tc in m.tool_calls or []:
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    calls.append({"name": names.get(tc.function.name, tc.function.name), "args": args})
                toks = resp.usage.completion_tokens if resp.usage else 0
            except Exception:
                calls, toks = [], 0
        return {"ok": bfcl_correct(calls, ans[q["id"]]), "s": time.time() - t, "tokens": toks}

    res = await asyncio.gather(*(one(q) for q in qs))
    return _summary(res, time.time() - t0)


def _summary(res: list[dict], wall: float) -> dict:
    lat = sorted(r["s"] for r in res)
    return {"n": len(res), "correct": sum(r["ok"] for r in res), "accuracy": round(100 * sum(r["ok"] for r in res) / max(1, len(res)), 1),
            "p50_s": round(lat[len(lat) // 2], 2) if lat else None, "wall_s": round(wall, 1),
            "tokens": sum(r["tokens"] for r in res)}


def merge(path: list[str], value) -> None:
    data = json.loads(OUT.read_text()) if OUT.exists() else {}
    d = data
    for k in path[:-1]:
        d = d.setdefault(k, {})
    d[path[-1]] = value
    data["updated"] = time.strftime("%Y-%m-%d %H:%M")
    OUT.write_text(json.dumps(data, indent=1))


async def main(a):
    if a.gsm8k:
        r = await run_gsm8k(a.tier, a.gsm8k)
        print("gsm8k", a.tier, r, flush=True)
        merge(["models", a.tier, "gsm8k"], r)
    for cat in ("simple", "multiple", "parallel"):
        if a.bfcl:
            r = await run_bfcl(a.tier, cat, a.bfcl)
            print("bfcl", cat, a.tier, r, flush=True)
            merge(["models", a.tier, f"bfcl_{cat}"], r)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", choices=["cloud", "device"], required=True)
    ap.add_argument("--gsm8k", type=int, default=0)
    ap.add_argument("--bfcl", type=int, default=0)
    asyncio.run(main(ap.parse_args()))
