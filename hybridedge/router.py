"""Multi-cloud + edge model router with link monitoring and mid-stream failover.

Targets, in priority order: every healthy cloud provider (e.g. Verda, then Modal),
then the on-device edge model (Ollama). Any cloud failure -- connection error,
5xx, stalled stream, simulated outage, or the link being cut while tokens are
flowing -- raises ``LinkLost`` so the caller re-runs the *same* step on the next
target, from the same checkpointed state.
"""
from __future__ import annotations

import asyncio
import json
import socket
import time
import uuid
from typing import Any, Awaitable, Callable

import httpx
import openai
from openai import AsyncOpenAI

from .config import Provider, settings

Emit = Callable[[dict], None]
EDGE = "edge"


def has_network_route() -> bool:
    """True if this device has a usable default route. A UDP ``connect`` sends no packets,
    it only asks the OS for a route, so this is instant and safe to call twice a second.
    Turning Wi-Fi off / losing cellular removes the route and this flips immediately."""
    for family, addr in ((socket.AF_INET, ("1.1.1.1", 53)), (socket.AF_INET6, ("2606:4700:4700::1111", 53))):
        try:
            with socket.socket(family, socket.SOCK_DGRAM) as sk:
                sk.connect(addr)
                ip = sk.getsockname()[0]
                if not (ip.startswith("127.") or ip == "::1"):
                    return True
        except OSError:
            continue
    return False


class LinkLost(Exception):
    """A cloud target became unusable while (or before) serving a request."""


class ProviderState:
    def __init__(self, p: Provider) -> None:
        self.p = p
        self.client = AsyncOpenAI(
            base_url=p.base_url or "http://invalid.local/v1",
            api_key=p.api_key or "missing",
            max_retries=0,
            timeout=httpx.Timeout(connect=6.0, read=settings.first_token_timeout_s + 5, write=10.0, pool=6.0),
        )
        self.reachable = False
        self.inflight = 0  # generations currently streaming from this provider
        self.served = 0  # generations completed (for the settings view)
        self.disabled = False  # operator-simulated outage
        self.latency_ms: float | None = None
        self.fails = 0
        self.last_error: str | None = None if p.configured else "not configured"
        self.cut = asyncio.Event()


