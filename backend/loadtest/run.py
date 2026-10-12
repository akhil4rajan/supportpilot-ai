"""Load generator for SupportPilot (open-loop: arrivals do not slow down when the server does - that is what real
customers do). Run it against a server started with LLM_PROVIDER=simulated so the test measures THE PLATFORM, not model quota.

  # terminal 1: a server under test
  LLM_PROVIDER=simulated SIM_LLM_MEDIAN_SECONDS=2.5 MESSAGES_PER_MINUTE=100000 uvicorn app.main:app_factory --factory --port 8000
  # terminal 2:
  python -m loadtest.run ramp --base-url http://localhost:8000 --user-secret $ACME_USER_TOKEN_SECRET
  python -m loadtest.run ws   --base-url http://localhost:8000 --sockets 1000 ...

Think time is compressed (default 1 s vs ~20 s real): the TURN ARRIVAL RATE (conv/s x turns/conv) is what loads the server,
and it is identical. Open sockets are covered by the `ws` mode."""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import jwt

from capacity.model import Assumptions, derive
from evals.stats import percentile

QUESTIONS = ["How long do refunds take?", "do you ship to Canada", "how do I reset my password", "what is your return policy", "can I pay with PayPal",
             "how long is the warranty", "is shipping free", "where is order ACM-1001", "what's the status of ACM-2001", "I want to open a ticket about a broken zipper"]
HUMAN = "I want to speak to a human"


@dataclass
class Stats:
    samples: list[tuple[float, float, float]] = field(default_factory=list)  # (seconds since step start, latency ms, overhead ms)
    status: Counter = field(default_factory=Counter)
    turns: int = 0
    convs: int = 0
    handoffs: int = 0
    errors: int = 0
    t0: float = 0.0
    t1: float = 0.0


def user_token(secret: str, sub: str) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": sub, "aud": "supportpilot", "iat": now, "exp": now + timedelta(minutes=5), "name": sub, "email": f"{sub}@load.test"}, secret, algorithm="HS256")


async def conversation(c: httpx.AsyncClient, a: argparse.Namespace, st: Stats, rng: random.Random, n: int) -> None:
    body: dict[str, Any] = {"public_key": a.public_key}
    if a.user_secret and rng.random() < 0.5:
        body["user_token"] = user_token(a.user_secret, f"load-u{n}")
    hdr = {"origin": a.origin} if a.origin else {}
    try:
        r = await c.post("/v1/widget/sessions", json=body, headers=hdr)
        st.status[r.status_code] += 1
        if r.status_code != 200:
            st.errors += 1
            return
    except httpx.HTTPError:
        st.errors += 1; st.status["conn_error"] += 1
        return
    tok = {"Authorization": f"Bearer {r.json()['token']}"}
    st.convs += 1
    turns = max(1, round(rng.gauss(a.turns, 1.5)))
    handoff = rng.random() < a.handoff_rate
    for i in range(turns):
        text = HUMAN if (handoff and i == turns - 1) else rng.choice(QUESTIONS)
        t0 = time.perf_counter()
        try:
            r = await c.post("/v1/widget/messages", json={"content": text}, headers=tok)
        except httpx.HTTPError:
            st.errors += 1; st.status["conn_error"] += 1
            return
        lat = (time.perf_counter() - t0) * 1000
        st.status[r.status_code] += 1
        if r.status_code != 200:
            st.errors += 1
            if r.status_code == 429:
                return
            continue
        j = r.json()
        llm_ms = max((m.get("meta", {}).get("timing", {}).get("llm_ms", 0) for m in j["messages"]), default=0)
        st.samples.append((time.perf_counter() - st.t0, lat, max(lat - llm_ms, 0.0))); st.turns += 1
        if j["conversation"]["status"] == "waiting_human":
            st.handoffs += 1
            return
        await asyncio.sleep(rng.expovariate(1 / a.think))


