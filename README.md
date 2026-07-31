# Anything.AI (Rabbit Hole Mapper)

A research intelligence platform that maps complex topics into knowledge graphs
with automated claim extraction, contradiction detection, timeline building, and
AI-powered narrative synthesis.

## What It Does

Give it a topic, and it will:

1. **Expand** the topic into diverse search angles via LLM
2. **Crawl** articles, Reddit threads, YouTube videos, and academic papers
3. **Extract** atomic claims with source provenance from each source
4. **Detect contradictions** between claims from different sources
5. **Build timelines** from temporal expressions in claims
6. **Score credibility** of each source based on domain reputation and source type
7. **Answer questions** via RAG grounded strictly in the extracted evidence
8. **Generate narratives** — a documentary walkthrough and a Devil's Advocate brief
9. **Export** to Obsidian vault, JSON, or PDF

## Architecture

```
Browser Extension ──→ FastAPI Gateway ──→ Celery Workers
                          │                    │
                    ┌─────┼─────┐      ┌───────┼───────┐
                    │     │     │      │       │       │
                 Neo4j  Qdrant  PostgreSQL  Redis  S3/MinIO
                 (graph) (vectors) (app state) (pub/sub) (blobs)
```

- **API Gateway**: FastAPI with Clerk JWT auth, WebSocket hub, rate limiting
- **Workers**: Celery with 3 queues (crawl, extraction, contradiction)
- **Graph DB**: Neo4j 5.x with APOC + GDS
- **Vector DB**: Qdrant for semantic claim search
- **App DB**: PostgreSQL for users, rabbit holes, sources, jobs
- **Cache/PubSub**: Redis for WebSocket fanout + Celery broker
- **Object Storage**: MinIO (S3-compatible) for raw HTML/text blobs
- **LLM**: Anthropic Claude (Haiku for bulk, Sonnet for high-stakes)
- **Embeddings**: Voyage AI (preferred) or OpenAI (fallback)

## Quick Start

### Prerequisites

- Docker and Docker Compose
- An Anthropic API key (required for all LLM operations)

### Setup

```bash
# 1. Clone the repo
git clone https://github.com/shaikahad-tech/Anything.AI.git
cd Anything.AI

# 2. Configure environment
cp .env.example .env
# Edit .env — set ANTHROPIC_API_KEY at minimum

# 3. Start all infrastructure + services
cd infra/docker
docker compose up -d

# 4. Install shared packages (for local dev)
pip install -e packages/shared-types -e packages/llm-prompts

# 5. Initialize Neo4j schema
python packages/graph-schema/init_schema.py

# 6. Run database migrations
cd api && alembic upgrade head && cd ..
```

### Running locally (without Docker)

```bash
# Terminal 1: Start infrastructure
cd infra/docker && docker compose up -d postgres neo4j redis qdrant minio

# Terminal 2: Start API
cd api
uvicorn app.main:app --reload

# Terminal 3: Start Celery workers
celery -A workers.celery_app worker -Q crawl,extraction,contradiction --loglevel=info
```

- API: http://localhost:8000/docs
- Neo4j Browser: http://localhost:7474
- MinIO Console: http://localhost:9001
- Flower (Celery monitor): http://localhost:5555

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/rabbitholes` | Create a new rabbit hole |
| GET | `/api/v1/rabbitholes` | List your rabbit holes |
| GET | `/api/v1/rabbitholes/{id}` | Get rabbit hole details |
| GET | `/api/v1/rabbitholes/{id}/graph` | Get the knowledge graph |
| GET | `/api/v1/rabbitholes/{id}/timeline` | Get timeline of events |
| GET | `/api/v1/rabbitholes/{id}/contradictions` | Get contradiction pairs |
| POST | `/api/v1/rabbitholes/{id}/expand` | Expand a specific node |
| POST | `/api/v1/rabbitholes/{id}/narrative` | AI documentary narrative |
| POST | `/api/v1/rabbitholes/{id}/devils-advocate` | Devil's Advocate brief |
| DELETE | `/api/v1/rabbitholes/{id}` | Delete a rabbit hole |
| POST | `/api/v1/chat` | RAG chat over a rabbit hole |
| POST | `/api/v1/export/{id}` | Export (Obsidian/PDF/JSON) |
| GET | `/api/v1/sources/{id}/credibility` | Source credibility breakdown |
| WS | `/ws/rabbitholes/{id}` | Live graph updates stream |

## Browser Extension

A Chrome MV3 extension lets you right-click any page to seed a new rabbit hole.
Load it via `chrome://extensions` → Load unpacked → select `extension/`.

## Project Structure

```
├── api/                    # FastAPI gateway
│   ├── app/
│   │   ├── main.py          # App factory
│   │   ├── config.py        # Pydantic settings
│   │   ├── auth.py          # Clerk JWT validation
│   │   ├── routers/         # REST + WebSocket routes
│   │   ├── services/        # Business logic (graph, RAG, narrator, export)
│   │   ├── db/              # PostgreSQL + Neo4j clients
│   │   └── middleware/      # Rate limiting
│   ├── alembic/             # DB migrations
│   └── requirements.txt
├── workers/                 # Celery workers
│   ├── workers/
│   │   ├── celery_app.py    # Celery config + queue routing
│   │   ├── tasks/           # crawl_*, extraction, contradiction, timeline, credibility
│   │   └── utils/           # pubsub, db, s3
│   └── requirements.txt
├── packages/
│   ├── shared-types/       # rhm_shared_types — Pydantic models
│   ├── llm-prompts/         # rhm_llm_prompts — versioned prompt templates
│   └── graph-schema/        # Neo4j constraints + init script
├── extension/               # Chrome extension
├── infra/docker/            # docker-compose.yml + prometheus
└── docs/ARCHITECTURE.md    # Canonical architecture reference
```

## Tech Stack

- **Backend**: Python 3.12, FastAPI, Celery, SQLAlchemy 2.0 (async)
- **Databases**: Neo4j 5.x, PostgreSQL 16, Qdrant, Redis
- **LLM**: Anthropic Claude (Haiku + Sonnet)
- **Embeddings**: Voyage AI / OpenAI
- **NLP**: spaCy (NER), trafilatura (text extraction)
- **Crawlers**: httpx, PRAW, YouTube Data API, arXiv API
- **Storage**: MinIO (S3-compatible)
- **Observability**: Sentry, OpenTelemetry, Langfuse, Prometheus

## License

Proprietary. All rights reserved.
