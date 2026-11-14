"""HybridEdge under link loss: the same GSM8K / BFCL items, but on a fraction of them the cloud
link drops mid-answer. Those items are restarted on the device, exactly as the router does
(the step re-runs on the on-device model from the last checkpoint).

    uv run python bench/hybrid.py --outage 0.3
Results are merged into bench/results.json under "hybrid".
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bench.models import DATA, _tools, bfcl_correct, client, gsm8k_extract, _num, merge, req_kwargs  # noqa: E402

GSM_SYS = "Solve the maths word problem. Show brief working, then finish with a final line 'Answer: <number>'."


async def cloud_then_cut(c, model, kw, cut_after_s: float) -> float:
    """Start the request on the cloud and drop the link after ``cut_after_s``. Returns time lost."""
    t = time.time()
    try:
        stream = await c.chat.completions.create(model=model, stream=True, **kw)
        async for _ in stream:
            if time.time() - t >= cut_after_s:
                break
        await stream.close()
    except Exception:
        pass
    return time.time() - t


async def run(outage: float, n_gsm: int, n_bfcl: int, seed: int = 7) -> dict:
    rng = random.Random(seed)
    cc, cmodel, _ = client("cloud")
    dc, dmodel, _ = client("device")
    csem, dsem = asyncio.Semaphore(16), asyncio.Semaphore(1)

    async def item(messages, tools, grade):
        cut = rng.random() < outage
        cut_at = rng.uniform(1.0, 4.0)
        t0 = time.time()
        if cut:
            async with csem:
                await cloud_then_cut(cc, cmodel, {"messages": messages, **({"tools": tools} if tools else {}), **req_kwargs("cloud", 512)}, cut_at)
            async with dsem:
                resp = await dc.chat.completions.create(model=dmodel, messages=messages, **({"tools": tools} if tools else {}), temperature=0, **req_kwargs("device", 512))
        else:
            async with csem:
                resp = await cc.chat.completions.create(model=cmodel, messages=messages, **({"tools": tools} if tools else {}), temperature=0, **req_kwargs("cloud", 512))
        return {"ok": grade(resp.choices[0].message), "cut": cut, "s": time.time() - t0}

    out = {"outage": outage, "seed": seed}
    rows = [json.loads(l) for l in (DATA / "gsm8k_test.jsonl").read_text().splitlines()[:n_gsm]]

    def gsm_grader(r):
        gold = _num(r["answer"].split("####")[-1])
        return lambda m: (lambda p: p is not None and abs(p - gold) < 1e-6)(gsm8k_extract(m.content or ""))

    jobs = {"gsm8k": [item([{"role": "system", "content": GSM_SYS}, {"role": "user", "content": r["question"]}], None, gsm_grader(r)) for r in rows]}
    for cat in ("simple", "multiple", "parallel"):
        qs = [json.loads(l) for l in (DATA / f"BFCL_v3_{cat}.json").read_text().splitlines()[:n_bfcl]]
        ans = {a["id"]: a["ground_truth"] for a in map(json.loads, (DATA / f"ans_BFCL_v3_{cat}.json").read_text().splitlines())}
        lst = []
        for q in qs:
            tools, names = _tools(q["function"])

            def grade(m, names=names, gt=ans[q["id"]]):
                calls = []
                for tc in m.tool_calls or []:
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    calls.append({"name": names.get(tc.function.name, tc.function.name), "args": args})
                return bfcl_correct(calls, gt)
            lst.append(item(q["question"][0], tools, grade))
        jobs[f"bfcl_{cat}"] = lst
    for k, js in jobs.items():
        res = await asyncio.gather(*js)
        out[k] = {"n": len(res), "accuracy": round(100 * sum(r["ok"] for r in res) / len(res), 1), "cut": sum(r["cut"] for r in res),
                  "completed": len(res)}
        print(k, out[k], flush=True)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--outage", type=float, default=0.3)
    ap.add_argument("--gsm8k", type=int, default=60)
    ap.add_argument("--bfcl", type=int, default=40)
    a = ap.parse_args()
    merge(["hybrid"], asyncio.run(run(a.outage, a.gsm8k, a.bfcl)))
