# KnowledgeQL — Technical Architecture Documentation

> **Audience**: All engineers working on or integrating with KnowledgeQL.
> **Purpose**: Explain every component, data flow, and design decision in precise technical detail.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Repository Layout](#2-repository-layout)
3. [Startup Sequence](#3-startup-sequence)
4. [Knowledge Graph Layer](#4-knowledge-graph-layer)
5. [Agent Pipeline](#5-agent-pipeline)
6. [LLM Graph Enhancer](#6-llm-graph-enhancer)
7. [FastAPI Backend](#7-fastapi-backend)
8. [React Frontend](#8-react-frontend)
9. [Data Flow: End-to-End Query](#9-data-flow-end-to-end-query)
10. [Multi-Turn Clarification Protocol](#10-multi-turn-clarification-protocol)
11. [Prompt System](#11-prompt-system)
12. [Graph Cache](#12-graph-cache)
13. [Oracle Data Access](#13-oracle-data-access)
14. [Environment Configuration Reference](#14-environment-configuration-reference)

---

## 1. System Overview

KnowledgeQL is a **natural-language-to-SQL assistant** for Oracle databases. It transforms arbitrary user questions into validated, optimised Oracle SQL queries and executes them — without requiring users to know SQL, table names, or schema structure.

The system is composed of four distinct layers:

```
┌──────────────────────────────────────────────────────────────────┐
│  React SPA (8 tabs)                                              │
│  Chat · SQL Editor · Schema · Knowledge Graph · History · ...    │
└────────────────────────┬─────────────────────────────────────────┘
                         │  SSE + REST (HTTP/1.1)
┌────────────────────────▼─────────────────────────────────────────┐
│  FastAPI Backend                                                  │
│  /api/query (SSE) · /api/schema · /api/graph · /api/admin        │
└──────┬──────────────────────────────────────────┬────────────────┘
       │  LangGraph pipeline.invoke()              │  graph.get_*/merge_*
┌──────▼──────────────┐              ┌─────────────▼──────────────┐
│  Agent Pipeline      │   traversal  │  Knowledge Graph           │
│  8 LangGraph nodes   │◄────────────│  Pure-Python property graph │
│  + LLM calls         │             │  in app.state.graph         │
└──────────────────────┘             └─────────────┬──────────────┘
                                                   │  initialize_graph()
                                     ┌─────────────▼──────────────┐
                                     │  Oracle Database            │
                                     │  ALL_* metadata views       │
                                     │  Application tables         │
                                     └────────────────────────────┘
```

**Key design choices:**
- No external graph database (Neo4j, Memgraph, etc.) — graph lives in Python dicts in RAM
- No vector database — fuzzy search uses Levenshtein distance + token overlap scoring
- LangGraph orchestrates the multi-step pipeline with streaming state updates
- The React SPA is served as a static bundle by the same FastAPI process
- Prompt files on disk can be hot-reloaded without restarting the server

---

## 2. Repository Layout

```
nlp2sql/
├── backend/              FastAPI app, routers, DI, Pydantic models
│   ├── main.py           Lifespan, app factory, SPA catch-all
│   ├── deps.py           Dependency injection (get_config, get_graph, get_pipeline, get_llm)
│   ├── models.py         All Pydantic request/response schemas
│   └── routers/
│       ├── query.py      POST /api/query  (SSE streaming)
│       ├── schema.py     GET  /api/schema/tables, /search, etc.
│       ├── graph.py      GET  /api/graph/visualization, /join-path
│       ├── admin.py      POST /api/admin/rebuild, /rebuild-pipeline
│       ├── prompts.py    GET/PUT /api/prompts/:name
│       ├── sql.py        POST /api/sql/execute, /format
│       └── health.py     GET  /api/health
│
├── agent/
│   ├── pipeline.py       build_pipeline() → LangGraph CompiledGraph | _SequentialPipeline
│   ├── state.py          AgentState TypedDict (all 20+ fields)
│   ├── llm.py            get_llm() dispatches openai | anthropic | vertex
│   ├── prompts.py        load_prompt(): disk file with inline fallback
│   ├── trace.py          TraceStep — per-node audit object w/ set_llm_call(), add_graph_op()
│   └── nodes/
│       ├── query_enricher.py      Enriches user query with domain vocabulary
│       ├── intent_classifier.py   DATA_QUERY | SCHEMA_EXPLORE | QUERY_EXPLAIN | QUERY_REFINE
│       ├── entity_extractor.py    Agentic tool-calling loop → entity_table_fqns
│       ├── context_builder.py     Resolves FQNs → DDL schema context string
│       ├── clarification_agent.py Expert KYC analyst — checks ambiguity
│       ├── sql_generator.py       Chain-of-thought Oracle SQL generation
│       ├── sql_validator.py       sqlglot-based syntax + safety check
│       ├── query_optimizer.py     Rule-based SQL rewrites (ROWNUM, hints)
│       ├── query_executor.py      Runs SQL against live Oracle
│       └── result_formatter.py    Shapes raw rows into JSON response
│
├── knowledge_graph/
│   ├── oracle_extractor.py   Queries ALL_* views → OracleMetadata dataclass
│   ├── graph_builder.py      OracleMetadata → KnowledgeGraph (nodes + edges)
│   ├── graph_store.py        KnowledgeGraph: in-memory property graph (pure Python)
│   ├── traversal.py          12 query functions (search_schema, find_join_path, …)
│   ├── graph_cache.py        Pickle serialisation + SHA1 cache key
│   ├── init_graph.py         5-step build pipeline → (KnowledgeGraph, report)
│   ├── llm_enhancer.py       Post-build LLM annotation (importance, inferred FKs, descriptions)
│   ├── glossary_loader.py    Infers BusinessTerm nodes from column names/comments
│   ├── glossary_loader_json.py  Loads .json glossary file
│   ├── knowledge_generator.py   LLM-generates kyc_business_knowledge.txt
│   ├── models.py             Typed dataclasses: TableNode, ColumnNode, IndexNode, …
│   └── config.py             OracleConfig + GraphConfig (@dataclass, reads from env)
│
├── prompts/              7 editable .txt files loaded at pipeline build time
├── frontend/src/         React + TypeScript SPA (Vite build → dist/)
├── docker/               Compose file, Dockerfiles, Oracle init SQL
└── tests/                pytest unit + E2E tests (152 unit, 30 E2E)
```

---

## 3. Startup Sequence

```
FastAPI lifespan (backend/main.py)
│
├─ 1. AppConfig.from_env()          — reads all env vars via pydantic-settings
│
├─ 2. get_llm(config)               — builds LLM client for configured provider
│      openai    → ChatOpenAI
│      anthropic  → ChatAnthropic
│      vertex    → _VertexGenAIChat (custom wrapper around google.genai)
│
├─ 3. _load_or_build_graph()        — returns _GraphBundle{graph, llm_enhanced}
│      ├─ try load_graph(cache_path)          # SHA1-keyed pickle on disk
│      │   └─ if stale/missing/version mismatch → fall through
│      └─ initialize_graph(config.graph)      # full Oracle extraction
│           ├─ OracleMetadataExtractor.extract()
│           │    ├─ _extract_tables()         ALL_TABLES / DBA_TABLES
│           │    ├─ _extract_columns()        ALL_TAB_COLUMNS
│           │    ├─ _extract_primary_keys()   ALL_CONSTRAINTS (P)
│           │    ├─ _extract_foreign_keys()   ALL_CONSTRAINTS (R) + ALL_CONS_COLUMNS
│           │    ├─ _extract_views()          ALL_VIEWS
│           │    ├─ _extract_indexes()        ALL_INDEXES + ALL_IND_COLUMNS
│           │    ├─ _extract_procedures()     ALL_PROCEDURES + ALL_OBJECTS
│           │    ├─ _extract_synonyms()       ALL_SYNONYMS
│           │    ├─ _extract_sequences()      ALL_SEQUENCES
│           │    └─ _extract_sample_data()    SELECT * ... FETCH FIRST N ROWS
│           ├─ GraphBuilder(config).build(metadata)
│           ├─ InferredGlossaryBuilder(graph).build(metadata)
│           └─ validate_graph(graph)
│           → save_graph(graph, cache_path)
│
├─ 4. build_pipeline(graph, config, llm)
│      ├─ load all node factories (make_query_enricher, make_entity_extractor, …)
│      ├─ compile LangGraph StateGraph with 8 nodes + conditional edges
│      └─ store compiled pipeline in app.state.pipeline
│
└─ 5. Background tasks (asyncio):
       ├─ enhance_graph_with_llm(graph, llm)   if not already enhanced
       │    → _assign_table_importance()
       │    → _infer_missing_relationships()
       │    → _fill_missing_descriptions()
       │    → save_graph(graph, cache_path, llm_enhanced=True)
       │    → rebuild pipeline (re-reads prompt files)
       └─ _maybe_generate_knowledge_file()      if kyc_business_knowledge.txt is empty
            → generate_knowledge_file(graph, llm, output_path)
            → rebuild pipeline (uses newly generated knowledge)
```

**Why background tasks?** LLM-based graph enhancement can take 30–120s for large schemas. Rather than blocking all HTTP traffic, the server becomes ready as soon as the graph is loaded (cold cache: ~5–30s, warm cache: <1s). The LLM enhancement runs concurrently. The `/api/health` endpoint exposes `llm_enhanced: bool` so clients know the enhancement status.

---

## 4. Knowledge Graph Layer

### 4.1 Graph Storage (`graph_store.py`)

`KnowledgeGraph` is a pure-Python in-memory property graph — no Cypher, no bolt driver, no external process.

```python
class KnowledgeGraph:
    _nodes: Dict[str, Dict[str, Dict[str, Any]]]
    #        ^label    ^node_id  ^properties

    _edges: Dict[str, List[Dict[str, Any]]]
    #        ^rel_type    ^[{_from, _to, **props}]

    _out_idx: defaultdict(_dict_of_lists)
    #          rel_type → from_id → [edges]   (forward lookup)

    _in_idx:  defaultdict(_dict_of_lists)
    #          rel_type → to_id   → [edges]   (reverse lookup)
```

**Critical pickle detail**: `_out_idx` and `_in_idx` use `defaultdict(_dict_of_lists)` where `_dict_of_lists` is a **module-level function** (not a lambda). Python cannot pickle lambdas defined inside methods. This was a deliberate fix to enable disk caching.

Core API:
```python
graph.merge_node(label, node_id, props)          # upsert node
graph.merge_edge(rel_type, from_id, to_id, **kw) # upsert directed edge
graph.get_node(label, node_id)                   # single node props dict
graph.get_all_nodes(label)                       # list of all prop dicts for label
graph.get_out_edges(rel_type, from_id)           # edges originating from from_id
graph.get_in_edges(rel_type, to_id)              # edges pointing to to_id
graph.get_all_edges(rel_type)                    # all edges of rel_type
graph.count_nodes(label)                         # len of label bucket
```

### 4.2 Node and Edge Types

**Node labels** and their FQN patterns:

| Label | Node ID format | Key properties |
|---|---|---|
| `Schema` | `SCHEMA_NAME` | `name` |
| `Table` | `SCHEMA.TABLE` | `name`, `schema`, `fqn`, `row_count`, `comments`, `importance_rank`, `importance_tier`, `llm_description` |
| `Column` | `SCHEMA.TABLE.COL` | `name`, `table_name`, `data_type`, `nullable`, `is_pk`, `is_fk`, `comments` |
| `View` | `SCHEMA.VIEW_NAME` | `name`, `schema`, `text` (DDL) |
| `Index` | `SCHEMA.TABLE.IDX` | `name`, `table_name`, `uniqueness`, `columns` |
| `Procedure` | `SCHEMA.PROC` | `name`, `object_type`, `status` |
| `Synonym` | `SCHEMA.SYN` | `name`, `table_owner`, `table_name` |
| `BusinessTerm` | `term:<normalised>` | `term`, `description`, `canonical_column` |

**Edge relationship types**:

| Rel type | From → To | Semantics |
|---|---|---|
| `BELONGS_TO` | Column → Table | Column is member of Table |
| `IN_SCHEMA` | Table → Schema | Table lives in Schema |
| `HAS_INDEX` | Table → Index | Table has an index |
| `HAS_FOREIGN_KEY` | Column → Column | FK constraint (from col → referenced col) |
| `JOIN_PATH` | Table → Table | Traversable join path (FK-derived or LLM-inferred); props: `join_columns`, `join_type`, `cardinality`, `weight`, `source` |
| `MAPS_TO` | BusinessTerm → Column | Glossary term resolves to a column |
| `SYNONYM_OF` | Synonym → Table | Synonym points to Table |

### 4.3 Oracle Metadata Extraction (`oracle_extractor.py`)

The extractor always uses `ALL_*` views (never `DBA_*` by default) to stay within standard user privileges. Each extraction function is individually wrapped in `_safe_extract()`:

```python
def _safe_extract(self, label: str, fn, *args, default):
    try:
        return fn(*args)
    except Exception as e:
        logger.warning("Extraction of %s failed: %s", label, e)
        return default
```

This means a single broken Oracle view never aborts the full build.

**Notable quirks fixed in this codebase**:
- `ALL_PROCEDURES` in Oracle 23c has no `STATUS` column — uses LEFT JOIN with `ALL_OBJECTS`
- `ALL_VIEWS.TEXT` and `ALL_TAB_COLUMNS.DATA_DEFAULT` are `LONG` type — requires `cursor.var(DB_TYPE_LONG, size, cursor.arraysize)` with explicit `arraysize`; omitting it causes `DPY-2016` in thin mode
- Disabled FK constraints (`status != 'ENABLED'`) are still extracted — omitting them would hide JOIN paths in schemas where FKs are defined but enforcement is turned off

### 4.4 Graph Builder (`graph_builder.py`)

Transforms `OracleMetadata` (plain dataclasses) into `KnowledgeGraph` nodes + edges. Key steps:

1. One `Schema` node per unique schema name
2. One `Table` node per `TableNode`; FQN = `SCHEMA.TABLE_NAME`
3. One `Column` node per `ColumnNode`; FQN = `SCHEMA.TABLE.COLUMN_NAME`
4. `BELONGS_TO` edge: Column → Table
5. `IN_SCHEMA` edge: Table → Schema
6. `HAS_FOREIGN_KEY` edge: FK source column → FK target column (using `ALL_CONS_COLUMNS`)
7. `JOIN_PATH` edges: bidirectional Table-to-Table edges derived from FK edges; properties include `join_columns` array (each with `from_col`, `to_col`, `constraint_name`, etc.)
8. Index, View, Procedure, Synonym nodes

### 4.5 Glossary Building (`glossary_loader.py`)

`InferredGlossaryBuilder` scans all column names and comments for patterns matching KYC/financial domain terms (e.g. `RISK_RATING`, `KYC_STATUS`, `PEP_FLAG`, `BENEFICIAL_OWNER`). Creates `BusinessTerm` nodes with `MAPS_TO` edges pointing to the relevant column. This allows the entity extractor to resolve vague user terms ("high-risk client", "PEP") to exact column FQNs.

### 4.6 Traversal API (`traversal.py`)

12 pure-read functions. All inputs/outputs are plain Python dicts (no ORM).

| Function | Purpose |
|---|---|
| `search_schema(graph, query, limit)` | Fuzzy multi-field search (name, comments, llm_description) using Levenshtein + token overlap scoring |
| `get_table_detail(graph, fqn)` | Full table dict: props + columns + PKs + FKs + constraints |
| `get_columns_for_table(graph, fqn)` | Columns sorted by `column_id` |
| `find_join_path(graph, from_fqn, to_fqn, max_hops)` | Bidirectional BFS via NetworkX `shortest_path`; returns `{join_columns, join_type, cardinality, weight, source}` |
| `get_context_subgraph(graph, fqns)` | Expands FQN list to include all 1-hop FK neighbours; returns list of `{table, columns, foreign_keys, …}` dicts |
| `get_all_join_paths(graph, fqns)` | All pairwise join paths between a set of tables |
| `list_all_tables(graph, schema, skip, limit)` | Paginated table list sorted by importance_rank |
| `resolve_business_term(graph, term)` | Finds `BusinessTerm` node; returns linked column details |
| `list_related_tables(graph, fqn)` | Tables 1–2 hops away via JOIN_PATH edges |
| `serialize_context_to_ddl(subgraph_items)` | Converts `get_context_subgraph()` output to DDL text for LLM prompt |
| `get_schema_stats(graph)` | Table/column/FK/join-path counts |
| `validate_graph(graph)` | Returns list of validation warnings |

---

## 5. Agent Pipeline

### 5.1 LangGraph DAG

```
enrich_query
    │
classify_intent
    │
extract_entities  ◄── agentic loop (0–8 tool calls)
    │
retrieve_schema   ◄── fast-path if entity_table_fqns populated
    │
check_clarification ──[need_clarification=True]──► END (emit clarification event)
    │ [need_clarification=False]
generate_sql  ◄──────────────────────────────────────────────────┐
    │                                                             │
validate_sql ──[fail, retry_count < 3]───────────────────────────┘
    │ [pass]
optimize_sql
    │
execute_query
    │
format_result
    │
   END
```

**Conditional edges**:
- After `check_clarification`: if `state["need_clarification"]` → `END`; else → `generate_sql`
- After `validate_sql`: if `state["validation_passed"]` → `optimize_sql`; elif `retry_count < 3` → `generate_sql`; else → `optimize_sql` (force)

**Fallback pipeline**: If `langgraph` is not installed or no LLM key is configured, `build_pipeline()` returns a `_SequentialPipeline` object that calls each node function in order. All retry/clarification logic is simulated with a list-based loop.

### 5.2 AgentState

All pipeline nodes share a single `TypedDict`:

```python
class AgentState(TypedDict):
    # Input
    user_input: str
    conversation_history: List[Dict[str, str]]  # [{role, content}]

    # Enriched query
    enriched_query: Optional[str]

    # Classification
    intent: str  # DATA_QUERY | SCHEMA_EXPLORE | QUERY_EXPLAIN | QUERY_REFINE

    # Entity extraction
    entities: Dict[str, Any]       # {tables, columns, conditions, …}
    entity_table_fqns: List[str]   # SCHEMA.TABLE FQNs from agentic loop

    # Schema context
    schema_context: str            # DDL text injected into SQL generator prompt

    # SQL pipeline
    generated_sql: str
    sql_explanation: str
    validation_passed: bool
    validation_errors: List[str]
    optimized_sql: str

    # Execution
    execution_result: Dict[str, Any]  # {columns, rows, total_rows, execution_time_ms}
    formatted_response: str           # JSON string of final response object

    # Meta
    step: str
    error: Optional[str]
    retry_count: int

    # Clarification
    need_clarification: bool
    clarification_question: str
    clarification_options: List[str]
    clarification_context: str  # agent's understanding summary shown to user

    # Trace
    _trace: List[Any]   # List[TraceStep.to_dict()]
```

### 5.3 Node Details

#### query_enricher
- Loads `kyc_business_knowledge.txt` once via `@lru_cache`
- Formats system prompt **at factory creation** (expensive knowledge text included once)
- Per-call: sends `HumanMessage({user_input})` only; system message already cached
- Output: `enriched_query` — structured English spec with TABLES, FILTERS, JOINS sections
- Controllable via `QUERY_ENRICHER_ENABLED=false` env var

#### intent_classifier
- 4 intents: `DATA_QUERY`, `SCHEMA_EXPLORE`, `QUERY_EXPLAIN`, `QUERY_REFINE`
- JSON-only response: `{"intent": "...", "confidence": 0.95, "reasoning": "..."}`
- Currently mainly used for routing decisions and trace visibility; most queries are `DATA_QUERY`

#### entity_extractor (agentic loop)

This is the most complex node. It implements a **ReAct-style tool-calling agent** using a raw JSON protocol (no LangChain `.bind_tools()` API) for provider compatibility.

**Schema tree** built at factory creation time:
- Grouped by importance tier: core → reference → audit → utility → unranked
- Top ~60 tables shown; each shows PKs, FK targets, up to 5 data columns
- Final section always lists 13 Oracle data dictionary views (ALL_TABLES, ALL_COLUMNS, etc.)

**Tool-call loop**:
```
[System: schema tree + tools spec + rules]
[Human: "Query: {enriched_query or user_input}"]
  ↓
LLM → {"thought": "...", "action": "get_table_detail", "args": {"fqn": "KYC.CUSTOMERS"}}
  ↓
_call_graph_tool(graph, "get_table_detail", {"fqn": "KYC.CUSTOMERS"}, trace)
  → returns formatted result string
  ↓
[Tool result injected as Human message: "Tool result: ..."]
  ↓
LLM → {"thought": "...", "action": "submit_entities", "args": {"table_fqns": ["KYC.CUSTOMERS"], ...}}
  ↓
Loop exits; entity_table_fqns = ["KYC.CUSTOMERS"]
```

**Available tools**:
- `search_schema(query, limit)` — fuzzy search all tables/columns
- `get_table_detail(fqn)` — columns + PKs + FKs for one table
- `find_join_path(from_fqn, to_fqn)` — join columns between two tables
- `resolve_business_term(term)` — glossary lookup
- `list_related_tables(fqn)` — 1-2 hop neighbours via JOIN_PATH
- `submit_entities(table_fqns, columns, conditions, …)` — FINAL action that exits loop

**Safety**: After `MAX_TOOL_CALLS=8` iterations without `submit_entities`, a "force submit" call is made with all discovered context. If LLM parsing fails entirely, keyword fallback matches table names from `user_input` text.

**`_safe_format(template, **kwargs)`**: Escapes all `{`/`}` in prompt templates to `{{`/`}}`, then un-doubles only known placeholder keys. This allows prompt files to contain raw JSON examples without Python's `str.format()` raising `KeyError`.

#### context_builder (retrieve_schema)

**Fast path** (when `entity_table_fqns` is populated):
```python
agent_fqns = state.get("entity_table_fqns", [])
if agent_fqns:
    collected_fqns = set(agent_fqns)
    # skip name resolution entirely
else:
    # slow path: search_schema + resolve_business_term + FK-neighbour expansion
```

After FQN collection (via either path):
1. `get_context_subgraph(graph, fqns)` — expands to 1-hop FK neighbours
2. `get_all_join_paths(graph, fqns)` — pre-computes JOIN hints
3. `serialize_context_to_ddl(subgraph_items)` — produces DDL string:

```sql
-- TABLE: KYC.CUSTOMERS
-- Rows: 15 | Tier: core | Rank: 1
CREATE TABLE KYC.CUSTOMERS (
    CUSTOMER_ID NUMBER NOT NULL,       -- PK
    FIRST_NAME VARCHAR2(100),
    RISK_RATING VARCHAR2(10),          -- Values: HIGH, MEDIUM, LOW
    ...
);
-- Available indexes: PK_CUSTOMERS(CUSTOMER_ID), IDX_CUST_RISK(RISK_RATING)
```

#### clarification_agent

**Expert KYC data analyst prompt** — decides whether the query is genuinely ambiguous. Passes last 10 history turns. Returns:

```json
{
  "needs_clarification": true,
  "understanding": "You want to retrieve customer records with their risk assessments...",
  "question": "Should I include all historical assessments or only the most recent?",
  "options": ["Most recent risk assessment only", "All historical records", ...],
  "multi_select": false
}
```

The `understanding` field is stored as `clarification_context` in state and surfaced to the user via the `context` field of the SSE `clarification` event.

**When to ask**: missing essential filter, ambiguous term mapping, genuinely different JOIN paths, unclear aggregation level.
**When NOT to ask**: conversation history already contains the answer, user just answered a clarification, cosmetic ambiguity, schema exploration queries.

#### sql_generator

- 16-rule Oracle SQL system prompt (loaded from `prompts/sql_generator_system.txt`)
- Uses `enriched_query` if available, else `user_input`
- Schema context from `state["schema_context"]` injected into human message
- LLM produces: reasoning prose + ` ```sql ``` ` + ` ```explanation ``` `
- Output parsed with regex; retry if parsing fails

**Critical rule (15)**: Use EXACT FQNs from `-- TABLE: SCHEMA.TABLE` DDL headers — never invent schema names.

#### sql_validator

Uses `sqlglot` for parsing. Checks:
- Parse without error for `dialect="oracle"`
- No DML/DDL statements (INSERT, UPDATE, DELETE, DROP, CREATE, ALTER, TRUNCATE)
- `WITH` clause depth < 5
- Result sets > 10,000 rows trigger a warning (not failure)

#### query_optimizer

Rule-based SQL rewrites:
- Injects `FETCH FIRST N ROWS ONLY` if no row limit and result set is large
- Adds `ORDER BY 1` if query has aggregate with no ORDER BY
- Rewrites `LIMIT N` to `FETCH FIRST N ROWS ONLY` (MySQL→Oracle syntax)

#### query_executor

Always connects to live Oracle via `oracledb`. No mock/demo mode. On Oracle error returns `execution_result = {"error": str(exc), "rows": [], ...}`. The `optimized_sql` is used if non-empty; falls back to `generated_sql`.

#### result_formatter

Produces a JSON string stored in `state["formatted_response"]`:
```json
{
  "type": "query_result",
  "summary": "Found 15 rows from Oracle in 0.05s.",
  "sql": "SELECT ...",
  "explanation": "This query retrieves...",
  "columns": [...],
  "rows": [[...]],
  "total_rows": 15,
  "execution_time_ms": 50,
  "data_source": "oracle",
  "schema_context_tables": ["KYC.CUSTOMERS", "KYC.ACCOUNTS"]
}
```

### 5.4 TraceStep

Each node records a `TraceStep` for the Investigate tab:

```python
class TraceStep:
    node: str            # e.g. "extract_entities"
    step_label: str      # e.g. "extracting"
    start_time: float    # time.perf_counter()
    end_time: float
    duration_ms: float
    llm_call: Optional[LlmCallRecord]  # {system_prompt, user_prompt, raw_response, parsed_output}
    graph_ops: List[GraphOpRecord]     # [{op, input_summary, result_count, result_sample}]
    output_summary: Dict[str, Any]     # node-specific summary for UI display
    error: Optional[str]
```

All TraceSteps are collected in `state["_trace"]` and emitted via SSE as `event: trace` immediately after each node completes.

---

## 6. LLM Graph Enhancer

Runs once after graph build (or after fresh Oracle extraction). All three steps are individually try/except guarded.

### Step 1: Table Importance (`_assign_table_importance`)

Batches all tables (≤50 per LLM call). Prompt instructs LLM to rank tables 1..N by business centrality. Stores on each `Table` node:
- `importance_rank` (int, 1 = most important)
- `importance_tier` (one of: `core`, `reference`, `audit`, `utility`)
- `importance_reason` (brief text)

Tables the LLM misses get structural fallback ranks based on FK degree.

### Step 2: Missing Relationships (`_infer_missing_relationships`)

Identifies tables with no `JOIN_PATH` edges. For each such table, scans columns for FK-suffix patterns (`_ID`, `_CODE`, `_KEY`, `_REF`, `_FK`, `_NO`, `_NUM`). Asks LLM whether each candidate pair (current table, likely referenced table) is a valid join. Adds bidirectional `JOIN_PATH` edges with `source="llm_inferred"` for HIGH/MEDIUM confidence matches.

### Step 3: Missing Descriptions (`_fill_missing_descriptions`)

For `Table` nodes with empty `comments` (no Oracle `COMMENT ON TABLE` defined), asks LLM for a one-line business description. Stored as `llm_description` so as not to overwrite any Oracle-sourced `comments`.

---

## 7. FastAPI Backend

### 7.1 Application State

```python
app.state.config    # AppConfig (pydantic-settings)
app.state.graph     # _GraphBundle{graph: KnowledgeGraph, llm_enhanced: bool}
app.state.pipeline  # CompiledStateGraph | _SequentialPipeline
app.state.llm       # LLM client | None
```

`_GraphBundle` is a simple class with `__slots__ = ("graph", "llm_enhanced")`. Being mutable and cached by `@st.cache_resource` (in Streamlit) or `app.state` (in FastAPI), mutating `bundle.llm_enhanced = True` after LLM enhancement is immediately visible to all requests without re-creating the object.

### 7.2 Query SSE Endpoint (`/api/query`)

Protocol: `POST`, request body = `{user_input, conversation_history}`, response = `text/event-stream`.

```
Pipeline runs in asyncio thread pool (run_in_executor) to never block event loop.
Results pushed via asyncio.Queue → yielded as SSE events.

Event sequence:
  event: step    data: {"step": "enriching"}
  event: step    data: {"step": "classifying"}
  event: step    data: {"step": "extracting"}
  event: step    data: {"step": "retrieving"}
  event: step    data: {"step": "generating"}
  event: sql     data: {"sql": "SELECT ..."}
  event: trace   data: {node, step_label, duration_ms, llm_call, graph_ops, output_summary}
  event: result  data: {type, summary, sql, explanation, columns, rows, total_rows, ...}

OR, if clarification needed:
  event: clarification  data: {"question": "...", "options": [...], "context": "..."}
```

### 7.3 Admin Endpoints

- `POST /api/admin/rebuild` — full rebuild: invalidates cache → re-extracts from Oracle → rebuilds graph → LLM-enhances → rebuilds pipeline. Runs async in background.
- `POST /api/admin/rebuild-pipeline` — pipeline-only rebuild: re-calls `build_pipeline()` with current graph. Re-reads all `prompts/*.txt` from disk. Completes in ~2s. Used by Prompt Studio.

### 7.4 Graph Visualization (`/api/graph/visualization`)

Returns nodes (Table nodes with importance_rank, row_count, tier) and edges (JOIN_PATH edges with join_columns). Limit defaults to 10,000 — shows all tables. Sorts by importance_rank + FK degree so the most connected tables appear first if any sampling is applied.

### 7.5 Dependency Injection (`deps.py`)

```python
def get_config(request: Request) -> AppConfig:
    return request.app.state.config

def get_graph(request: Request) -> KnowledgeGraph:
    return request.app.state.graph.graph

def get_pipeline(request: Request):
    return request.app.state.pipeline

def get_llm(request: Request):
    return getattr(request.app.state, "llm", None)
```

### 7.6 SPA Serving

Static files from `frontend/dist/` served at `/`. Catch-all route returns `index.html` for all non-`/api/` paths, enabling client-side routing.

---

## 8. React Frontend

### 8.1 Technology Stack

- **React 18** with TypeScript
- **Vite** build system
- **Zustand** state management (no Redux)
- **TanStack Query** (react-query) for caching REST calls
- **AG Grid** for large data tables
- **Monaco Editor** for SQL editing
- **vis-network** for graph visualisation

### 8.2 Application Tabs

| Tab | Component | Purpose |
|---|---|---|
| Chat | `ChatPanel` + `MessageList` | Multi-turn NL query with clarification |
| SQL Editor | `SqlEditorPage` | Write/execute Oracle SQL with autocomplete |
| Schema | `SchemaPage` | Browse all tables + columns + FKs |
| Knowledge Graph | `GraphPage` | D3/vis interactive graph visualisation |
| Relationships | `RelationshipsPage` | FK table + join-path explorer |
| History | `HistoryPage` | Saved chat sessions from localStorage |
| Investigate | `InvestigatePage` | Per-query LLM trace + entity agent iterations |
| Prompt Studio | `PromptStudioPage` | Edit prompt files + trigger pipeline rebuild |

### 8.3 Zustand Stores

**`chatStore`**:
```typescript
interface ChatStore {
  messages: ChatMessage[]         // all chat bubbles
  history: ConversationMessage[]  // last 20 turns for backend context
  activeBaseQuery: string         // original query at start of clarification chain
  clarificationPairs: {question, answer}[]  // accumulated Q&A requirements

  // Actions
  addUserMessage(content)
  addResultMessage(result)
  addClarificationMessage(question, options, context?, multiSelect?)
  markClarificationAnswered(id)
  setActiveBaseQuery(query)
  addClarificationPair(question, answer)
  getCumulativeQuery(): string   // baseQuery + "Additional requirements: ..."
  clearMessages()
  restoreSession(messages, history)
}
```

**`chatHistoryStore`** (persisted to `localStorage`):
- Max 50 sessions, each = `{id, title, createdAt, messages, history}`
- `saveSession`, `deleteSession`, `clearAllSessions`

**`traceStore`** (in-memory):
- Per-query `QueryTrace` objects with `TraceStep[]` arrays
- Fed by SSE `event: trace` events during streaming
- Read by Investigate tab

### 8.4 SSE Client (`api/query.ts`)

```typescript
export function streamQuery(
  userInput: string,
  history: ConversationMessage[],
  onStep: (step: QueryStep) => void,
  onSql: (sql: string) => void,
  onResult: (result: QueryResult & { _trace? }) => void,
  onError: (msg: string) => void,
  onClarification?: (question, options, context?, multiSelect?) => void,
  onTrace?: (step: TraceStep) => void,
): AbortController
```

Uses `fetch` + `ReadableStream` + `TextDecoder({ stream: true })`. Buffers incoming chunks and splits on `\n\n` SSE block boundaries. Returns an `AbortController` — calling `.abort()` cancels mid-stream.

---

## 9. Data Flow: End-to-End Query

**User**: "Show me customers with overdue KYC reviews"

**Step 1 — enrich_query**:
- Loads `kyc_business_knowledge.txt` (cached)
- LLM annotates: `TABLES: KYC_REVIEWS, CUSTOMERS; FILTERS: STATUS IN ('PENDING', 'OVERDUE') AND NEXT_REVIEW_DATE < SYSDATE; JOINS: KYC_REVIEWS.CUSTOMER_ID = CUSTOMERS.CUSTOMER_ID`
- Outputs `enriched_query` string

**Step 2 — classify_intent**:
- Input: enriched_query
- LLM: `{"intent": "DATA_QUERY", "confidence": 0.98}`

**Step 3 — extract_entities** (agentic loop):
- Schema tree injected (8 KYC tables + Oracle data dict)
- Iteration 1: LLM → `search_schema("KYC review overdue")` → finds `KYC.KYC_REVIEWS`, `KYC.CUSTOMERS`
- Iteration 2: LLM → `get_table_detail("KYC.KYC_REVIEWS")` → gets STATUS column values, NEXT_REVIEW_DATE
- Iteration 3: LLM → `submit_entities(table_fqns=["KYC.KYC_REVIEWS","KYC.CUSTOMERS"], columns=["STATUS","NEXT_REVIEW_DATE","CUSTOMER_ID","FIRST_NAME","LAST_NAME"], conditions=["NEXT_REVIEW_DATE < SYSDATE", "STATUS != 'COMPLETED'"])`
- Outputs `entity_table_fqns = ["KYC.KYC_REVIEWS", "KYC.CUSTOMERS"]`

**Step 4 — retrieve_schema** (fast path):
- `entity_table_fqns` is non-empty → skip name resolution
- `get_context_subgraph(graph, ["KYC.KYC_REVIEWS", "KYC.CUSTOMERS"])` → expands to include `KYC.EMPLOYEES` (via FK from KYC_REVIEWS.REVIEWER_ID)
- `find_join_path("KYC.KYC_REVIEWS", "KYC.CUSTOMERS")` → `CUSTOMER_ID = CUSTOMER_ID`
- `serialize_context_to_ddl(...)` → DDL text with table headers and column definitions
- Outputs `schema_context` string (~1,929 chars)

**Step 5 — check_clarification**:
- LLM analyses: is the query ambiguous? What dimension is missing?
- Decision: `{"needs_clarification": false}` ← query is specific enough
- Continues to generate_sql

**Step 6 — generate_sql**:
- LLM receives: system prompt (16 Oracle rules) + schema_context DDL + enriched_query
- Output:
```sql
SELECT
    c.CUSTOMER_ID,
    c.FIRST_NAME || ' ' || c.LAST_NAME AS FULL_NAME,
    r.REVIEW_DATE,
    r.STATUS,
    r.NEXT_REVIEW_DATE
FROM
    KYC.KYC_REVIEWS r
    JOIN KYC.CUSTOMERS c ON r.CUSTOMER_ID = c.CUSTOMER_ID
WHERE
    r.NEXT_REVIEW_DATE < SYSDATE
    AND r.STATUS != 'COMPLETED'
ORDER BY r.NEXT_REVIEW_DATE ASC
```

**Step 7 — validate_sql**:
- `sqlglot.parse(sql, dialect="oracle")` → no errors
- No DML detected
- `validation_passed = True`

**Step 8 — optimize_sql**:
- No row limit detected, estimated rows may be large
- Injects: `FETCH FIRST 10000 ROWS ONLY`

**Step 9 — execute_query**:
- `oracledb.connect(dsn, user, password).cursor().execute(optimized_sql)`
- Returns: 3 rows, 50ms execution time

**Step 10 — format_result**:
- JSON response with `summary="Found 3 rows from Oracle in 0.05s."`, columns, rows, SQL, explanation

**SSE events emitted** (from backend to frontend):
```
event: step    data: {"step": "enriching"}
event: step    data: {"step": "classifying"}
event: step    data: {"step": "extracting"}
event: step    data: {"step": "retrieving"}
event: step    data: {"step": "generating"}
event: sql     data: {"sql": "SELECT c.CUSTOMER_ID, ..."}
event: trace   data: {node: "generate_sql", duration_ms: 2100, llm_call: {...}}
event: step    data: {"step": "executing"}
event: result  data: {type: "query_result", summary: "Found 3 rows...", columns: [...], rows: [...]}
```

---

## 10. Multi-Turn Clarification Protocol

### Protocol Overview

The clarification system maintains requirements across multiple turns using a **cumulative query** approach.

**Backend side**:
1. `check_clarification` node runs after `retrieve_schema`
2. If `needs_clarification=true`, emits `event: clarification {question, options, context}` and routes pipeline to `END`
3. `context` = agent's plain-English understanding summary

**Frontend side**:
```
User types "analyze customer compliance"
    ↓
chatStore.setActiveBaseQuery("analyze customer compliance")
    ↓
POST /api/query {user_input: "analyze customer compliance", history: []}
    ↓
SSE: event: clarification
     {question: "How should multiple assessments be handled?",
      options: ["Most recent only", "All historical", ...],
      context: "I'll query customer compliance data including risk assessments..."}
    ↓
chatStore.addClarificationMessage(question, options, context)
    ↓
User clicks "Most recent only"
    ↓
chatStore.addClarificationPair("How should multiple...?", "Most recent only")
chatStore.addUserMessage("Most recent only")
    ↓
cumulativeQuery = getCumulativeQuery()
= "analyze customer compliance\n\nAdditional requirements clarified:\n- How should multiple assessments be handled?: Most recent only"
    ↓
POST /api/query {user_input: cumulativeQuery, history: [..., {user: "Most recent only"}]}
    ↓
(pipeline runs with full context — no ambiguity — generates SQL directly)
```

**Why cumulative query (not just history)?**
- The `entity_extractor` agentic loop primarily uses `user_input` (not history) as the query to understand
- If `user_input = "Most recent only"`, the entity extractor has no idea what tables are needed
- The cumulative query packages all requirements into a self-contained spec that any pipeline node can use in isolation

---

## 11. Prompt System

### File Loading

```python
# agent/prompts.py
def load_prompt(name: str, default: str = "") -> str:
    path = PROMPTS_DIR / f"{name}.txt"
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return default
```

Prompt files are loaded at **pipeline build time** (in `build_pipeline()`), not per-request. To hot-reload prompt changes: call `POST /api/admin/rebuild-pipeline`. This re-runs `build_pipeline()` without touching Oracle or the graph.

### Safe Format (`_safe_format`)

Used in `entity_extractor.py` to safely substitute placeholders in prompts that contain raw JSON:

```python
def _safe_format(template: str, **kwargs) -> str:
    safe = template.replace("{", "{{").replace("}", "}}")
    for key in kwargs:
        safe = safe.replace("{{" + key + "}}", "{" + key + "}")
    return safe.format(**kwargs)
```

This allows prompts to contain literal `{"thought": "...", "action": "..."}` JSON examples without triggering `KeyError` from Python's `str.format()`.

### Prompt Files

| File | Node | Key placeholders |
|---|---|---|
| `query_enricher_system.txt` | query_enricher | `{knowledge}` |
| `query_enricher_human.txt` | query_enricher | `{user_input}` |
| `entity_extractor_system.txt` | entity_extractor | `{schemas}`, `{schema_tree}`, `{tools_spec}`, `{max_calls}` |
| `intent_classifier_system.txt` | intent_classifier | (none — static) |
| `clarification_agent_system.txt` | clarification_agent | (none — static) |
| `clarification_agent_human.txt` | clarification_agent | `{query}`, `{entities}`, `{history}`, `{schema}` |
| `sql_generator_system.txt` | sql_generator | (none — static) |

---

## 12. Graph Cache

Cache key generation:
```python
raw = f"{config.dsn}|{config.user}|{sorted_schemas}|{FORMAT_VERSION}|{GRAPH_CACHE_VERSION}"
sha = hashlib.sha1(raw.encode()).hexdigest()[:12]
filename = f"graph_{sha}.pkl"
```

Changing any component of the key (different DSN, added schema, or bumping `GRAPH_CACHE_VERSION` env var) produces a new filename → automatic cache miss → full rebuild.

Pickle payload:
```python
{
    "version": _CACHE_FORMAT_VERSION,  # "2"
    "created_at": datetime.utcnow().isoformat(),
    "graph": KnowledgeGraph,            # full graph object
    "llm_enhanced": bool                # True if LLM annotation completed
}
```

Atomic write: saves to `{path}.tmp` then `os.replace()` — safe against partial writes from power loss or container kill.

Default cache location:
- Docker: `/data/graph_cache` (named volume `graph_cache_data`, persists across container restarts)
- Local dev: `~/.cache/knowledgeql`
- Override: `GRAPH_CACHE_PATH` env var

---

## 13. Oracle Data Access

### Thin Mode vs Thick Mode

`oracledb` runs in **thin mode** by default (no Oracle Instant Client required). Thick mode can be enabled via `ORACLE_THICK_MODE=true` if the Oracle Instant Client libraries are on `LD_LIBRARY_PATH`. However: if thin mode was already used in the same process (e.g. by a health-check probe), `init_oracle_client()` fails with `DPY-2019`. The extractor wraps this in try/except and falls back to thin mode silently.

### LONG Column Handling

Oracle's `ALL_TAB_COLUMNS.DATA_DEFAULT` and `ALL_VIEWS.TEXT` are `LONG` type (legacy Oracle). In `oracledb` thin mode, reading these with default settings raises `DPY-2016` ("buffer too small"). Fix:

```python
long_var = cursor.var(oracledb.DB_TYPE_LONG, 32767, cursor.arraysize)
```

The third argument (`cursor.arraysize`) is **required** — omitting it defaults to 1 and still triggers the error for batch fetches.

### Schema Filtering

All extraction queries use `WHERE owner IN (:s0, :s1, ...)` with named bind parameters. The `_bind_schemas(schemas)` helper returns `{"s0": "KYC", "s1": "FINANCE", ...}`. A previous bug used `_bind_schemas() + _bind_schemas()` which failed because Python dicts do not support `+` — both IN clauses now share the same bind dict.

---

## 14. Environment Configuration Reference

| Variable | Default | Description |
|---|---|---|
| `ORACLE_DSN` | — | `host:port/service_name` |
| `ORACLE_USER` | — | Oracle username |
| `ORACLE_PASSWORD` | — | Oracle password |
| `ORACLE_TARGET_SCHEMAS` | — | Comma-separated schema names |
| `ORACLE_USE_DBA_VIEWS` | `false` | Use DBA_* views (requires DBA role) |
| `ORACLE_SAMPLE_ROWS` | `10` | Rows sampled for context hints |
| `LLM_PROVIDER` | `openai` | `openai` \| `anthropic` \| `vertex` |
| `LLM_MODEL` | `gemini-2.5-flash` | Model name for configured provider |
| `LLM_API_KEY` | — | API key (not needed for Vertex) |
| `VERTEX_PROJECT` | — | GCP project ID |
| `VERTEX_LOCATION` | `us-central1` | GCP region |
| `VERTEX_THINKING_BUDGET` | `1024` | Token budget for Gemini thinking (0=disable) |
| `QUERY_ENRICHER_ENABLED` | `true` | Enable/disable query enrichment step |
| `KYC_KNOWLEDGE_FILE` | `kyc_business_knowledge.txt` | Path to domain knowledge file |
| `MAX_RESULT_ROWS` | `10000` | Max rows returned per query |
| `QUERY_TIMEOUT_SECONDS` | `30` | Oracle query timeout |
| `GRAPH_CACHE_VERSION` | `1` | Bump to force full graph rebuild |
| `GRAPH_CACHE_PATH` | `/data/graph_cache` (Docker), `~/.cache/knowledgeql` (local) | Cache directory |
| `GRAPH_CACHE_TTL_HOURS` | `0` | Hours before cache expires (0=never) |
| `MAX_JOIN_PATH_HOPS` | `4` | Max hops for join path search |
| `LOG_LEVEL` | `INFO` | Python logging level |