async def run_step(a: argparse.Namespace, conv_rate: float, seconds: float) -> dict[str, Any]:
    st, rng = Stats(), random.Random(7)
    limits = httpx.Limits(max_connections=4000, max_keepalive_connections=2000)
    async with httpx.AsyncClient(base_url=a.base_url, timeout=60, limits=limits) as c:
        tasks: list[asyncio.Task] = []
        st.t0 = time.perf_counter()
        n = 0
        while time.perf_counter() - st.t0 < seconds:  # Poisson arrivals
            n += 1
            tasks.append(asyncio.create_task(conversation(c, a, st, rng, n)))
            await asyncio.sleep(rng.expovariate(conv_rate))
        arrival_end = time.perf_counter()
        await asyncio.gather(*tasks)
        st.t1 = time.perf_counter()
        metrics = (await c.get("/metrics", headers={"Authorization": f"Bearer {a.metrics_token}"} if a.metrics_token else {})).text
    elapsed = arrival_end - st.t0
    # Steady state only: a conversation lasts ~40 s, so the first ~40 s is ramp-up (fewer concurrent chats than steady state).
    warm = min(a.warmup, elapsed * 0.6)
    steady = [x for x in st.samples if warm <= x[0] <= elapsed]
    lat = [x[1] for x in steady]
    ovh = [x[2] for x in steady]
    steady_turns_s = len(steady) / max(elapsed - warm, 1e-9)
    total = sum(v for k, v in st.status.items() if isinstance(k, int))
    bad = sum(v for k, v in st.status.items() if not (isinstance(k, int) and k == 200))
    return {
        "conv_per_s_offered": conv_rate, "turns_per_s_steady": round(steady_turns_s, 1), "conversations": st.convs, "turns": st.turns,
        "turns_per_conversation": round(st.turns / max(st.convs, 1), 2),
        "handoffs": st.handoffs, "http_requests": total, "errors": bad, "error_rate": round(bad / max(total, 1), 5), "status": {str(k): v for k, v in st.status.items()},
        "latency_ms": {"p50": round(percentile(lat, .5)), "p95": round(percentile(lat, .95)), "p99": round(percentile(lat, .99))},
        "overhead_ms": {"p50": round(percentile(ovh, .5)), "p95": round(percentile(ovh, .95)), "p99": round(percentile(ovh, .99))},
        "window": {"arrival_s": round(elapsed, 1), "warmup_s": round(warm, 1), "steady_samples": len(steady), "total_wall_s": round(st.t1 - st.t0, 1)},
        "server_metrics_sample": [l for l in metrics.splitlines() if l.startswith(("sp_llm_inflight", "sp_ticket_outbox", "sp_authz_violations_total", "sp_ws_connections"))],
    }


async def ramp(a: argparse.Namespace) -> dict[str, Any]:
    need = derive(Assumptions(conversations_per_day=a.conversations_per_day, turns_per_conversation=a.turns))["demand"]["design_2x_peak"]
    required_turns_s = need["turns_per_s"]
    steps, best = [], None
    rate = a.start_rate
    while rate <= a.max_rate:
        print(f"▶ offered {rate:.1f} conv/s for {a.step_seconds}s ...", flush=True)
        r = await run_step(a, rate, a.step_seconds)
        steps.append(r)
        ok = r["error_rate"] <= 0.001 and r["overhead_ms"]["p95"] <= 500
        print(f"  steady turns/s={r['turns_per_s_steady']}  err={r['error_rate']:.4f}  p95 total={r['latency_ms']['p95']}ms  p95 overhead={r['overhead_ms']['p95']}ms  -> {'OK' if ok else 'SATURATED'}", flush=True)
        if ok:
            best = r
        else:
            break
        rate *= a.step_factor
    sustained = best["turns_per_s_steady"] if best else 0.0
    gates = {"error_rate": (best or steps[-1])["error_rate"], "p95_overhead_ms": (best or steps[-1])["overhead_ms"]["p95"], "capacity_ratio": round(sustained / required_turns_s, 3)}
    return {"mode": "ramp", "requirement": {"conversations_per_day": a.conversations_per_day, "design_turns_per_s": required_turns_s},
            "sustained_turns_per_s_per_process": sustained, "gates": gates, "steps": steps, "when": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "caveat": "Measured against ONE server process on this machine with the DB named in --note. Replicas scale this roughly linearly until the database saturates.", "note": a.note}


