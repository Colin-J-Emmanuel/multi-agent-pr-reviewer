# Multi-Agent PR Reviewer

> An event-driven, multi-agent system that reviews GitHub pull requests the way a senior engineer would — with parallel specialist agents for security, code quality, testing, and documentation.

This is a hands-on study of **production system design for AI agents**: not "an LLM in a loop," but the reliability engineering around it — webhook verification, latency isolation, idempotency, verification gates, and cost control. Load-bearing components are hand-built for understanding; the goal is to learn *why* each production pattern exists, not just to wire one up.

**Status: work in progress.** Ingestion and the async processing pipeline are built and tested. The agent graph and data layer are next. See [Status](#status) for the honest breakdown.

---

## Architecture

The system is an event-driven pipeline with one fan-out/fan-in in the middle:

```
GitHub PR opened / updated
   │  webhook (HTTP POST)
   ▼
FastAPI receiver ──► verify HMAC ──► dedupe delivery ──► return 202 fast
   │  (enqueue job)
   ▼
Redis queue ──► ARQ worker picks up job
   │
   ▼
Fetch PR diff + changed files (GitHub API)        [planned]
   │
   ▼
LangGraph StateGraph                              [planned]
   ├─ fan-out ─┬─ Security agent
   │           ├─ Code-quality agent   (parallel)
   │           ├─ Testing agent
   │           └─ Docs agent
   └─ fan-in ──► Aggregator (dedupe + confidence-score findings)
   │
   ▼
Post review comments + approval gate + persist    [planned]
```

Two ideas carry the design:

- **Fast-ack.** The web process does only fast, bounded work (verify → dedupe → enqueue → `202`) and hands slow LLM work to a separate worker. GitHub times webhooks out, and a blocked request path exhausts the server under concurrent PRs — so the review never runs inline.
- **Fan-out / fan-in.** Each specialist agent reads the same PR state and writes findings into a shared list; a reducer merges the parallel writes. An independent aggregator then scrutinizes those findings (maker ≠ checker) and attaches confidence scores.

---

## Status

| Phase | Component | State |
|-------|-----------|-------|
| 1 | Webhook ingestion — HMAC-SHA256 verification, event/action filtering, fast `202` ack | ✅ Built & tested |
| 2 | Async pipeline — Redis queue, idempotent enqueue (`SET NX`), ARQ worker | ✅ Built & tested |
| 3 | PR context fetch — GitHub API diff + changed files | 🔲 Planned |
| 4 | Multi-agent graph — LangGraph specialists + aggregator + confidence scoring | 🔲 Planned |
| 5 | Unified data layer — Postgres (relational + pgvector + time-series) | 🔲 Planned |
| 6 | Results + approval gates — post comments, confidence-threshold review queue | 🔲 Planned |
| 7 | Observability — tracing, token economics, cost guardrails | 🔲 Planned |

---

## Tech stack

**In use**
- Python 3.12
- FastAPI + Uvicorn (webhook receiver)
- Redis + ARQ (queue and async worker)
- Docker / Docker Compose (local Redis)

**Planned**
- LangGraph (multi-agent orchestration)
- PostgreSQL with pgvector + time-series (unified data layer)
- GitHub REST API client
- LangSmith (tracing / observability)

---

## Getting started

### Prerequisites
- Python 3.12
- Docker Desktop (running)

### Setup

```bash
git clone https://github.com/Colin-J-Emmanuel/multi-agent-pr-reviewer.git
cd multi-agent-pr-reviewer

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Run it

The system runs as three processes. Start Redis, then the web receiver, then the worker:

```bash
# 1. Redis (background)
docker compose up -d

# 2. Web receiver
GITHUB_WEBHOOK_SECRET=<your-secret> uvicorn app.main:app --reload

# 3. Worker (separate terminal, venv activated)
arq app.worker.WorkerSettings
```

### Test it

A local harness signs a payload with a valid HMAC and exercises the security gate — no GitHub or tunnel required:

```bash
GITHUB_WEBHOOK_SECRET=<your-secret> python scripts/send_test_webhook.py
```

Expected output:

```
valid            -> 202 {"status":"accepted","delivery_id":"test-delivery-001"}
tampered         -> 403 {"detail":"Invalid signature"}
no-signature     -> 403 {"detail":"Missing signature"}
```

Send it a second time (without flushing Redis) and the `valid` line becomes `202 duplicate` — the same delivery ID is not processed twice. To reset the dedupe key: `docker exec -it pr-reviewer-redis redis-cli FLUSHALL`.

---

## Design decisions

The choices worth explaining, and why they were made:

- **Verify HMAC over the raw request body, in constant time.** The signature covers the exact bytes GitHub sent, so the body must be read before JSON parsing (re-serializing changes the bytes and breaks the match). Comparison uses `hmac.compare_digest`, not `==`, to avoid leaking timing information an attacker could use to forge a signature.
- **Fast-ack, then queue.** The webhook handler never does LLM work. It verifies, enqueues, and returns `202` in milliseconds; a separate worker does the slow review. This is latency isolation — it keeps the request path bounded and prevents thread-pool exhaustion under load.
- **Idempotent enqueue via `SET NX`.** GitHub retries and can redeliver. Each delivery carries a unique ID; recording it with `SET NX` means a duplicate delivery is dropped before it costs an LLM call.
- **Separate, single-purpose processes.** Receiver and processor are distinct so they can fail, scale, and be reasoned about independently.
- **Pinned dependencies + Dependabot.** `requirements.txt` pins exact versions for reproducible builds; Dependabot opens grouped bump PRs so pins don't rot.

### Known gaps (deliberately deferred)

- The dedupe key is set on *acceptance* and carries no success/failure state — a worker crash mid-review marks the delivery "seen" and won't retry. Proper delivery-status tracking arrives with the database in Phase 5.

---

## Roadmap

Next up (Phase 3) is giving the worker eyes: fetching the PR's diff and changed files from the GitHub API so there's real code to review. From there, the LangGraph agent graph (Phase 4), a unified Postgres data layer (Phase 5), posting results with confidence-threshold approval gates (Phase 6), and observability with token-cost guardrails (Phase 7).

---

## Acknowledgments

Architecture inspired by the freeCodeCamp course *"Learn System Design for AI Agents: Build a Production-Ready Multi-Agent PR Reviewer"* by Ayush Singh. Implementation and design decisions are my own.

## License

  © 2026 Colin Emmanuel. All rights reserved. This is a personal learning project and is not licensed for reuse.

## Author

Colin Emmanuel — [@Colin-J-Emmanuel](https://github.com/Colin-J-Emmanuel) · c.j.emmanuel@columbia.edu