# KnowledgeQL — Engineering Design Document

**Audience:** Backend engineers, frontend engineers, DevOps, and technical leads
**Purpose:** System design rationale, development workflow, scaling strategy, and operational guidelines
**Companion doc:** `TECHNICAL_ARCHITECTURE.md` (deep technical reference)

---

## Table of Contents

1. [System Design Principles](#1-system-design-principles)
2. [Component Architecture & Rationale](#2-component-architecture--rationale)
3. [Development Workflow](#3-development-workflow)
4. [Data Flow & State Management](#4-data-flow--state-management)
5. [Scaling Strategy](#5-scaling-strategy)
6. [Performance Optimization Guidelines](#6-performance-optimization-guidelines)
7. [LLM Cost & Latency Management](#7-llm-cost--latency-management)
8. [Deployment Architecture](#8-deployment-architecture)
9. [Testing Strategy](#9-testing-strategy)
10. [Prompt Engineering Guidelines](#10-prompt-engineering-guidelines)
11. [Monitoring & Observability](#11-monitoring--observability)
12. [Operational Runbooks](#12-operational-runbooks)
13. [Security Considerations](#13-security-considerations)
14. [Future Roadmap & Extension Points](#14-future-roadmap--extension-points)

---

## 1. System Design Principles

### 1.1 Core Constraints

| Constraint | Implication |
|---|---|
| Oracle DB is read-only from the agent's perspective | All writes are user-initiated SQL; pipeline never mutates source data |
| Graph must survive process restarts | Pickle cache on shared volume; TTL-based refresh |
| One LLM call = one reasoning step | Agents are stateless; state lives in `AgentState` dict |
| SSE streams cannot be buffered | nginx/proxy must disable buffering on `/api/query` |
| Single uvicorn worker | Graph singleton in `app.state`; asyncio + thread pool for concurrency |

### 1.2 Design Decisions (ADRs)

**ADR-001: In-memory knowledge graph (no external graph DB)**
*Decision:* `KnowledgeGraph` is a pure-Python dict-of-dicts with NetworkX for path computation.
*Rationale:* Oracle has 1000+ tables. A Neo4j/Neptune instance adds operational complexity, cost, and a network hop on every query. The full graph fits in ~200–500 MB RAM for 2000-table schemas. Persistence is handled by pickle cache.
*Trade-off:* Cannot run multiple backend workers (graph would be duplicated per process). Mitigated by single-worker async uvicorn + thread pool for CPU-bound operations.

**ADR-002: LangGraph for pipeline orchestration**
*Decision:* Agent pipeline is a LangGraph compiled DAG, falling back to sequential execution if LangGraph is unavailable or LLM credentials are absent.
*Rationale:* LangGraph gives per-node streaming events (real step progress in SSE), conditional edges (clarification branching), and future parallelism support. Sequential fallback ensures the system is trainable/testable without credentials.
*Trade-off:* LangGraph adds ~50 MB to the dependency footprint.

**ADR-003: Cumulative query approach for multi-turn clarification**
*Decision:* When a user answers a clarification question, the frontend assembles a single self-contained `user_input` string: original query + all Q&A pairs accumulated so far.
*Rationale:* Sending just the isolated answer (e.g., "last 30 days") as `user_input` breaks entity resolution and enrichment — these nodes have no memory of prior turns. The cumulative string is a complete, unambiguous requirements spec that works with any stateless pipeline node.
*Trade-off:* Token count grows with each clarification turn. Mitigated by conversation_history truncation (last 10 turns passed to LLM).

**ADR-004: Agentic entity extraction (ReAct loop)**
*Decision:* Entity extractor runs a tool-calling loop (up to `MAX_TOOL_CALLS=8`) rather than a single LLM call.
*Rationale:* A single call cannot reliably resolve "customers with pending KYC" to `KYC.CUSTOMER_MASTER`, `KYC.KYC_STATUS` across 1000+ tables. The loop allows the agent to search, inspect table detail, follow FK paths, and confirm before committing.
*Trade-off:* 3–8 LLM calls per query (vs. 1). Managed by importance-ranked schema tree (top 60 tables pre-sorted) so the agent usually finds what it needs in 2–3 calls.

**ADR-005: React SPA with Vite (not Streamlit)**
*Decision:* Replaced Streamlit with a React + FastAPI architecture.
*Rationale:* Streamlit re-runs the entire Python script on every interaction. With 1000+ tables, schema sidebar re-renders all table DOM nodes on every keypress. React + TanStack Virtual renders only visible rows; `staleTime: Infinity` prevents repeated schema fetches.
*Trade-off:* Requires maintaining a separate frontend codebase. Offset by much better UX and ~10× reduction in browser DOM nodes for large schemas.

---

## 2. Component Architecture & Rationale

### 2.1 Backend Layer Map

```
Request → FastAPI router → deps injection → node/service → KnowledgeGraph/OracleDB
                              ↓
                      app.state.pipeline (LangGraph DAG)
                      app.state.graph    (KnowledgeGraph singleton)
                      app.state.config   (AppConfig, reads .env)
```

**Key files:**

| File | Responsibility |
|---|---|
| `backend/main.py` | `lifespan`: load/build graph, build pipeline, register background tasks |
| `backend/deps.py` | `Depends()` providers: `get_graph()`, `get_pipeline()`, `get_config()` |
| `backend/routers/query.py` | POST `/api/query` → SSE stream |
| `backend/routers/schema.py` | GET schema endpoints (tables, columns, search) |
| `backend/routers/graph.py` | GET `/api/graph/visualization` |
| `backend/routers/admin.py` | POST rebuild, GET cache-info, POST rebuild-pipeline |
| `backend/streaming.py` | `stream_pipeline_events()` async generator |
| `agent/pipeline.py` | `build_pipeline(graph, config, llm)` → compiled DAG |
| `agent/nodes/` | One file per LangGraph node |
| `knowledge_graph/` | Graph construction, traversal, cache |

### 2.2 Frontend Layer Map

```
App.tsx
├── AppShell.tsx                  ← tab routing, layout
│   ├── Sidebar.tsx               ← schema tree, history
│   └── <ActiveTab>
│       ├── ChatPanel.tsx         ← query submission, clarification orchestration
│       │   ├── MessageList.tsx
│       │   └── MessageBubble.tsx → SqlResultCard | ClarificationCard
│       ├── SqlEditor.tsx (Monaco)
│       ├── GraphCanvas.tsx (Sigma.js WebGL)
│       ├── RelationshipsPage.tsx
│       ├── HistoryPage.tsx
│       └── PromptStudioPage.tsx
│
Store (Zustand)
├── chatStore.ts                  ← messages, history, clarification state
├── chatHistoryStore.ts           ← persisted sessions
└── traceStore.ts                 ← agent trace (SSE trace events)

API layer
├── query.ts                      ← streamQuery() SSE client
├── schema.ts                     ← REST calls
└── graph.ts                      ← graph visualization fetch
```

### 2.3 Agent Pipeline Nodes

```
[enrich_query]
    Reads kyc_business_knowledge.txt; expands cryptic input to domain-rich query.
    Skipped if QUERY_ENRICHER_ENABLED=false or knowledge file empty.

[classify_intent]
    Categorises query: data_query | schema_exploration | metadata_query | relationship_query
    Used downstream to tune sql_generator behaviour.

[extract_entities] ← AGENTIC LOOP
    ReAct tool-calling: search_schema → get_table_detail → find_join_path
    Produces entity_table_fqns: list of confirmed SCHEMA.TABLE strings.

[retrieve_schema]
    Builds DDL context from entity_table_fqns + join path hints.
    Fast path: uses pre-resolved FQNs directly (skips synonym/fuzzy resolution).

[check_clarification]
    LLM assesses if query is still ambiguous after schema retrieval.
    If needs_clarification=True and no history → emits SSE clarification event → END.
    Skipped entirely when conversation_history is non-empty (already clarifying).

[generate_sql]
    Context: enriched_query + DDL + join hints + conversation_history.
    System prompt: rule set for FQN usage, date handling, ROWNUM vs FETCH FIRST.

[validate_sql]
    sqlglot parse check; catches syntax errors before Oracle round-trip.

[optimize_sql]
    Adds ROWNUM / FETCH FIRST guard; applies hint rules.

[execute_query]
    Live Oracle execution via oracledb thin mode.
    Returns rows, columns, execution_time_ms.

[format_result]
    Structures final response; attaches _trace for frontend DevTools.
```

---

## 3. Development Workflow

### 3.1 Local Setup

```bash
# 1. Python environment
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Oracle (Docker)
docker compose -f docker/docker-compose.yml up oracle -d
# Wait ~60s for Oracle to initialize, then:
docker exec nlp2sql_oracle sqlplus -S kyc/KycPassword1@localhost:1521/FREEPDB1 \
  @/docker-entrypoint-initdb.d/01_create_tables.sql
docker exec nlp2sql_oracle sqlplus -S kyc/KycPassword1@localhost:1521/FREEPDB1 \
  @/docker-entrypoint-initdb.d/02_load_data.sql

# 3. Environment
cp .env.example .env
# Edit: ORACLE_DSN, LLM_PROVIDER, LLM_API_KEY (or VERTEX_PROJECT etc.)

# 4. Backend
uvicorn backend.main:app --reload --port 8000

# 5. Frontend
cd frontend && npm install && npm run dev
# Vite proxy: /api → localhost:8000
```

### 3.2 Branch and PR Conventions

- **Feature branches**: `feat/<area>/<short-name>` (e.g., `feat/agent/multi-step-clarification`)
- **Fix branches**: `fix/<area>/<issue-id>`
- **Prompt changes**: treated as code; go through PR review; include before/after example outputs
- **Tests required**: every new agent node must have unit tests in `tests/`; E2E if Oracle-dependent

### 3.3 Hot-Reloading During Development

| Component | Hot-reload method |
|---|---|
| FastAPI routes | `uvicorn --reload` watches `*.py` |
| Prompt files (`prompts/*.txt`) | `POST /api/admin/rebuild-pipeline` — no restart needed |
| React components | Vite HMR |
| Agent node logic | Requires backend restart (Python caches modules) |
| Knowledge graph | Requires `POST /api/admin/rebuild` or `GRAPH_CACHE_TTL_HOURS` expiry |

### 3.4 Environment Variable Reference (Development)

```env
# Oracle
ORACLE_DSN=localhost:1521/FREEPDB1
ORACLE_USER=kyc
ORACLE_PASSWORD=KycPassword1
ORACLE_SCHEMA=KYC

# LLM — pick one provider
LLM_PROVIDER=anthropic          # openai | anthropic | vertex
LLM_API_KEY=sk-ant-...
LLM_MODEL=claude-sonnet-4-6

# Vertex AI (alternative)
LLM_PROVIDER=vertex
VERTEX_PROJECT=my-gcp-project
VERTEX_LOCATION=us-central1
LLM_MODEL=gemini-2.5-flash
VERTEX_THINKING_BUDGET=1024

# Graph cache
GRAPH_CACHE_PATH=~/.cache/knowledgeql   # default; override for shared volume
GRAPH_CACHE_TTL_HOURS=0                  # 0 = never expire
GRAPH_CACHE_VERSION=1                    # bump to force full rebuild

# Feature flags
QUERY_ENRICHER_ENABLED=true
```

---

## 4. Data Flow & State Management

### 4.1 SSE Event Sequence

A full query execution emits the following SSE events in order:

```
event: step   {"step": "enriching"}
event: step   {"step": "classifying"}
event: step   {"step": "extracting"}      # repeated per tool call
event: step   {"step": "retrieving"}
event: clarification  {"question": "...", "options": [...], "context": "...", "multi_select": false}
  ← OR (no clarification needed) →
event: step   {"step": "generating"}
event: sql    {"sql": "SELECT ..."}       # emitted as soon as SQL is generated
event: step   {"step": "validating"}
event: step   {"step": "optimizing"}
event: step   {"step": "executing"}
event: step   {"step": "formatting"}
event: result {"sql": "...", "columns": [...], "rows": [[...]], "total_rows": N, ...}
  ← OR on error →
event: error  {"message": "..."}
```

The frontend `StreamingIndicator` renders each step label as it arrives. SQL preview appears immediately on `event: sql` before execution starts.

### 4.2 Multi-Turn Clarification State

```
Turn 1: User types "Show me pending KYC customers"
  → Frontend: setActiveBaseQuery("Show me pending KYC customers")
  → Backend: clarification emitted (ambiguous date range)
  → Frontend: addClarificationMessage({question, options, context})

Turn 2: User selects "Last 30 days"
  → Frontend: addClarificationPair("What date range?", "Last 30 days")
  → Frontend: addUserMessage("Last 30 days")
  → Frontend: getCumulativeQuery() returns:
      "Show me pending KYC customers

       Additional requirements clarified:
       - Q: What date range? A: Last 30 days"
  → Backend receives this as user_input (self-contained spec)
  → conversation_history includes {role: user, content: "Last 30 days"}
  → check_clarification skipped (history is non-empty)

Turn 3: (if another clarification needed)
  → getCumulativeQuery() returns:
      "Show me pending KYC customers

       Additional requirements clarified:
       - Q: What date range? A: Last 30 days
       - Q: Include expired documents? A: Yes, show all statuses"
```

### 4.3 Frontend State Stores

```
chatStore (in-memory, lost on page refresh)
├── messages: ChatMessage[]              ← rendered message list
├── history: ConversationMessage[]       ← sent to backend as conversation_history
├── activeBaseQuery: string              ← original query for clarification chain
└── clarificationPairs: {q, a}[]         ← accumulated Q&A for getCumulativeQuery()

chatHistoryStore (persisted to localStorage)
└── sessions: ChatSession[]              ← max 50; restored on "Resume"

traceStore (in-memory)
└── traces: {[queryId]: TraceStep[]}     ← agent reasoning trace from SSE trace events
```

---

## 5. Scaling Strategy

### 5.1 Concurrency Model

**Current:** Single uvicorn worker with asyncio event loop + `ThreadPoolExecutor` for blocking I/O.

```
Concurrent requests → asyncio event loop
                         ↓
              await run_in_executor(ThreadPoolExecutor)
                         ↓
                LangGraph .stream() in thread
                (blocking, releases GIL during Oracle I/O)
```

**Throughput estimate:** With 30-second average query time and default thread pool (min 32 threads, `max_workers` = CPU cores × 4), the system handles ~30–100 concurrent streaming queries on a 4-core machine before thread starvation. This is adequate for internal enterprise use (10–50 concurrent users).

### 5.2 Horizontal Scaling Options

Since the knowledge graph is a singleton in `app.state`, horizontal scaling requires externalising the graph:

**Option A: Shared graph cache (pickle on NFS/EFS) — recommended for most deployments**
```
Load balancer
├── backend-1 (uvicorn, --workers 1)  ←┐
├── backend-2 (uvicorn, --workers 1)  ←┤── reads same graph_{hash}.pkl from shared volume
└── backend-3 (uvicorn, --workers 1)  ←┘
```
All instances load from the same pickle file. Cache invalidation via `POST /api/admin/rebuild` on any instance; other instances detect the new file on next cache miss.

**Option B: Redis-cached graph (future)**
Serialize `KnowledgeGraph` to Redis (MessagePack or JSON) and deserialize per-request. Adds ~50ms per request for deserialization; enables true stateless horizontal scaling.

**Option C: Separate graph service (future)**
Extract `KnowledgeGraph` into a dedicated gRPC service. Other services call it via RPC. Enables independent scaling and graph versioning.

### 5.3 Database Connection Scaling

**Current:** `oracledb` opens a connection per query execution. No connection pool.

**Recommended for production:**
```python
# backend/main.py lifespan — add connection pool
import oracledb
pool = oracledb.create_pool(
    user=config.oracle_user,
    password=config.oracle_password,
    dsn=config.oracle_dsn,
    min=2, max=10, increment=1
)
app.state.oracle_pool = pool
```

With a pool of 10, 10 queries can execute in Oracle simultaneously. Above that, queries wait for a free connection (~10ms for typical OLTP queries).

### 5.4 LLM Throughput

LLM API rate limits are the most likely bottleneck for large enterprise deployments. Mitigations:

| Strategy | Implementation |
|---|---|
| Per-user rate limiting | Middleware using Redis counter: `INCR user:{id}:minute`, check against limit |
| Request queuing | `asyncio.Queue` with max depth; return 429 when full |
| LLM response caching | Hash `(enriched_query, schema_context)` → cache result in Redis (TTL 5 min) |
| Parallel entity tool calls | Batch `get_table_detail` calls when agent identifies multiple candidate tables |
| Reduce MAX_TOOL_CALLS | Tune down to 5 for cost-sensitive environments |

### 5.5 Knowledge Graph Size Limits

| Tables | Graph build time | RAM | Pickle size |
|---|---|---|---|
| 100 | ~5s | ~20 MB | ~1 MB |
| 500 | ~25s | ~80 MB | ~5 MB |
| 1000 | ~60s | ~150 MB | ~10 MB |
| 3000 | ~3 min | ~400 MB | ~30 MB |
| 10000 | ~10 min | ~1.2 GB | ~100 MB |

For schemas >3000 tables:
- Use `TARGET_SCHEMAS` env var to limit extraction to relevant schemas
- Enable `GRAPH_CACHE_PATH` on persistent volume (avoid rebuilding on restart)
- Consider schema partitioning: separate graph instances per business domain

---

## 6. Performance Optimization Guidelines

### 6.1 Backend

**Graph traversal hot paths:**

`find_join_path()` uses NetworkX `shortest_path` over the JOIN_PATH edge subgraph. For 1000+ tables, call it on demand (per query) — do not precompute all pairs at startup (10000 pairs = 100M combinations).

**SQL execution timeout:**

Oracle queries without bounds can run indefinitely. Always wrap in:
```python
cursor.callTimeout = 30000  # 30s timeout (milliseconds)
```

**Batch LLM enhancer calls:**

`_assign_table_importance` batches ≤50 tables per LLM call. If the schema has 2000 tables, this means 40 LLM calls during enhancement. Run in background thread; set `GRAPH_CACHE_VERSION` to force re-enhancement only when needed.

**Schema endpoint caching:**

`/api/schema/tables` results are static between rebuilds. Add an in-memory LRU cache:
```python
from functools import lru_cache

@lru_cache(maxsize=128)
def _cached_table_list(skip: int, limit: int, search: str, schema: str):
    ...
```
Invalidate on `POST /api/admin/rebuild`.

### 6.2 Frontend

**Virtual scroll — critical for large schemas:**

The `useVirtualizer` hook in `Sidebar.tsx` only renders visible rows. Never use `data.pages.flatMap(p => p.items).map(row => <RowComponent>)` — this creates 1000+ DOM nodes. Always pass raw `allTables` array to the virtualizer and render from `virtualizer.getVirtualItems()`.

**`staleTime: Infinity` for schema data:**

Schema data changes only when `POST /api/admin/rebuild` is called. Setting `staleTime: Infinity` prevents TanStack Query from re-fetching on window focus, component mount, or tab switch. After admin rebuild:
```typescript
queryClient.invalidateQueries({ queryKey: ['schema'] })
queryClient.invalidateQueries({ queryKey: ['graph'] })
```

**Graph visualization — node budget:**

`GraphCanvas.tsx` should cap at ~500 nodes for smooth 60fps WebGL rendering. Use `importance_rank` from the backend to keep only top-N tables when the graph exceeds this. The ForceAtlas2 layout runs in a Web Worker via `graphology-layout-forceatlas2/worker` — never run layout on the main thread.

**AG Grid row model:**

For query results >1000 rows, switch to `infiniteRowModel` with server-side pagination instead of loading all rows into the grid at once.

### 6.3 Oracle Query Optimization

**Rule of thumb:** All queries generated by the agent must use at least one indexed column in WHERE clauses. The SQL generator system prompt includes:
- Use indexed columns (PKs, columns with ALL_INDEXES entries) as primary filters
- Avoid `LIKE '%value%'` prefix wildcards on unindexed columns
- Prefer `DATE` range filters over string comparisons

**FETCH FIRST / ROWNUM guard:**

The optimizer node always adds a row limit unless `ORDER BY` or aggregation is present. This prevents runaway SELECTs on 100M-row tables.

---

## 7. LLM Cost & Latency Management

### 7.1 Token Budget Per Query

| Node | Typical input tokens | Typical output tokens |
|---|---|---|
| enrich_query | 1000–2000 | 200–400 |
| check_clarification | 3000–6000 | 100–200 |
| extract_entities (×3–8 tool calls) | 5000–12000 total | 500–1500 total |
| generate_sql | 8000–20000 | 300–800 |
| validate_sql | 2000–4000 (optional LLM) | 100–200 |
| **Total per query** | **~19k–44k tokens** | **~1.2k–3.1k tokens** |

At $3/$15 per million tokens (Sonnet 4):
- Average query: ~30k input + ~2k output ≈ **$0.12 per query**
- At 1000 queries/day: **$120/day** — budget accordingly

### 7.2 Latency Targets

| Phase | P50 | P95 | Notes |
|---|---|---|---|
| Graph build (cold) | 60s | 3 min | Happens once; cached |
| LLM enhancement | 2 min | 10 min | Runs in background |
| Query pipeline (with LLM) | 8s | 30s | 3–5 tool calls typical |
| Query pipeline (no LLM) | 0.5s | 2s | Graph traversal + Oracle only |
| Schema API | 20ms | 100ms | Cached in process |
| Oracle execution | 100ms | 10s | Depends on query complexity |

### 7.3 Reducing LLM Calls

**1. Skip enrichment for explicit queries:**
If `user_input` already contains table names or SQL keywords, `query_enricher` adds little value. Detect with a simple heuristic and pass through:
```python
if re.search(r'\b(SELECT|FROM|WHERE|JOIN)\b', user_input, re.I):
    state["enriched_query"] = user_input  # skip enricher
```

**2. Cache entity extraction results:**
When the same base query is repeated (e.g., in clarification loop), the entity tables don't change. Cache `(enriched_query_hash → entity_table_fqns)` in `app.state` with TTL=5 min.

**3. Disable thinking budget for simple queries:**
Vertex AI `thinking_budget=0` reduces latency by ~40% for simple factual queries. Detect "simple" by intent classification result:
```python
if state.get("intent") == "simple_lookup":
    config.vertex_thinking_budget = 0
```

**4. Reduce max_output_tokens for non-SQL nodes:**
`enrich_query`, `check_clarification` don't need 8192 tokens. Cap at 512:
```python
class QueryEnricher:
    def _call_llm(self, prompt):
        return self.llm.invoke(prompt, max_tokens=512)
```

---

## 8. Deployment Architecture

### 8.1 Docker Compose (Single Host)

```yaml
version: "3.9"
services:
  oracle:
    image: gvenzl/oracle-free:latest
    environment: { ORACLE_PASSWORD: ..., APP_USER: kyc, APP_USER_PASSWORD: ... }
    volumes: [oracle_data:/opt/oracle/oradata]
    healthcheck: { test: ["CMD", "healthcheck.sh"], interval: 10s, retries: 20 }

  backend:
    build: { context: ., dockerfile: Dockerfile.backend }
    ports: ["8000:8000"]
    env_file: .env
    environment: { ORACLE_DSN: oracle:1521/FREEPDB1, GRAPH_CACHE_PATH: /data/graph_cache }
    volumes: [graph_cache_data:/data/graph_cache]
    depends_on: { oracle: { condition: service_healthy } }
    command: uvicorn backend.main:app --host 0.0.0.0 --port 8000 --workers 1
    healthcheck: { test: ["CMD", "curl", "-f", "http://localhost:8000/api/health"] }

  frontend:
    build: { context: ., dockerfile: Dockerfile.frontend }
    ports: ["80:80"]
    depends_on: [backend]

volumes:
  oracle_data:
  graph_cache_data:
```

### 8.2 Kubernetes (Multi-Instance)

For multi-instance deployments, the graph cache must be on a shared `ReadWriteMany` persistent volume (e.g., EFS, NFS):

```yaml
# PVC for graph cache (shared)
apiVersion: v1
kind: PersistentVolumeClaim
metadata: { name: graph-cache-pvc }
spec:
  accessModes: [ReadWriteMany]
  resources: { requests: { storage: 5Gi } }
  storageClassName: efs-sc  # or nfs-client

# Backend deployment
apiVersion: apps/v1
kind: Deployment
metadata: { name: knowledgeql-backend }
spec:
  replicas: 3
  template:
    spec:
      containers:
      - name: backend
        image: knowledgeql-backend:latest
        args: [uvicorn, backend.main:app, --host, "0.0.0.0", --port, "8000", --workers, "1"]
        volumeMounts:
        - name: graph-cache
          mountPath: /data/graph_cache
        env:
        - { name: GRAPH_CACHE_PATH, value: /data/graph_cache }
      volumes:
      - name: graph-cache
        persistentVolumeClaim: { claimName: graph-cache-pvc }
```

**Important:** All replicas share the same pickle file. On startup, each replica loads from disk (no rebuild). Only one replica should be designated graph-builder to avoid concurrent writes — implement with a distributed lock (Redis `SET NX` or k8s leader election).

### 8.3 nginx Configuration (SSE-critical)

```nginx
upstream backend {
    server backend:8000;
    keepalive 32;  # persistent connections for SSE
}

server {
    listen 80;

    # SSE endpoint — MUST disable buffering
    location /api/query {
        proxy_pass http://backend;
        proxy_buffering off;           # critical — SSE dies with buffering on
        proxy_cache off;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_read_timeout 300s;       # 5 min; queries can take 30s+
        proxy_set_header X-Accel-Buffering no;
    }

    # Other API endpoints
    location /api/ {
        proxy_pass http://backend;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_read_timeout 60s;
    }

    # SPA
    location / {
        root /usr/share/nginx/html;
        try_files $uri $uri/ /index.html;
        expires 1y;
        add_header Cache-Control "public, immutable";
    }
}
```

---

## 9. Testing Strategy

### 9.1 Test Pyramid

```
                    /\
                   /E2E\          tests/test_e2e.py (30 tests)
                  /------\        Requires live Oracle; tests full pipeline
                 /  Integ  \      tests/test_pipeline_integration.py
                /------------\    LangGraph + KnowledgeGraph without Oracle
               / Unit Tests   \   tests/test_*.py (152 tests)
              /________________\  Pure Python, no external dependencies
```

### 9.2 Unit Testing Guidelines

**Agent nodes:** Each node is a pure function `(AgentState) → AgentState`. Test with mock LLM:
```python
# Example: entity extractor unit test
def test_entity_extractor_finds_kyc_tables(kyc_graph):
    # Mock LLM returns known JSON tool calls
    mock_llm = MockLLM(responses=[
        '{"thought": "search", "action": "search_schema", "args": {"query": "kyc status"}}',
        '{"thought": "found", "action": "submit_entities", "args": {"entities": {...}}}'
    ])
    extractor = make_entity_extractor(mock_llm, graph=kyc_graph)
    state = {"user_input": "show pending kyc", "enriched_query": "show pending kyc"}
    result = extractor(state)
    assert "KYC.KYC_STATUS" in result["entity_table_fqns"]
```

**Knowledge graph traversal:** Use `kyc_graph` fixture (8 tables, well-known FK structure):
```python
def test_find_join_path_customer_to_kyc(kyc_graph):
    path = find_join_path(kyc_graph, "KYC.CUSTOMER_MASTER", "KYC.KYC_STATUS")
    assert len(path) > 0
    assert path[0]["join_columns"][0]["from_table"] == "KYC.CUSTOMER_MASTER"
```

### 9.3 Integration Testing

Run backend + mock Oracle using the `kyc_graph` fixture:
```python
@pytest.fixture
def backend_client(kyc_graph):
    app.state.graph = kyc_graph
    app.state.pipeline = build_pipeline(kyc_graph, test_config, mock_llm)
    app.state.startup_complete = True
    with TestClient(app) as client:
        yield client

def test_query_endpoint_returns_sse(backend_client):
    with backend_client.stream("POST", "/api/query",
                               json={"user_input": "show all tables"}) as r:
        events = list(r.iter_lines())
    assert any("result" in e for e in events)
```

### 9.4 E2E Testing

Requires live Oracle (Docker). Run via:
```bash
ORACLE_DSN=localhost:1521/FREEPDB1 ORACLE_USER=kyc ORACLE_PASSWORD=KycPassword1 \
  python -m pytest tests/test_e2e.py -v
```

Or via convenience script:
```bash
./scripts/e2e_test.sh
```

### 9.5 Prompt Regression Testing

Prompt changes must be validated against a golden set of queries:
```bash
# run_prompt_tests.py (to be built) — compares output SQL against expected SQL
python scripts/run_prompt_tests.py \
  --golden-file tests/golden_queries.jsonl \
  --report tests/prompt_regression_report.html
```

Golden file format:
```json
{"user_input": "show pending KYC customers from last 30 days",
 "expected_tables": ["KYC.CUSTOMER_MASTER", "KYC.KYC_STATUS"],
 "expected_sql_contains": ["WHERE", "KYC_STATUS = 'PENDING'", "SYSDATE - 30"]}
```

---

## 10. Prompt Engineering Guidelines

### 10.1 Prompt File Conventions

All prompts live in `prompts/*.txt`. The `_safe_format(template, **kwargs)` helper in each node escapes `{`/`}` before substituting known placeholders — this means JSON examples in prompts are safe:

```
# prompts/entity_extractor_system.txt
Example tool call:
{"thought": "I need to search for KYC tables", "action": "search_schema", "args": {"query": "kyc"}}
```

**DO NOT** write prompts with unescaped braces that don't match a known placeholder — they will silently appear as `{{` in the rendered prompt if they happen to be escaped, or raise `KeyError` if they are valid Python format keys.

### 10.2 Prompt Hot-Reload

After editing a prompt file, call:
```bash
curl -X POST http://localhost:8000/api/admin/rebuild-pipeline
```

No backend restart needed. The next query will use the new prompt.

### 10.3 Prompt Testing Discipline

Every prompt change should be validated with at least 5 diverse test queries in the Prompt Studio tab before merging. Document the prompt version, date, and observed output samples in the PR description.

### 10.4 System Prompt Structure Pattern

All system prompts follow this pattern:
```
ROLE: [who the LLM is pretending to be]

THINKING APPROACH: [how to reason step-by-step]

RULES: [numbered, specific, testable rules]

OUTPUT FORMAT: [exact JSON schema or text format]

EXAMPLES: [2-3 input/output examples]
```

---

## 11. Monitoring & Observability

### 11.1 Health Endpoint

`GET /api/health` returns:
```json
{
  "status": "healthy",
  "startup_complete": true,
  "graph_loaded": true,
  "llm_enhanced": true,
  "table_count": 1247,
  "uptime_seconds": 3600
}
```

Use this for:
- Load balancer health checks (`/api/health` → 200 only when `startup_complete=true`)
- Kubernetes readiness probe (same condition)
- Frontend polling during graph rebuild

### 11.2 Structured Logging

Add request-scoped trace IDs to all agent node logs:
```python
import structlog
log = structlog.get_logger()

# In each pipeline node:
log.info("entity_extraction_complete",
         trace_id=state.get("trace_id"),
         user_input_hash=hash(state["user_input"])[:8],
         entity_table_fqns=state["entity_table_fqns"],
         tool_calls_used=state.get("tool_calls_count", 0))
```

### 11.3 Key Metrics to Instrument

| Metric | Type | Alert threshold |
|---|---|---|
| `pipeline_duration_seconds` | Histogram | P95 > 60s |
| `llm_call_duration_seconds` | Histogram per node | P95 > 30s |
| `entity_tool_calls_count` | Counter | avg > 6 per query |
| `clarification_rate` | Rate | > 60% of queries |
| `oracle_execution_errors` | Counter | > 5/min |
| `graph_cache_age_hours` | Gauge | > 24h (stale) |
| `active_sse_streams` | Gauge | > 50 (capacity warning) |

### 11.4 Distributed Tracing

The `_trace` field attached to every result event contains the full agent execution trace (node name, duration, inputs, outputs). This is surfaced in the frontend Investigate tab. For production, forward these to OpenTelemetry:

```python
from opentelemetry import trace
tracer = trace.get_tracer("knowledgeql.pipeline")

# In streaming.py, wrap each node event
with tracer.start_as_current_span(f"pipeline.{node_name}"):
    ...
```

---

## 12. Operational Runbooks

### 12.1 Graph Rebuild After Schema Change

When columns/tables are added/removed in Oracle:

1. **Via API (preferred):**
   ```bash
   curl -X POST http://localhost:8000/api/admin/rebuild
   ```
   Backend invalidates cache and rebuilds in background. Frontend polls `/api/health` until `startup_complete` is true again.

2. **Via environment (force re-enhancement):**
   ```bash
   # Bump GRAPH_CACHE_VERSION in .env, then restart
   GRAPH_CACHE_VERSION=2 docker compose restart backend
   ```

3. **Manual cache clear:**
   ```bash
   rm ~/.cache/knowledgeql/graph_*.pkl
   # or on Docker volume:
   docker exec backend rm -f /data/graph_cache/graph_*.pkl
   ```

### 12.2 LLM Provider Failover

If the configured LLM provider is unavailable:

1. Change `LLM_PROVIDER` and `LLM_API_KEY` in `.env`
2. Call `POST /api/admin/rebuild-pipeline` (no restart needed for provider change if provider is already configured)
3. Or restart backend to pick up new env vars

The pipeline falls back to sequential execution (no LangGraph) if `LLM_API_KEY` is absent — queries still work but without enrichment, clarification, or SQL generation.

### 12.3 Common Issues

**Issue:** Clarification questions appear repeatedly without progressing
**Cause:** `check_clarification` node fires again because `conversation_history` was not properly passed
**Fix:** Verify `handleClarificationAnswer` in ChatPanel builds `historyWithAnswer` correctly

**Issue:** "Table not found" in generated SQL
**Cause:** Entity extractor resolved wrong FQN; `entity_table_fqns` contains non-existent table
**Fix:** Check `_trace` in Investigate tab → look at `extract_entities` output; inspect `search_schema` tool call results; may need to add table to `importance_tier: core` via LLM enhancement

**Issue:** SSE stream cuts off mid-query
**Cause:** nginx buffering enabled, or `proxy_read_timeout` too short
**Fix:** Verify nginx `proxy_buffering off` and `proxy_read_timeout 300s` on `/api/query` location

**Issue:** Graph build takes >5 minutes
**Cause:** Oracle `ALL_VIEWS` query is slow (DBMS_METADATA fallback)
**Fix:** `oracle_extractor.py` already uses `SUBSTR(v.text, 1, 4000)` instead of `DBMS_METADATA` — check if a newer version introduced a regression; also verify `TARGET_SCHEMAS` is set to limit extraction

**Issue:** LLM enhancement never completes
**Cause:** Background task silently failed
**Fix:** Check backend logs for `_background_enhance` errors; `GET /api/health` will show `llm_enhanced: false` permanently — restart to retry

---

## 13. Security Considerations

### 13.1 SQL Injection Mitigations

The system generates SQL from natural language. Defense layers:

1. **Validator node:** sqlglot parses generated SQL; invalid SQL (including injection payloads) fails parse.
2. **Oracle read-only user:** the `kyc` Oracle user has only SELECT grants — DDL/DML fails at DB level.
3. **No parameterized queries needed for generated SQL** — SQL is generated by LLM, not constructed from user string concatenation. Users cannot inject via the NLP input.
4. **Row limits:** optimizer always adds FETCH FIRST or ROWNUM to prevent data exfiltration of entire tables.

### 13.2 LLM API Keys

- Never log `LLM_API_KEY` or `ORACLE_PASSWORD`
- `.env` is in `.dockerignore` and `.gitignore`
- In Kubernetes, use Secrets (not ConfigMaps) for credentials

### 13.3 Oracle Connection Security

- Use TLS/SSL for Oracle connections: `oracledb.connect(dsn="...", ssl_server_dn_match=False)` for self-signed certs in dev; enforce `ssl_server_dn_match=True` in production
- Rotate Oracle passwords quarterly; update via `ORACLE_PASSWORD` env var + backend restart

### 13.4 Frontend Security

- No secrets in frontend code or build artifacts
- LLM provider keys are in backend only; frontend never sees them
- If users need different LLM-key access levels, implement API key auth middleware in FastAPI

---

## 14. Future Roadmap & Extension Points

### 14.1 Query Result Caching

Cache `(sql_hash → result)` in Redis with TTL = 5 minutes. Identical queries (after normalization) return instantly without Oracle round-trip. Implementation sketch:

```python
cache_key = f"qresult:{hashlib.sha1(normalized_sql.encode()).hexdigest()}"
if cached := redis.get(cache_key):
    return json.loads(cached)
result = execute_oracle_query(sql)
redis.set(cache_key, json.dumps(result), ex=300)
return result
```

### 14.2 Multi-Schema Support

Currently, `TARGET_SCHEMAS` controls which schemas are extracted. To support multiple user-facing schemas in the same deployment:

- Add `schema_context` to `AgentState` — entity extractor filters to the relevant schema
- `GET /api/schema/tables?schema=HR` returns HR schema tables only
- Graph is built once for all schemas; entity extractor's `search_schema` tool accepts `schema_filter` argument

### 14.3 Query History & Saved Queries

Already partially implemented via `chatHistoryStore` (sessions in localStorage). Backend persistence:

- `POST /api/history` — save session (messages + generated SQL) to DB
- `GET /api/history` — paginated list
- `GET /api/history/{id}` — restore session

Requires adding a PostgreSQL/SQLite sidecar for session storage.

### 14.4 Scheduled Reports

Allow users to save a query + schedule it:
```
POST /api/scheduled-queries
{ "sql": "SELECT ...", "cron": "0 8 * * 1-5", "email": "analyst@corp.com" }
```
Backend runs via APScheduler; emails CSV using SendGrid/SES.

### 14.5 Query Explanation Mode

Add a new pipeline branch: instead of executing SQL, explain the generated SQL in business English. Toggle via intent classification or user command ("explain this query").

### 14.6 Graph Incremental Refresh

Instead of full rebuild on schema change, detect delta:
- Compare current `ALL_TABLES` count against cached snapshot
- Only re-extract tables with `LAST_ANALYZED > cache_created_at`
- Merge delta into existing graph without rebuilding from scratch

Estimated rebuild time for delta: 10s for 50 changed tables vs. 60s full rebuild.

---

## Appendix: Key Dependency Versions

| Dependency | Version | Purpose |
|---|---|---|
| `fastapi` | ≥0.115 | Backend framework |
| `uvicorn[standard]` | ≥0.30 | ASGI server with WebSocket/SSE support |
| `oracledb` | ≥2.0 | Oracle thin mode (no Instant Client required) |
| `langgraph` | ≥0.1 | Pipeline DAG orchestration |
| `langchain-core` | ≥0.2 | `BaseChatModel` interface |
| `google-genai` | ≥0.7 | Vertex AI / Gemini direct client |
| `networkx` | ≥3.0 | JOIN_PATH shortest-path computation |
| `sqlglot` | ≥23.0 | SQL parsing, validation, formatting |
| `react` | 18.x | UI framework |
| `@tanstack/react-query` | 5.x | Server state management |
| `@tanstack/react-virtual` | 3.x | Virtual scroll |
| `ag-grid-react` | 32.x | Result grid with row virtualisation |
| `@monaco-editor/react` | 4.x | SQL editor |
| `sigma` | 3.x | WebGL graph canvas |
| `zustand` | 4.x | Client state management |
