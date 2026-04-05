# KnowledgeQL – Graph Schema Reference

Complete property-level reference for every node label and relationship type in the KnowledgeQL in-memory knowledge graph. Use this document as the authoritative source when working with the `KnowledgeGraph` Python API or extending the schema.

There is no external graph database. The graph lives entirely in Python dicts inside the `KnowledgeGraph` class (`knowledge_graph/graph_store.py`). All queries use the Python API: `graph.get_node()`, `graph.get_all_nodes()`, `graph.get_out_edges()`, `graph.get_in_edges()`.

---

## Node Labels

### `:Schema`

**Uniqueness key:** `name`

| Property      | Type     | Nullable | Oracle Source              | Example         |
|---------------|----------|----------|----------------------------|-----------------|
| `name`        | String   | No       | `ALL_USERS.USERNAME`       | `"KYC"`         |
| `created_date`| String   | Yes      | `ALL_USERS.CREATED`        | `"2020-01-15"`  |
| `status`      | String   | Yes      | `ALL_USERS.ACCOUNT_STATUS` | `"OPEN"`        |

**Python API:**
```python
schema_node = graph.get_node("Schema", "KYC")
print(schema_node["name"], schema_node.get("status"))
```

---

### `:Table`

**Uniqueness key:** `fqn`

| Property          | Type     | Nullable | Oracle Source                   | Example                  |
|-------------------|----------|----------|---------------------------------|--------------------------|
| `fqn`             | String   | No       | `OWNER.TABLE_NAME` (uppercase)  | `"KYC.CUSTOMERS"`        |
| `name`            | String   | No       | `ALL_TABLES.TABLE_NAME`         | `"CUSTOMERS"`            |
| `schema`          | String   | No       | `ALL_TABLES.OWNER`              | `"KYC"`                  |
| `row_count`       | Integer  | Yes      | `ALL_TABLES.NUM_ROWS`           | `50000`                  |
| `avg_row_len`     | Integer  | Yes      | `ALL_TABLES.AVG_ROW_LEN`        | `248`                    |
| `partitioned`     | Boolean  | No       | `ALL_TABLES.PARTITIONED`        | `false`                  |
| `temporary`       | Boolean  | No       | `ALL_TABLES.TEMPORARY`          | `false`                  |
| `last_analyzed`   | String   | Yes      | `ALL_TABLES.LAST_ANALYZED`      | `"2024-11-01"`           |
| `comments`        | String   | Yes      | `ALL_TAB_COMMENTS.COMMENTS`     | `"Core customer entity"` |
| `importance_rank` | Integer  | Yes      | LLM enhancer                    | `1`                      |
| `importance_tier` | String   | Yes      | LLM enhancer                    | `"core"`                 |
| `llm_description` | String   | Yes      | LLM enhancer                    | `"Central customer entity storing KYC profiles"` |

`importance_rank` (1 = most important), `importance_tier` (one of `core` / `reference` / `audit` / `utility`), and `llm_description` are written by `knowledge_graph/llm_enhancer.py` after the graph is built. Tables without LLM enhancement have these properties as `None`.

**Python API:**
```python
table_node = graph.get_node("Table", "KYC.CUSTOMERS")
print(table_node["name"], table_node.get("row_count"), table_node.get("comments"))
print(table_node.get("importance_tier"), table_node.get("importance_rank"))
```

---

### `:Column`

**Uniqueness key:** `fqn`

