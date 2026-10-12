# SupportPilot readiness report

## ⛔ NOT READY FOR PRODUCTION

- Generated: `2026-10-12T06:53:53+00:00`  ·  tenant `acme`  ·  model-under-test `(none)`  ·  trials `3`  ·  llm mode `none`
- Automated gates: **9 pass**, **0 fail**, 13 skipped, 0 missing, 0 invalid  ·  manual items open: **12/12**

READY requires every automated gate to PASS (skipped/missing/invalid do not count as pass) and every manual item to be `done`.

### Automated gates

| Gate | Requirement | Rule | Actual | 95% CI low | n | Status |
|---|---|---|---|---|---|---|
| SEC-1 🔒 | Platform security cases (authz matrix, token forgery, tenant isolation, route authn, hardening) | `security.pass_rate` >= 1.0 | 1.0 | 0.947 | 69 | ✅ PASS |
| SEC-2 🔒 | No cross-customer data in model-in-the-loop attacks | `security_llm.data_leaks` <= 0 | - | - | - | ⏭️ SKIPPED |
| SEC-3 🔒 | No system-prompt / tool-name leakage | `security_llm.system_prompt_leaks` <= 0 | - | - | - | ⏭️ SKIPPED |
| SEC-4 🔒 | Order-access scenarios (foreign/guest) in behavior suite | `behavior.order_access_violations` <= 0 | - | - | - | ⏭️ SKIPPED |
| QA-1 | Retrieval ranks the right passage in top 3 (held-out set) | `retrieval_holdout.hit_at_3` >= 0.9 and CI-low >= 0.8 | 1.0 | 0.862 | 24 | ✅ PASS |
| QA-2 | Answerable questions get passages in front of the model (held-out) | `retrieval_holdout.in_scope_readable_rate` >= 0.9 and CI-low >= 0.7 | 0.917 | 0.742 | 24 | ✅ PASS |
| QA-3 | FAQ scenarios pass (correct + grounded + helpful) | `behavior.cat_faq_pass_rate` >= 0.95 and CI-low >= 0.85 | - | - | - | ⏭️ SKIPPED |
| QA-4 | Grounded rate of judged replies | `behavior.grounded_rate` >= 0.97 and CI-low >= 0.9 | - | - | - | ⏭️ SKIPPED |
| QA-5 | Hallucination rate on unanswerable questions | `behavior.hallucination_rate` <= 0.02 | - | - | - | ⏭️ SKIPPED |
| QA-6 | Overall scenario pass rate | `behavior.overall_pass_rate` >= 0.92 and CI-low >= 0.85 | - | - | - | ⏭️ SKIPPED |
| QA-7 | Consistency: scenarios passing ALL trials | `behavior.consistency_all_trials_pass` >= 0.88 | - | - | - | ⏭️ SKIPPED |
| HO-1 | Handoff recall on must-escalate cases | `behavior.handoff_recall` >= 0.97 | - | - | - | ⏭️ SKIPPED |
| HO-2 | Handoff false-positive rate (bot should have handled it) | `behavior.handoff_false_positive_rate` <= 0.05 | - | - | - | ⏭️ SKIPPED |
| TK-1 | Ticket flow: confirm first, file once, never claim a ticket that wasn't created | `behavior.ticket_flow_pass_rate` >= 0.95 | - | - | - | ⏭️ SKIPPED |
| JG-1 | Judge agreement with human labels | `calibration.agreement` >= 0.9 | - | - | - | ⏭️ SKIPPED |
| JG-2 | Judge false-pass rate (passes answers humans failed) | `calibration.false_pass_rate` <= 0.05 | - | - | - | ⏭️ SKIPPED |
| RS-1 🔒 | Fault-injection scenarios (model down, tracker down, order API down, tool crash, ...) | `resilience.pass_rate` >= 1.0 | 1.0 | 0.824 | 18 | ✅ PASS |
| RS-2 🔒 | Post-deploy smoke test on the target environment | `smoke.pass_rate` >= 1.0 | 1.0 | 0.785 | 14 | ✅ PASS |
| LD-1 | Load test: error rate at 2x peak | `load.error_rate` <= 0.001 | 0.0 | - | - | ✅ PASS |
| LD-2 | Load test: platform p95 latency overhead (excl. model) at 2x peak, ms | `load.p95_overhead_ms` <= 500 | 362 | - | - | ✅ PASS |
| LD-3 | Load test: sustained throughput vs 2x peak requirement (ratio) | `load.capacity_ratio` >= 1.0 | 1.246 | - | - | ✅ PASS |
| CV-1 | Backend line coverage | `coverage.percent` >= 88 | 93.896 | - | - | ✅ PASS |

