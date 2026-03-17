# KnowledgeQL Graph — Full Reference

---

## 1. How the Graph Database Is Populated (Step by Step)

The entry point is `knowledge_graph/init_graph.py` — specifically the `initialize_graph()` function. It orchestrates 5 major phases:

---

### Phase 1 — Health Checks

Both databases are verified before any work begins:
- **Oracle**: `OracleMetadataExtractor.check_connectivity()` runs `SELECT 1 FROM DUAL`
- **Neo4j**: `GraphBuilder.check_connectivity()` calls `driver.verify_connectivity()`

If either fails, the pipeline aborts immediately (fail-fast).

---

### Phase 2 — Oracle Metadata Extraction (`oracle_extractor.py`)

`OracleMetadataExtractor.extract()` connects to Oracle and calls `_extract_all()`, which sequentially queries Oracle's data dictionary views (`DBA_*` or `ALL_*`) and populates an `OracleMetadata` container:

| What | Data Dictionary Source |
|---|---|
| Schemas | `DBA_TABLES` (distinct owners) |
| Tables | `DBA_TABLES` / `ALL_TABLES` |
| Columns | `DBA_TAB_COLUMNS` + comments, statistics |
| Primary Keys | `DBA_CONSTRAINTS` + `DBA_CONS_COLUMNS` |
| Foreign Keys | Same, filtered for FK type |
| Views | `DBA_VIEWS` + `DBA_MVIEWS` |
| Indexes | `DBA_INDEXES` + `DBA_IND_COLUMNS` |
| Constraints | `DBA_CONSTRAINTS` |
| Procedures | `DBA_PROCEDURES` |
| Synonyms | `DBA_SYNONYMS` |
| Sequences | `DBA_SEQUENCES` |
| View Dependencies | `DBA_DEPENDENCIES` |
| Sample Data | Live `SELECT * ... FETCH FIRST N ROWS` per table |

After extraction, it flags columns as `is_pk`, `is_fk`, `is_indexed` based on the metadata.

---

### Phase 3 — Neo4j Graph Construction (`graph_builder.py`)

`GraphBuilder.build(metadata)` runs **13 sequential steps** inside a single Neo4j session, all using idempotent `MERGE` operations (so re-runs are safe):

| Step | What's Written | Node/Edge Type |
|---|---|---|
| 1 | Uniqueness constraints & full-text indexes | DDL setup |
| 2 | Schema nodes | `Schema` |
| 3 | Table nodes + links to Schema | `Table` → `BELONGS_TO` → `Schema` |
| 4 | Column nodes + links to Tables | `Column` ← `HAS_COLUMN` ← `Table` |
| 5 | Primary Key edges | `Table` → `HAS_PRIMARY_KEY` → `Column` |
| 6 | Foreign Key edges | `Column` → `HAS_FOREIGN_KEY` → `Column` |
| 7 | Index nodes + Column links | `Index`, `HAS_INDEX`, `INDEXED_BY` |
| 8 | Constraint nodes | `Constraint` → `HAS_CONSTRAINT` |
| 9 | View nodes + dependency edges | `View`, `BELONGS_TO`, `DEPENDS_ON` |
| 10 | Procedure nodes | `Procedure` → `BELONGS_TO` |
| 11 | Synonym & Sequence nodes | `Synonym`, `Sequence` → `BELONGS_TO` |
| 12 | **JOIN_PATH edges** (computed via BFS over FK graph using NetworkX) | `Table` → `JOIN_PATH` → `Table` |
| 13 | **SIMILAR_TO edges** (name-based Levenshtein similarity between columns) | `Column` → `SIMILAR_TO` → `Column` |

All writes are done in batches (default 500 rows) for performance.

---

### Phase 4 — Business Glossary Loading (`glossary_loader.py`)

`GlossaryLoader.load()` reads `data/kyc_glossary.json` and:
1. Upserts `BusinessTerm` nodes with term, definition, aliases, domain, sensitivity level
2. Creates `MAPS_TO` edges linking each `BusinessTerm` to its corresponding `Table` or `Column` nodes (matched by `fqn`)