| Property         | Type     | Nullable | Oracle Source                          | Example                       |
|------------------|----------|----------|----------------------------------------|-------------------------------|
| `fqn`            | String   | No       | `OWNER.TABLE.COLUMN_NAME`              | `"KYC.CUSTOMERS.CUSTOMER_ID"` |
| `name`           | String   | No       | `ALL_TAB_COLUMNS.COLUMN_NAME`          | `"CUSTOMER_ID"`               |
| `table_fqn`      | String   | No       | Derived: owner + table_name            | `"KYC.CUSTOMERS"`             |
| `data_type`      | String   | No       | `ALL_TAB_COLUMNS.DATA_TYPE`            | `"NUMBER"`                    |
| `data_length`    | Integer  | Yes      | `ALL_TAB_COLUMNS.DATA_LENGTH`          | `null` (for NUMBER)           |
| `precision`      | Integer  | Yes      | `ALL_TAB_COLUMNS.DATA_PRECISION`       | `10`                          |
| `scale`          | Integer  | Yes      | `ALL_TAB_COLUMNS.DATA_SCALE`           | `null`                        |
| `nullable`       | String   | No       | `ALL_TAB_COLUMNS.NULLABLE`             | `"N"`                         |
| `column_id`      | Integer  | No       | `ALL_TAB_COLUMNS.COLUMN_ID`            | `1`                           |
| `default_value`  | String   | Yes      | `ALL_TAB_COLUMNS.DATA_DEFAULT`         | `"SYSDATE"`                   |
| `comments`       | String   | Yes      | `ALL_COL_COMMENTS.COMMENTS`            | `"Unique customer identifier"`|
| `is_pk`          | Boolean  | No       | Derived: appears in PK constraint      | `true`                        |
| `is_fk`          | Boolean  | No       | Derived: appears as FK source          | `false`                       |
| `is_indexed`     | Boolean  | No       | Derived: appears in any index          | `true`                        |
| `num_distinct`   | Integer  | Yes      | `ALL_TAB_COL_STATISTICS.NUM_DISTINCT`  | `50000`                       |
| `sample_values`  | List     | Yes      | `SELECT DISTINCT … FETCH FIRST 10`    | `["1001", "1002"]`            |

**Python API:**
```python
col_edges = graph.get_out_edges("HAS_COLUMN", "KYC.CUSTOMERS")
pk_cols = [
    graph.get_node("Column", e["_to"])
    for e in col_edges
    if graph.get_node("Column", e["_to"]).get("is_pk")
]
for col in pk_cols:
    print(col["name"], col["data_type"])
```

---

### `:View`

**Uniqueness key:** `fqn`

| Property     | Type   | Nullable | Oracle Source                  | Example                   |
|--------------|--------|----------|--------------------------------|---------------------------|
| `fqn`        | String | No       | `OWNER.VIEW_NAME`              | `"KYC.V_HIGH_RISK_CUSTS"` |
| `name`       | String | No       | `ALL_VIEWS.VIEW_NAME`          | `"V_HIGH_RISK_CUSTS"`     |
| `schema`     | String | No       | `ALL_VIEWS.OWNER`              | `"KYC"`                   |
| `definition` | String | Yes      | `ALL_VIEWS.TEXT` (SUBSTR 4000) | `"SELECT ... FROM ..."`   |
| `comments`   | String | Yes      | `ALL_TAB_COMMENTS.COMMENTS`    | `"High-risk customer view"`|

**Python API:**
```python
views = graph.get_all_nodes("View")
kyc_views = [v for v in views if v.get("schema") == "KYC"]
for v in kyc_views:
    deps = graph.get_out_edges("DEPENDS_ON", v["fqn"])
    base_tables = [e["_to"] for e in deps]
    print(v["name"], base_tables)
```

---

### `:Index`

**Uniqueness key:** `fqn`

| Property       | Type   | Nullable | Oracle Source                  | Example              |
|----------------|--------|----------|--------------------------------|----------------------|
| `fqn`          | String | No       | `OWNER.INDEX_NAME`             | `"KYC.IDX_CUST_RISK"`|
| `name`         | String | No       | `ALL_INDEXES.INDEX_NAME`       | `"IDX_CUST_RISK"`    |
| `table_fqn`    | String | No       | Derived: owner + table         | `"KYC.CUSTOMERS"`    |
| `index_type`   | String | No       | `ALL_INDEXES.INDEX_TYPE`       | `"NORMAL"`           |
| `uniqueness`   | String | No       | `ALL_INDEXES.UNIQUENESS`       | `"NONUNIQUE"`        |
| `columns_list` | String | No       | `LISTAGG(column_name, ',')`    | `"RISK_RATING"`      |
| `tablespace`   | String | Yes      | `ALL_INDEXES.TABLESPACE_NAME`  | `"USERS"`            |

