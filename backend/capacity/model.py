"""Capacity & cost model. Every number in docs/SCALING.md comes from here:  python -m capacity.model [--json]

All inputs are ASSUMPTIONS to be replaced with your own traffic data (see Assumptions). Outputs are planning estimates, not
guarantees: validate with the load test (loadtest/run.py) and production metrics, then re-run with measured values."""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass

# Anthropic list prices, USD per million tokens (cached 2026-10-06 from the claude-api reference; prompts <= 100K tokens
# for Haiku 5.5). Re-check https://docs.anthropic.com/en/docs/about-claude/pricing before budgeting.
PRICES = {
    "claude-opus-5-5": {"in": 4.00, "out": 20.00, "cache_read": 0.20, "cache_write_5m": 5.00},
    "claude-sonnet-5-5": {"in": 2.00, "out": 10.00, "cache_read": 0.20, "cache_write_5m": 2.50},
    "claude-haiku-5-5": {"in": 0.10, "out": 0.50, "cache_read": 0.01, "cache_write_5m": 0.125},
}


@dataclass
class Assumptions:
    # ---- demand
    conversations_per_day: int = 30_000      # "30,000 tickets/day" interpreted as 30k support conversations/day (see docs)
    peak_hour_share: float = 0.15            # busiest hour carries 15% of the day (flat would be 4.2%)
    burst_factor: float = 2.0                # busiest minute vs the peak-hour average
    design_headroom: float = 2.0             # provision for 2x the peak
    turns_per_conversation: float = 6.5      # customer messages per conversation (mean)
    think_time_s: float = 20.0               # customer typing/reading time between turns
    # ---- per-turn work
    llm_calls_per_turn: float = 1.8          # ~60% of turns call a tool first -> 2 model calls
    llm_latency_s: float = 3.5               # median model call latency (incl. tool-use round trip)
    input_tokens_per_call: int = 3_400       # static prefix + dynamic prompt + history + tool results
    static_prefix_tokens: int = 1_600        # tools + tenant system prompt (the cacheable part). Measured below.
    output_tokens_per_call: int = 260
    cache_hit_rate: float = 0.90             # share of static-prefix tokens served from cache (needs warm 5-min TTL)
    db_commits_per_turn: float = 7.0         # user msg, bot msg, conv update, audit rows, ... (measured by reading the code)
    ws_events_per_turn: float = 3.0
    # ---- business outcomes
    ticket_rate: float = 0.10                # share of conversations that create a ticket
    handoff_rate: float = 0.12               # share escalated to a human
    human_handle_minutes: float = 8.0        # agent time per escalated conversation
    agent_productive_hours: float = 6.0
    agent_target_occupancy: float = 0.80
    # ---- infra
    messages_bytes: int = 700                # avg stored bytes per message row (content + meta + index share)
    system_msgs_per_conv: float = 2.0
    measured_turns_per_sec_per_replica: float = 40.0  # MEASURED: 1 process, SQLite, 4-vCPU sandbox (loadtest ramp). Re-measure on staging with Postgres.
    llm_max_concurrency_per_pod: int = 64   # LLM_MAX_CONCURRENCY (default). Pods needed for model concurrency = in-flight calls / this.


