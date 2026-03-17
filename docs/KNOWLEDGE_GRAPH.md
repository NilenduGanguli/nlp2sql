# KnowledgeQL – Knowledge Graph Documentation

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Graph Construction Pipeline](#graph-construction-pipeline)
4. [Node Types](#node-types)
5. [Relationship Types](#relationship-types)
6. [Edge Inference Methodology](#edge-inference-methodology)
7. [Runtime Schema Retrieval](#runtime-schema-retrieval)
8. [Initialization & Refresh](#initialization--refresh)
9. [Business Glossary Integration](#business-glossary-integration)
10. [KYC Domain Schema Walkthrough](#kyc-domain-schema-walkthrough)

---

## Overview

The **knowledge graph** is a Neo4j property graph that mirrors the structure, semantics, and
relationships of a target Oracle schema. It is constructed once (and incrementally refreshed)
**before user queries arrive** so that the NLP-to-SQL engine can answer schema-navigation
questions in milliseconds using Cypher rather than round-tripping to Oracle on every request.

### Why a graph?

Relational schema information is itself a graph:

- Tables are nodes.
- Foreign-key relationships are directed edges.
- Indexes, constraints, and views are satellite nodes attached to the primary structural nodes.
- Business terms from a domain glossary map *across* the structural layer, connecting natural
  language to specific columns or tables.

A property graph (Neo4j) makes all of this traversable with a single Cypher query.

### Where does the graph fit in the system?

```
┌────────────────────┐        init_graph.py        ┌──────────────────────┐
│   Oracle Database  │  ──────────────────────────► │   Neo4j Graph DB     │
│  (DBA_* views)     │  1. extract metadata         │  (knowledge graph)   │
└────────────────────┘  2. build nodes/edges        └──────────┬───────────┘
                         3. load glossary                       │
                         4. validate & warm cache               │  Cypher traversal
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
4. Serializes the relevant subgraph as DDL text.
5. Injects the DDL into the LLM prompt.

---

## Architecture

The knowledge graph module (`knowledge_graph/`) is structured as follows:

```
knowledge_graph/
├── config.py          – Environment-driven configuration (OracleConfig, Neo4jConfig, GraphConfig)
├── models.py          – Typed Python dataclasses for every node and relationship type
├── oracle_extractor.py– Reads Oracle data-dictionary views into OracleMetadata
├── graph_builder.py   – Takes OracleMetadata and builds/refreshes the Neo4j graph
├── traversal.py       – Cypher-backed query functions consumed by the agent at runtime
├── glossary_loader.py – Ingests JSON business glossary into BusinessTerm/MAPS_TO nodes
└── init_graph.py      – CLI orchestrator: health-check → extract → build → validate
```

### Dependencies

| Package              | Version  | Purpose                              |
|----------------------|----------|--------------------------------------|
| `neo4j`              | ≥5.14.0  | Neo4j Python driver (Bolt/async)     |
| `python-oracledb`    | ≥2.0.0   | Pure-Python Oracle connector         |
| `networkx`           | ≥3.2.0   | BFS shortest-path for JOIN_PATH      |
| `python-Levenshtein` | ≥0.23.0  | Edit-distance for SIMILAR_TO         |
| `python-dotenv`      | ≥1.0.0   | `.env` loading                       |

---

## Graph Construction Pipeline

`graph_builder.GraphBuilder.build(oracle_metadata)` executes 13 ordered steps:

| Step | Operation                        | Cypher Pattern                        |
|------|----------------------------------|---------------------------------------|
|  1   | Schema constraints & indexes     | `CREATE CONSTRAINT IF NOT EXISTS`     |
|  2   | Schema nodes                     | `MERGE (s:Schema {name}) SET ...`     |
|  3   | Table nodes + BELONGS_TO         | `MERGE (t:Table {fqn}) MERGE (t)→(s)` |
|  4   | Column nodes + HAS_COLUMN        | `MERGE (c:Column {fqn}) MERGE (t)→(c)`|
|  5   | HAS_PRIMARY_KEY edges            | `MATCH (t),(c) MERGE (t)-[:HAS_PK]→(c)` |
|  6   | HAS_FOREIGN_KEY edges            | `MATCH (src),(tgt) MERGE (src)→(tgt)` |
|  7   | Index nodes + HAS_INDEX + INDEXED_BY | `MERGE (idx:Index) MERGE (t)→(idx) MERGE (c)→(idx)` |
|  8   | Constraint nodes + HAS_CONSTRAINT| `MERGE (con:Constraint) MERGE (t)→(con)` |
|  9   | View nodes + BELONGS_TO + DEPENDS_ON | `MERGE (v:View) MERGE (v)→(s) MERGE (v)→(t)` |
| 10   | Procedure nodes + BELONGS_TO     | `MERGE (p:Procedure) MERGE (p)→(s)`  |
| 11   | Synonym & Sequence nodes         | `MERGE (syn:Synonym) MERGE (seq:Sequence)` |
| 12   | JOIN_PATH edges (BFS via NetworkX)| `MERGE (t1)-[:JOIN_PATH]→(t2)`       |
| 13   | SIMILAR_TO edges (3 strategies)  | `MERGE (c1)-[:SIMILAR_TO]→(c2)`      |

All MERGE operations are **idempotent** — re-running the pipeline on the same schema
updates properties in place without creating duplicates.

### Batching

All node-creation Cypher uses `UNWIND $rows AS row … MERGE … SET` with a configurable
`batch_size` (default 500) to prevent large transaction timeouts. The builder slices
lists into chunks before each `session.run()` call.

---

## Node Types

### Schema

Represents an Oracle schema (user/owner).

| Property      | Type     | Source                      |
|---------------|----------|-----------------------------|
| `name`        | String   | `DBA_USERS.USERNAME`        |
| `created_date`| String   | `DBA_USERS.CREATED`         |
| `status`      | String   | `DBA_USERS.ACCOUNT_STATUS`  |

### Table

Represents a base table within a schema.

| Property        | Type     | Source                           |
|-----------------|----------|----------------------------------|
| `fqn`           | String   | `SCHEMA.TABLE_NAME` (uppercase)  |
| `name`          | String   | `DBA_TABLES.TABLE_NAME`         |
| `schema`        | String   | `DBA_TABLES.OWNER`              |
| `row_count`     | Integer  | `DBA_TABLES.NUM_ROWS`           |
| `avg_row_len`   | Integer  | `DBA_TABLES.AVG_ROW_LEN`        |
| `partitioned`   | Boolean  | `DBA_TABLES.PARTITIONED`        |
| `temporary`     | Boolean  | `DBA_TABLES.TEMPORARY`          |
| `comments`      | String   | `DBA_TAB_COMMENTS.COMMENTS`     |
| `last_analyzed` | String   | `DBA_TABLES.LAST_ANALYZED`      |

### Column

Represents a column within a table.

| Property        | Type     | Source                                     |
|-----------------|----------|--------------------------------------------|
| `fqn`           | String   | `SCHEMA.TABLE.COLUMN_NAME` (uppercase)     |
| `name`          | String   | `DBA_TAB_COLUMNS.COLUMN_NAME`             |
| `table_fqn`     | String   | Parent table FQN                           |
| `data_type`     | String   | `DBA_TAB_COLUMNS.DATA_TYPE`               |
| `data_length`   | Integer  | `DBA_TAB_COLUMNS.DATA_LENGTH`             |
| `precision`     | Integer  | `DBA_TAB_COLUMNS.DATA_PRECISION`          |
| `scale`         | Integer  | `DBA_TAB_COLUMNS.DATA_SCALE`              |
| `nullable`      | String   | `DBA_TAB_COLUMNS.NULLABLE`                |
| `column_id`     | Integer  | `DBA_TAB_COLUMNS.COLUMN_ID`               |
| `default_value` | String   | `DBA_TAB_COLUMNS.DATA_DEFAULT`            |
| `comments`      | String   | `DBA_COL_COMMENTS.COMMENTS`               |
| `is_pk`         | Boolean  | Derived: appears in a PK constraint        |
| `is_fk`         | Boolean  | Derived: appears as FK source column       |
| `is_indexed`    | Boolean  | Derived: appears in any index              |
| `num_distinct`  | Integer  | `DBA_TAB_COL_STATISTICS.NUM_DISTINCT`     |
| `sample_values` | List     | `SELECT DISTINCT col … FETCH FIRST 10`    |

### View

Represents a database view.

| Property    | Type   | Source                          |
|-------------|--------|---------------------------------|
| `fqn`       | String | `SCHEMA.VIEW_NAME`              |
| `name`      | String | `DBA_VIEWS.VIEW_NAME`           |
| `schema`    | String | `DBA_VIEWS.OWNER`               |
| `definition`| String | `DBA_VIEWS.TEXT` (truncated 4 KB)|
| `comments`  | String | `DBA_TAB_COMMENTS`              |

### Index

Represents a schema index.

| Property      | Type   | Source                    |
|---------------|--------|---------------------------|
| `fqn`         | String | `SCHEMA.INDEX_NAME`       |
| `name`        | String | `DBA_INDEXES.INDEX_NAME`  |
| `table_fqn`   | String | Parent table FQN          |
| `index_type`  | String | `DBA_INDEXES.INDEX_TYPE`  |
| `uniqueness`  | String | `DBA_INDEXES.UNIQUENESS`  |
| `columns_list`| String | Comma-joined column names |

### Constraint

| Property    | Type   | Source                           |
|-------------|--------|----------------------------------|
| `fqn`       | String | `SCHEMA.CONSTRAINT_NAME`         |
| `name`      | String | `DBA_CONSTRAINTS.CONSTRAINT_NAME`|
| `type`      | String | `DBA_CONSTRAINTS.CONSTRAINT_TYPE`|
| `table_fqn` | String | Parent table FQN                 |
| `status`    | String | `DBA_CONSTRAINTS.STATUS`         |

### Procedure

| Property  | Type   | Source                       |
|-----------|--------|------------------------------|
| `fqn`     | String | `SCHEMA.OBJECT_NAME`         |
| `name`    | String | `DBA_PROCEDURES.OBJECT_NAME` |
| `type`    | String | PROCEDURE / FUNCTION / PACKAGE|
| `status`  | String | `DBA_PROCEDURES.STATUS`      |

### Synonym

| Property    | Type   | Source                      |
|-------------|--------|-----------------------------|
| `fqn`       | String | `SCHEMA.SYNONYM_NAME`       |
| `name`      | String | `DBA_SYNONYMS.SYNONYM_NAME` |
| `target_fqn`| String | `OWNER.TABLE_NAME` the synonym points to |

### Sequence

| Property     | Type    | Source                         |
|--------------|---------|--------------------------------|
| `fqn`        | String  | `SCHEMA.SEQUENCE_NAME`         |
| `name`       | String  | `DBA_SEQUENCES.SEQUENCE_NAME`  |
| `min_value`  | Integer | `DBA_SEQUENCES.MIN_VALUE`      |
| `max_value`  | Integer | `DBA_SEQUENCES.MAX_VALUE`      |
| `increment`  | Integer | `DBA_SEQUENCES.INCREMENT_BY`   |
| `cache_size` | Integer | `DBA_SEQUENCES.CACHE_SIZE`     |

### BusinessTerm

Domain-specific business terms loaded from the JSON glossary.

| Property          | Type   | Source             |
|-------------------|--------|--------------------|
| `term`            | String | Glossary `term`    |
| `definition`      | String | Glossary `definition` |
| `aliases`         | List   | Glossary `aliases` |
| `domain`          | String | Glossary `domain`  |
| `sensitivity_level`| String| Glossary `sensitivity_level` |

### QueryPattern

Stores hand-crafted or ML-generated SQL templates for common question types.

| Property       | Type   | Notes                   |
|----------------|--------|-------------------------|
| `pattern_id`   | String | Unique identifier        |
| `nl_pattern`   | String | Natural-language template|
| `sql_template` | String | Parameterized SQL        |
| `tables`       | List   | Referenced table FQNs    |

---

## Relationship Types

### BELONGS_TO

`(:Table)-[:BELONGS_TO]→(:Schema)`
`(:View)-[:BELONGS_TO]→(:Schema)`
`(:Procedure)-[:BELONGS_TO]→(:Schema)`
`(:Synonym)-[:BELONGS_TO]→(:Schema)`
`(:Sequence)-[:BELONGS_TO]→(:Schema)`

Structural containment: a schema object belongs to its Oracle owner.

---

### HAS_COLUMN

`(:Table)-[:HAS_COLUMN {ordinal_position}]→(:Column)`

- `ordinal_position` (Integer): `DBA_TAB_COLUMNS.COLUMN_ID`

---

### HAS_PRIMARY_KEY

`(:Table)-[:HAS_PRIMARY_KEY {constraint_name}]→(:Column)`

- `constraint_name` (String): `DBA_CONSTRAINTS.CONSTRAINT_NAME`

---

### HAS_FOREIGN_KEY

`(:Column)-[:HAS_FOREIGN_KEY {constraint_name, on_delete_action, position}]→(:Column)`

Connects the FK source column directly to the referenced PK/UK column.

- `constraint_name` (String)
- `on_delete_action` (String): CASCADE | SET NULL | NO ACTION
- `position` (Integer): column position within a composite FK

---

### HAS_INDEX

`(:Table)-[:HAS_INDEX]→(:Index)`

---

### INDEXED_BY

`(:Column)-[:INDEXED_BY {column_position}]→(:Index)`

- `column_position` (Integer): position within a composite index

---

### HAS_CONSTRAINT

`(:Table)-[:HAS_CONSTRAINT]→(:Constraint)`

---

### DEPENDS_ON

`(:View)-[:DEPENDS_ON {dependency_type}]→(:Table | :View)`

- `dependency_type` (String): SELECT (default)

Inferred from `DBA_DEPENDENCIES` where `type = 'VIEW'`.

---

### CALLS

`(:Procedure)-[:CALLS]→(:Procedure)`

Inferred from `DBA_DEPENDENCIES` where `type IN ('PROCEDURE','FUNCTION')` and
`referenced_type IN ('PROCEDURE','FUNCTION')`.

---

### MAPS_TO

`(:BusinessTerm)-[:MAPS_TO {confidence, mapping_type}]→(:Table | :Column | :View)`

- `confidence` (Float): 0.0–1.0 (1.0 = manually confirmed)
- `mapping_type` (String): manual | semantic | pattern | inferred

---

### JOIN_PATH

`(:Table)-[:JOIN_PATH {path_key, join_columns, join_type, cardinality, weight}]→(:Table)`

Pre-computed bidirectional join path stored on both directions.

- `path_key` (String): `SRC_FQN::TGT_FQN`
- `join_columns` (List of Maps): `[{src, tgt}]`
- `join_type` (String): INNER (always; outer-join decisions are made by the LLM)
- `cardinality` (String): 1:1 | 1:N | N:1 | N:N (derived from PK/FK status)
- `weight` (Integer): number of hops in the FK chain

---

### SIMILAR_TO

`(:Column)-[:SIMILAR_TO {similarity_score, match_type}]→(:Column)`

Connects columns in *different* tables that share naming conventions.

- `similarity_score` (Float): 0.0–1.0
- `match_type` (String): exact_name | fk_suffix | levenshtein

---

## Edge Inference Methodology

### HAS_FOREIGN_KEY

**Source:** `DBA_CONSTRAINTS` (type `R`, status `ENABLED`) joined to
`DBA_CONS_COLUMNS` (FK side) and `DBA_CONS_COLUMNS` (referenced PK/UK side).

The extractor builds a Python `ForeignKeyRel` dataclass for each FK column pair.
The graph builder then issues:

```cypher
MATCH (src:Column {fqn: $src_fqn})
MATCH (tgt:Column {fqn: $tgt_fqn})
MERGE (src)-[fk:HAS_FOREIGN_KEY {constraint_name: $name}]->(tgt)
SET fk.on_delete_action = $on_delete_action, fk.position = $position
```

### JOIN_PATH (BFS via NetworkX)

1. After all FK edges are loaded, `GraphBuilder._compute_join_paths()` queries Neo4j
   for every `(src:Column)-[:HAS_FOREIGN_KEY]->(tgt:Column)` edge.
2. A `networkx.MultiDiGraph` is built: each table is a node; each FK pair
   adds a directed edge **and its reverse** (so joins are traversable both ways).
3. `networkx.shortest_path()` on the *undirected* view finds all paths between
   table pairs within `max_join_path_hops` (default 4).
4. The intermediate join columns are reconstructed by walking the path edges.
5. A `JOIN_PATH` edge is MERGE'd for both `(A→B)` and `(B→A)` directions
   with `weight = number of hops`.

```python
# Simplified: graph building
G = nx.MultiDiGraph()
for fk in fk_edges:
    G.add_edge(fk.source_table, fk.target_table, join_col=fk)
    G.add_edge(fk.target_table, fk.source_table, join_col=fk)

# Shortest paths (undirected view, max 4 hops)
for src, tgt in combinations(tables, 2):
    try:
        path = nx.shortest_path(G.to_undirected(), src, tgt)
        if len(path) - 1 <= max_hops:
            store_join_path(src, tgt, path)
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

**Source:** `DBA_DEPENDENCIES` filtered to:
```sql
WHERE type = 'VIEW'
  AND owner IN :schemas
  AND referenced_type IN ('TABLE', 'VIEW')
```

Each row becomes a `DEPENDS_ON` edge from the View node to the referenced Table/View node.

---

## Runtime Schema Retrieval

At query time, `knowledge_graph/traversal.py` exposes these functions:

| Function                  | Purpose                                                  |
|---------------------------|----------------------------------------------------------|
| `get_columns_for_table`   | All columns for a table, ordered by `column_id`          |
| `get_table_detail`        | Full table + columns + FKs + indexes in one Cypher query |
| `find_join_path`          | Precomputed JOIN_PATH first; live traversal as fallback  |
| `resolve_business_term`   | Glossary MAPS_TO first; schema name-search as fallback   |
| `get_context_subgraph`    | Retrieve multi-table subgraph as Python dicts            |
| `serialize_context_to_ddl`| Convert subgraph to LLM-ready DDL string                 |
| `search_schema`           | Full-text index search; CONTAINS fallback                |
| `list_all_tables`         | Paginated table listing with optional schema filter      |
| `get_index_hints`         | Indexes covering a set of columns                        |
| `get_view_lineage`        | Upstream tables a view depends on                        |
| `get_procedure_calls`     | Procedure call graph                                     |
| `get_query_patterns`      | Stored SQL templates for a set of tables                 |
| `get_similar_columns`     | Columns similar to a given column                        |

### Context Serialization Format

`serialize_context_to_ddl()` produces a DDL-like text block per table:

```sql
-- KYC.CUSTOMERS (50,000 rows)
-- Core customer entity for KYC compliance
CREATE TABLE KYC.CUSTOMERS (
    CUSTOMER_ID   NUMBER(10)     NOT NULL,   -- PK
    FIRST_NAME    VARCHAR2(100)  NOT NULL,
    RISK_RATING   VARCHAR2(10)   NOT NULL,   -- IDX: IDX_CUST_RISK
    ACCOUNT_MANAGER_ID NUMBER(10) NULL,      -- FK: KYC.EMPLOYEES.EMPLOYEE_ID
);
-- Business terms: Customer Due Diligence (CDD), Risk Rating
```

---

## Initialization & Refresh

### Full initialization

```bash
python -m knowledge_graph.init_graph
```

Pipeline:
1. Validate config (required env vars present)
2. Health-check Oracle (`SELECT 1 FROM DUAL`) and Neo4j (`RETURN 1`)
3. Extract Oracle metadata (16 DBA_* views, configurable schemas)
4. Build Neo4j graph (13-step pipeline)
5. Load glossary (`data/kyc_glossary.json`)
6. Validate graph (4 post-build checks)
7. Print summary report

### Refresh-only mode

```bash
python -m knowledge_graph.init_graph --refresh-only
```

Skips constraints/index setup; re-runs all MERGE steps to apply schema drift.

### Validation checks

| Check                                  | Expected     |
|----------------------------------------|--------------|
| Tables without a Schema node           | 0            |
| Columns without a Table node           | 0            |
| FK edges pointing to missing columns   | 0            |
| Total Table nodes                      | ≥ 1          |

---

## Business Glossary Integration

The glossary is a JSON file (`data/kyc_glossary.json`) with entries like:

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

`GlossaryLoader.load()`:
1. Reads all glossary entries.
2. MERGE's `BusinessTerm` nodes.
3. For each mapping, MERGE's a `MAPS_TO` edge to the target node (Column or Table).

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