**Python API:**
```python
idx_edges = graph.get_out_edges("HAS_INDEX", "KYC.CUSTOMERS")
for e in idx_edges:
    idx = graph.get_node("Index", e["_to"])
    print(idx["name"], idx["uniqueness"], idx["columns_list"])
```

---

### `:Constraint`

**Uniqueness key:** `fqn`

| Property    | Type   | Nullable | Oracle Source                      | Example                 |
|-------------|--------|----------|------------------------------------|-------------------------|
| `fqn`       | String | No       | `OWNER.CONSTRAINT_NAME`            | `"KYC.PK_CUSTOMERS"`    |
| `name`      | String | No       | `ALL_CONSTRAINTS.CONSTRAINT_NAME`  | `"PK_CUSTOMERS"`        |
| `type`      | String | No       | `ALL_CONSTRAINTS.CONSTRAINT_TYPE`  | `"P"` / `"U"` / `"C"`  |
| `table_fqn` | String | No       | Derived                            | `"KYC.CUSTOMERS"`       |
| `status`    | String | No       | `ALL_CONSTRAINTS.STATUS`           | `"ENABLED"`             |
| `rely`      | String | Yes      | `ALL_CONSTRAINTS.RELY`             | `null`                  |

---

### `:Procedure`

**Uniqueness key:** `fqn`

| Property | Type   | Nullable | Oracle Source                    | Example                        |
|----------|--------|----------|----------------------------------|--------------------------------|
| `fqn`    | String | No       | `OWNER.OBJECT_NAME`              | `"KYC.SP_RISK_ASSESSMENT"`     |
| `name`   | String | No       | `ALL_PROCEDURES.OBJECT_NAME`     | `"SP_RISK_ASSESSMENT"`         |
| `schema` | String | No       | `ALL_PROCEDURES.OWNER`           | `"KYC"`                        |
| `type`   | String | No       | `ALL_PROCEDURES.OBJECT_TYPE`     | `"PROCEDURE"` / `"FUNCTION"`   |
| `status` | String | No       | `ALL_OBJECTS.STATUS` (via JOIN)  | `"VALID"`                      |

> **Note:** `ALL_PROCEDURES` in Oracle 23c has no `STATUS` column. Status is always retrieved via `LEFT JOIN ALL_OBJECTS` on `owner + object_name + object_type`.

---

### `:Synonym`

**Uniqueness key:** `fqn`

| Property     | Type   | Nullable | Oracle Source                 | Example                     |
|--------------|--------|----------|-------------------------------|-----------------------------|
| `fqn`        | String | No       | `OWNER.SYNONYM_NAME`          | `"PUBLIC.CUSTOMERS"`        |
| `name`       | String | No       | `ALL_SYNONYMS.SYNONYM_NAME`   | `"CUSTOMERS"`               |
| `schema`     | String | No       | `ALL_SYNONYMS.OWNER`          | `"PUBLIC"`                  |
| `target_fqn` | String | No       | `TABLE_OWNER.TABLE_NAME`      | `"KYC.CUSTOMERS"`           |

---

### `:Sequence`

**Uniqueness key:** `fqn`

| Property    | Type    | Nullable | Oracle Source                      | Example                    |
|-------------|---------|----------|------------------------------------|----------------------------|
| `fqn`       | String  | No       | `SEQUENCE_OWNER.SEQUENCE_NAME`     | `"KYC.SEQ_CUSTOMER_ID"`    |
| `name`      | String  | No       | `ALL_SEQUENCES.SEQUENCE_NAME`      | `"SEQ_CUSTOMER_ID"`        |
| `schema`    | String  | No       | `ALL_SEQUENCES.SEQUENCE_OWNER`     | `"KYC"`                    |
| `min_value` | Integer | No       | `ALL_SEQUENCES.MIN_VALUE`          | `1`                        |
| `max_value` | Integer | Yes      | `ALL_SEQUENCES.MAX_VALUE`          | `999999999999`             |
| `increment` | Integer | No       | `ALL_SEQUENCES.INCREMENT_BY`       | `1`                        |
| `cache_size`| Integer | No       | `ALL_SEQUENCES.CACHE_SIZE`         | `20`                       |

