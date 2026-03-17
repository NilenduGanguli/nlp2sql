# KnowledgeQL – Graph Schema Reference

Complete property-level reference for every node label, relationship type, Neo4j constraint,
and index in the KnowledgeQL knowledge graph. Use this document as the authoritative source
when writing Cypher queries or extending the schema.

---

## Node Labels

### `:Schema`

**Uniqueness key:** `name`

| Property      | Type     | Nullable | Oracle Source              | Example         |
|---------------|----------|----------|----------------------------|-----------------|
| `name`        | String   | No       | `DBA_USERS.USERNAME`       | `"KYC"`         |
| `created_date`| String   | Yes      | `DBA_USERS.CREATED`        | `"2020-01-15"`  |
| `status`      | String   | Yes      | `DBA_USERS.ACCOUNT_STATUS` | `"OPEN"`        |

**Sample Cypher:**
```cypher
MATCH (s:Schema {name: 'KYC'})
RETURN s.name, s.status
```

---

### `:Table`

**Uniqueness key:** `fqn`

| Property        | Type     | Nullable | Oracle Source                   | Example                  |
|-----------------|----------|----------|---------------------------------|--------------------------|
| `fqn`           | String   | No       | `OWNER.TABLE_NAME` (uppercase)  | `"KYC.CUSTOMERS"`        |
| `name`          | String   | No       | `DBA_TABLES.TABLE_NAME`         | `"CUSTOMERS"`            |
| `schema`        | String   | No       | `DBA_TABLES.OWNER`              | `"KYC"`                  |
| `row_count`     | Integer  | Yes      | `DBA_TABLES.NUM_ROWS`           | `50000`                  |
| `avg_row_len`   | Integer  | Yes      | `DBA_TABLES.AVG_ROW_LEN`        | `248`                    |
| `partitioned`   | Boolean  | No       | `DBA_TABLES.PARTITIONED`        | `false`                  |
| `temporary`     | Boolean  | No       | `DBA_TABLES.TEMPORARY`          | `false`                  |
| `last_analyzed` | String   | Yes      | `DBA_TABLES.LAST_ANALYZED`      | `"2024-11-01"`           |
| `comments`      | String   | Yes      | `DBA_TAB_COMMENTS.COMMENTS`     | `"Core customer entity"` |

**Sample Cypher:**
```cypher
MATCH (t:Table {fqn: 'KYC.CUSTOMERS'})
RETURN t.name, t.row_count, t.comments
```

---

### `:Column`

**Uniqueness key:** `fqn`

| Property         | Type     | Nullable | Oracle Source                          | Example                       |
|------------------|----------|----------|----------------------------------------|-------------------------------|
| `fqn`            | String   | No       | `OWNER.TABLE.COLUMN_NAME`              | `"KYC.CUSTOMERS.CUSTOMER_ID"` |
| `name`           | String   | No       | `DBA_TAB_COLUMNS.COLUMN_NAME`          | `"CUSTOMER_ID"`               |
| `table_fqn`      | String   | No       | Derived: owner + table_name            | `"KYC.CUSTOMERS"`             |
| `data_type`      | String   | No       | `DBA_TAB_COLUMNS.DATA_TYPE`            | `"NUMBER"`                    |
| `data_length`    | Integer  | Yes      | `DBA_TAB_COLUMNS.DATA_LENGTH`          | `null` (for NUMBER)           |
| `precision`      | Integer  | Yes      | `DBA_TAB_COLUMNS.DATA_PRECISION`       | `10`                          |
| `scale`          | Integer  | Yes      | `DBA_TAB_COLUMNS.DATA_SCALE`           | `null`                        |
| `nullable`       | String   | No       | `DBA_TAB_COLUMNS.NULLABLE`             | `"N"`                         |
| `column_id`      | Integer  | No       | `DBA_TAB_COLUMNS.COLUMN_ID`            | `1`                           |
| `default_value`  | String   | Yes      | `DBA_TAB_COLUMNS.DATA_DEFAULT`         | `"SYSDATE"`                   |
| `comments`       | String   | Yes      | `DBA_COL_COMMENTS.COMMENTS`            | `"Unique customer identifier"`|
| `is_pk`          | Boolean  | No       | Derived: appears in PK constraint      | `true`                        |
| `is_fk`          | Boolean  | No       | Derived: appears as FK source          | `false`                       |
| `is_indexed`     | Boolean  | No       | Derived: appears in any index          | `true`                        |
| `num_distinct`   | Integer  | Yes      | `DBA_TAB_COL_STATISTICS.NUM_DISTINCT`  | `50000`                       |
| `sample_values`  | List     | Yes      | `SELECT DISTINCT … FETCH FIRST 10`    | `["1001", "1002"]`            |