---

### Phase 5 — Validation (`init_graph.py`)

Four Cypher consistency checks run against the live graph:
- Every `Table` must have a `BELONGS_TO` → `Schema`
- Every `Column` must have a `HAS_COLUMN` ← `Table`
- No `HAS_FOREIGN_KEY` edges pointing to non-`Column` nodes
- At least one `Table` exists (sanity check)

Any failure is logged as a warning but doesn't roll back the data.

---

### Summary Flow

```
Oracle DB ──extract──▶ OracleMetadata ──build──▶ Neo4j Graph
                                                      │
kyc_glossary.json ──load──▶ BusinessTerm nodes ───────┘
                                                      │
                                               validate_graph()
```

---

---

## 2. Business Glossary Loading — Explained

### Purpose

The glossary bridges the gap between **what business users say** (e.g., "high risk customer", "PEP status") and **what the database actually contains** (e.g., `KYC.CUSTOMERS.RISK_RATING`). This is critical for NLP-to-SQL translation — when a user asks a natural language question, the system can look up the term and find the relevant tables/columns.

---

### Source: `data/kyc_glossary.json`

The glossary is a JSON array where each entry has this shape:

```json
{
  "term": "PEP Status",
  "definition": "Politically Exposed Person status...",
  "aliases": ["PEP", "politically exposed person", "PEP flag"],
  "domain": "KYC",
  "sensitivity_level": "RESTRICTED",
  "mappings": [
    { "fqn": "KYC.PEP_STATUS",          "label": "Table",  "confidence": 1.0,  "mapping_type": "manual" },
    { "fqn": "KYC.PEP_STATUS.IS_PEP",   "label": "Column", "confidence": 1.0,  "mapping_type": "manual" },
    { "fqn": "KYC.CUSTOMERS.RISK_RATING","label": "Column", "confidence": 0.7,  "mapping_type": "fuzzy"  }
  ]
}
```

Key fields:
- **`aliases`** — alternative names a user might use for the concept
- **`sensitivity_level`** — `PUBLIC | INTERNAL | CONFIDENTIAL | RESTRICTED`
- **`mappings`** — which Oracle tables or columns this term refers to, with a confidence score

---

### How `GlossaryLoader.load()` Works

**Step 1 — Parse the JSON file**

Opens `data/kyc_glossary.json` and reads the full array. If the file doesn't exist, it logs a warning and returns `{"terms": 0, "mappings": 0}` (graceful skip, not an error).

**Step 2 — Build two row lists**

For each glossary entry, it creates:
- A `BusinessTermNode` → goes into `term_rows`
- One `MapsToRel` per mapping entry → goes into `mapping_rows`

```python
# BusinessTermNode captures the term's metadata
term_node = BusinessTermNode(
    term=entry["term"],
    definition=entry.get("definition", ""),
    aliases=entry.get("aliases", []),
    domain=entry.get("domain", "KYC"),
    sensitivity_level=entry.get("sensitivity_level", "INTERNAL"),
)

# MapsToRel captures one link to a table/column
rel = MapsToRel(
    term=entry["term"],
    target_fqn=mapping["fqn"].upper(),   # always uppercased to match Oracle fqns
    confidence=float(mapping.get("confidence", 1.0)),
    mapping_type=mapping.get("mapping_type", "manual"),
)
```

**Step 3 — Write `BusinessTerm` nodes to Neo4j**

Uses a single `UNWIND` + `MERGE` Cypher query (idempotent — safe to re-run):

```cypher
UNWIND $rows AS row
MERGE (bt:BusinessTerm {term: row.term})
SET bt.definition        = row.definition,
    bt.aliases           = row.aliases,
    bt.domain            = row.domain,
    bt.sensitivity_level = row.sensitivity_level,
    bt.last_updated      = timestamp()
```

**Step 4 — Write `MAPS_TO` edges to Neo4j**