---

### `:BusinessTerm`

**Uniqueness key:** `term`

| Property           | Type   | Nullable | Source            | Example                            |
|--------------------|--------|----------|-------------------|------------------------------------|
| `term`             | String | No       | Glossary `term`   | `"Risk Rating"`                    |
| `definition`       | String | No       | Glossary          | `"Risk classification for customer"`|
| `aliases`          | List   | Yes      | Glossary `aliases`| `["risk_level", "risk_score"]`     |
| `domain`           | String | Yes      | Glossary `domain` | `"KYC"`                            |
| `sensitivity_level`| String | Yes      | Glossary          | `"CONFIDENTIAL"`                   |

---

### `:QueryPattern`

**Uniqueness key:** `pattern_id`

| Property      | Type   | Nullable | Notes                              |
|---------------|--------|----------|------------------------------------|
| `pattern_id`  | String | No       | Slug or UUID                       |
| `nl_pattern`  | String | No       | Natural-language template string   |
| `sql_template`| String | No       | Parameterized Oracle SQL           |
| `tables`      | List   | No       | Referenced table FQNs              |
| `description` | String | Yes      | Human-readable explanation         |

---

## Relationship Types

### `[:BELONGS_TO]`

**Pattern:**
```
(:Table | :View | :Procedure | :Synonym | :Sequence)-[:BELONGS_TO]->(:Schema)
```

**Properties:** none

**Inference:** Determined by the `owner` column in every `ALL_*` view.

**Python API:**
```python
# All objects belonging to schema KYC
schema_members = graph.get_in_edges("BELONGS_TO", "KYC")
for e in schema_members:
    print(e["_from"])
```

---

### `[:HAS_COLUMN]`

**Pattern:**
```
(:Table)-[:HAS_COLUMN {ordinal_position}]->(:Column)
```

| Property          | Type    | Notes                              |
|-------------------|---------|------------------------------------|
| `ordinal_position`| Integer | `ALL_TAB_COLUMNS.COLUMN_ID`       |

**Inference:** Direct row from `ALL_TAB_COLUMNS` joined to parent table.

**Python API:**
```python
col_edges = graph.get_out_edges("HAS_COLUMN", "KYC.CUSTOMERS")
cols = sorted(col_edges, key=lambda e: e.get("ordinal_position", 0))
for e in cols:
    col = graph.get_node("Column", e["_to"])
    print(col["name"], col["data_type"], col["nullable"])
```

---

### `[:HAS_PRIMARY_KEY]`

**Pattern:**
```
(:Table)-[:HAS_PRIMARY_KEY {constraint_name}]->(:Column)
```

| Property          | Type   | Notes                                        |
|-------------------|--------|----------------------------------------------|
| `constraint_name` | String | `ALL_CONSTRAINTS.CONSTRAINT_NAME`            |

**Inference:**
```sql
SELECT a.owner, a.table_name, a.constraint_name, b.column_name
FROM   all_constraints a
JOIN   all_cons_columns b ON ...
WHERE  a.constraint_type = 'P' AND a.status = 'ENABLED'
```

**Python API:**
```python
pk_edges = graph.get_out_edges("HAS_PRIMARY_KEY", "KYC.CUSTOMERS")
for e in pk_edges:
    print(e["_to"], e.get("constraint_name"))
```

---

### `[:HAS_FOREIGN_KEY]`

**Pattern:**
```
(:Column)-[:HAS_FOREIGN_KEY {constraint_name, on_delete_action, position}]->(:Column)
```

| Property          | Type    | Notes                                      |
|-------------------|---------|--------------------------------------------|
| `constraint_name` | String  | `ALL_CONSTRAINTS.CONSTRAINT_NAME`          |
| `on_delete_action`| String  | CASCADE \| SET NULL \| NO ACTION           |
| `position`        | Integer | Column position in composite FK            |

**Inference:**
```sql
SELECT a.owner, a.table_name, a.constraint_name, a.delete_rule,
       b.column_name, b.position,
       c.owner ref_owner, c.table_name ref_table, d.column_name ref_col
FROM   all_constraints a
JOIN   all_cons_columns b ON ...
JOIN   all_constraints  c ON c.constraint_name = a.r_constraint_name ...
JOIN   all_cons_columns d ON ... AND d.position = b.position
WHERE  a.constraint_type = 'R'
```