async def ws_soak(a: argparse.Namespace) -> dict[str, Any]:
    """M open sockets; agent replies to each customer; measure reply -> socket delivery latency."""
    import websockets

    async with httpx.AsyncClient(base_url=a.base_url, timeout=60, limits=httpx.Limits(max_connections=500)) as c:
        ag = (await c.post("/v1/agent/login", json={"tenant_id": a.tenant_id, "api_key": a.agent_key})).json()["token"]
        ah = {"Authorization": f"Bearer {ag}"}
        sem = asyncio.Semaphore(100)

        async def setup(i: int):
            async with sem:
                r = await c.post("/v1/widget/sessions", json={"public_key": a.public_key})
                j = r.json()
                th = {"Authorization": f"Bearer {j['token']}"}
                await c.post("/v1/widget/handoff", headers=th)
                await c.post(f"/v1/agent/conversations/{j['conversation']['id']}/claim", headers=ah)
                return j["token"], j["conversation"]["id"]
        t0 = time.perf_counter()
        convs = await asyncio.gather(*[setup(i) for i in range(a.sockets)])
        print(f"set up {len(convs)} conversations in {time.perf_counter() - t0:.1f}s", flush=True)
        base_ws = a.base_url.replace("http", "ws", 1)
        received: dict[str, float] = {}
        sent: dict[str, float] = {}
        ready = 0

        async def listen(tok: str, cid: str):
            nonlocal ready
            async with websockets.connect(f"{base_ws}/v1/widget/ws?token={tok}", max_size=2**20) as ws:
                ready += 1
                async for raw in ws:
                    ev = json.loads(raw)
                    if ev.get("type") == "message" and ev["message"]["role"] == "agent":
                        received[cid] = time.perf_counter()
                        return
        listeners = [asyncio.create_task(listen(t, cid)) for t, cid in convs]
        for _ in range(300):
            if ready >= len(convs):
                break
            await asyncio.sleep(0.1)
        print(f"{ready} sockets open", flush=True)
        await asyncio.sleep(1)

        async def reply(cid: str):
            async with sem:
                sent[cid] = time.perf_counter()
                await c.post(f"/v1/agent/conversations/{cid}/messages", json={"content": "hello from an agent"}, headers=ah)
        await asyncio.gather(*[reply(cid) for _, cid in convs])
        await asyncio.wait(listeners, timeout=20)
        lat = [(received[c_] - sent[c_]) * 1000 for c_ in received if c_ in sent]
        for t in listeners:
            t.cancel()
        metrics = (await c.get("/metrics")).text
    return {"mode": "ws", "sockets_requested": a.sockets, "sockets_open": ready, "delivered": len(lat), "delivery_rate": round(len(lat) / max(len(convs), 1), 4),
            "delivery_ms": {"p50": round(percentile(lat, .5)), "p95": round(percentile(lat, .95)), "p99": round(percentile(lat, .99)), "max": round(max(lat, default=0))},
            "ws_gauge": [l for l in metrics.splitlines() if l.startswith("sp_ws_connections")]}


def main() -> None:
    ap = argparse.ArgumentParser(prog="loadtest.run")
    ap.add_argument("mode", choices=["ramp", "steady", "ws"])
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--public-key", default="pk_acme_demo")
    ap.add_argument("--origin", default="http://localhost:5173")
    ap.add_argument("--user-secret", default=None, help="tenant user-token secret: half the conversations sign in")
    ap.add_argument("--metrics-token", default="")
    ap.add_argument("--tenant-id", default="acme")
    ap.add_argument("--agent-key", default="sam-demo-key")
    ap.add_argument("--turns", type=float, default=6.5)
    ap.add_argument("--think", type=float, default=1.0, help="mean seconds between a customer's turns (real life ~20; compressed here)")
    ap.add_argument("--handoff-rate", type=float, default=0.12)
    ap.add_argument("--conversations-per-day", type=int, default=30_000)
    ap.add_argument("--start-rate", type=float, default=1.0)
    ap.add_argument("--step-factor", type=float, default=1.5)
    ap.add_argument("--max-rate", type=float, default=100.0)
    ap.add_argument("--step-seconds", type=float, default=75, help="arrival window per step; must exceed a conversation's lifetime (~40 s) to reach steady state")
    ap.add_argument("--warmup", type=float, default=45, help="seconds excluded from latency stats at the start of each step")
    ap.add_argument("--rate", type=float, default=5.0, help="steady mode: conversations/s")
    ap.add_argument("--duration", type=float, default=60)
    ap.add_argument("--sockets", type=int, default=1000)
    ap.add_argument("--note", default="", help="what this run measured, e.g. 'uvicorn x1, SQLite, 4 vCPU'")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "evals" / "reports"))
    a = ap.parse_args()
    res = asyncio.run({"ramp": ramp, "ws": ws_soak, "steady": lambda a: run_step(a, a.rate, a.duration)}[a.mode](a))
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    name = {"ramp": "load.json", "ws": "load_ws.json", "steady": "load_steady.json"}[a.mode]
    (out / name).write_text(json.dumps(res, indent=1))
    print(json.dumps({k: v for k, v in res.items() if k != "steps"}, indent=1))
    print(f"wrote {out / name}")


if __name__ == "__main__":
    main()