```cypher
UNWIND $rows AS row
MATCH (bt:BusinessTerm {term: row.term})
MATCH (target {fqn: row.target_fqn})       -- matches any label: Table OR Column
MERGE (bt)-[m:MAPS_TO {target_fqn: row.target_fqn}]->(target)
SET m.confidence   = row.confidence,
    m.mapping_type = row.mapping_type
```

The `MATCH (target {fqn: row.target_fqn})` deliberately omits label — it finds the already-existing node whether it's a `Table` or `Column`, using the `fqn` uniqueness constraints set up in Step 1 of the build.

---

### The Resulting Graph Structure

```
(BusinessTerm {term: "Risk Rating", aliases: ["risk score", "risk level", ...]})
    │
    ├──[MAPS_TO {confidence: 1.0,  mapping_type: "manual"}]──▶ (Column {fqn: "KYC.CUSTOMERS.RISK_RATING"})
    ├──[MAPS_TO {confidence: 0.95, mapping_type: "manual"}]──▶ (Column {fqn: "KYC.RISK_ASSESSMENTS.RISK_LEVEL"})
    ├──[MAPS_TO {confidence: 0.9,  mapping_type: "manual"}]──▶ (Column {fqn: "KYC.RISK_ASSESSMENTS.RISK_SCORE"})
    └──[MAPS_TO {confidence: 0.85, mapping_type: "fuzzy"}] ──▶ (Table  {fqn: "KYC.RISK_ASSESSMENTS"})
```

---

### `mapping_type` Values

| Value | Meaning |
|---|---|
| `manual` | Human-curated, high confidence |
| `fuzzy` | Name-similarity based, lower confidence |
| `semantic` | Embedding/semantic-similarity based |
| `exact` | Exact name match |

---

### Why This Matters for NLP-to-SQL

When a user asks *"show me all high risk customers"*, the traversal layer can:
1. Look up `BusinessTerm` nodes whose `term` or `aliases` match "high risk"
2. Follow `MAPS_TO` edges (filtered by confidence) to find `KYC.CUSTOMERS.RISK_RATING`
3. Use that column in the generated SQL — without the user ever knowing the column name

---

---

## 3. Guide: How to Create `kyc_glossary.json`

### Overview

The file is a **JSON array** where each element is one business term. The loader reads it to create `BusinessTerm` nodes and `MAPS_TO` edges in the Neo4j knowledge graph. Every term must be resolvable to at least one already-existing `Table` or `Column` node (identified by its `fqn`).

---

### Top-Level Structure

```json
[
  { /* term 1 */ },
  { /* term 2 */ },
  ...
]
```

---

### Anatomy of a Single Term Entry

