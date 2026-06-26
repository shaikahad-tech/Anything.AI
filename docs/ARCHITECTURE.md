# Rabbit Hole Mapper — Architecture Reference

> This file is the canonical source of truth for system design decisions, data schemas,
> API contracts, and pipeline logic. All services should be built consistent with this document.

---

## 1. Locked-In Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Orchestration | **Celery + Redis** | Temporal adds ops overhead; Celery retry/backoff/chord covers all pipeline needs |
| Auth scope | **Multi-tenant SaaS** from day 1 | Multiplayer + billing require it; Clerk for JWT auth |
| Hosting | **Docker Compose (dev) → Railway/Fly.io (prod)** | Avoids k8s overhead until load demands it |
| LLM routing | **Claude Haiku for bulk extraction, Claude Sonnet for contradiction + synthesis** | Best quality/cost; Anthropic SDK throughout |
| Vector DB | **Qdrant** | Better production filtering than pgvector at this scale |
| Graph DB | **Neo4j 5.x + APOC + GDS** | Native graph algorithms, full-text indexes |

---

## 2. System Architecture

```
                              ┌─────────────────────┐
                              │   Browser Extension   │
                              └──────────┬───────────┘
                                         │
┌──────────────┐    ┌──────────────────┴──────────────────┐
│   Frontend    │◄──►│            API Gateway (FastAPI)      │
│ React+ReactFlow│    │  auth · rate limiting · WS hub        │
└──────────────┘    └───────┬───────────────────┬───────────┘
                            │                   │
                  ┌─────────▼────────┐  ┌───────▼─────────┐
                  │ Ingestion         │  │  RAG / Chat      │
                  │ Orchestrator      │  │  Service         │
                  │ (Celery)          │  └───────┬─────────┘
                  └───┬────┬────┬────┘          │
          ┌───────────┘    │    └───────────┐    │
   ┌──────▼─────┐  ┌──────▼─────┐  ┌────────▼───┐│
   │  Crawler    │  │ Extraction │  │ Contradiction││
   │  Workers    │  │  Service   │  │   Engine     ││
   │ (per-source)│  │ (NER+LLM)  │  │ (NLI + LLM)  ││
   └──────┬─────┘  └──────┬─────┘  └────────┬─────┘│
          │               │                  │      │
          └───────┬───────┴───────┬──────────┘      │
                  │               │                 │
           ┌──────▼──────┐ ┌──────▼──────┐   ┌──────▼──────┐
           │   Neo4j     │ │   Qdrant    │   │  PostgreSQL  │
           │ (graph)     │ │ (vectors)   │   │ (app state)  │
           └─────────────┘ └─────────────┘   └─────────────┘

   Cross-cutting: Redis (cache + pub/sub) · S3/MinIO (raw blobs)
   Observability: Langfuse · Prometheus · Sentry · OpenTelemetry
```

---

## 3. Repository Layout

```
rabbit-hole-mapper/
├── apps/
│   ├── api/                   # FastAPI gateway — auth, REST API, WebSocket hub
│   │   ├── app/
│   │   │   ├── main.py        # FastAPI app factory
│   │   │   ├── config.py      # pydantic-settings Settings
│   │   │   ├── auth.py        # Clerk JWT validation
│   │   │   ├── pubsub.py      # Redis pub/sub for WebSocket fanout
│   │   │   ├── telemetry.py   # OpenTelemetry (optional)
│   │   │   ├── db/
│   │   │   │   ├── postgres.py      # Async SQLAlchemy session factory
│   │   │   │   ├── neo4j_client.py  # Parameterized Cypher query runner
│   │   │   │   └── models.py        # ORM: users, rabbit_holes, sources, jobs, billing
│   │   │   ├── routers/       # FastAPI routers (one file per resource)
│   │   │   ├── services/      # Business logic (graph, ingestion, RAG, export, narrator)
│   │   │   └── middleware/    # Rate limiting
│   │   └── alembic/           # PostgreSQL migrations
│   ├── workers/               # Celery workers
│   │   └── workers/
│   │       ├── celery_app.py  # Celery app + queue routing
│   │       ├── tasks/         # One file per pipeline stage
│   │       └── utils/         # Shared utilities (pubsub, s3, db)
│   ├── web/                   # React + ReactFlow frontend
│   │   └── src/
│   │       ├── api/client.ts  # Typed fetch wrapper
│   │       ├── store/         # Zustand global state
│   │       ├── components/    # React components + graph nodes/edges
│   │       ├── hooks/         # Custom hooks (WebSocket)
│   │       ├── pages/         # Route-level components
│   │       └── utils/         # Layout algorithms
│   └── extension/             # Chrome MV3 browser extension
├── packages/
│   ├── shared-types/          # Pydantic models — rhm_shared_types package
│   │   └── rhm_shared_types/  # Installable: pip install -e packages/shared-types
│   ├── llm-prompts/           # Versioned prompt templates — rhm_llm_prompts package
│   │   └── rhm_llm_prompts/   # Installable: pip install -e packages/llm-prompts
│   └── graph-schema/          # Neo4j Cypher constraints + init script
├── infra/
│   └── docker/
│       ├── docker-compose.yml # Full stack (all 8 infra services + 3 app services)
│       └── prometheus.yml     # Scrape config
├── tests/
│   ├── api/                   # pytest: API endpoints + service unit tests
│   └── workers/               # pytest: worker unit tests (no infra needed)
└── pytest.ini                 # pythonpath, asyncio_mode, test env vars
```