**Sample Cypher:**
```cypher
MATCH (t:Table {fqn: 'KYC.CUSTOMERS'})-[:HAS_COLUMN]->(c:Column)
WHERE c.is_pk = true
RETURN c.name, c.data_type
```

---

### `:View`

**Uniqueness key:** `fqn`

| Property     | Type   | Nullable | Oracle Source              | Example                   |
|--------------|--------|----------|----------------------------|---------------------------|
| `fqn`        | String | No       | `OWNER.VIEW_NAME`          | `"KYC.V_HIGH_RISK_CUSTS"` |
| `name`       | String | No       | `DBA_VIEWS.VIEW_NAME`      | `"V_HIGH_RISK_CUSTS"`     |
| `schema`     | String | No       | `DBA_VIEWS.OWNER`          | `"KYC"`                   |
| `definition` | String | Yes      | `DBA_VIEWS.TEXT`           | `"SELECT ... FROM ..."`   |
| `comments`   | String | Yes      | `DBA_TAB_COMMENTS.COMMENTS`| `"High-risk customer view"`|

**Sample Cypher:**
```cypher
MATCH (v:View)-[:DEPENDS_ON]->(t:Table)
WHERE v.schema = 'KYC'
RETURN v.name, collect(t.name) AS base_tables
```

---

### `:Index`

**Uniqueness key:** `fqn`

| Property       | Type   | Nullable | Oracle Source              | Example              |
|----------------|--------|----------|----------------------------|----------------------|
| `fqn`          | String | No       | `OWNER.INDEX_NAME`         | `"KYC.IDX_CUST_RISK"`|
| `name`         | String | No       | `DBA_INDEXES.INDEX_NAME`   | `"IDX_CUST_RISK"`    |
| `table_fqn`    | String | No       | Derived: owner + table     | `"KYC.CUSTOMERS"`    |
| `index_type`   | String | No       | `DBA_INDEXES.INDEX_TYPE`   | `"NORMAL"`           |
| `uniqueness`   | String | No       | `DBA_INDEXES.UNIQUENESS`   | `"NONUNIQUE"`        |
| `columns_list` | String | No       | `LISTAGG(column_name, ',')`| `"RISK_RATING"`      |
| `tablespace`   | String | Yes      | `DBA_INDEXES.TABLESPACE_NAME`| `"USERS"`           |

**Sample Cypher:**
```cypher
MATCH (t:Table {fqn: 'KYC.CUSTOMERS'})-[:HAS_INDEX]->(idx:Index)
RETURN idx.name, idx.uniqueness, idx.columns_list
```

---

### `:Constraint`

**Uniqueness key:** `fqn`

| Property    | Type   | Nullable | Oracle Source                      | Example                 |
|-------------|--------|----------|------------------------------------|-------------------------|
| `fqn`       | String | No       | `OWNER.CONSTRAINT_NAME`            | `"KYC.PK_CUSTOMERS"`    |
| `name`      | String | No       | `DBA_CONSTRAINTS.CONSTRAINT_NAME`  | `"PK_CUSTOMERS"`        |
| `type`      | String | No       | `DBA_CONSTRAINTS.CONSTRAINT_TYPE`  | `"P"` / `"U"` / `"C"`  |
| `table_fqn` | String | No       | Derived                            | `"KYC.CUSTOMERS"`       |
| `status`    | String | No       | `DBA_CONSTRAINTS.STATUS`           | `"ENABLED"`             |
| `rely`      | String | Yes      | `DBA_CONSTRAINTS.RELY`             | `null`                  |

---

### `:Procedure`

**Uniqueness key:** `fqn`

