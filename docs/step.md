# KnowledgeQL Graph — Full Reference

---

## 1. How the Graph Is Populated (Step by Step)

The entry point is `knowledge_graph/init_graph.py` — specifically the `initialize_graph()`
function. It orchestrates 5 core phases and returns a `(KnowledgeGraph, report)` tuple:

```python
from knowledge_graph.init_graph import initialize_graph

graph, report = initialize_graph()
# graph: KnowledgeGraph — ready for traversal queries
# report: dict with success, extraction counts, build stats, elapsed_seconds
```

---

### Phase 1 — Health Check

Only Oracle is checked before any work begins:

- **Oracle**: `OracleMetadataExtractor.check_connectivity()` runs `SELECT 1 FROM DUAL`

If Oracle is unreachable the pipeline aborts immediately (fail-fast) and returns an empty
`KnowledgeGraph` with `report["success"] = False`.

There is no external graph database to check. The `GraphBuilder.check_connectivity()` method
always returns `True` — it exists only for interface compatibility.

---

### Phase 2 — Oracle Metadata Extraction (`oracle_extractor.py`)

`OracleMetadataExtractor.extract()` connects to Oracle and calls `_extract_all()`, which
sequentially queries Oracle's data dictionary views and populates an `OracleMetadata` container.

`ALL_*` views are used by default (portable for any schema owner without DBA privileges).
`DBA_*` views can be enabled via `use_dba_views=True` in the config for accounts with DBA access.

| What | Data Dictionary Source |
|---|---|
| Schemas | `ALL_TABLES` (distinct owners) |
| Tables | `ALL_TABLES` |
| Columns | `ALL_TAB_COLUMNS` + comments, statistics |
| Primary Keys | `ALL_CONSTRAINTS` + `ALL_CONS_COLUMNS` |
| Foreign Keys | Same, filtered for FK type (includes DISABLED constraints) |
| Views | `ALL_VIEWS` |
| Indexes | `ALL_INDEXES` + `ALL_IND_COLUMNS` |
| Constraints | `ALL_CONSTRAINTS` |
| Procedures | `ALL_PROCEDURES` + `ALL_OBJECTS` (LEFT JOIN for status) |
| Synonyms | `ALL_SYNONYMS` |
| Sequences | `ALL_SEQUENCES` |
| View Dependencies | `ALL_DEPENDENCIES` |
| Sample Data | Live `SELECT * ... FETCH FIRST N ROWS` per table |

After extraction, it flags columns as `is_pk`, `is_fk`, `is_indexed` based on the metadata.

Each `_extract_*` call is wrapped in `_safe_extract()` — any Oracle error logs a warning and
returns an empty default, so the graph build always continues even if one extraction step fails.

---

### Phase 3 — In-Memory Graph Construction (`graph_builder.py`)

`GraphBuilder.build(metadata)` runs **13 sequential steps**, writing directly to the
`KnowledgeGraph` instance via `merge_node` / `merge_edge`. No external database is involved.

| Step | What's Written | In-Memory Operation |
|---|---|---|
| 1 | Schema nodes | `merge_node("Schema", name, props)` |
| 2 | Table nodes + links to Schema | `merge_node("Table", fqn, props)` + `merge_edge("BELONGS_TO", ...)` |
| 3 | Column nodes + links to Tables | `merge_node("Column", fqn, props)` + `merge_edge("HAS_COLUMN", ...)` |
| 4 | Primary Key edges | `merge_edge("HAS_PRIMARY_KEY", table_fqn, col_fqn, ...)` |
| 5 | Foreign Key edges | `merge_edge("HAS_FOREIGN_KEY", src_col_fqn, tgt_col_fqn, ...)` |
| 6 | Index nodes + Column links | `merge_node("Index", ...)` + `merge_edge("HAS_INDEX", ...)` + `merge_edge("INDEXED_BY", ...)` |
| 7 | Constraint nodes | `merge_node("Constraint", ...)` + `merge_edge("HAS_CONSTRAINT", ...)` |
| 8 | View nodes + dependency edges | `merge_node("View", ...)` + `merge_edge("BELONGS_TO", ...)` + `merge_edge("DEPENDS_ON", ...)` |
| 9 | Procedure nodes | `merge_node("Procedure", ...)` + `merge_edge("BELONGS_TO", ...)` |
| 10 | Synonym nodes | `merge_node("Synonym", ...)` |
| 11 | Sequence nodes + links | `merge_node("Sequence", ...)` + `merge_edge("BELONGS_TO", ...)` |
| 12 | **JOIN_PATH edges** (BFS over FK graph using NetworkX) | `merge_edge("JOIN_PATH", t1_fqn, t2_fqn, ...)` (bidirectional) |
| 13 | **SIMILAR_TO edges** (name-based Levenshtein similarity between columns) | `merge_edge("SIMILAR_TO", c1_fqn, c2_fqn, ...)` |