---

## 4. Service Responsibilities

| Service | File(s) | Responsibility |
|---|---|---|
| API Gateway | `apps/api/app/main.py` | Auth, routing, WebSocket fanout |
| Ingestion Orchestrator | `services/ingestion.py` | Topic → query expansion → Celery fan-out |
| Crawler Workers | `tasks/crawl_*.py` | One per source type, isolated failure domains |
| Extraction Service | `tasks/extraction.py` | Chunk → NER → LLM claims → Neo4j + Qdrant |
| Contradiction Engine | `tasks/contradiction.py` | Similarity cluster → LLM NLI → CONTRADICTS edges |
| Timeline Engine | `tasks/timeline.py` | Temporal extraction → Event nodes + PRECEDES edges |
| Credibility Service | `tasks/credibility.py` | Domain reputation + source type scoring |
| Graph Service | `services/graph.py` | Neo4j read/write, provenance enforcement |
| RAG Service | `services/rag.py` | Qdrant retrieval + Claude answer |
| Narrator Service | `services/narrator.py` | Documentary narrative + Devil's Advocate |
| Export Service | `services/export.py` | Obsidian vault ZIP, JSON snapshot, S3 presigned URL |

---

## 5. Neo4j Data Model

### Node Labels

```cypher
(:Topic       {id, name, rabbit_hole_id, created_at})
(:Source      {id, url, source_type, title, author, published_at,
               credibility_score, bias_score, retrieved_at, s3_blob_key, rabbit_hole_id})
(:Claim       {id, text, confidence, sentiment, stance,
               source_span_text, source_span_start, source_span_end, rabbit_hole_id})
(:Person      {id, name, aliases[], wikidata_id, rabbit_hole_id})
(:Organization{id, name, aliases[], rabbit_hole_id})
(:Event       {id, title, date_start, date_end, location, description, rabbit_hole_id})
```

### Relationship Types

```cypher
(:Source)-[:CONTAINS_CLAIM]->(:Claim)                    -- provenance invariant
(:Claim)-[:CONTRADICTS {confidence, llm_rationale}]->(:Claim)
(:Claim)-[:SUPPORTS {confidence}]->(:Claim)
(:Claim)-[:MENTIONS]->(:Person | :Organization | :Event)
(:Person)-[:AFFILIATED_WITH {role, start_date, end_date}]->(:Organization)
(:Event)-[:PRECEDES]->(:Event)
(:Event)-[:INVOLVES]->(:Person | :Organization)
(:Source)-[:CITES]->(:Source)
```

### Hard Invariants (enforced in write path, not just UI)

1. **Every `:Claim` must have exactly one `CONTAINS_CLAIM` edge** before it is allowed
   to render. `GraphService.enforce_claim_provenance()` provides a double-check.
   Claims extracted without `source_span_text` are dropped in `extraction.py` before writing.
2. **Every `CONTRADICTS` edge must carry `confidence` and `llm_rationale`** — never bare.
3. **No string-built Cypher** — all queries go through `neo4j_client.run_query()` with
   parameters. Node labels and relationship types used in dynamic MERGE statements come from
   internal enums, never from user input.

---

## 6. PostgreSQL Schema

Tables: `users`, `rabbit_holes`, `sources`, `jobs`, `audit_log`, `billing_usage`

Migration: `apps/api/alembic/versions/001_initial_schema.py`

---

## 7. Celery Queue Layout

| Queue | Workers | Tasks |
|---|---|---|
| `crawl` | `worker-crawler` (4 concurrency) | expansion, all crawl_* tasks |
| `extraction` | `worker-extraction` (2 concurrency) | extraction, timeline |
| `contradiction` | `worker-contradiction` (2 concurrency) | contradiction, credibility |

**Durability:** `task_acks_late=True` + `task_reject_on_worker_lost=True` — a worker crash
never silently drops a job. The job remains in the queue for the next worker pickup.

---

## 8. Shared Packages

Both `rhm_shared_types` and `rhm_llm_prompts` are installable Python packages.