def prompt_token_estimate() -> dict[str, int]:
    """Estimate the cacheable prefix from the real code (chars/4 is a rough proxy; use count_tokens for exact numbers)."""
    from pathlib import Path

    from app.agent.prompts import build_system_prompt
    from app.tenancy.registry import TenantRegistry
    from app.agent.tools import tool_schemas

    rt = TenantRegistry(Path(__file__).resolve().parent.parent / "tenants").by_id("acme")
    static, dynamic = build_system_prompt(rt, None)
    tools = json.dumps(tool_schemas(rt))
    return {"static_system_tokens": len(static) // 4, "dynamic_tokens": len(dynamic) // 4, "tool_schema_tokens": len(tools) // 4,
            "static_prefix_tokens": (len(static) + len(tools)) // 4}


def derive(a: Assumptions) -> dict:
    day = a.conversations_per_day
    avg_conv_s = day / 86_400
    peak_conv_s = day * a.peak_hour_share / 3_600 * a.burst_factor
    design_conv_s = peak_conv_s * a.design_headroom
    turn_latency = a.llm_calls_per_turn * a.llm_latency_s
    conv_duration = a.turns_per_conversation * (turn_latency + a.think_time_s)

    def block(conv_s: float) -> dict:
        turns_s = conv_s * a.turns_per_conversation
        return {
            "conversations_per_s": round(conv_s, 2),
            "turns_per_s": round(turns_s, 1),
            "model_calls_per_s": round(turns_s * a.llm_calls_per_turn, 1),
            "model_calls_in_flight": math.ceil(turns_s * a.llm_calls_per_turn * a.llm_latency_s),
            "concurrent_chats": math.ceil(conv_s * conv_duration),
            "db_commits_per_s": round(turns_s * a.db_commits_per_turn),
            "ws_events_per_s": round(turns_s * a.ws_events_per_turn),
        }

    d = block(design_conv_s)
    by_throughput = math.ceil(d["turns_per_s"] / a.measured_turns_per_sec_per_replica)
    by_model_slots = math.ceil(d["model_calls_in_flight"] / a.llm_max_concurrency_per_pod)
    replicas = max(by_throughput, by_model_slots)
    # ---- tokens & cost per day
    calls_day = day * a.turns_per_conversation * a.llm_calls_per_turn
    in_tok = calls_day * a.input_tokens_per_call
    out_tok = calls_day * a.output_tokens_per_call
    cacheable = calls_day * min(a.static_prefix_tokens, a.input_tokens_per_call)
    cost = {}
    for m, p in PRICES.items():
        no_cache = in_tok * p["in"] / 1e6 + out_tok * p["out"] / 1e6
        hit = cacheable * a.cache_hit_rate
        miss = cacheable * (1 - a.cache_hit_rate)
        with_cache = (in_tok - cacheable) * p["in"] / 1e6 + hit * p["cache_read"] / 1e6 + miss * p["cache_write_5m"] / 1e6 + out_tok * p["out"] / 1e6
        cost[m] = {"per_day_no_cache": round(no_cache), "per_day_with_cache": round(with_cache), "per_conversation_with_cache": round(with_cache / day, 4),
                   "per_month_with_cache": round(with_cache * 30), "cache_saving_pct": round(100 * (1 - with_cache / no_cache), 1)}
    # ---- storage
    msgs_day = day * (a.turns_per_conversation * 2 + a.system_msgs_per_conv)
    # ---- humans (the real cost driver at this scale)
    handoffs_day = day * a.handoff_rate
    agent_hours = handoffs_day * a.human_handle_minutes / 60
    agents_fte = agent_hours / (a.agent_productive_hours * a.agent_target_occupancy)
    peak_handoffs_h = handoffs_day * a.peak_hour_share
    agents_peak = peak_handoffs_h * a.human_handle_minutes / 60 / a.agent_target_occupancy
    tickets_day = day * a.ticket_rate
    return {
        "assumptions": asdict(a),
        "demand": {"avg_conversations_per_s": round(avg_conv_s, 2), "peak": block(peak_conv_s), "design_2x_peak": d,
                   "turn_latency_s": round(turn_latency, 1), "conversation_duration_s": round(conv_duration)},
        "compute": {"replicas_by_throughput": by_throughput, "replicas_by_model_concurrency": by_model_slots, "replicas_at_design_load": replicas,
                    "replicas_recommended_min": max(3, replicas + 1),
                    "note": "per-replica throughput is an INPUT (measured_turns_per_sec_per_replica); pods = max(throughput, model slots) + 1 for N+1"},
        "tokens_per_day": {"input_total_M": round(in_tok / 1e6, 1), "output_total_M": round(out_tok / 1e6, 1), "cacheable_prefix_M": round(cacheable / 1e6, 1)},
        "model_cost_usd": cost,
        "storage": {"messages_per_day": round(msgs_day), "messages_per_year_M": round(msgs_day * 365 / 1e6, 1),
                    "gb_per_year": round(msgs_day * 365 * a.messages_bytes / 1e9, 1)},
        "humans": {"handoffs_per_day": round(handoffs_day), "agent_hours_per_day": round(agent_hours), "agents_fte": math.ceil(agents_fte),
                   "agents_online_at_peak_hour": math.ceil(agents_peak)},
        "tickets": {"created_per_day": round(tickets_day), "avg_per_s": round(tickets_day / 86_400, 2),
                    "peak_per_s": round(tickets_day * a.peak_hour_share / 3_600 * a.burst_factor, 2)},
    }


def render(d: dict) -> str:
    de, c, t, m, s, h, tk = d["demand"], d["compute"], d["tokens_per_day"], d["model_cost_usd"], d["storage"], d["humans"], d["tickets"]
    rows = [("Metric", "Average", "Peak (busiest minute)", "Design (2x peak)")]
    pk, ds = de["peak"], de["design_2x_peak"]
    rows += [("Conversations / s", de["avg_conversations_per_s"], pk["conversations_per_s"], ds["conversations_per_s"]),
             ("Bot turns / s", round(de["avg_conversations_per_s"] * d["assumptions"]["turns_per_conversation"], 1), pk["turns_per_s"], ds["turns_per_s"]),
             ("Model calls / s", "", pk["model_calls_per_s"], ds["model_calls_per_s"]),
             ("Model calls in flight", "", pk["model_calls_in_flight"], ds["model_calls_in_flight"]),
             ("Concurrent open chats (WebSockets)", "", pk["concurrent_chats"], ds["concurrent_chats"]),
             ("DB commits / s", "", pk["db_commits_per_s"], ds["db_commits_per_s"]),
             ("WS events / s", "", pk["ws_events_per_s"], ds["ws_events_per_s"])]
    out = ["| " + " | ".join(map(str, r)) + " |" for r in rows]
    out.insert(1, "|---|---|---|---|")
    out += ["", f"Turn latency ≈ {de['turn_latency_s']} s; conversation lasts ≈ {de['conversation_duration_s']} s.",
            f"Pods at design load: {c['replicas_at_design_load']} = max({c['replicas_by_throughput']} by throughput, {c['replicas_by_model_concurrency']} by model-concurrency slots); recommended minimum {c['replicas_recommended_min']} (N+1, ≥3 for AZ spread).",
            f"Tokens/day: {t['input_total_M']} M in ({t['cacheable_prefix_M']} M cacheable), {t['output_total_M']} M out.", "", "| Model | $/day no cache | $/day with cache | $/conversation | $/month | cache saves |", "|---|---|---|---|---|---|"]
    out += [f"| {k} | {v['per_day_no_cache']:,} | {v['per_day_with_cache']:,} | {v['per_conversation_with_cache']} | {v['per_month_with_cache']:,} | {v['cache_saving_pct']}% |" for k, v in m.items()]
    out += ["", f"Storage: {s['messages_per_day']:,} messages/day → {s['messages_per_year_M']} M/yr ≈ {s['gb_per_year']} GB/yr.",
            f"Humans: {h['handoffs_per_day']:,} handoffs/day ≈ {h['agent_hours_per_day']} agent-hours/day ≈ {h['agents_fte']} FTE; {h['agents_online_at_peak_hour']} agents online in the peak hour.",
            f"Tickets: {tk['created_per_day']:,}/day (avg {tk['avg_per_s']}/s, peak {tk['peak_per_s']}/s)."]
    return "\n".join(out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--conversations", type=int, default=30_000)
    ap.add_argument("--per-replica-turns-per-s", type=float, default=60.0)
    ns = ap.parse_args()
    est = prompt_token_estimate()
    a = Assumptions(conversations_per_day=ns.conversations, measured_turns_per_sec_per_replica=ns.per_replica_turns_per_s, static_prefix_tokens=est["static_prefix_tokens"])
    res = derive(a)
    res["prompt_estimate"] = est
    print(json.dumps(res, indent=1) if ns.json else f"Static prefix ≈ {est['static_prefix_tokens']} tokens (estimate)\n\n" + render(res))