| Property | Type   | Nullable | Oracle Source                 | Example                        |
|----------|--------|----------|-------------------------------|--------------------------------|
| `fqn`    | String | No       | `OWNER.OBJECT_NAME`           | `"KYC.SP_RISK_ASSESSMENT"`     |
| `name`   | String | No       | `DBA_PROCEDURES.OBJECT_NAME`  | `"SP_RISK_ASSESSMENT"`         |
| `schema` | String | No       | `DBA_PROCEDURES.OWNER`        | `"KYC"`                        |
| `type`   | String | No       | `DBA_PROCEDURES.OBJECT_TYPE`  | `"PROCEDURE"` / `"FUNCTION"`   |
| `status` | String | No       | `DBA_PROCEDURES.STATUS`       | `"VALID"`                      |

---

### `:Synonym`

**Uniqueness key:** `fqn`

| Property     | Type   | Nullable | Oracle Source                | Example                     |
|--------------|--------|----------|------------------------------|-----------------------------|
| `fqn`        | String | No       | `OWNER.SYNONYM_NAME`         | `"PUBLIC.CUSTOMERS"`        |
| `name`       | String | No       | `DBA_SYNONYMS.SYNONYM_NAME`  | `"CUSTOMERS"`               |
| `schema`     | String | No       | `DBA_SYNONYMS.OWNER`         | `"PUBLIC"`                  |
| `target_fqn` | String | No       | `TABLE_OWNER.TABLE_NAME`     | `"KYC.CUSTOMERS"`           |

---

### `:Sequence`

**Uniqueness key:** `fqn`

| Property    | Type    | Nullable | Oracle Source                    | Example                    |
|-------------|---------|----------|----------------------------------|----------------------------|
| `fqn`       | String  | No       | `SEQUENCE_OWNER.SEQUENCE_NAME`   | `"KYC.SEQ_CUSTOMER_ID"`    |
| `name`      | String  | No       | `DBA_SEQUENCES.SEQUENCE_NAME`    | `"SEQ_CUSTOMER_ID"`        |
| `schema`    | String  | No       | `DBA_SEQUENCES.SEQUENCE_OWNER`   | `"KYC"`                    |
| `min_value` | Integer | No       | `DBA_SEQUENCES.MIN_VALUE`        | `1`                        |
| `max_value` | Integer | Yes      | `DBA_SEQUENCES.MAX_VALUE`        | `999999999999`             |
| `increment` | Integer | No       | `DBA_SEQUENCES.INCREMENT_BY`     | `1`                        |
| `cache_size`| Integer | No       | `DBA_SEQUENCES.CACHE_SIZE`       | `20`                       |

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

**Inference:** Determined by `owner` column in every `DBA_*` view.

---

### `[:HAS_COLUMN]`

**Pattern:**
```
(:Table)-[:HAS_COLUMN {ordinal_position}]->(:Column)
```

| Property          | Type    | Notes                              |
|-------------------|---------|------------------------------------|
| `ordinal_position`| Integer | `DBA_TAB_COLUMNS.COLUMN_ID`       |

**Inference:** Direct row from `DBA_TAB_COLUMNS` joined to parent table.

---

### `[:HAS_PRIMARY_KEY]`

**Pattern:**
```
(:Table)-[:HAS_PRIMARY_KEY {constraint_name}]->(:Column)
```

| Property          | Type   | Notes                                        |
|-------------------|--------|----------------------------------------------|
| `constraint_name` | String | `DBA_CONSTRAINTS.CONSTRAINT_NAME`            |

**Inference:**
```sql
SELECT a.owner, a.table_name, a.constraint_name, b.column_name
FROM   dba_constraints a
JOIN   dba_cons_columns b ON ...
WHERE  a.constraint_type = 'P' AND a.status = 'ENABLED'
```

---

### `[:HAS_FOREIGN_KEY]`

**Pattern:**
```
(:Column)-[:HAS_FOREIGN_KEY {constraint_name, on_delete_action, position}]->(:Column)
```

| Property          | Type    | Notes                                      |
|-------------------|---------|--------------------------------------------|
| `constraint_name` | String  | `DBA_CONSTRAINTS.CONSTRAINT_NAME`          |
| `on_delete_action`| String  | CASCADE \| SET NULL \| NO ACTION           |
| `position`        | Integer | Column position in composite FK            |

**Inference:**
```sql
SELECT a.owner, a.table_name, a.constraint_name, a.delete_rule,
       b.column_name, b.position,
       c.owner ref_owner, c.table_name ref_table, d.column_name ref_col
FROM   dba_constraints a
JOIN   dba_cons_columns b ON ...
JOIN   dba_constraints  c ON c.constraint_name = a.r_constraint_name ...
JOIN   dba_cons_columns d ON ... AND d.position = b.position
WHERE  a.constraint_type = 'R' AND a.status = 'ENABLED'
```