```bash
# Install for development
pip install -e packages/shared-types
pip install -e packages/llm-prompts
```

**Prompt versioning convention:**
- Constants are `SCREAMING_SNAKE_CASE_V{N}` (e.g., `CLAIM_EXTRACTION_V1`)
- Import from `rhm_llm_prompts.prompts`, never inline in service code
- Roll back by bumping the import version constant

---

## 9. API Contract

```
POST   /api/v1/rabbitholes                 { topic, depth, source_types[] } → { id }
GET    /api/v1/rabbitholes                 → [{ id, topic, status, ... }]
GET    /api/v1/rabbitholes/{id}            → RabbitHoleResponse
GET    /api/v1/rabbitholes/{id}/graph      → { nodes[], edges[] }   (React Flow shape)
GET    /api/v1/rabbitholes/{id}/timeline   → { events[] }
GET    /api/v1/rabbitholes/{id}/contradictions → { pairs[] }
POST   /api/v1/rabbitholes/{id}/expand     { node_id, depth } → 202 Accepted
POST   /api/v1/rabbitholes/{id}/narrative  → { text, type: "narrative" }
POST   /api/v1/rabbitholes/{id}/devils-advocate → { text, type: "devils_advocate" }
DELETE /api/v1/rabbitholes/{id}            → 204 No Content
POST   /api/v1/chat                        { rabbit_hole_id, message } → RAG answer
POST   /api/v1/export/{id}                 { format: obsidian|pdf|json }
GET    /api/v1/sources/{id}/credibility    → breakdown

WS     /ws/rabbitholes/{id}                → streams { type, rabbit_hole_id, payload }
WS     /ws/collab/{id}                     → Yjs CRDT + cursor positions
```

---

## 10. LLM Model Routing

| Operation | Model | Reason |
|---|---|---|
| Query expansion | `claude-haiku-4-5` | Low stakes, high volume |
| Claim extraction | `claude-haiku-4-5` | High volume, cheap |
| Temporal extraction | `claude-haiku-4-5` | Structured, low stakes |
| Contradiction adjudication | `claude-sonnet-4-5` | High stakes — visible errors |
| Documentary narrative | `claude-sonnet-4-5` | Quality synthesis |
| Devil's Advocate | `claude-sonnet-4-5` | Quality synthesis |
| RAG chat answers | `claude-haiku-4-5` | Interactive latency-sensitive |

**Token budget:** `MAX_COST_PER_RABBIT_HOLE = $2.00` (configurable in `.env`)

---

## 11. Phase Build Roadmap

| Phase | Scope | Status |
|---|---|---|
| 1 | Core backbone: article crawler, extraction, Neo4j write, Clean-mode React Flow | ✅ Built |
| 2 | Reddit, YouTube, papers crawlers + entity resolution | ✅ Built |
| 3 | Contradiction engine + credibility scoring | ✅ Built |
| 4 | Timeline engine + Board-mode UI | ✅ Built |
| 5 | RAG chat + Narrator + Devil's Advocate | ✅ Built |
| 6 | Collaboration, Obsidian/PDF export, browser extension | ✅ Built |
| 7 | Scale, billing, full observability stack | 🔲 Planned |

---

## 12. Definition of Done Checklist

- [x] Every visible claim traces to a specific source span — zero orphan claims
- [x] Contradiction edges always carry a rationale and confidence score, never bare
- [x] Clean mode and Board mode both functional on the same underlying graph
- [x] Crawl jobs survive worker restarts (`task_acks_late=True`, `task_reject_on_worker_lost=True`)
- [x] No Cypher built via string concatenation in the codebase
- [x] CI blocks merge on failing tests or lint (`.github/workflows/ci.yml`)
- [x] Export to Obsidian produces vault with working backlinks
- [ ] Cost-per-rabbit-hole tracked in admin dashboard (Phase 7)
- [ ] Full multiplayer CRDT (Phase 6 polish)

---

## 13. Local Development Setup

```bash
# 1. Clone and configure
cp .env.example .env
# Minimum required: ANTHROPIC_API_KEY (all others have dev defaults)

# 2. Start all infrastructure
cd infra/docker
docker compose up -d

# 3. Set up Python environment
python3 -m venv .venv
source .venv/bin/activate
pip install -e packages/shared-types -e packages/llm-prompts

# 4. Initialize Neo4j schema
python packages/graph-schema/init_schema.py

# 5. Run Alembic migrations
cd apps/api
alembic upgrade head

# 6. Run tests (no infra needed for unit tests)
cd ../..
pytest tests/workers/ tests/api/test_graph_service.py -v

# 7. Start frontend
cd apps/web && npm install && npm run dev

# 8. Start API (separate terminal)
cd apps/api && uvicorn app.main:app --reload
```

The API is at http://localhost:8000/docs · React app at http://localhost:3000