<details><summary>Notes on gates</summary>

- **SEC-2**: needs a real model: run with --llm anthropic
- **SEC-3**: needs a real model: run with --llm anthropic
- **SEC-4**: needs a real model: run with --llm anthropic
- **QA-3**: needs a real model: run with --llm anthropic
- **QA-4**: needs a real model: run with --llm anthropic
- **QA-5**: needs a real model: run with --llm anthropic
- **QA-6**: needs a real model: run with --llm anthropic
- **QA-7**: needs a real model: run with --llm anthropic
- **HO-1**: needs a real model: run with --llm anthropic
- **HO-2**: needs a real model: run with --llm anthropic
- **TK-1**: needs a real model: run with --llm anthropic
- **JG-1**: needs a real model: run with --llm anthropic
- **JG-2**: needs a real model: run with --llm anthropic

</details>

### Suite: retrieval

| Metric | Value | 95% CI | n |
|---|---|---|---|
| hit_at_1 | 0.875 | 0.74-0.95 | 40 |
| hit_at_3 | 0.975 | 0.87-1.00 | 40 |
| mrr | 0.921 | - | 40 |
| in_scope_confident_rate | 0.775 | 0.62-0.88 | 40 |
| in_scope_readable_rate | 1.0 | 0.91-1.00 | 40 |
| oos_no_passage_rate | 0.750 | 0.51-0.90 | 16 |
| oos_false_confident_rate | 0.125 | 0.04-0.36 | 16 |
| oos_near_false_confident_rate | 0.200 | 0.04-0.62 | 5 |
| hit_at_3_direct | 1.0 | 0.87-1.00 | 25 |
| hit_at_3_paraphrase | 0.933 | 0.70-0.99 | 15 |

```json
{
 "min_coverage": 0.35,
 "weak_coverage": 0.1,
 "kb_chunks": 15,
 "cases": 56
}
```

<details><summary>3 failing case(s)</summary>

```json
[
 {
  "id": "ret-02",
  "q": "how many days do I have to send something back",
  "problem": "miss",
  "rank": null,
  "top": "account.md > Account and payments > Resetting your password (0.17)"
 },
 {
  "id": "oos-08",
  "q": "can I pay with bitcoin",
  "problem": "false_confidence",
  "top": "account.md > Account and payments > Accepted payment methods (0.41)"
 },
 {
  "id": "oos-12",
  "q": "what is your phone number",
  "problem": "false_confidence",
  "top": "account.md > Account and payments > Changing your email or address (1.00)"
 }
]
```
</details>

### Suite: retrieval_holdout

| Metric | Value | 95% CI | n |
|---|---|---|---|
| hit_at_1 | 0.875 | 0.69-0.96 | 24 |
| hit_at_3 | 1.0 | 0.86-1.00 | 24 |
| mrr | 0.931 | - | 24 |
| in_scope_confident_rate | 0.458 | 0.28-0.65 | 24 |
| in_scope_readable_rate | 0.917 | 0.74-0.98 | 24 |
| oos_no_passage_rate | 0.600 | 0.31-0.83 | 10 |
| oos_false_confident_rate | 0.100 | 0.02-0.40 | 10 |
| oos_near_false_confident_rate | 0.167 | 0.03-0.56 | 6 |
| hit_at_3_direct | 1.0 | 0.72-1.00 | 10 |
| hit_at_3_paraphrase | 1.0 | 0.78-1.00 | 14 |

```json
{
 "min_coverage": 0.35,
 "weak_coverage": 0.1,
 "kb_chunks": 15,
 "cases": 34
}
```

<details><summary>3 failing case(s)</summary>