All merge operations are **idempotent** — re-running the pipeline on the same schema updates
properties in place without creating duplicates.

---

### Phase 4 — Business Glossary Inference (`glossary_loader.py`)

`InferredGlossaryBuilder(graph).build(metadata)` derives `BusinessTerm` nodes and `MAPS_TO`
edges directly from Oracle metadata already captured in `OracleMetadata` — no external file
required.

**Sources used:**
1. `ALL_COL_COMMENTS` — column-level business definitions (confidence 0.95)
2. `ALL_TAB_COMMENTS` — table-level business descriptions (confidence 0.80)
3. `ColumnNode.sample_values` — enriches definitions with a valid-value enumeration for
   low-cardinality / categorical columns (confidence 0.65)
4. Column name (humanized UPPER_SNAKE → Title Case) — term label when no comment exists
   (confidence 0.50)

Multiple columns sharing the same humanized term name (e.g., `CUSTOMER_ID` in CUSTOMERS,
ACCOUNTS, KYC_REVIEWS) are deduplicated: the definition is kept from the highest-confidence
source, and a `MAPS_TO` edge is created for every matching column or table.

Sensitivity is inferred from column name patterns: columns matching PII / financial keywords
are automatically tagged `RESTRICTED` or `CONFIDENTIAL`; all others default to `INTERNAL`.

An optional JSON glossary (`glossary_loader_json.py`) can supplement or override inferred
terms by loading a hand-crafted `data/kyc_glossary.json` file. See the Business Glossary
section below for the file format.

---

### Phase 5 — Validation

Four consistency checks run against the in-memory graph directly — no external query:

| Check | Expected |
|---|---|
| `graph.count_nodes("Table") >= 1` | True |
| `graph.count_nodes("Column") >= 1` | True |
| `graph.count_edges("HAS_COLUMN") >= 1` | True |
| Orphan columns (columns with no incoming HAS_COLUMN edge) | 0 |

Any failure is logged as a warning but does not abort — the graph is returned and remains
usable. Validation is skipped in `--refresh-only` mode.

---

### Summary Flow

```
Oracle DB ──extract──▶ OracleMetadata ──build──▶ KnowledgeGraph (in-memory)
                                                         │
                                               glossary inference
                                                         │
                                               validate_graph()
                                                         │
                                          ◀── (graph, report) returned
                                                         │
                                             [optional] LLM enhancement
                                                         │
                                             [optional] pickle cache save
```

---

---

## 2. Business Glossary Loading — Explained

### Purpose

The glossary bridges the gap between **what business users say** (e.g., "high risk customer",
"PEP status") and **what the database actually contains** (e.g.,
`KYC.CUSTOMERS.RISK_RATING`). This is critical for NLP-to-SQL translation — when a user asks
a natural language question, the system can look up the term and find the relevant
tables/columns.

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

### How `GlossaryLoaderJson.load()` Works

**Step 1 — Parse the JSON file**

Opens `data/kyc_glossary.json` and reads the full array. If the file does not exist, it logs
a warning and returns `{"terms": 0, "mappings": 0}` (graceful skip, not an error).

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

**Step 3 — Write `BusinessTerm` nodes into the KnowledgeGraph**

```python
graph.merge_node("BusinessTerm", term_node.term, {
    "term": term_node.term,
    "definition": term_node.definition,
    "aliases": term_node.aliases,
    "domain": term_node.domain,
    "sensitivity_level": term_node.sensitivity_level,
})
```

**Step 4 — Write `MAPS_TO` edges into the KnowledgeGraph**

```python
graph.merge_edge(
    "MAPS_TO",
    from_id=rel.term,          # BusinessTerm node id
    to_id=rel.target_fqn,      # Table or Column node id
    confidence=rel.confidence,
    mapping_type=rel.mapping_type,
)
```

If the target `fqn` does not match any existing node in the graph, the edge is silently
skipped (the `get_node` lookup returns `None`).

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

The file is a **JSON array** where each element is one business term. The loader reads it to
create `BusinessTerm` nodes and `MAPS_TO` edges in the in-memory knowledge graph. Every term
should be resolvable to at least one already-existing `Table` or `Column` node (identified by
its `fqn`).

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
| `term` | string | **Yes** | Canonical business name. Must be **unique** across the file — it is the node's primary key in the graph. |
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

The `fqn` (fully-qualified name) must exactly match what was extracted from Oracle. The format
depends on whether you're mapping to a table or column:

| Target | Format | Example |
|---|---|---|
| Table | `SCHEMA.TABLE_NAME` | `KYC.CUSTOMERS` |
| Column | `SCHEMA.TABLE_NAME.COLUMN_NAME` | `KYC.CUSTOMERS.RISK_RATING` |

**Important rules:**
- Always use **UPPERCASE** — the loader calls `.upper()` on every `fqn`
- Use the **Oracle owner/schema name**, not an alias
- The node must already exist in the graph (written during Phase 3 of graph build) before
  `MAPS_TO` edges are created; if the `fqn` does not match, the edge is silently skipped

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

