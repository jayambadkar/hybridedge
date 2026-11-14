"""Unit checks: device network-loss detection and capacity-aware overflow routing."""
import asyncio
import hybridedge.router as R

async def main():
    r = R.Router()
    v, m = r.by_name["verda"], r.by_name["modal"]
    for s in (v, m):
        s.reachable = True
    # --- overflow routing
    assert r.pick() == "verda", r.pick()
    v.inflight = v.p.capacity                      # Verda full
    assert r.pick() == "modal", "overflow should go to Modal"
    assert not r._standby_idle(m), "standby must wake when primary is saturated"
    v.inflight = 0
    assert r._standby_idle(m), "standby should idle when primary has capacity"
    assert r.pick(exclude=["verda"]) == "modal"
    v.inflight, m.inflight = v.p.capacity, m.p.capacity // 2
    assert r.pick() == "modal", "both full-ish -> least loaded"
    v.inflight = m.inflight = 0
    print("overflow routing: OK")

    # --- device network loss: patch the route check, run the watcher briefly
    r._refresh()
    cut = v.cut
    R.has_network_route = lambda: False
    task = asyncio.create_task(r._net_loop())
    await asyncio.sleep(0.7)
    assert r.net_up is False and cut.is_set(), "cloud streams must be cut on network loss"
    assert r.pick() == R.EDGE and r.status()["network"]["up"] is False
    print("network loss -> instant cut, routing to device: OK")
    checked = asyncio.Event()
    async def fake_check():
        checked.set()
    r.check = fake_check
    R.has_network_route = lambda: True
    await asyncio.sleep(0.7)
    assert r.net_up and checked.is_set(), "network return must trigger an immediate re-probe"
    assert r.status()["reconnecting"] is True
    print("network return -> immediate re-probe, 'reconnecting' state: OK")
    task.cancel()
    await r._http.aclose()

asyncio.run(main())