class Router:
    def __init__(self) -> None:
        self.providers = [ProviderState(p) for p in settings.providers]
        self.by_name = {s.p.name: s for s in self.providers}
        self.edge = AsyncOpenAI(base_url=settings.edge_base_url, api_key="ollama", max_retries=0, timeout=300.0)
        self.edge_sem = asyncio.Semaphore(settings.edge_concurrency)
        self._http = httpx.AsyncClient()
        self.offline = False  # operator-simulated total link loss
        self.net_up = has_network_route()  # does this device have any network at all?
        self.net_changed_at = time.time()
        self.edge_ok = False
        self.edge_inflight = 0
        self.changed_at = time.time()
        self.last_check: float | None = None
        self._active = self.active
        self._tasks: list[asyncio.Task] = []

    # ------------------------------------------------------------------ status
    def usable(self, s: ProviderState) -> bool:
        return self.net_up and not self.offline and not s.disabled and s.reachable and s.p.configured

    def online_targets(self) -> list[str]:
        return [s.p.name for s in self.providers if self.usable(s)]

    def saturated(self, s: ProviderState) -> bool:
        return s.inflight >= s.p.capacity

    def pick(self, exclude: list[str] | tuple = ()) -> str:
        """Choose where the next LLM step runs.

        Priority order, but load-aware: the first usable cloud with free capacity wins;
        if every usable cloud is saturated, the least-loaded one (relative to capacity)
        takes it; with no usable cloud, the step runs on this device.
        """
        clouds = [s for s in self.providers if self.usable(s) and s.p.name not in exclude]
        if not clouds:
            return EDGE
        for s in clouds:
            if not self.saturated(s):
                return s.p.name
        return min(clouds, key=lambda s: s.inflight / max(1, s.p.capacity)).p.name

    @property
    def active(self) -> str:
        t = self.online_targets()
        return t[0] if t else EDGE

    @property
    def online(self) -> bool:
        return self.active != EDGE

    def _refresh(self) -> None:
        """Fire cut events for providers that just became unusable; track active-target changes."""
        for s in self.providers:
            if not self.usable(s):
                s.cut.set()
            elif s.cut.is_set():
                s.cut = asyncio.Event()
        if self.active != self._active:
            self._active = self.active
            self.changed_at = time.time()

    def set_offline(self, offline: bool) -> None:
        self.offline = offline
        self._refresh()

    def set_disabled(self, name: str, disabled: bool) -> None:
        self.by_name[name].disabled = disabled
        self._refresh()

    def mark_failure(self, name: str, reason: str) -> None:
        s = self.by_name.get(name)
        if not s:
            return
        s.fails = settings.fail_threshold
        s.last_error = reason
        s.reachable = False
        self._refresh()

    def label(self, target: str) -> str:
        return settings.edge_label if target == EDGE else settings.cloud_model_label

    def status(self) -> dict:
        return {
            "active": self.active,
            "tier": "cloud" if self.online else "edge",
            "offline": self.offline,
            "network": {"up": self.net_up, "changed_at": self.net_changed_at},
            "reconnecting": self.net_up
            and not self.offline
            and not self.online
            and time.time() - self.net_changed_at < 20
            and any(s.p.configured and not s.disabled for s in self.providers),
            "providers": [
                {
                    "name": s.p.name,
                    "label": s.p.label,
                    "hardware": s.p.hardware,
                    "model": settings.cloud_model_label,
                    "configured": s.p.configured,
                    "reachable": s.reachable,
                    "disabled": s.disabled,
                    "usable": self.usable(s),
                    "standby": s.p.standby,
                    "idle": self._standby_idle(s),
                    "latency_ms": s.latency_ms,
                    "last_error": s.last_error,
                    "inflight": s.inflight,
                    "capacity": s.p.capacity,
                    "served": s.served,
                }
                for s in self.providers
            ],
            "edge": {"ok": self.edge_ok, "model": settings.edge_label, "inflight": self.edge_inflight, "capacity": settings.edge_concurrency},
            "budget": {"max_agents": settings.cloud_max_agents if self.online else settings.edge_max_agents},
            "changed_at": self.changed_at,
            "last_check": self.last_check,
        }

    # ----------------------------------------------------------------- monitor
    def start(self) -> None:
        self._tasks = [asyncio.create_task(self._loop()), asyncio.create_task(self._net_loop())]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await self._http.aclose()

    async def _net_loop(self) -> None:
        """Watch the device's own connectivity twice a second and react immediately."""
        while True:
            try:
                up = has_network_route()
                if up != self.net_up:
                    self.net_up = up
                    self.net_changed_at = time.time()
                    if not up:
                        for s in self.providers:
                            s.reachable = False
                            s.last_error = "no network on this device"
                        self._refresh()  # aborts in-flight cloud streams right now
                    else:
                        asyncio.create_task(self.check())  # reconnect as soon as a cloud answers
            except Exception:
                pass
            await asyncio.sleep(0.5)

    async def _loop(self) -> None:
        while True:
            try:
                await self.check()
            except Exception:
                pass  # the monitor must never die
            await asyncio.sleep(settings.health_interval_s)

    def _standby_idle(self, s: ProviderState) -> bool:
        """A standby stays cold while a higher-priority provider is usable and has spare capacity.
        It is woken (probed) when the primary is down *or* saturated, so bursts overflow to it."""
        if not s.p.standby:
            return False
        for other in self.providers:
            if other is s:
                return False
            if self.usable(other) and not self.saturated(other):
                return True
        return False

    async def check(self) -> None:
        # Simulated total link loss: do not touch the network at all (also keeps standbys cold).
        skip = self.offline or not self.net_up
        probes = [] if skip else [self._probe(s) for s in self.providers if not self._standby_idle(s)]
        await asyncio.gather(*probes, self._check_edge())
        self.last_check = time.time()
        self._refresh()

    async def _probe(self, s: ProviderState) -> None:
        if not s.p.configured:
            s.reachable, s.last_error = False, "not configured"
            return
        t0 = time.perf_counter()
        try:
            r = await self._http.get(
                f"{s.p.base_url}/models", headers={"Authorization": f"Bearer {s.p.api_key}"}, timeout=settings.health_timeout_s
            )
            ok = r.status_code == 200 and any(m.get("id") == s.p.model for m in r.json().get("data", []))
            err = None if ok else (f"HTTP {r.status_code}" if r.status_code != 200 else "model not loaded")
        except Exception as e:
            ok, err = False, type(e).__name__
            if isinstance(e, (httpx.ConnectError, httpx.NetworkError)):
                s.fails = settings.fail_threshold - 1  # hard network error: mark down on this probe
        if ok:
            s.latency_ms = round((time.perf_counter() - t0) * 1000)
            s.fails, s.last_error, s.reachable = 0, None, True
        else:
            s.fails += 1
            s.last_error = err
            if s.fails >= settings.fail_threshold:
                s.reachable = False

    async def _check_edge(self) -> None:
        try:
            base = settings.edge_base_url.rsplit("/v1", 1)[0]
            r = await self._http.get(f"{base}/api/tags", timeout=2.0)
            self.edge_ok = r.status_code == 200
        except Exception:
            self.edge_ok = False

    # --------------------------------------------------------------- inference
    async def stream_step(
        self,
        target: str,
        messages: list[dict],
        tools: list[dict] | None,
        emit: Emit,
        step_id: str,
        *,
        json_mode: bool = False,
        max_tokens: int | None = None,
        silent: bool = False,
    ) -> dict:
        """Stream one assistant turn from ``target``. Returns an OpenAI-format assistant message."""
        if target == EDGE:
            async with self.edge_sem:  # the laptop serves one generation at a time: scale down
                self.edge_inflight += 1
                try:
                    return await self._stream(target, messages, tools, emit, step_id, json_mode, max_tokens, silent)
                finally:
                    self.edge_inflight -= 1
        s = self.by_name[target]
        s.inflight += 1
        if self.saturated(s):
            asyncio.ensure_future(self.check())  # saturation may need to wake a standby
        try:
            msg = await self._stream(target, messages, tools, emit, step_id, json_mode, max_tokens, silent)
            s.served += 1
            return msg
        finally:
            s.inflight -= 1

    async def _stream(self, target, messages, tools, emit, step_id, json_mode, max_tokens, silent) -> dict:
        req: dict[str, Any] = {"messages": messages, "stream": True, "temperature": 0.3}
        if target == EDGE:
            client, cut = self.edge, None
            req.update(model=settings.edge_model, max_tokens=max_tokens or 1024, reasoning_effort="none")
        else:
            s = self.by_name[target]
            if not self.usable(s):
                raise LinkLost(s.last_error or "unavailable")
            client, cut = s.client, s.cut
            req.update(model=s.p.model, max_tokens=max_tokens or (4096 if settings.cloud_thinking else 2048))
            req["extra_body"] = {"chat_template_kwargs": {"enable_thinking": settings.cloud_thinking}}
        if tools:
            req["tools"] = tools
            req["tool_choice"] = "auto"
        if json_mode and target != EDGE:  # Ollama lacks structured output for this model; prompt enforces JSON
            req["response_format"] = {"type": "json_object"}

        t0 = time.perf_counter()
        stream = None
        text: list[str] = []
        calls: dict[int, dict] = {}
        ttft = None
        try:
            stream = await self._guard(client.chat.completions.create(**req), settings.first_token_timeout_s, cut)
            it = stream.__aiter__()
            while True:
                timeout = settings.first_token_timeout_s if ttft is None else settings.stall_timeout_s
                try:
                    chunk = await self._guard(it.__anext__(), timeout, cut)
                except StopAsyncIteration:
                    break
                if ttft is None:
                    ttft = round((time.perf_counter() - t0) * 1000)
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                extra = delta.model_extra or {}
                reasoning = extra.get("reasoning_content") or extra.get("reasoning")
                if reasoning and not silent:
                    emit({"type": "reasoning", "step_id": step_id, "text": reasoning})
                if delta.content:
                    text.append(delta.content)
                    if not silent:
                        emit({"type": "token", "step_id": step_id, "text": delta.content})
                for tc in delta.tool_calls or []:
                    idx = tc.index if tc.index is not None else len(calls)
                    slot = calls.get(idx)
                    if slot is not None and tc.id and slot["id"] and tc.id != slot["id"]:
                        idx, slot = max(calls) + 1, None  # some servers reuse index 0 for parallel calls
                    if slot is None:
                        slot = calls[idx] = {"id": tc.id or "", "name": "", "arguments": ""}
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function and tc.function.name:
                        slot["name"] += tc.function.name
                    if tc.function and tc.function.arguments:
                        slot["arguments"] += tc.function.arguments
        except LinkLost:
            if stream is not None:
                asyncio.ensure_future(_quiet_close(stream))
            raise
        except (openai.APIConnectionError, openai.APITimeoutError, httpx.HTTPError) as e:
            if target != EDGE:
                raise LinkLost(f"connection: {type(e).__name__}") from e
            raise
        except openai.APIStatusError as e:
            if target != EDGE:
                raise LinkLost(f"HTTP {e.status_code}") from e
            raise

        tool_calls = []
        for i, slot in sorted(calls.items()):
            if not slot["name"]:
                continue
            args = slot["arguments"].strip() or "{}"
            try:
                json.loads(args)
            except json.JSONDecodeError:
                args = "{}"
            tool_calls.append({"id": slot["id"] or f"call_{step_id[:8]}_{i}", "type": "function", "function": {"name": slot["name"], "arguments": args}})
        msg: dict[str, Any] = {"role": "assistant", "content": "".join(text)}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        msg["meta"] = {
            "target": target,
            "tier": "edge" if target == EDGE else "cloud",
            "model": self.label(target),
            "step_id": step_id,
            "ms": round((time.perf_counter() - t0) * 1000),
            "ttft_ms": ttft,
        }
        return msg

    async def _guard(self, aw: Awaitable, timeout: float, cut: asyncio.Event | None):
        """Await ``aw``; for cloud targets abort with LinkLost if it stalls or the link is cut."""
        task = asyncio.ensure_future(aw)
        if cut is None:
            return await task
        if cut.is_set():
            task.cancel()
            raise LinkLost("link cut")
        cut_task = asyncio.ensure_future(cut.wait())
        try:
            done, _ = await asyncio.wait({task, cut_task}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        finally:
            cut_task.cancel()
        if task in done:
            return task.result()
        task.cancel()
        if cut.is_set():
            raise LinkLost("link cut mid-stream")
        raise LinkLost(f"stalled > {timeout:.0f}s")


async def _quiet_close(stream: Any) -> None:
    try:
        await stream.close()
    except Exception:
        pass


async def run_with_failover(fn: Callable[[str, str], Awaitable[dict]], emit: Emit, ctx: dict) -> dict:
    """Run ``fn(target, step_id)`` on the best target, falling through clouds then to the edge.

    The next target is chosen *after* each failure: a failure triggers an immediate
    re-probe so a cold-standby cloud (e.g. Modal) that is reachable is preferred over
    the device. ``ctx`` is merged into every emitted lifecycle event.
    """
    tried: list[str] = []
    failed: list[dict] = []
    target = router.pick()
    while True:
        step_id = uuid.uuid4().hex
        emit({"type": "step_start", "step_id": step_id, "target": target, "tier": "edge" if target == EDGE else "cloud", "model": router.label(target), **ctx})
        try:
            msg = await fn(target, step_id)
        except LinkLost as e:
            router.mark_failure(target, str(e))
            tried.append(target)
            failed.append({"from": target, "reason": str(e)})
            try:
                await asyncio.wait_for(router.check(), timeout=settings.health_timeout_s + 1)
            except Exception:
                pass
            target = router.pick(exclude=tried)
            emit({"type": "failover", "step_id": step_id, "from": failed[-1]["from"], "to": target, "reason": str(e), **ctx})
            continue
        if failed:
            msg["meta"]["failover"] = failed
        emit({"type": "step_end", "step_id": step_id, "meta": msg["meta"], "has_tool_calls": bool(msg.get("tool_calls")), **ctx})
        return msg


router = Router()