---

### `[:HAS_INDEX]`

**Pattern:**
```
(:Table)-[:HAS_INDEX]->(:Index)
```

**Properties:** none

**Inference:** Joins `DBA_INDEXES` `table_name`/`owner` to `(:Table {fqn})`.

---

### `[:INDEXED_BY]`

**Pattern:**
```
(:Column)-[:INDEXED_BY {column_position}]->(:Index)
```

| Property          | Type    | Notes                         |
|-------------------|---------|-------------------------------|
| `column_position` | Integer | Position in composite index   |

**Inference:** From `DBA_IND_COLUMNS`, column FQN → index FQN.

---

### `[:HAS_CONSTRAINT]`

**Pattern:**
```
(:Table)-[:HAS_CONSTRAINT]->(:Constraint)
```

**Properties:** none

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
FROM   dba_dependencies
WHERE  type = 'VIEW'
  AND  owner IN :schemas
  AND  referenced_type IN ('TABLE', 'VIEW')
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
FROM   dba_dependencies
WHERE  type IN ('PROCEDURE', 'FUNCTION')
  AND  referenced_type IN ('PROCEDURE', 'FUNCTION')
  AND  owner IN :schemas
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
1. Query: MATCH (src:Column)-[:HAS_FOREIGN_KEY]->(tgt:Column)
          RETURN src.table_fqn, tgt.table_fqn, src.fqn, tgt.fqn

2. Build nx.MultiDiGraph:
     - Nodes: table FQNs
     - Edges: one per FK pair (+ reverse)

3. For every (src_table, tgt_table) pair not already connected:
     path = nx.shortest_path(G.to_undirected(), src_table, tgt_table)
     if len(path) - 1 <= max_join_path_hops:
         Store JOIN_PATH with weight = len(path) - 1

4. MERGE both (src→tgt) and (tgt→src) directions.
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

---

## Neo4j Constraints

Defined in `graph_builder.GraphBuilder._setup_schema()`.
All use `IF NOT EXISTS` so they are safe to re-run.

| # | Constraint Name                  | Label         | Property        |
|---|----------------------------------|---------------|-----------------|
| 1 | `schema_name_unique`             | Schema        | `name`          |
| 2 | `table_fqn_unique`               | Table         | `fqn`           |
| 3 | `column_fqn_unique`              | Column        | `fqn`           |
| 4 | `view_fqn_unique`                | View          | `fqn`           |
| 5 | `index_fqn_unique`               | Index         | `fqn`           |
| 6 | `constraint_fqn_unique`          | Constraint    | `fqn`           |
| 7 | `procedure_fqn_unique`           | Procedure     | `fqn`           |
| 8 | `synonym_fqn_unique`             | Synonym       | `fqn`           |
| 9 | `sequence_fqn_unique`            | Sequence      | `fqn`           |
|10 | `business_term_unique`           | BusinessTerm  | `term`          |
|11 | `query_pattern_id_unique`        | QueryPattern  | `pattern_id`    |

---

## Neo4j Indexes

| # | Index Name                  | Label         | Property / Type                   |
|---|-----------------------------|---------------|-----------------------------------|
| 1 | `idx_table_name`            | Table         | `name` (BTREE)                    |
| 2 | `idx_column_name`           | Column        | `name` (BTREE)                    |
| 3 | `idx_table_schema`          | Table         | `schema` (BTREE)                  |
| 4 | `ft_table_comments`         | Table         | `name, comments` (FULLTEXT)       |
| 5 | `ft_column_comments`        | Column        | `name, comments` (FULLTEXT)       |
| 6 | `ft_business_term`          | BusinessTerm  | `term, aliases, definition` (FULLTEXT) |

> **Note:** Full-text indexes (items 4–6) require Neo4j 5.x. They are created in a
> `try/except` block so the build does not fail on older or community editions.
> `search_schema()` falls back to `CONTAINS` queries automatically.

---

## Sample Cypher Recipes

### All tables in a schema

```cypher
MATCH (s:Schema {name: 'KYC'})<-[:BELONGS_TO]-(t:Table)
RETURN t.fqn, t.name, t.row_count
ORDER BY t.name
```

### Full column list with FK annotations