> **Note:** The `AND a.status = 'ENABLED'` filter is intentionally omitted so that DISABLED FK constraints are included and contribute to JOIN_PATH computation.

**Python API:**
```python
# All FKs from a column
fk_edges = graph.get_out_edges("HAS_FOREIGN_KEY", "KYC.ACCOUNTS.CUSTOMER_ID")
for e in fk_edges:
    print(f"{e['_from']} -> {e['_to']} ({e.get('constraint_name')})")

# All FKs pointing into a column
incoming = graph.get_in_edges("HAS_FOREIGN_KEY", "KYC.CUSTOMERS.CUSTOMER_ID")
for e in incoming:
    print(f"Referenced by: {e['_from']}")
```

---

### `[:HAS_INDEX]`

**Pattern:**
```
(:Table)-[:HAS_INDEX]->(:Index)
```

**Properties:** none

**Inference:** Joins `ALL_INDEXES` `table_name`/`owner` to the parent `Table` node.

**Python API:**
```python
idx_edges = graph.get_out_edges("HAS_INDEX", "KYC.CUSTOMERS")
for e in idx_edges:
    idx = graph.get_node("Index", e["_to"])
    print(idx["name"], idx["uniqueness"], idx["columns_list"])
```

---

### `[:INDEXED_BY]`

**Pattern:**
```
(:Column)-[:INDEXED_BY {column_position}]->(:Index)
```

| Property          | Type    | Notes                         |
|-------------------|---------|-------------------------------|
| `column_position` | Integer | Position in composite index   |

**Inference:** From `ALL_IND_COLUMNS`, column FQN → index FQN.

**Python API:**
```python
idx_edges = graph.get_out_edges("INDEXED_BY", "KYC.CUSTOMERS.RISK_RATING")
for e in idx_edges:
    idx = graph.get_node("Index", e["_to"])
    print(idx["name"], idx["index_type"], idx["uniqueness"])
```

---

### `[:HAS_CONSTRAINT]`

**Pattern:**
```
(:Table)-[:HAS_CONSTRAINT]->(:Constraint)
```

**Properties:** none

**Python API:**
```python
con_edges = graph.get_out_edges("HAS_CONSTRAINT", "KYC.CUSTOMERS")
for e in con_edges:
    con = graph.get_node("Constraint", e["_to"])
    print(con["name"], con["type"], con.get("status"))
```

---

### `[:DEPENDS_ON]`

**Pattern:**
```
(:View)-[:DEPENDS_ON {dependency_type}]->(:Table | :View)
```

| Property          | Type   | Notes             |
|-------------------|--------|-------------------|
| `dependency_type` | String | `"SELECT"` always |

**Inference:**
```sql
SELECT name, owner, referenced_owner, referenced_name, referenced_type
FROM   all_dependencies
WHERE  type = 'VIEW'
  AND  owner IN :schemas
  AND  referenced_type IN ('TABLE', 'VIEW')
```

**Python API:**
```python
deps = graph.get_out_edges("DEPENDS_ON", "KYC.V_HIGH_RISK_CUSTS")
for e in deps:
    print(f"  depends on: {e['_to']}")
```

---

### `[:CALLS]`

**Pattern:**
```
(:Procedure)-[:CALLS]->(:Procedure)
```

**Properties:** none

**Inference:**
```sql
SELECT name, owner, referenced_owner, referenced_name
FROM   all_dependencies
WHERE  type IN ('PROCEDURE', 'FUNCTION')
  AND  referenced_type IN ('PROCEDURE', 'FUNCTION')
  AND  owner IN :schemas
```

**Python API:**
```python
calls = graph.get_out_edges("CALLS", "KYC.SP_RISK_ASSESSMENT")
for e in calls:
    print(f"  calls: {e['_to']}")
```

---

### `[:MAPS_TO]`

**Pattern:**
```
(:BusinessTerm)-[:MAPS_TO {confidence, mapping_type}]->(:Table | :Column | :View)
```

