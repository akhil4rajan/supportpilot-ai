# SupportPilot AI

A multi-tenant, drop-in customer-support AI. One `<script>` tag on any website gives visitors a chat assistant that can:

| Capability | How it works |
|---|---|
| **Answer support questions** from *each customer's own* documents | Per-tenant knowledge base (Markdown/text) → chunked, BM25-indexed → Claude answers only from retrieved passages, with source citations. A confidence gate stops it guessing. |
| **Check order status — only the user's own orders** | Identity comes from a token signed by the *customer's backend*, never from the chat. Tools can't take a user id; ownership is enforced in 3 layers. |
| **Open a support ticket** | Jira via the [`jira`](https://pypi.org/project/jira/) Python package (pluggable provider). Asks for confirmation first, attaches the transcript, de-duplicates. |
| **Hand off to a human** | Model-initiated, button-initiated, deterministic phrase match ("get me a human"), automatic after repeated failures, or on model outage. Live agent console over WebSocket. |

```
 Customer's site                     SupportPilot (FastAPI)                         Integrations
┌────────────────┐  user token  ┌──────────────────────────────────────┐
│ Customer's own │─ (HS256) ───▶│ /v1/widget/*  session, chat, WS      │      ┌─────────────┐
│ backend        │              │    │                                 │─────▶│ Claude API  │
└────────────────┘              │    ▼                                 │      └─────────────┘
┌────────────────┐  widget.js   │  Orchestrator (tool-use loop)        │      ┌─────────────┐
│ Widget (React, │◀────────────▶│   ├ search_knowledge_base → BM25 KB  │─────▶│ Order API   │ (demo | http)
│ Shadow DOM)    │   REST + WS  │   ├ get_order_status / list_my_orders│      └─────────────┘
└────────────────┘              │   ├ create_support_ticket            │      ┌─────────────┐
┌────────────────┐  REST + WS   │   └ escalate_to_human                │─────▶│ Jira        │ (jira pkg)
│ Agent console  │◀────────────▶│ /v1/agent/*   queue, claim, reply    │      └─────────────┘
│ (React)        │              │ /v1/admin/*   tenants, knowledge     │      ┌─────────────┐
└────────────────┘              │ SQL (SQLite | Postgres)              │─────▶│ Slack hook  │ (optional)
                                └──────────────────────────────────────┘      └─────────────┘
```

## Quick start (no API key needed)

```bash
cp .env.example .env                       # demo secrets; set LLM_PROVIDER=demo to run without a key
cd backend && python -m venv .venv && .venv/bin/pip install -e ".[dev]"
cd ../frontend && npm install && npm run build
cd ../backend && LLM_PROVIDER=demo STATIC_DIR=../frontend/dist .venv/bin/uvicorn app.main:app_factory --factory --port 8000
```

* Demo storefront: <http://localhost:8000/> — switch between Guest / Alice / Bob and chat.
* Agent console: <http://localhost:8000/console/> — company `acme`, key `sam-demo-key`.
* `LLM_PROVIDER=demo` is a keyword stand-in that exercises the real pipeline (auth, KB, tickets, handoff). For real answers set `LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY`.

Dev mode with hot reload: `uvicorn app.main:app_factory --factory --reload` + `cd frontend && npm run dev` (port 5173, proxies `/v1` to 8000).
Docker: `docker compose up --build` (Postgres included).

## Onboarding a new customer (no code changes)

1. Create `backend/tenants/<id>/tenant.yaml` (copy `tenants/acme/`). It defines the public key, allowed origins, branding, persona, order provider, ticket provider, human agents. **Secrets are never in YAML** — it names env vars (`user_token_secret_env`, `api_token_env`, …).
2. Put their docs in `backend/tenants/<id>/knowledge/*.md|txt`, or upload via the admin API:
   ```bash
   curl -X POST localhost:8000/v1/admin/tenants/acme/knowledge -H "X-Admin-Key: $ADMIN_API_KEY" \
        -H 'Content-Type: application/json' -d '{"filename":"boutique.md","content":"# Boutique\n\nOpen 10am-8pm."}'
   curl -X POST localhost:8000/v1/admin/tenants/reload -H "X-Admin-Key: $ADMIN_API_KEY"
   ```
3. Give the customer the snippet ([`examples/embed.html`](examples/embed.html)) and the token endpoint example ([`examples/host_token_server.py`](examples/host_token_server.py)):
   ```html
   <script src="https://YOUR-HOST/widget.js" data-public-key="pk_..." data-token-url="/api/support-token" defer></script>
   ```
   Or programmatically: `SupportPilot.init({ publicKey, apiUrl, getUserToken })`.

Each tenant is isolated: its own KB, providers, secrets, conversations and agents (every DB lookup is tenant-scoped; covered by tests).

## Security model (order access)

The browser and the model are untrusted. A user can only ever see their own orders because:

1. **Identity is signed by the customer's backend.** The widget forwards a short-lived HS256 JWT (`sub` = their customer id; `aud=supportpilot`; `exp` ≤ 1h) minted with a secret only the customer and SupportPilot hold. Wrong secret/audience/expiry, `alg=none`, or another tenant's token → 401. Verified once at session start and stored on the conversation.
2. **The model cannot choose whose data to read.** Tool schemas contain only `order_id`; any extra argument (e.g. an injected `user_id`) is dropped. The verified id is injected server-side from `ToolContext`.
3. **The provider API takes `customer_id` as a mandatory argument**, so there is no "get order by id alone" path. The HTTP provider scopes the upstream URL to the customer and the tool layer re-asserts `order.customer_id == user.id`.
4. **No oracle:** a foreign order and a non-existent order produce byte-identical responses. The model only sees an allow-listed view (no address/payment/owner).
5. Every lookup is written to `audit_log`; malformed ids are rejected before any lookup; guests get `login_required`.

Other controls: per-tenant `allowed_origins` (enforced in prod), separate JWT audiences for user/widget/agent tokens, constant-time key comparison, per-conversation rate limiting, message-size caps, `Shadow DOM` isolation, no `innerHTML` anywhere, prompt-injection rules (tool/KB output is data), `/v1/dev/*` only mounted when `ENV=dev`.

## Human handoff

Triggers: (a) the model calls `escalate_to_human` with a summary; (b) the visitor's "Talk to a human" button; (c) a regex safety net for explicit requests, which bypasses the model entirely; (d) automatically after `max_unresolved_turns` KB misses in a row; (e) model outage/refusal — the customer is never stranded.
Conversation states: `bot → waiting_human → with_human → resolved` (or back to `bot`). While a human owns the chat the bot is silent. Agents see the reason, the summary and any tickets already filed, reply in real time (WebSocket), and can resolve or return the chat to the bot. An optional Slack-compatible webhook pings your team on each handoff.

## Jira

```yaml
tickets:
  provider: jira
  jira: { server: https://acme.atlassian.net, email_env: ACME_JIRA_EMAIL, api_token_env: ACME_JIRA_TOKEN, project_key: SUP, issue_type: Task }
```
Uses `JIRA(server, basic_auth=(email, api_token))` (Jira Cloud API token) in a worker thread. Priority is mapped via `priority_map` and automatically retried without it if the project's create screen doesn't expose the field. The description carries reporter details, the conversation id and the transcript (inside a `{noformat}` block, with user-supplied markers neutralised). To support another tracker, implement the one-method `TicketProvider` protocol (`integrations/tickets/base.py`).

## Project layout

```
backend/app/
  agent/        orchestrator (tool loop, handoff logic), tools (+authz), prompts
  api/          widget.py, agent_console.py, admin.py, dev.py, deps.py, ws.py
  integrations/ orders/{demo,http}, tickets/{jira_provider,memory}, notify.py
  llm/          Claude client (official SDK), demo model
  rag/          chunker, BM25 index
  tenancy/      tenant schema + registry      services/  conversations, handoff, realtime hub
backend/tenants/acme/   sample customer (config, knowledge, demo orders)
frontend/src/   widget/ (embeddable, Shadow DOM)  console/ (agent UI)  demo/ (sample storefront)  shared/
```

## Tests and what is (and isn't) verified

* `cd backend && pytest` — 62 tests: retrieval, order authorization (owner / other user / guest / injected args / PII / audit), token forgery, origin rules, tenant isolation, ticket creation + dedupe, Jira provider (fake client), every handoff path, admin upload (incl. path traversal), WebSocket delivery, Claude client parsing.
* `cd frontend && npm run typecheck && npm test && npm run build`.
* A real-browser run (Playwright/Chromium) exercised: KB answer + citation, Alice vs Bob vs guest order lookups, ticket card, handoff, live agent↔customer messaging via the console, resolve, and the one-tag embed on a hostile-CSS page.

**Not verified in the build environment** (no credentials/infrastructure there): calls to the real Claude API (the client is unit-tested against a stubbed SDK, and the orchestration against a scripted model), a real Jira instance (tested against a fake `JIRA` object), the Docker build, and Postgres. Do a smoke test of those four before go-live.

## Production checklist & known limits

* Set `ENV=prod`, a strong stable `SERVER_SECRET`, `ADMIN_API_KEY`, `DATABASE_URL=postgresql+asyncpg://…`, real `allowed_origins`; terminate TLS in front.
* The realtime hub and rate limiter are in-process: run one worker, or swap them for Redis pub/sub / Redis counters to scale out. Two agents claiming the same chat in the same instant isn't locked at the DB level (add `SELECT … FOR UPDATE` on Postgres).
* Retrieval is BM25 (zero infrastructure, great for FAQ/policy docs). For paraphrase-heavy corpora swap `KnowledgeBase` for embeddings/pgvector — the `search()` contract is the only seam. Only `.md/.txt` ingestion today (PDF/HTML ingestion is a natural next step).
* Replies are not streamed token-by-token yet (the widget shows a typing indicator). Order-ownership lives in your backend's data; if your order API ignores the customer scoping, SupportPilot's checks still hold on its side but you should enforce it there too.
* Model: defaults to `claude-opus-5-5`; `claude-sonnet-5-5` / `claude-haiku-5-5` are cheaper/faster options via `ANTHROPIC_MODEL`. Adaptive thinking is on by default for these models; `ANTHROPIC_EFFORT` tunes depth/cost.
* Add auth/SSO for the agent console (currently per-agent access keys) and email/Slack notification routing for your org.