```json
{
  "term": "Risk Rating",
  "definition": "A classification assigned to a customer indicating the level of risk: LOW, MEDIUM, HIGH, or VERY_HIGH.",
  "aliases": ["risk score", "risk level", "AML risk"],
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

---

### Field Reference

#### Term-level fields

| Field | Type | Required | Description |
|---|---|---|---|
| `term` | string | **Yes** | Canonical business name. Must be **unique** across the file — it's the node's primary key in Neo4j. |
| `definition` | string | Yes | Full human-readable description of what the term means. |
| `aliases` | array of strings | No | Alternative names a user might type (e.g. abbreviations, synonyms). Used for NLP matching. |
| `domain` | string | No | Business domain. Defaults to `"KYC"` if omitted. |
| `sensitivity_level` | string | No | Data classification. Must be one of: `"PUBLIC"`, `"INTERNAL"`, `"CONFIDENTIAL"`, `"RESTRICTED"`. Defaults to `"INTERNAL"`. |
| `mappings` | array of objects | Yes | Links to Oracle tables/columns. At least one mapping is strongly recommended. |

---

#### Mapping-level fields

| Field | Type | Required | Description |
|---|---|---|---|
| `fqn` | string | **Yes** | Fully-qualified name of the target node in the graph. Must match an existing node exactly. |
| `label` | string | Yes | Node type of the target: `"Table"` or `"Column"`. |
| `confidence` | float | Yes | How strongly this term maps to this node. Range: `0.0` – `1.0`. |
| `mapping_type` | string | Yes | How the mapping was established. One of: `"manual"`, `"fuzzy"`, `"semantic"`, `"exact"`. |

---

### FQN Format Rules

The `fqn` (fully-qualified name) must exactly match what was extracted from Oracle. The format depends on whether you're mapping to a table or column:

| Target | Format | Example |
|---|---|---|
| Table | `SCHEMA.TABLE_NAME` | `KYC.CUSTOMERS` |
| Column | `SCHEMA.TABLE_NAME.COLUMN_NAME` | `KYC.CUSTOMERS.RISK_RATING` |

**Important rules:**
- Always use **UPPERCASE** — the loader calls `.upper()` on every `fqn`
- Use the **Oracle owner/schema name**, not an alias
- The node must already exist in Neo4j (written during Phase 3 of graph build) before `MAPS_TO` edges are created; if the `fqn` doesn't match, the edge is silently skipped

---

### Confidence Scoring Guidelines

| Score | When to Use |
|---|---|
| `1.0` | The term *is* this table/column — perfect, unambiguous match |
| `0.9 – 0.95` | Primary column/table, very high certainty |
| `0.8 – 0.89` | Strong but indirect relationship |
| `0.7 – 0.79` | Fuzzy match — related concept, not a direct mapping |
| `< 0.7` | Avoid; low-confidence mappings add noise |

---

### Mapping Type Guidelines

| Type | When to Use |
|---|---|
| `manual` | A human explicitly verified this mapping |
| `exact` | Term name exactly matches the column/table name |
| `fuzzy` | Name similarity (e.g. Levenshtein) suggested the link |
| `semantic` | Embedding similarity suggested the link |

---

### Sensitivity Level Guidelines

| Level | Meaning |
|---|---|
| `PUBLIC` | No restrictions on access |
| `INTERNAL` | Internal staff only |
| `CONFIDENTIAL` | Restricted to need-to-know teams |
| `RESTRICTED` | Highest sensitivity — regulatory/legal data (e.g. PEP, SAR) |

---

### A Term Can Map to Multiple Targets

A single business term often spans multiple tables and columns. Order mappings by descending confidence:

```json
{
  "term": "KYC Review",
  "mappings": [
    { "fqn": "KYC.KYC_REVIEWS",                  "label": "Table",  "confidence": 1.0,  "mapping_type": "manual" },
    { "fqn": "KYC.KYC_REVIEWS.REVIEW_DATE",      "label": "Column", "confidence": 0.95, "mapping_type": "manual" },
    { "fqn": "KYC.KYC_REVIEWS.NEXT_REVIEW_DATE", "label": "Column", "confidence": 0.9,  "mapping_type": "manual" },
    { "fqn": "KYC.KYC_REVIEWS.STATUS",           "label": "Column", "confidence": 0.85, "mapping_type": "manual" }
  ]
}
```

---

### Multiple Terms Can Map to the Same Column

This is intentional and supported. For example, both `"Risk Rating"` and `"High Risk Customer"` map to `KYC.CUSTOMERS.RISK_RATING` — they represent different business concepts that share the same physical column.

---

### Common Mistakes to Avoid

| Mistake | Effect |
|---|---|
| FQN uses lowercase | Edge silently skipped — loader uppercases it, but the existing node fqn must also be uppercase |
| FQN maps to a non-existent table/column | The `MATCH` in Cypher finds nothing; edge is not created, no error thrown |
| Duplicate `term` values | Second `MERGE` overwrites the first node's properties (idempotent, but last-write-wins) |
| Missing `mappings` array | Term node is created in Neo4j but has no connections — unusable for query generation |
| `confidence` outside `0.0–1.0` | Stored as-is; traversal filters may exclude it if threshold is `≤ 1.0` |

---

### Minimal Valid Entry

```json
{
  "term": "My Term",
  "definition": "What this term means.",
  "aliases": [],
  "domain": "KYC",
  "sensitivity_level": "INTERNAL",
  "mappings": [
    { "fqn": "SCHEMA.TABLE_NAME", "label": "Table", "confidence": 1.0, "mapping_type": "manual" }
  ]
}
```