```cypher
MATCH (t:Table {fqn: 'KYC.CUSTOMERS'})-[:HAS_COLUMN]->(c:Column)
OPTIONAL MATCH (c)-[:HAS_FOREIGN_KEY]->(ref:Column)
RETURN c.name, c.data_type, c.nullable, c.is_pk,
       ref.fqn AS references
ORDER BY c.column_id
```

### Shortest join path between two tables

```cypher
MATCH p = (t1:Table {fqn: 'KYC.TRANSACTIONS'})-[:JOIN_PATH]->(t2:Table {fqn: 'KYC.CUSTOMERS'})
RETURN p.join_columns, p.weight, p.cardinality
ORDER BY p.weight
LIMIT 1
```

### Resolve a business term to schema elements

```cypher
MATCH (bt:BusinessTerm)
WHERE bt.term =~ '(?i).*risk.*'
MATCH (bt)-[m:MAPS_TO]->(target)
RETURN bt.term, labels(target)[0] AS kind, target.fqn, m.confidence
ORDER BY m.confidence DESC
```

### Tables that have a full-text search index

```cypher
CALL db.index.fulltext.queryNodes('ft_table_comments', 'customer')
YIELD node AS t, score
RETURN t.fqn, t.comments, score
ORDER BY score DESC
LIMIT 10
```

### View lineage (all base tables for a view)

```cypher
MATCH (v:View {fqn: 'KYC.V_HIGH_RISK_CUSTS'})-[:DEPENDS_ON*1..]->(t:Table)
RETURN DISTINCT t.fqn, t.name
```

### Columns similar to a given column

```cypher
MATCH (c:Column {fqn: 'KYC.CUSTOMERS.CUSTOMER_ID'})-[sim:SIMILAR_TO]->(c2:Column)
RETURN c2.fqn, sim.similarity_score, sim.match_type
ORDER BY sim.similarity_score DESC
```

### Indexes covering a specific column

```cypher
MATCH (c:Column {fqn: 'KYC.CUSTOMERS.RISK_RATING'})-[:INDEXED_BY]->(idx:Index)
RETURN idx.name, idx.index_type, idx.uniqueness, idx.columns_list
```

### Procedure call graph

```cypher
MATCH path = (p:Procedure {fqn: 'KYC.SP_RISK_ASSESSMENT'})-[:CALLS*1..4]->(called:Procedure)
RETURN [node IN nodes(path) | node.fqn] AS call_chain
```

### Tables missing statistics (never analyzed)

```cypher
MATCH (t:Table)
WHERE t.schema = 'KYC' AND t.last_analyzed IS NULL
RETURN t.fqn, t.row_count
ORDER BY t.name
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

| Variable                  | Required | Default          | Description                              |
|---------------------------|----------|------------------|------------------------------------------|
| `ORACLE_DSN`              | Yes      | —                | Oracle connection string or TNS alias    |
| `ORACLE_USER`             | Yes      | —                | Oracle username                          |
| `ORACLE_PASSWORD`         | Yes      | —                | Oracle password                          |
| `ORACLE_TARGET_SCHEMAS`   | No       | (current user)   | Comma-separated schema names to extract  |
| `NEO4J_URI`               | Yes      | bolt://localhost:7687 | Neo4j Bolt URI                      |
| `NEO4J_USER`              | Yes      | `neo4j`          | Neo4j username                           |
| `NEO4J_PASSWORD`          | Yes      | —                | Neo4j password                           |
| `NEO4J_DATABASE`          | No       | `neo4j`          | Neo4j database name                      |
| `GRAPH_BATCH_SIZE`        | No       | `500`            | UNWIND batch size for Cypher MERGE       |
| `GRAPH_MAX_JOIN_HOPS`     | No       | `4`              | Max FK hops for JOIN_PATH BFS            |
| `GRAPH_SIMILARITY_MIN`    | No       | `0.75`           | Min score for SIMILAR_TO edge creation   |
| `GRAPH_LEVENSHTEIN_MAX`   | No       | `2`              | Max edit distance for SIMILAR_TO         |
| `GLOSSARY_PATH`           | No       | `data/kyc_glossary.json` | Path to business glossary JSON  |
| `ORACLE_USE_DBA_VIEWS`    | No       | `true`           | `true` = DBA_* views, `false` = ALL_*   |
| `ORACLE_SAMPLE_ROWS`      | No       | `10`             | Rows per table for sample_values         |