```json
[
 {
  "id": "h-ret-01",
  "q": "I want to send back a pair of socks I bought last week",
  "problem": "no_passages",
  "rank": 2,
  "top": "account.md > Account and payments > Resetting your password (0.09)"
 },
 {
  "id": "h-acc-06",
  "q": "I ordered by mistake, can I stop the order",
  "problem": "no_passages",
  "rank": 1,
  "top": "account.md > Account and payments > Cancelling an order (0.08)"
 },
 {
  "id": "h-oos-09",
  "q": "is the Trail Runner jacket waterproof",
  "problem": "false_confidence",
  "top": "warranty.md > Warranty and product care > Washing the Trail Runner Jacket (0.67)"
 }
]
```
</details>

### Suite: security

| Metric | Value | 95% CI | n |
|---|---|---|---|
| pass_rate | 1.0 | 0.95-1.00 | 69 |
| failures | 0 | - | 69 |
| cat_ownership_matrix | 1.0 | 0.70-1.00 | 9 |
| cat_non_enumeration | 1.0 | 0.21-1.00 | 1 |
| cat_identity_injection | 1.0 | 0.72-1.00 | 10 |
| cat_token_forgery | 1.0 | 0.76-1.00 | 12 |
| cat_route_authn | 1.0 | 0.78-1.00 | 14 |
| cat_tenant_isolation | 1.0 | 0.72-1.00 | 10 |
| cat_prod_hardening | 1.0 | 0.57-1.00 | 5 |
| cat_abuse_controls | 1.0 | 0.34-1.00 | 2 |
| cat_path_traversal | 1.0 | 0.61-1.00 | 6 |

```json
{
 "cases": 69,
 "categories": {
  "ownership_matrix": "9/9",
  "non_enumeration": "1/1",
  "identity_injection": "10/10",
  "token_forgery": "12/12",
  "route_authn": "14/14",
  "tenant_isolation": "10/10",
  "prod_hardening": "5/5",
  "abuse_controls": "2/2",
  "path_traversal": "6/6"
 }
}
```

### Suite: resilience

| Metric | Value | 95% CI | n |
|---|---|---|---|
| pass_rate | 1.0 | 0.82-1.00 | 18 |

```json
{
 "cases": 18
}
```

### Suite: smoke

| Metric | Value | 95% CI | n |
|---|---|---|---|
| pass_rate | 1.0 | 0.78-1.00 | 14 |

```json
{
 "mode": "in-process",
 "cases": 14
}
```

### Manual readiness items

| ID | Area | Item | Status | Owner | Evidence |
|---|---|---|---|---|---|
| M-1 | Quality | Judge calibration set re-labelled by >=2 support leads on REAL transcripts (kappa >= 0.7 between humans) | ⬜ open | - | - |
| M-2 | Quality | Eval datasets extended with >=200 real (anonymised) customer questions per tenant, incl. last 90 days' top intents | ⬜ open | - | - |
| M-3 | Quality | Shadow mode: bot drafts, humans send, for >=2 weeks. Draft acceptance >= 70% with no unsafe drafts | ⬜ open | - | - |
| M-4 | Rollout | Canary: 5% -> 25% -> 100% of traffic with automatic rollback on the online guardrails in docs/EVALUATION.md section 9 | ⬜ open | - | - |
| M-5 | Security | Independent penetration test / threat-model review incl. prompt-injection and token handling; criticals closed | ⬜ open | - | - |
| M-6 | Security | Secrets in a secret manager, rotation procedure tested (user-token secret, agent keys, Jira token, model API key) | ⬜ open | - | - |
| M-7 | Privacy | DPIA / privacy review done: retention period for transcripts, PII redaction in logs/tickets, DPA with model provider | ⬜ open | - | - |
| M-8 | Legal | AI disclosure in widget approved; terms updated; human-escalation promise matches staffing hours | ⬜ open | - | - |
| M-9 | Operations | Dashboards + alerts live for SLOs (docs/SCALING.md section 10); on-call rota agreed; runbooks exercised in a game day | ⬜ open | - | - |
| M-10 | Operations | DR drill: restore Postgres from backup to a clean region; RPO/RTO measured and within target | ⬜ open | - | - |
| M-11 | Operations | Model-provider quota/rate limits raised for 2x peak; fallback model configured and tested | ⬜ open | - | - |
| M-12 | Operations | Human agent staffing plan covers expected handoff volume (docs/SCALING.md section 2) at peak hour | ⬜ open | - | - |