A single business term often spans multiple tables and columns. Order mappings by descending
confidence:

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

This is intentional and supported. For example, both `"Risk Rating"` and `"High Risk Customer"`
map to `KYC.CUSTOMERS.RISK_RATING` — they represent different business concepts that share the
same physical column.

---

### Common Mistakes to Avoid

| Mistake | Effect |
|---|---|
| FQN uses lowercase | Edge silently skipped — loader uppercases the input, but the existing node fqn must also be uppercase |
| FQN maps to a non-existent table/column | The `get_node` lookup returns `None`; edge is not created, no error thrown |
| Duplicate `term` values | Second `merge_node` call overwrites the first node's properties (last-write-wins) |
| Missing `mappings` array | Term node is created in the graph but has no connections — unusable for query generation |
| `confidence` outside `0.0–1.0` | Stored as-is; traversal filters may exclude it if threshold is `<= 1.0` |

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

---

---

## 4. LLM Graph Enhancement (Phase 6)

After the core graph build completes, `knowledge_graph/llm_enhancer.py` runs an optional
post-processing pass that enriches the graph with LLM-derived metadata.

`enhance_graph_with_llm(graph, llm)` executes three independent steps:

### Step 1 — Table Importance Ranking

All tables are sent to the LLM in batches (≤50/call), asking it to rank them by business
centrality. Tables are pre-sorted by FK degree + row_count for context priming.

Properties written to each `Table` node:
- `importance_rank` (Integer): 1 = most central
- `importance_tier` (String): `core` | `reference` | `audit` | `utility`
- `importance_reason` (String): one-line rationale from the LLM

Tables the LLM omits receive a structural fallback rank based on FK degree.

These properties feed the entity extractor's tiered schema tree prompt: core tables appear
first, giving the LLM far better context about which tables matter most.

### Step 2 — Missing Relationship Inference

Isolated tables (no JOIN_PATH edges after the FK-based BFS) are identified. For each,
FK-candidate columns (suffix `_ID/_CODE/_KEY/_FK/_NUM/_NO/_REF`) are presented to the LLM
for join-pair confirmation.

Confirmed pairs with confidence HIGH or MEDIUM get synthesized `JOIN_PATH` edges with
`source="llm_inferred"`. The context builder and entity extractor use these normally.

### Step 3 — Missing Table Descriptions

Tables with a NULL `ALL_TAB_COMMENTS` entry receive an LLM-generated one-line description
stored as `llm_description`. Oracle's `comments` field is never overwritten.

### When Enhancement Runs

In `app.py`: once after graph + pipeline initialization, only when LLM credentials are
present. Guarded by a `graph_llm_enhanced` session-state flag so it does not re-run across
Streamlit sessions once the graph cache has been saved with `llm_enhanced=True`.

---

## 5. Graph Cache (Phase 7)

`knowledge_graph/graph_cache.py` persists the fully-built (and optionally LLM-enhanced)
`KnowledgeGraph` to disk so container restarts do not require a full Oracle re-extraction.

### Cache file format

Pickle dict containing:
```
{
  "version":       str    — internal serialization format version
  "cache_version": str    — value of GRAPH_CACHE_VERSION env var at save time
  "created_at":    float  — Unix timestamp
  "schema_hash":   str    — SHA1 of DSN + user + schemas
  "graph":         KnowledgeGraph
  "llm_enhanced":  bool
}
```

### Configuration

| Env Var | Default | Purpose |
|---|---|---|
| `GRAPH_CACHE_PATH` | `/data/graph_cache` (Docker) or `~/.cache/knowledgeql` (local) | Directory for cache files |
| `GRAPH_CACHE_VERSION` | `"1"` | Bump to force full rebuild — changes the cache filename |
| `GRAPH_CACHE_TTL_HOURS` | `0` (no expiry) | Auto-rebuild caches older than this many hours |

### Cache key

SHA1 of `ORACLE_DSN|ORACLE_USER|TARGET_SCHEMAS|FORMAT_VERSION|GRAPH_CACHE_VERSION` →
12-char hex → `graph_{hash}.pkl`

Changing any of these values produces a different filename, guaranteeing a cache miss without
having to delete the old file manually.

### Pickle compatibility note

`KnowledgeGraph._out_idx` and `_in_idx` use `defaultdict(_dict_of_lists)` where
`_dict_of_lists` is a **module-level** factory function. This is required for pickle
compatibility — lambdas or inner methods defined inside a class are not picklable by the
standard `pickle` module.

### Docker setup

In `docker/docker-compose.yml`, the `app` service mounts a named volume
`graph_cache_data` at `/data/graph_cache` and sets `GRAPH_CACHE_PATH=/data/graph_cache`.
This ensures the cache survives container image rebuilds.
