# Multi-Agent PR Reviewer

> An event-driven, multi-agent system that reviews GitHub pull requests the way a senior engineer would — with parallel specialist agents for security, code quality, testing, and documentation.

This is a hands-on study of **production system design for AI agents**: not "an LLM in a loop," but the reliability engineering around it — webhook verification, latency isolation, idempotency, verification gates, and cost control. Load-bearing components are hand-built for understanding; the goal is to learn *why* each production pattern exists, not just to wire one up.

**Status:** Phases 1–3 complete; Phase 4a (single-agent review) complete and running end to end; Phase 4b onward planned. Every phase is broken into small, individually tested steps — see the [Build log](#build-log) for the full brick-by-brick decomposition, which doubles as a study map of the project.

---

## Architecture

The system is an event-driven pipeline with one fan-out/fan-in in the middle. `✅` = built and tested, `[planned]` = not yet built:

```
GitHub PR opened / updated
   │  webhook (HTTP POST)
   ▼
FastAPI receiver ──► verify HMAC ──► dedupe delivery ──► return 202 fast   ✅
   │  (enqueue job)
   ▼
Redis queue ──► ARQ worker picks up job                                    ✅
   │
   ▼
Fetch PR diff + changed files (GitHub API)                                 ✅
   │
   ▼
LangGraph StateGraph
   ├─ Security agent                                                       ✅
   ├─ Code-quality agent   ┐                                        [planned]
   ├─ Testing agent        │  fan-out (parallel)                    [planned]
   ├─ Docs agent           ┘                                        [planned]
   └─ Aggregator ──► fan-in: merge + dedupe + confidence-gate       [planned]
   │
   ▼
Post review comments + approval gate + persist                     [planned]
```

Two ideas carry the design:

- **Fast-ack.** The web process does only fast, bounded work (verify → dedupe → enqueue → `202`) and hands slow LLM work to a separate worker. GitHub times webhooks out, and a blocked request path exhausts the server under concurrent PRs — so the review never runs inline.
- **Fan-out / fan-in.** Each specialist agent reads the same PR state and writes findings into a shared list; a reducer merges the parallel writes. An independent aggregator then scrutinizes those findings (maker ≠ checker) and attaches confidence scores. (Today only the security agent is live; the fan-out is Phase 4b.)

---

## Build log

The project is built brick by brick — each step is implemented and tested in isolation (happy path *and* failure path) before the next. This section is the full decomposition; it's meant to be read as a study map of how the system was assembled.

### Phase 1 — Webhook ingestion ✅
*Files: `app/main.py`, `scripts/send_test_webhook.py`*

- **1a — Receiver + verification.** FastAPI `/webhook` endpoint: HMAC-SHA256 verification over the *raw* request body (constant-time compare), event/action filtering (`pull_request`; `opened`/`synchronize`/`reopened`), fast `202` acknowledgement.
- **1b — Local test harness.** Signs payloads and exercises the security gate three ways: valid → `202`, tampered body → `403`, missing signature → `403`.

### Phase 2 — Async pipeline ✅
*Files: `docker-compose.yml`, `app/queue.py`, `app/worker.py`, `app/main.py`*

- **2a — Stand up Redis.** Redis via Docker Compose; a shared `RedisSettings` (`app/queue.py`); reachability proven with `redis-cli ping` → `PONG`.
- **2b — Enqueue + consume.** The web process enqueues onto Redis via a lifespan-managed ARQ pool; idempotent dedupe using `SET NX` on the `X-GitHub-Delivery` ID; a separate ARQ worker consumes `review_pr`. Proven: a job crosses the process boundary; a duplicate delivery ID is dropped before it enqueues.

### Phase 3 — PR context fetch ✅
*Files: `app/config.py`, `app/github_client.py`, `scripts/test_github_client.py`, `app/worker.py`, `.env.example`*

- **3a — Centralized config.** `pydantic-settings` + `.env` as a single source of truth; typed, validated settings; optional per-process secrets; fail-fast at boot for required values. `.env.example` documents required config.
- **3b — GitHub API client.** Fetches PR metadata + changed files; a deliberate *diff budget* (cap file count, skip generated/vendored paths, truncate oversized patches); typed error handling (`401`/`403`/`404`/rate-limit → `GitHubError`). Tested in isolation against a real PR, plus a `404` failure path.
- **3c — Wire into the worker.** `review_pr` calls `GitHubClient().fetch_pr()`, catches `GitHubError`, and re-raises so ARQ marks the job failed rather than swallowing it. Proven end to end: webhook → queue → worker → live GitHub fetch.

### Phase 4 — Multi-agent graph — 4a ✅ / 4b 🔲
*Files: `app/graph.py`, `scripts/test_security_agent.py`, `app/config.py`, `app/worker.py`*

- **4a — Single agent, end to end ✅**
  - **4a-1 — Config.** Add `ANTHROPIC_API_KEY` (optional per-process secret); install `langgraph` + `langchain-anthropic`.
  - **4a-2 — State + Finding model.** Graph `State` (`PRState`) and a Pydantic `Finding` (file / line / category / severity / confidence / message). `findings` uses an `Annotated[list, add]` reducer so parallel writes merge instead of overwrite; an `AgentResponse` wrapper enables list-valued structured output.
  - **4a-3 — Security agent node.** Claude via `ChatAnthropic().with_structured_output()`; a prompt that demands honest confidence and an empty list on a clean PR; the node force-stamps `category="security"` rather than trusting the model to self-label. Tested both ways: benign PR → 0 findings, planted-secret PR → caught with high confidence.
  - **4a-4 — Assemble + invoke.** Compile the review graph (`START → security → END`) once at import; invoke it from the worker after the fetch. Proven end to end: webhook → queue → fetch → graph → Claude → structured findings.
- **4b — Fan-out to specialists 🔲**
  - Add quality / testing / docs agent nodes (near-clones of the security node with different prompts and categories).
  - Wire all four from `START` to run in parallel (fan-out); merge their findings via the reducer (fan-in).
  - Aggregator node: scrutinize findings (maker ≠ checker), dedupe overlaps, confidence-gate.

### Phase 5 — Unified data layer 🔲
- Postgres (Tiger Cloud): relational truth (PRs, findings), `pgvector` for semantic code search, time-series for event/token traces.
- Proper delivery-status tracking (`accepted` / `in-progress` / `done` / `failed`), which closes the Phase-2 dedupe gap (see [Known gaps](#known-gaps-deliberately-deferred)).

### Phase 6 — Results + approval gates 🔲
- Post review comments back to the PR via the GitHub API.
- Confidence-threshold approval queue: auto-post high-confidence findings, hold low-confidence ones for human review.
- Independent verifier sub-agent / regression checks.

### Phase 7 — Observability & cost 🔲
- LangSmith tracing.
- Token-economics view / per-PR cost.
- Guardrails against hallucination and runaway spend.

---

## Tech stack

**In use**
- Python 3.12
- FastAPI + Uvicorn (webhook receiver)
- Redis + ARQ (queue and async worker)
- `pydantic-settings` (centralized config), `httpx` (GitHub API client)
- LangGraph + `langchain-anthropic`, Claude via the Anthropic API (agent graph)
- Docker / Docker Compose (local Redis)

**Planned**
- PostgreSQL with `pgvector` + time-series, via Tiger Cloud (unified data layer)
- LangSmith (tracing / observability)

---

## Getting started

### Prerequisites
- Python 3.12
- Docker Desktop (running)
- A GitHub fine-grained PAT with **read** access to Pull requests + Contents
- An **Anthropic API key** (workspace-scoped)

### Setup

```bash
git clone https://github.com/Colin-J-Emmanuel/multi-agent-pr-reviewer.git
cd multi-agent-pr-reviewer

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env             # then fill in the values below
```

Config lives in `.env` (never committed):

| Variable | Purpose |
|----------|---------|
| `GITHUB_WEBHOOK_SECRET` | Shared secret set on the GitHub webhook; used for HMAC verification |
| `GITHUB_TOKEN` | Fine-grained PAT (read: PRs + contents); the worker uses it to fetch diffs |
| `ANTHROPIC_API_KEY` | Workspace-scoped Anthropic key; the worker uses it to call Claude |
| `REDIS_URL` | Defaults to `redis://localhost:6379` |

### Run it

Three processes (venv active, from the repo root — secrets load from `.env`):

```bash
docker compose up -d                 # 1. Redis
uvicorn app.main:app --reload        # 2. Web receiver
arq app.worker.WorkerSettings        # 3. Worker (separate terminal)
```

### Test it

The HMAC gate, no GitHub or tunnel required (the harness holds the secret itself, standing in for GitHub):

```bash
GITHUB_WEBHOOK_SECRET=<your-secret> python scripts/send_test_webhook.py
```

Expected:

```
valid            -> 202 {"status":"accepted","delivery_id":"test-delivery-001"}
tampered         -> 403 {"detail":"Invalid signature"}
no-signature     -> 403 {"detail":"Missing signature"}
```

Send it again without flushing Redis and `valid` becomes `202 duplicate` (idempotency). Reset the dedupe key with `docker exec -it pr-reviewer-redis redis-cli FLUSHALL`.

Component tests (each exercises one piece in isolation against a real PR):

```bash
python -m scripts.test_github_client  <owner/repo> <pr_number>   # PR fetch
python -m scripts.test_security_agent <owner/repo> <pr_number>   # security agent
```

---

## Design decisions

The choices worth explaining, and why they were made:

- **Verify HMAC over the raw request body, in constant time.** The signature covers the exact bytes GitHub sent, so the body must be read before JSON parsing (re-serializing changes the bytes and breaks the match). Comparison uses `hmac.compare_digest`, not `==`, to avoid leaking timing information an attacker could use to forge a signature.
- **Fast-ack, then queue.** The webhook handler never does LLM work. It verifies, enqueues, and returns `202` in milliseconds; a separate worker does the slow review. This is latency isolation — it keeps the request path bounded and prevents thread-pool exhaustion under load.
- **Idempotent enqueue via `SET NX`.** GitHub retries and can redeliver. Each delivery carries a unique ID; recording it with `SET NX` means a duplicate delivery is dropped before it costs an LLM call.
- **Centralized, validated config with per-process secrets.** One `pydantic-settings` source of truth. Secrets are optional at the type level and required only where used, so the web receiver boots without a model key while the worker fails with a clear message if its key is missing — the right secret needed by the right process, validated fail-fast.
- **A diff budget in the GitHub client.** File-count cap, skip lists for generated/vendored files, and patch truncation decide *what the reviewer pays attention to*. A naive "review everything" falls over on large PRs (cost, latency, noise); the budget keeps it bounded.
- **Structured LLM output for findings.** `with_structured_output()` + a Pydantic `Finding` returns typed, validated objects (`confidence` constrained to `[0,1]`) instead of prose to parse — "computed, not narrated." The node also force-stamps its own category rather than trusting the model to self-label.
- **A reducer on `findings`.** `Annotated[list, operator.add]` merges parallel agent writes instead of letting the last one overwrite the rest — the mechanism the Phase-4b fan-out depends on.
- **Separate, single-purpose processes.** Receiver and worker are distinct so they can fail, scale, and be reasoned about independently.
- **Pinned dependencies + Dependabot.** `requirements.txt` pins exact versions for reproducible builds; Dependabot opens grouped bump PRs so pins don't rot.

### Known gaps (deliberately deferred)

- **Dedupe carries no success/failure state.** The dedupe key is set on *acceptance*, so a worker crash mid-review marks the delivery "seen" and it won't retry. Proper delivery-status tracking arrives with the database in Phase 5.
- **Model/client constructed per call.** `GitHubClient` and `ChatAnthropic` are built fresh on each job (no connection reuse). Invisible at this volume; would be hoisted into worker startup if throughput ever mattered.

---

## Roadmap

Immediate next step is **Phase 4b**: fan the graph out to quality, testing, and docs agents running in parallel, then add the aggregator that merges and confidence-gates their findings — the point where "multi-agent" stops being aspirational. After that: the unified Postgres data layer (Phase 5), posting results with approval gates (Phase 6), and observability with cost guardrails (Phase 7). See the [Build log](#build-log) for the full breakdown.

---

## Acknowledgments

Architecture inspired by the freeCodeCamp course *"Learn System Design for AI Agents: Build a Production-Ready Multi-Agent PR Reviewer"* by Ayush Singh. Implementation and design decisions are my own.

## License

© 2026 Colin Emmanuel. All rights reserved. This is a personal learning project and is not licensed for reuse.

## Author

Colin Emmanuel — [@Colin-J-Emmanuel](https://github.com/Colin-J-Emmanuel) · c.j.emmanuel@columbia.edu