| Property       | Type   | Notes                                           |
|----------------|--------|-------------------------------------------------|
| `confidence`   | Float  | 0.0–1.0 (1.0 = manually confirmed mapping)     |
| `mapping_type` | String | `manual` \| `semantic` \| `pattern` \| `inferred` |

**Inference:** Loaded from JSON glossary file (`data/kyc_glossary.json`).
Each mapping entry specifies the `fqn` of the target node in the graph.

**Python API:**
```python
mappings = graph.get_out_edges("MAPS_TO", "Risk Rating")
for m in sorted(mappings, key=lambda x: x.get("confidence", 0), reverse=True):
    print(m["_to"], m.get("confidence"), m.get("mapping_type"))
```

---

### `[:JOIN_PATH]`

**Pattern:**
```
(:Table)-[:JOIN_PATH {path_key, join_columns, join_type, cardinality, weight}]->(:Table)
```

| Property      | Type         | Notes                                                    |
|---------------|--------------|----------------------------------------------------------|
| `path_key`    | String       | `"SRC_FQN::TGT_FQN"` — unique per direction             |
| `join_columns`| List of Maps | `[{src: "KYC.A.COL", tgt: "KYC.B.COL"}, ...]`          |
| `join_type`   | String       | `"INNER"` (always; outer-join choice is LLM's decision)  |
| `cardinality` | String       | `1:1` \| `1:N` \| `N:1` \| `N:N`                       |
| `weight`      | Integer      | Number of FK hops in the path (≥ 1)                     |

**Inference algorithm (BFS via NetworkX):**

```
1. Build nx.MultiDiGraph from HAS_FOREIGN_KEY edges
   (graph.get_all_edges("HAS_FOREIGN_KEY")):
     - Nodes: table FQNs
     - Edges: one per FK pair (+ reverse)

2. For every (src_table, tgt_table) pair not already connected:
     path = nx.shortest_path(G.to_undirected(), src_table, tgt_table)
     if len(path) - 1 <= max_join_path_hops:
         Store JOIN_PATH with weight = len(path) - 1

3. MERGE both (src->tgt) and (tgt->src) directions.
```

**Python API:**
```python
jp_edges = graph.get_out_edges("JOIN_PATH", "KYC.TRANSACTIONS")
path = next((e for e in jp_edges if e["_to"] == "KYC.CUSTOMERS"), None)
if path:
    print(path["join_columns"], path["weight"], path["cardinality"])
```

---

### `[:SIMILAR_TO]`

**Pattern:**
```
(:Column)-[:SIMILAR_TO {similarity_score, match_type}]->(:Column)
```

| Property          | Type   | Notes                                                  |
|-------------------|--------|--------------------------------------------------------|
| `similarity_score`| Float  | 0.0–1.0                                                |
| `match_type`      | String | `exact_name` \| `fk_suffix` \| `levenshtein`          |

**Inference — three strategies (applied in order, first match wins):**

**Strategy 1: Exact column name match**
- Condition: `col1.name.upper() == col2.name.upper()`
- Excluded: `is_pk = true` on either column
- Score: `1.0`
- Match type: `exact_name`

**Strategy 2: FK suffix pattern**
- Condition: `col1.name.removesuffix(suffix).upper() == table2.name.upper()`
  for suffix in `(_ID, _CODE, _KEY, _NO, _NUM, _REF)`
- Score: `0.9`
- Match type: `fk_suffix`

**Strategy 3: Levenshtein distance**
- Condition: `Levenshtein.distance(n1, n2) <= max_distance`
  and `1 - dist / max(len(n1), len(n2)) >= min_score`
- Config: `levenshtein_max_distance = 2`, `similarity_min_score = 0.75`
- Score: `1 - distance / max(len(name1), len(name2))`
- Match type: `levenshtein`
- Filter: min column name length = 4 chars

All SIMILAR_TO edges are bidirectional: if `(c1)-[sim]->(c2)` is created,
a reverse edge `(c2)-[sim]->(c1)` is also created with the same score.

**Python API:**
```python
similar = graph.get_out_edges("SIMILAR_TO", "KYC.CUSTOMERS.CUSTOMER_ID")
for s in sorted(similar, key=lambda x: x.get("similarity_score", 0), reverse=True):
    print(s["_to"], s["similarity_score"], s["match_type"])
```

---

## Python API Examples

### All tables in a schema

```python
tables = [t for t in graph.get_all_nodes("Table") if t.get("schema") == "KYC"]
for t in sorted(tables, key=lambda x: x["name"]):
    print(t["fqn"], t.get("row_count"))
```

### Full column list with FK annotations

```python
col_edges = graph.get_out_edges("HAS_COLUMN", "KYC.CUSTOMERS")
for e in sorted(col_edges, key=lambda x: x.get("ordinal_position", 0)):
    col = graph.get_node("Column", e["_to"])
    fk_edges = graph.get_out_edges("HAS_FOREIGN_KEY", e["_to"])
    ref = fk_edges[0]["_to"] if fk_edges else None
    print(col["name"], col["data_type"], col["nullable"], col.get("is_pk"), ref)
```

### Shortest join path between two tables

```python
jp_edges = graph.get_out_edges("JOIN_PATH", "KYC.TRANSACTIONS")
path = next((e for e in jp_edges if e["_to"] == "KYC.CUSTOMERS"), None)
if path:
    print(path["join_columns"], path["weight"], path["cardinality"])
```

### Resolve a business term to schema elements

```python
mappings = graph.get_out_edges("MAPS_TO", "Risk Rating")
for m in sorted(mappings, key=lambda x: x.get("confidence", 0), reverse=True):
    print(m["_to"], m.get("confidence"), m.get("mapping_type"))
```

### Tables ranked by business importance

```python
ranked = [t for t in graph.get_all_nodes("Table") if t.get("importance_rank") is not None]
for t in sorted(ranked, key=lambda x: x["importance_rank"]):
    print(t["importance_rank"], t["importance_tier"], t["fqn"],
          t.get("llm_description", ""))
```

### View lineage (all base tables for a view)

```python
def get_base_tables(graph, view_fqn, visited=None):
    if visited is None:
        visited = set()
    for e in graph.get_out_edges("DEPENDS_ON", view_fqn):
        if e["_to"] not in visited:
            visited.add(e["_to"])
            get_base_tables(graph, e["_to"], visited)
    return visited

base = get_base_tables(graph, "KYC.V_HIGH_RISK_CUSTS")
print(sorted(base))
```

### Columns similar to a given column

```python
similar = graph.get_out_edges("SIMILAR_TO", "KYC.CUSTOMERS.CUSTOMER_ID")
for s in sorted(similar, key=lambda x: x.get("similarity_score", 0), reverse=True):
    print(s["_to"], s["similarity_score"], s["match_type"])
```

### Indexes covering a specific column

```python
idx_edges = graph.get_out_edges("INDEXED_BY", "KYC.CUSTOMERS.RISK_RATING")
for e in idx_edges:
    idx = graph.get_node("Index", e["_to"])
    print(idx["name"], idx["index_type"], idx["uniqueness"], idx["columns_list"])
```

### Procedure call graph

```python
def get_call_chain(graph, proc_fqn, depth=4, visited=None):
    if visited is None:
        visited = []
    if depth == 0 or proc_fqn in visited:
        return visited
    visited.append(proc_fqn)
    for e in graph.get_out_edges("CALLS", proc_fqn):
        get_call_chain(graph, e["_to"], depth - 1, visited)
    return visited

chain = get_call_chain(graph, "KYC.SP_RISK_ASSESSMENT")
print(chain)
```

### Tables missing statistics (never analyzed)

```python
stale = [t for t in graph.get_all_nodes("Table")
         if t.get("schema") == "KYC" and t.get("last_analyzed") is None]
for t in sorted(stale, key=lambda x: x["name"]):
    print(t["fqn"], t.get("row_count"))
```

---

## KYC Glossary Reference

All 15 terms shipped in `data/kyc_glossary.json`.

| Term                    | Domain | Sensitivity   | Maps To (FQN)                          | Confidence |
|-------------------------|--------|---------------|----------------------------------------|------------|
| Customer Due Diligence  | KYC    | RESTRICTED    | KYC.CUSTOMERS (Table)                  | 1.0        |
| Beneficial Owner        | KYC    | RESTRICTED    | KYC.BENEFICIAL_OWNERS (Table)          | 1.0        |
| PEP Status              | KYC    | RESTRICTED    | KYC.PEP_STATUS (Table)                 | 1.0        |
| Risk Rating             | KYC    | CONFIDENTIAL  | KYC.CUSTOMERS.RISK_RATING (Column)     | 1.0        |
| KYC Review              | KYC    | INTERNAL      | KYC.KYC_REVIEWS (Table)               | 1.0        |
| Account Manager         | KYC    | INTERNAL      | KYC.CUSTOMERS.ACCOUNT_MANAGER_ID (Col) | 0.9        |
| High Risk Customer      | KYC    | CONFIDENTIAL  | KYC.CUSTOMERS.RISK_RATING (Column)     | 0.85       |
| Transaction             | Finance| PUBLIC        | KYC.TRANSACTIONS (Table)              | 1.0        |
| Suspicious Activity     | AML    | RESTRICTED    | KYC.TRANSACTIONS (Table)              | 0.8        |
| Sanctions Screening     | AML    | RESTRICTED    | KYC.PEP_STATUS (Table)                | 0.75       |
| Account Balance         | Finance| CONFIDENTIAL  | KYC.ACCOUNTS.BALANCE (Column)          | 1.0        |
| Onboarding Date         | KYC    | INTERNAL      | KYC.CUSTOMERS.CREATED_DATE (Column)    | 0.9        |
| Nationality             | KYC    | CONFIDENTIAL  | KYC.CUSTOMERS.NATIONALITY (Column)     | 1.0        |
| Enhanced Due Diligence  | KYC    | RESTRICTED    | KYC.KYC_REVIEWS (Table)               | 0.9        |
| Document Expiry         | KYC    | CONFIDENTIAL  | KYC.CUSTOMERS.DOC_EXPIRY_DATE (Column) | 0.9        |

---

## Environment Variables Reference

| Variable                  | Required | Default               | Description                              |
|---------------------------|----------|-----------------------|------------------------------------------|
| `ORACLE_DSN`              | Yes      | —                     | Oracle connection string or TNS alias    |
| `ORACLE_USER`             | Yes      | —                     | Oracle username                          |
| `ORACLE_PASSWORD`         | Yes      | —                     | Oracle password                          |
| `ORACLE_TARGET_SCHEMAS`   | No       | (current user)        | Comma-separated schema names to extract  |
| `ORACLE_USE_DBA_VIEWS`    | No       | `false`               | `true` = DBA_* views (requires DBA privilege); `false` = ALL_* views (default) |
| `ORACLE_SAMPLE_ROWS`      | No       | `10`                  | Rows per table for sample_values         |
| `GRAPH_MAX_JOIN_HOPS`     | No       | `4`                   | Max FK hops for JOIN_PATH BFS            |
| `GRAPH_SIMILARITY_MIN`    | No       | `0.75`                | Min score for SIMILAR_TO edge creation   |
| `GRAPH_LEVENSHTEIN_MAX`   | No       | `2`                   | Max edit distance for SIMILAR_TO         |
| `GRAPH_CACHE_PATH`        | No       | `~/.cache/knowledgeql`| Directory for pickle-based graph cache   |
| `GRAPH_CACHE_TTL_HOURS`   | No       | `0`                   | Cache TTL in hours; `0` = no expiry      |
| `GRAPH_CACHE_VERSION`     | No       | `1`                   | Bump to force full cache rebuild         |
| `GLOSSARY_PATH`           | No       | `data/kyc_glossary.json` | Path to business glossary JSON        |
| `LLM_PROVIDER`            | No       | `openai`              | LLM backend: `openai` \| `anthropic` \| `vertex` |
| `QUERY_ENRICHER_ENABLED`  | No       | `true`                | Enable query enrichment step in pipeline |
| `PROMPTS_PERSIST_PATH`    | No       | (none)                | Directory for persisted prompt versions  |
