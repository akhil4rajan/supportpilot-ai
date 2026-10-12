"""Retrieval suite (no LLM, deterministic, runs in CI).

Questions: does the right passage rank at the top (hit@1/hit@3/MRR)?  Does the system KNOW when it doesn't know
(abstention on out-of-scope questions, including 'near' questions that share vocabulary with real articles)?
This suite measures the part of the pipeline that decides whether the model is even allowed to answer."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.rag.index import KnowledgeBase
from app.tenancy.registry import TenantRegistry

from ..stats import rate


def load(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def run(ctx: dict[str, Any]) -> dict[str, Any]:
    reg = TenantRegistry(ctx["tenants_dir"])
    rt = reg.by_id(ctx["tenant"])
    kb: KnowledgeBase = rt.kb
    min_cov, weak_cov = rt.tenant.knowledge.min_coverage, rt.tenant.knowledge.weak_coverage
    syn = rt.tenant.knowledge.synonyms
    cases = load(ctx["datasets"] / ctx["tenant"] / ctx.get("retrieval_file", "retrieval.jsonl"))

    inscope, oos = [c for c in cases if c["kind"] in ("direct", "paraphrase")], [c for c in cases if c["kind"].startswith("oos")]
    details: list[dict[str, Any]] = []
    hit1 = hit3 = confident = readable = 0
    rr = 0.0
    by_kind: dict[str, list[int]] = {"direct": [0, 0], "paraphrase": [0, 0]}
    for c in inscope:
        hits = kb.search(c["q"], top_k=3, synonyms=syn)
        rank = next((i + 1 for i, h in enumerate(hits) if h.chunk.source == c["source"] and c["heading"].lower() in h.chunk.title.lower()), None)
        top_cov = hits[0].coverage if hits else 0.0
        ok1, ok3 = rank == 1, rank is not None
        hit1 += ok1; hit3 += ok3; rr += (1 / rank) if rank else 0.0
        confident += top_cov >= min_cov
        readable += top_cov >= weak_cov  # the model is shown something to read
        by_kind[c["kind"]][0] += ok3; by_kind[c["kind"]][1] += 1
        if not ok3 or top_cov < weak_cov:
            details.append({"id": c["id"], "q": c["q"], "problem": "miss" if not ok3 else "no_passages", "rank": rank,
                            "top": f'{hits[0].chunk.source} > {hits[0].chunk.title} ({top_cov:.2f})' if hits else None})
    no_passages = false_confident = 0
    near_confident = near_total = 0
    for c in oos:
        hits = kb.search(c["q"], top_k=1, synonyms=syn)
        top_cov = hits[0].coverage if hits else 0.0
        no_passages += top_cov < weak_cov
        false_confident += top_cov >= min_cov
        if c["kind"] == "oos_near":
            near_total += 1; near_confident += top_cov >= min_cov
        if top_cov >= min_cov:
            details.append({"id": c["id"], "q": c["q"], "problem": "false_confidence", "top": f'{hits[0].chunk.source} > {hits[0].chunk.title} ({top_cov:.2f})'})
    n = len(inscope)
    return {
        "metrics": {
            "hit_at_1": rate(hit1, n), "hit_at_3": rate(hit3, n), "mrr": {"value": rr / n, "n": n},
            "in_scope_confident_rate": rate(confident, n),
            "in_scope_readable_rate": rate(readable, n),          # >= weak band: the model is given passages
            "oos_no_passage_rate": rate(no_passages, len(oos)),   # nothing misleading is shown for unanswerable questions
            "oos_false_confident_rate": rate(false_confident, len(oos)),
            "oos_near_false_confident_rate": rate(near_confident, near_total),
            "hit_at_3_direct": rate(*by_kind["direct"]), "hit_at_3_paraphrase": rate(*by_kind["paraphrase"]),
        },
        "failures": details,
        "info": {"min_coverage": min_cov, "weak_coverage": weak_cov, "kb_chunks": kb.size, "cases": len(cases)},
    }
