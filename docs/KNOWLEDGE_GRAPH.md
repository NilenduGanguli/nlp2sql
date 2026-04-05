# KnowledgeQL – Knowledge Graph Documentation

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Graph Construction Pipeline](#graph-construction-pipeline)
4. [Node Types](#node-types)
5. [Relationship Types](#relationship-types)
6. [Edge Inference Methodology](#edge-inference-methodology)
7. [Runtime Schema Retrieval](#runtime-schema-retrieval)
8. [Column Value Cache](#column-value-cache)
9. [Initialization & Refresh](#initialization--refresh)
10. [Graph Cache](#graph-cache)
11. [LLM Graph Enhancement](#llm-graph-enhancement)
12. [Business Glossary Integration](#business-glossary-integration)
13. [KYC Domain Schema Walkthrough](#kyc-domain-schema-walkthrough)

---

## Overview

The **knowledge graph** is a pure in-memory Python property graph that mirrors the structure,
semantics, and relationships of a target Oracle schema. It is constructed once (and refreshed on
demand) **before user queries arrive** so that the NLP-to-SQL engine can answer schema-navigation
questions in milliseconds using in-process traversal rather than round-tripping to Oracle on every
request.

There is no external graph database. All nodes and edges are stored in plain Python dicts inside
a `KnowledgeGraph` instance, keyed for O(1) lookup. The graph can optionally be serialized to
disk as a pickle cache to survive process restarts.

### Why a graph?

Relational schema information is itself a graph:

- Tables are nodes.
- Foreign-key relationships are directed edges.
- Indexes, constraints, and views are satellite nodes attached to the primary structural nodes.
- Business terms from a domain glossary map *across* the structural layer, connecting natural
  language to specific columns or tables.

A property graph makes all of this traversable with a single Python function call.

### Where does the graph fit in the system?

```
┌────────────────────┐        init_graph.py        ┌──────────────────────────┐
│   Oracle Database  │  ──────────────────────────► │   KnowledgeGraph         │
│  (ALL_* views)     │  1. extract metadata         │  (in-memory Python dicts)│
└────────────────────┘  2. build nodes/edges        └──────────┬───────────────┘
                         3. infer glossary                      │
                         4. validate & cache                    │  traversal.py
                                                                ▼
                                                     ┌──────────────────────┐
                                                     │  NLP-to-SQL Agent    │
                                                     │  (query time)        │
                                                     └──────────────────────┘
```

At query time the agent:
1. Extracts entities from the natural-language question.
2. Resolves entities to graph nodes (BusinessTerm → Column/Table).
3. Finds join paths between candidate tables.
4. Serializes the relevant subgraph as DDL text (annotated with real column values).
5. Injects the DDL into the LLM prompt.

---

## Architecture

The knowledge graph module (`knowledge_graph/`) is structured as follows:

```
knowledge_graph/
├── config.py              – Environment-driven configuration (OracleConfig, GraphConfig)
├── models.py              – Typed Python dataclasses for every node and relationship type
├── oracle_extractor.py    – Reads Oracle ALL_* views into OracleMetadata
├── graph_builder.py       – Takes OracleMetadata and builds the in-memory KnowledgeGraph
├── graph_store.py         – KnowledgeGraph: pure Python dict-backed property graph
├── traversal.py           – Query functions consumed by the agent at runtime
├── glossary_loader.py     – Infers BusinessTerm/MAPS_TO nodes from Oracle metadata
├── glossary_loader_json.py– Loads glossary from a JSON file
├── llm_enhancer.py        – Post-build LLM enhancement (importance ranks, inferred joins)
├── graph_cache.py         – Pickle-based disk cache for the KnowledgeGraph
├── column_value_cache.py  – Lazy Oracle fetch + in-process cache of distinct column values
├── init_graph.py          – Orchestrator: health-check → extract → build → validate
└── knowledge_generator.py – LLM-generated business knowledge file for the agent
```

### KnowledgeGraph API

```python
from knowledge_graph.graph_store import KnowledgeGraph

g = KnowledgeGraph()

# Upsert a node (idempotent; existing properties are merged)
g.merge_node("Table", "KYC.CUSTOMERS", {"name": "CUSTOMERS", "schema": "KYC"})

# Upsert an edge
g.merge_edge("HAS_COLUMN", "KYC.CUSTOMERS", "KYC.CUSTOMERS.CUSTOMER_ID",
             ordinal_position=1)

# Retrieve edges
cols = g.get_out_edges("HAS_COLUMN", "KYC.CUSTOMERS")   # list of edge dicts
fk_sources = g.get_in_edges("HAS_FOREIGN_KEY", "KYC.CUSTOMERS.CUSTOMER_ID")

# Bulk retrieval
all_tables = g.get_all_nodes("Table")        # list of property dicts
all_fks    = g.get_all_edges("HAS_FOREIGN_KEY")
```

All operations are O(1) or O(n-edges-for-node) using indexed dicts. There is no Cypher, no
driver, and no network I/O during traversal.

### Dependencies

| Package              | Version  | Purpose                              |
|----------------------|----------|--------------------------------------|
| `python-oracledb`    | ≥2.0.0   | Pure-Python Oracle connector (thin)  |
| `networkx`           | ≥3.2.0   | BFS shortest-path for JOIN_PATH      |
| `python-Levenshtein` | ≥0.23.0  | Edit-distance for SIMILAR_TO         |
| `python-dotenv`      | ≥1.0.0   | `.env` loading                       |

---

## Graph Construction Pipeline

`graph_builder.GraphBuilder.build(oracle_metadata)` executes 13 ordered steps, all writing
directly into the `KnowledgeGraph` instance via `merge_node` / `merge_edge`:

| Step | Operation                        | In-Memory Operation                        |
|------|----------------------------------|--------------------------------------------|
|  1   | Schema nodes                     | `merge_node("Schema", name, props)`        |
|  2   | Table nodes + BELONGS_TO         | `merge_node("Table", fqn, props)` + edge   |
|  3   | Column nodes + HAS_COLUMN        | `merge_node("Column", fqn, props)` + edge  |
|  4   | HAS_PRIMARY_KEY edges            | `merge_edge("HAS_PRIMARY_KEY", tbl, col)`  |
|  5   | HAS_FOREIGN_KEY edges            | `merge_edge("HAS_FOREIGN_KEY", src, tgt)`  |
|  6   | Index nodes + HAS_INDEX + INDEXED_BY | `merge_node("Index", ...)` + 2 edges   |
|  7   | Constraint nodes + HAS_CONSTRAINT| `merge_node("Constraint", ...)` + edge     |
|  8   | View nodes + BELONGS_TO + DEPENDS_ON | `merge_node("View", ...)` + edges      |
|  9   | Procedure nodes + BELONGS_TO     | `merge_node("Procedure", ...)` + edge      |
| 10   | Synonym nodes                    | `merge_node("Synonym", ...)`               |
| 11   | Sequence nodes + BELONGS_TO      | `merge_node("Sequence", ...)` + edge       |
| 12   | JOIN_PATH edges (BFS via NetworkX)| `merge_edge("JOIN_PATH", t1, t2, ...)`    |
| 13   | SIMILAR_TO edges (3 strategies)  | `merge_edge("SIMILAR_TO", c1, c2, ...)`   |

All merge operations are **idempotent** — re-running the pipeline on the same schema updates
properties in place without creating duplicates.

---

## Node Types

### Schema

Represents an Oracle schema (user/owner).

| Property      | Type     | Source                      |
|---------------|----------|-----------------------------|
| `name`        | String   | `ALL_TABLES.OWNER` (distinct)|
| `created_date`| String   | `ALL_USERS.CREATED`         |
| `status`      | String   | `ALL_USERS.ACCOUNT_STATUS`  |

### Table

Represents a base table within a schema.

| Property        | Type     | Source                           |
|-----------------|----------|----------------------------------|
| `fqn`           | String   | `SCHEMA.TABLE_NAME` (uppercase)  |
| `name`          | String   | `ALL_TABLES.TABLE_NAME`         |
| `schema`        | String   | `ALL_TABLES.OWNER`              |
| `row_count`     | Integer  | `ALL_TABLES.NUM_ROWS`           |
| `avg_row_len`   | Integer  | `ALL_TABLES.AVG_ROW_LEN`        |
| `partitioned`   | Boolean  | `ALL_TABLES.PARTITIONED`        |
| `temporary`     | Boolean  | `ALL_TABLES.TEMPORARY`          |
| `comments`      | String   | `ALL_TAB_COMMENTS.COMMENTS`     |
| `last_analyzed` | String   | `ALL_TABLES.LAST_ANALYZED`      |
| `importance_rank`| Integer | LLM-assigned (1 = most central); set by `llm_enhancer.py` |
| `importance_tier`| String  | core / reference / audit / utility; set by `llm_enhancer.py` |
| `llm_description`| String  | One-line description for tables with no Oracle comment |

### Column

Represents a column within a table.

| Property        | Type     | Source                                     |
|-----------------|----------|--------------------------------------------|
| `fqn`           | String   | `SCHEMA.TABLE.COLUMN_NAME` (uppercase)     |
| `name`          | String   | `ALL_TAB_COLUMNS.COLUMN_NAME`             |
| `table_fqn`     | String   | Parent table FQN                           |
| `data_type`     | String   | `ALL_TAB_COLUMNS.DATA_TYPE`               |
| `data_length`   | Integer  | `ALL_TAB_COLUMNS.DATA_LENGTH`             |
| `precision`     | Integer  | `ALL_TAB_COLUMNS.DATA_PRECISION`          |
| `scale`         | Integer  | `ALL_TAB_COLUMNS.DATA_SCALE`              |
| `nullable`      | String   | `ALL_TAB_COLUMNS.NULLABLE`                |
| `column_id`     | Integer  | `ALL_TAB_COLUMNS.COLUMN_ID`               |
| `default_value` | String   | `ALL_TAB_COLUMNS.DATA_DEFAULT`            |
| `comments`      | String   | `ALL_COL_COMMENTS.COMMENTS`               |
| `is_pk`         | Boolean  | Derived: appears in a PK constraint        |
| `is_fk`         | Boolean  | Derived: appears as FK source column       |
| `is_indexed`    | Boolean  | Derived: appears in any index              |
| `num_distinct`  | Integer  | `ALL_TAB_COL_STATISTICS.NUM_DISTINCT`     |
| `sample_values` | List     | `SELECT DISTINCT col … FETCH FIRST 10`    |

### View

Represents a database view.

| Property    | Type   | Source                              |
|-------------|--------|-------------------------------------|
| `fqn`       | String | `SCHEMA.VIEW_NAME`                  |
| `name`      | String | `ALL_VIEWS.VIEW_NAME`               |
| `schema`    | String | `ALL_VIEWS.OWNER`                   |
| `definition`| String | `ALL_VIEWS.TEXT` (truncated 4 KB)   |
| `comments`  | String | `ALL_TAB_COMMENTS`                  |

### Index

Represents a schema index.

| Property      | Type   | Source                      |
|---------------|--------|-----------------------------|
| `fqn`         | String | `SCHEMA.INDEX_NAME`         |
| `name`        | String | `ALL_INDEXES.INDEX_NAME`    |
| `table_fqn`   | String | Parent table FQN            |
| `index_type`  | String | `ALL_INDEXES.INDEX_TYPE`    |
| `uniqueness`  | String | `ALL_INDEXES.UNIQUENESS`    |
| `columns_list`| String | Comma-joined column names   |

### Constraint

| Property    | Type   | Source                              |
|-------------|--------|-------------------------------------|
| `fqn`       | String | `SCHEMA.CONSTRAINT_NAME`            |
| `name`      | String | `ALL_CONSTRAINTS.CONSTRAINT_NAME`   |
| `type`      | String | `ALL_CONSTRAINTS.CONSTRAINT_TYPE`   |
| `table_fqn` | String | Parent table FQN                    |
| `status`    | String | `ALL_CONSTRAINTS.STATUS`            |

### Procedure

| Property  | Type   | Source                          |
|-----------|--------|---------------------------------|
| `fqn`     | String | `SCHEMA.OBJECT_NAME`            |
| `name`    | String | `ALL_PROCEDURES.OBJECT_NAME`    |
| `type`    | String | PROCEDURE / FUNCTION / PACKAGE  |
| `status`  | String | via `ALL_OBJECTS` (LEFT JOIN)   |

### Synonym

| Property    | Type   | Source                       |
|-------------|--------|------------------------------|
| `fqn`       | String | `SCHEMA.SYNONYM_NAME`        |
| `name`      | String | `ALL_SYNONYMS.SYNONYM_NAME`  |
| `target_fqn`| String | `OWNER.TABLE_NAME` the synonym points to |

### Sequence

| Property     | Type    | Source                          |
|--------------|---------|---------------------------------|
| `fqn`        | String  | `SCHEMA.SEQUENCE_NAME`          |
| `name`       | String  | `ALL_SEQUENCES.SEQUENCE_NAME`   |
| `min_value`  | Integer | `ALL_SEQUENCES.MIN_VALUE`       |
| `max_value`  | Integer | `ALL_SEQUENCES.MAX_VALUE`       |
| `increment`  | Integer | `ALL_SEQUENCES.INCREMENT_BY`    |
| `cache_size` | Integer | `ALL_SEQUENCES.CACHE_SIZE`      |

### BusinessTerm

Domain-specific business terms inferred from Oracle metadata (or loaded from a JSON glossary).

| Property          | Type   | Source                          |
|-------------------|--------|---------------------------------|
| `term`            | String | Humanized column/table name     |
| `definition`      | String | Oracle comment or inferred text |
| `aliases`         | List   | Glossary `aliases`              |
| `domain`          | String | Glossary `domain`               |
| `sensitivity_level`| String| Inferred from column name patterns |

### QueryPattern

Stores hand-crafted or ML-generated SQL templates for common question types.

| Property       | Type   | Notes                    |
|----------------|--------|--------------------------|
| `pattern_id`   | String | Unique identifier         |
| `nl_pattern`   | String | Natural-language template |
| `sql_template` | String | Parameterized SQL         |
| `tables`       | List   | Referenced table FQNs     |

---

## Relationship Types

### BELONGS_TO

`Table → BELONGS_TO → Schema`
`View → BELONGS_TO → Schema`
`Procedure → BELONGS_TO → Schema`
`Synonym → BELONGS_TO → Schema`
`Sequence → BELONGS_TO → Schema`

Structural containment: a schema object belongs to its Oracle owner.

---

### HAS_COLUMN

`Table → HAS_COLUMN {ordinal_position} → Column`

- `ordinal_position` (Integer): `ALL_TAB_COLUMNS.COLUMN_ID`

---

### HAS_PRIMARY_KEY

`Table → HAS_PRIMARY_KEY {constraint_name} → Column`

- `constraint_name` (String): `ALL_CONSTRAINTS.CONSTRAINT_NAME`

---

### HAS_FOREIGN_KEY

`Column → HAS_FOREIGN_KEY {constraint_name, on_delete_action, position} → Column`

Connects the FK source column directly to the referenced PK/UK column.

- `constraint_name` (String)
- `on_delete_action` (String): CASCADE | SET NULL | NO ACTION
- `position` (Integer): column position within a composite FK

---

### HAS_INDEX

`Table → HAS_INDEX → Index`

---

### INDEXED_BY

`Column → INDEXED_BY {column_position} → Index`

- `column_position` (Integer): position within a composite index

---

### HAS_CONSTRAINT

`Table → HAS_CONSTRAINT → Constraint`

---

### DEPENDS_ON

`View → DEPENDS_ON {dependency_type} → (Table | View)`

- `dependency_type` (String): SELECT (default)

Inferred from `ALL_DEPENDENCIES` where `type = 'VIEW'`.

---

### CALLS

`Procedure → CALLS → Procedure`

Inferred from `ALL_DEPENDENCIES` where `type IN ('PROCEDURE','FUNCTION')` and
`referenced_type IN ('PROCEDURE','FUNCTION')`.

---

### MAPS_TO

`BusinessTerm → MAPS_TO {confidence, mapping_type} → (Table | Column | View)`

- `confidence` (Float): 0.0–1.0 (1.0 = manually confirmed)
- `mapping_type` (String): manual | semantic | pattern | inferred

---

### JOIN_PATH

`Table → JOIN_PATH {path_key, join_columns, join_type, cardinality, weight} → Table`

Pre-computed bidirectional join path stored in both directions.

- `path_key` (String): `SRC_FQN::TGT_FQN`
- `join_columns` (List of dicts): `[{"src": col_fqn, "tgt": col_fqn}]`
- `join_type` (String): INNER (always; outer-join decisions are made by the LLM)
- `cardinality` (String): 1:1 | 1:N | N:1 | N:N (derived from PK/FK status)
- `weight` (Integer): number of hops in the FK chain
- `source` (String): `fk_graph` for FK-derived paths, `llm_inferred` for LLM-inferred paths

---

### SIMILAR_TO

`Column → SIMILAR_TO {similarity_score, match_type} → Column`

Connects columns in *different* tables that share naming conventions.

- `similarity_score` (Float): 0.0–1.0
- `match_type` (String): exact_name | fk_suffix | levenshtein

---

## Edge Inference Methodology

### HAS_FOREIGN_KEY

**Source:** `ALL_CONSTRAINTS` (type `R`) joined to `ALL_CONS_COLUMNS` (FK side) and
`ALL_CONS_COLUMNS` (referenced PK/UK side).

The extractor builds a `ForeignKeyRel` dataclass for each FK column pair. The builder then
calls:

```python
graph.merge_edge(
    "HAS_FOREIGN_KEY",
    src_col_fqn,
    tgt_col_fqn,
    merge_key="constraint_name",
    constraint_name=fk.constraint_name,
    on_delete_action=fk.on_delete_action,
    position=fk.position,
)
```

Note: disabled FK constraints are included (the `status = 'ENABLED'` filter has been removed)
so JOIN_PATHs are computed even when FK enforcement is off.

### JOIN_PATH (BFS via NetworkX)

1. After all FK edges are written, `GraphBuilder._compute_join_paths()` reads every
   `HAS_FOREIGN_KEY` edge from the graph.
2. A `networkx.MultiDiGraph` is built: each table is a node; each FK pair adds a directed
   edge **and its reverse** (so joins are traversable both ways).
3. `networkx.shortest_path()` on the *undirected* view finds all paths between table pairs
   within `max_join_path_hops` (default 4).
4. The intermediate join columns are reconstructed by walking the path edges.
5. A `JOIN_PATH` edge is merged for both `(A→B)` and `(B→A)` directions with
   `weight = number of hops`.

```python
# Simplified illustration
G = nx.MultiDiGraph()
for fk in fk_edges:
    G.add_edge(fk.source_table, fk.target_table, join_col=fk)
    G.add_edge(fk.target_table, fk.source_table, join_col=fk)

for src, tgt in combinations(tables, 2):
    try:
        path = nx.shortest_path(G.to_undirected(), src, tgt)
        if len(path) - 1 <= max_hops:
            graph.merge_edge("JOIN_PATH", src, tgt, join_columns=..., weight=len(path)-1)
            graph.merge_edge("JOIN_PATH", tgt, src, join_columns=..., weight=len(path)-1)
    except nx.NetworkXNoPath:
        pass
```

### SIMILAR_TO (Three-Strategy Detection)

Columns in different tables are linked as `SIMILAR_TO` if:

**Strategy 1 – Exact name match** (score = 1.0):
Both columns share the same `COLUMN_NAME` (case-insensitive), are in different tables,
and neither is a primary key.

**Strategy 2 – FK suffix pattern** (score = 0.9):
The source column name minus a suffix (`_ID`, `_CODE`, `_KEY`, `_NO`, `_NUM`, `_REF`)
matches the other table's name. For example `CUSTOMER_ID` (suffix `_ID`) in ACCOUNTS
matches CUSTOMERS.

**Strategy 3 – Levenshtein distance** (score = `1 - distance/max_length`):
Edit distance between the two column names is ≤ `levenshtein_max_distance` (default 2)
and the resulting score is ≥ `similarity_min_score` (default 0.75).
Filters out columns with very short names (< 4 chars) to reduce noise.

### DEPENDS_ON

**Source:** `ALL_DEPENDENCIES` filtered to:
```sql
WHERE type = 'VIEW'
  AND owner IN :schemas
  AND referenced_type IN ('TABLE', 'VIEW')
```

Each row becomes a `DEPENDS_ON` edge from the View node to the referenced Table/View node.

---

## Runtime Schema Retrieval

At query time, `knowledge_graph/traversal.py` exposes these functions. All accept a
`KnowledgeGraph` instance and return typed Python dicts — no database round-trip.

| Function                  | Purpose                                                  |
|---------------------------|----------------------------------------------------------|
| `get_columns_for_table`   | All columns for a table, ordered by `column_id`          |
| `get_table_detail`        | Full table + columns + FKs + indexes in one pass         |
| `find_join_path`          | Precomputed JOIN_PATH lookup; BFS fallback if missing    |
| `resolve_business_term`   | Glossary MAPS_TO lookup; schema name-search as fallback  |
| `get_context_subgraph`    | Retrieve multi-table subgraph as Python dicts            |
| `serialize_context_to_ddl`| Convert subgraph to LLM-ready DDL string; accepts optional `get_values` callback |
| `search_schema`           | Text search over table and column names/comments         |
| `list_all_tables`         | Paginated table listing with optional schema filter      |
| `get_index_hints`         | Indexes covering a set of columns                        |
| `get_view_lineage`        | Upstream tables a view depends on                        |
| `get_procedure_calls`     | Procedure call graph                                     |
| `get_query_patterns`      | Stored SQL templates for a set of tables                 |
| `get_similar_columns`     | Columns similar to a given column                        |

### Context Serialization Format

`serialize_context_to_ddl(context, get_values=None)` produces a DDL-like text block per
table. When a `get_values` callable is supplied (from `column_value_cache.make_value_getter`),
columns that look like enums are annotated with their actual stored values:

```sql
-- TABLE: KYC.CUSTOMERS (50,000 rows)
-- Core customer entity for KYC compliance
CREATE TABLE KYC.CUSTOMERS (
    CUSTOMER_ID   NUMBER(10)     NOT NULL,   -- PK
    FIRST_NAME    VARCHAR2(100)  NOT NULL,
    STATUS        VARCHAR2(10)   NOT NULL,   -- Values(3): 'ACTIVE', 'INACTIVE', 'PENDING'
    RISK_RATING   VARCHAR2(10)   NOT NULL,   -- Values(4): 'LOW', 'MEDIUM', 'HIGH', 'VERY_HIGH'
    ACCOUNT_MANAGER_ID NUMBER(10) NULL,      -- FK: KYC.EMPLOYEES.EMPLOYEE_ID
);
-- Business terms: Customer Due Diligence (CDD), Risk Rating
```

The `-- Values(N): ...` annotation is added only for columns where:
- `is_likely_enum_column()` returns True (name contains STATUS/TYPE/FLAG/CODE/etc., or the
  column is a short CHAR/VARCHAR2), and
- the live Oracle fetch returns ≤ 30 distinct non-null values.

This bridges the gap between semantic intent ("active customers") and the actual stored value
("ACTIVE"), so the LLM can write precise WHERE clauses without guessing.

---

## Column Value Cache

**File:** `knowledge_graph/column_value_cache.py`

A lightweight in-process cache that fetches distinct values for low-cardinality columns from
Oracle on first access, then caches them in memory for the lifetime of the process.

| Function                  | Purpose                                                              |
|---------------------------|----------------------------------------------------------------------|
| `is_likely_enum_column`   | Heuristic: returns True for STATUS/TYPE/FLAG/CODE columns and short CHAR/VARCHAR2 |
| `get_distinct_values`     | Fetch distinct non-null values for `schema.table.column`; cached after first call |
| `make_value_getter`       | Returns a `(schema, table, column) → [values]` closure bound to a config object; pass to `serialize_context_to_ddl` |
| `invalidate_cache`        | Clears the in-process dict cache; call after a graph rebuild         |

Key design decisions:

- **Lazy**: values are fetched only when a column is actually included in a DDL context block.
- **Safe**: any Oracle error (timeout, privilege, network) returns `[]` silently; the DDL is
  still generated, just without the `-- Values` annotation.
- **Threshold**: columns with more than `MAX_DISTINCT_VALUES` (30) distinct values are treated
  as non-enum and cached as `[]` to prevent annotation of high-cardinality columns.
- **Timeout**: each Oracle connection uses a 5-second `callTimeout` to prevent slow fetches
  from blocking the agent.

---

## Initialization & Refresh

### Full initialization

```python
from knowledge_graph.init_graph import initialize_graph

graph, report = initialize_graph()   # returns (KnowledgeGraph, dict)
# report["success"] is True on success
# report keys: oracle_connected, extraction, build, glossary, validation_passed, elapsed_seconds
```

Or from the CLI:

```bash
python -m knowledge_graph.init_graph
```

Pipeline:
1. Validate config (required env vars present)
2. Health-check Oracle (`SELECT 1 FROM DUAL`)
3. Extract Oracle metadata (ALL_* views, configurable schemas)
4. Build in-memory knowledge graph (13-step pipeline)
5. Infer business glossary from Oracle metadata
6. Validate graph (consistency checks)
7. Return `(KnowledgeGraph, report)` tuple

### Refresh-only mode

```bash
python -m knowledge_graph.init_graph --refresh-only
```

Re-runs all merge steps to apply schema drift; skips validation checks.

### Validation checks

Validation runs against the in-memory graph directly (no external query):

| Check                              | Expected     |
|------------------------------------|--------------|
| Table count > 0                    | True         |
| Column count > 0                   | True         |
| HAS_COLUMN edges exist             | True         |
| Orphan columns (no HAS_COLUMN edge)| 0            |

---

## Graph Cache

**File:** `knowledge_graph/graph_cache.py`

Serializes the `KnowledgeGraph` to disk using pickle so that an expensive Oracle extraction +
graph build is not repeated on every process start (e.g. container restart).

| Function           | Purpose                                              |
|--------------------|------------------------------------------------------|
| `save_graph`       | Pickle the graph to disk (atomic `.tmp` + `os.replace`) |
| `load_graph`       | Load from disk; returns `None` on miss/stale/error   |
| `get_cache_path`   | Compute the cache file path from config (SHA1-based) |
| `invalidate_cache` | Delete the cache file                                |
| `cache_info`       | Return metadata (created_at, llm_enhanced, etc.)     |

**Cache key:** SHA1 of `ORACLE_DSN|ORACLE_USER|TARGET_SCHEMAS|FORMAT_VERSION|GRAPH_CACHE_VERSION`
→ 12-char hex → `graph_{hash}.pkl`

**Default path:**
- Docker container: `/data/graph_cache` (mount a named Docker volume here)
- Local dev: `~/.cache/knowledgeql`
- Override with `GRAPH_CACHE_PATH` env var

**TTL:** `GRAPH_CACHE_TTL_HOURS=0` (default) = no expiry; set to a positive integer to auto-rebuild
stale caches.

**Force rebuild:** bump `GRAPH_CACHE_VERSION` in `.env` — this changes the cache filename,
guaranteeing a cache miss without deleting the old file manually.

**`llm_enhanced` flag:** the cache stores whether the LLM enhancement pass has already run.
On load, a cache with `llm_enhanced=True` skips re-enhancement, preventing redundant LLM calls
across container restarts.

---

## LLM Graph Enhancement

**File:** `knowledge_graph/llm_enhancer.py`

`enhance_graph_with_llm(graph, llm)` runs three post-build steps to enrich the graph with
LLM-derived metadata. Each step is independently wrapped in try/except so a failure in one
never blocks the others.

### Step 1 — Table Importance Ranking

Sends all tables (batched ≤50/call) to the LLM, asking it to rank them by business centrality.
Tables are pre-sorted by FK degree + row_count so the model sees the most structurally important
ones first (context priming).

Properties written to each Table node:
- `importance_rank` (Integer): 1 = most central to the business domain
- `importance_tier` (String): `core` | `reference` | `audit` | `utility`
- `importance_reason` (String): one-line rationale

Tables the LLM misses receive a structural fallback rank (by FK degree).

These properties are used by the entity extractor to build a tiered schema tree in its system
prompt — core tables appear first, giving the LLM better context about what matters.

### Step 2 — Missing Relationship Inference

Finds Table nodes with no JOIN_PATH edges. For each isolated table, identifies FK-candidate
columns (suffix `_ID/_CODE/_KEY/_FK/_NUM/_NO/_REF`) and asks the LLM to confirm whether a
join relationship is plausible with another table.

Confirmed pairs (confidence HIGH or MEDIUM) get synthesized `JOIN_PATH` edges written into the
graph with `source="llm_inferred"`. This ensures the context builder and entity extractor can
use these relationships normally even when FK enforcement is off or the schema uses non-standard
conventions.

### Step 3 — Missing Table Descriptions

Table nodes whose Oracle `ALL_TAB_COMMENTS` entry is NULL receive an LLM-generated one-line
description stored as `llm_description`. This property is **separate from** `comments` — Oracle
comments are never overwritten.

---

## Business Glossary Integration

The glossary bridges the gap between **what business users say** (e.g., "high risk customer",
"PEP status") and **what the database actually contains** (e.g., `KYC.CUSTOMERS.RISK_RATING`).

### Two glossary sources

**1. Inferred glossary** (`glossary_loader.py` — `InferredGlossaryBuilder`):
Derives `BusinessTerm` nodes automatically from Oracle column and table metadata already
captured in `OracleMetadata`. No external file required. Sources:
- `ALL_COL_COMMENTS` — column-level definitions (confidence 0.95)
- `ALL_TAB_COMMENTS` — table-level descriptions (confidence 0.80)
- Column name + sample values — inferred term when no comment exists (confidence 0.50–0.65)

**2. JSON glossary** (`glossary_loader_json.py`):
Loads a hand-crafted `data/kyc_glossary.json` file and upserts `BusinessTerm` nodes and
`MAPS_TO` edges into the graph. JSON entries take the same shape:

```json
{
  "term": "Risk Rating",
  "definition": "Risk classification assigned to a customer",
  "aliases": ["risk_level", "risk_score", "customer_risk"],
  "domain": "KYC",
  "sensitivity_level": "CONFIDENTIAL",
  "mappings": [
    {
      "fqn": "KYC.CUSTOMERS.RISK_RATING",
      "label": "Column",
      "confidence": 1.0,
      "mapping_type": "manual"
    }
  ]
}
```

Both loaders produce the same graph structure: `BusinessTerm` nodes connected via `MAPS_TO`
edges to `Table` or `Column` nodes, matched by `fqn`.

### Why this matters for NLP-to-SQL

When a user asks *"show me all high risk customers"*, `resolve_business_term` can:
1. Look up `BusinessTerm` nodes whose `term` or `aliases` match "high risk"
2. Follow `MAPS_TO` edges (filtered by confidence) to find `KYC.CUSTOMERS.RISK_RATING`
3. Use that column in the generated SQL — without the user ever knowing the column name

---

## KYC Domain Schema Walkthrough

The reference implementation uses an 8-table KYC schema:

```
CUSTOMERS ←──────────────────────────────────────────────────────┐
    │ (ACCOUNT_MANAGER_ID → EMPLOYEES.EMPLOYEE_ID)               │
    │                                                             │
    ├──── ACCOUNTS (CUSTOMER_ID)                                  │
    │         │                                                   │
    │         └── TRANSACTIONS (ACCOUNT_ID)                       │
    │                                                             │
    ├──── KYC_REVIEWS (CUSTOMER_ID, REVIEWER_ID → EMPLOYEES)     │
    ├──── RISK_ASSESSMENTS (CUSTOMER_ID, ASSESSED_BY → EMPLOYEES)│
    ├──── BENEFICIAL_OWNERS (CUSTOMER_ID)                         │
    └──── PEP_STATUS (CUSTOMER_ID)                                │
                                                                  │
EMPLOYEES ────────────────────────────────────────────────────────┘
```

### Key JOIN_PATHs automatically computed

| Source Table      | Target Table    | Hops | Join Columns                        |
|-------------------|-----------------|------|-------------------------------------|
| TRANSACTIONS      | CUSTOMERS       | 2    | ACCOUNT_ID → ACCOUNT_ID, CUSTOMER_ID → CUSTOMER_ID |
| KYC_REVIEWS       | EMPLOYEES       | 1    | REVIEWER_ID → EMPLOYEE_ID           |
| BENEFICIAL_OWNERS | CUSTOMERS       | 1    | CUSTOMER_ID → CUSTOMER_ID           |
| PEP_STATUS        | TRANSACTIONS    | 3    | CUSTOMER_ID → CUSTOMER_ID, ACCOUNT_ID |
| RISK_ASSESSMENTS  | KYC_REVIEWS     | 2    | via CUSTOMERS                       |

### SIMILAR_TO edges automatically inferred

| Column 1                         | Column 2                        | Score | Strategy     |
|----------------------------------|---------------------------------|-------|--------------|
| CUSTOMERS.CUSTOMER_ID            | ACCOUNTS.CUSTOMER_ID            | 1.0   | exact_name   |
| ACCOUNTS.ACCOUNT_ID              | TRANSACTIONS.ACCOUNT_ID         | 1.0   | exact_name   |
| KYC_REVIEWS.REVIEWER_ID          | EMPLOYEES.EMPLOYEE_ID           | 0.9   | fk_suffix    |
| RISK_ASSESSMENTS.ASSESSED_BY     | EMPLOYEES.EMPLOYEE_ID           | 0.86  | levenshtein  |
