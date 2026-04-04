# KnowledgeQL (nlp2sql) Project Memory

## Architecture Overview
NLP-to-SQL system with an in-memory knowledge graph built from Oracle DB metadata.

## Key Files
- `knowledge_graph/graph_store.py` – `KnowledgeGraph` class (in-memory property graph)
- `knowledge_graph/graph_builder.py` – Builds `KnowledgeGraph` from `OracleMetadata`
- `knowledge_graph/traversal.py` – Query functions that accept `KnowledgeGraph` (not sessions)
- `knowledge_graph/glossary_loader.py` – Inferred business terms from Oracle metadata
- `knowledge_graph/glossary_loader_json.py` – Loads glossary from JSON file
- `knowledge_graph/oracle_extractor.py` – Extracts metadata from Oracle DB
- `knowledge_graph/models.py` – Typed dataclasses for nodes/relationships
- `knowledge_graph/config.py` – `OracleConfig` + `GraphConfig` (no Neo4j config)
- `knowledge_graph/init_graph.py` – `initialize_graph()` returns `(KnowledgeGraph, report)`

## Graph Storage
No external graph database. Uses `KnowledgeGraph` (pure Python dicts):
- `graph.merge_node(label, node_id, props)` – upsert node
- `graph.merge_edge(rel_type, from_id, to_id, **props)` – upsert edge
- `graph.get_out_edges(rel_type, from_id)` / `get_in_edges(rel_type, to_id)`
- `graph.get_all_nodes(label)` / `get_all_edges(rel_type)`
NetworkX used only for shortest-path JOIN_PATH computation.

## Test Fixtures
- `kyc_metadata` (session scope) – OracleMetadata with 8 KYC tables/columns/FKs
- `kyc_graph` (session scope) – Pre-built KnowledgeGraph from kyc_metadata
- `graph_config` – GraphConfig without Neo4j, just OracleConfig + tuning params

## Important Decisions
- neo4j library completely removed (no bolt driver, no Cypher)
- `traversal.py` functions accept `KnowledgeGraph` (was: `Session`)
- `InferredGlossaryBuilder` accepts `KnowledgeGraph` (was: `Session`)
- `initialize_graph()` returns `(KnowledgeGraph, report)` tuple (was: just report)
- `requirements.txt`: removed `neo4j`, `testcontainers`

## Tests
182 tests total (152 unit + 30 E2E). Run with:
- Unit only: `python -m pytest -q`
- E2E (needs Oracle): `ORACLE_DSN=localhost:1521/FREEPDB1 ORACLE_USER=kyc ORACLE_PASSWORD=KycPassword1 ORACLE_SCHEMA=KYC python -m pytest tests/test_e2e.py -v`

## Oracle Docker Setup
- Image: `gvenzl/oracle-free:latest` (3.27GB, already pulled)
- Compose file: `docker/docker-compose.yml` — container name: `nlp2sql_oracle`
- DSN: `localhost:1521/FREEPDB1`, user: `kyc`, password: `KycPassword1`
- DDL: `docker/init/01_create_tables.sql`, data: `docker/init/02_load_data.sql`
- NOTE: `latest` tag does NOT run .sql init scripts as APP_USER in PDB — they silently
  fail. Use `09_run_in_pdb.sh` or run manually via: `docker exec nlp2sql_oracle sqlplus -S kyc/KycPassword1@localhost:1521/FREEPDB1 @/docker-entrypoint-initdb.d/01_create_tables.sql`
- E2E test script: `scripts/e2e_test.sh` (has auto-fallback for table setup)

## Model Field Names (important for tests)
- `TableNode`: `.name` (not `.table_name`), `.schema`, `.comments`, `.row_count`
- `IndexNode`: `.name` (index name), `.table_name` (which table), `.uniqueness`

## Traversal Return Structures
- `find_join_path()` returns: `{join_columns, join_type, cardinality, weight, source}` — NO `hops` key; use `len(join_columns)` to measure path length
- `get_context_subgraph()` returns list of `{table: {fqn, name, schema, comments,...}, columns: [...], foreign_keys: [...], ...}` — `table` is a dict not a string
- `list_all_tables()` uses `use_dba_views=False` for APP_USER-level access

## oracle_extractor.py Known Bugs Fixed
- `_extract_procedures`: `ALL_PROCEDURES` in Oracle 23c has no `STATUS` column — use LEFT JOIN with `ALL_OBJECTS` for status
- `_extract_synonyms`: `_bind_schemas() + _bind_schemas()` TypeError (dicts can't be +) — fixed to use single dict (both IN clauses use same named binds `:s0, :s1, ...`)

## oracle_extractor.py Graceful Error Handling
- `_extract_all` wraps every `_extract_*` call via `_safe_extract(label, fn, *args, default=)` — any Oracle error logs a warning and returns the default ([] or {}); graph build always continues
- `_extract_views` hardcodes `ALL_VIEWS` (ignores `use_dba_views`/prefix) — more reliable for standard app accounts; uses `SUBSTR(v.text, 1, 4000)` instead of `DBMS_METADATA.GET_DDL` to avoid per-view privilege errors; per-row errors inside the view loop are also caught individually

## Agent Pipeline
- `agent/pipeline.py` builds LangGraph DAG; falls back to sequential if langgraph missing or no LLM key
- `agent/nodes/query_executor.py` — always uses live Oracle (`_oracle_execute`); **no demo/mock mode**; Oracle failures return error state (no fallback)
- `agent/llm.py` — `get_llm(config)` dispatches on `config.llm_provider`: `openai` (default) | `anthropic` | `vertex`
  - Vertex AI: custom `_VertexGenAIChat(BaseChatModel)` wraps `genai.Client.models.generate_content()` directly
  - `_VertexGenAIChat` uses `client_factory` (not `client`) + TTL: client rebuilt every 14 min via `_get_client()` — prevents stale proxy auth tokens
  - `thinking_budget` read from `config.vertex_thinking_budget` (env `VERTEX_THINKING_BUDGET`, default 8192); set to 0 to disable
  - `max_output_tokens=8192`; model default is `gemini-2.5-pro`
  - `GOOGLE_APPLICATION_CREDENTIALS` optional — omit when org proxy handles auth transparently
  - `VERTEX_PROJECT`, `VERTEX_LOCATION`, `LLM_MODEL=gemini-2.5-pro`, `VERTEX_THINKING_BUDGET=8192` env vars
- `app.py` — Streamlit app (chat + schema explorer + SQL editor tabs)
  - `get_knowledge_graph()` uses real Oracle via `initialize_graph(config.graph)` when `DEMO_MODE=false`; falls back to hardcoded mock schema on error or when `DEMO_MODE=true`
  - Settings panel: LLM provider selectbox supports openai/anthropic/vertex; API key field disabled for vertex
- Run: `streamlit run app.py`

## Agent Node Conventions (critical — schema-agnostic)
- `entity_extractor.make_entity_extractor(llm, graph=graph)` — MUST pass graph; builds dynamic system prompt listing actual tables ranked by LLM `importance_rank` (then JP degree, row_count); shows `importance_tier` tag for core/reference tables; uses `llm_description` if Oracle comments empty
- `entity_extractor._build_schema_summary(graph)` returns 3-tuple `(table_list_text, all_table_names, all_schemas)` — callers unpack all 3 values
- `sql_generator._extract_fqn_from_context(schema_context, hint_name)` — shared helper parsing `-- TABLE: SCHEMA.TABLE_NAME` DDL headers; used by `_build_fallback_sql` and `pipeline._graph_fallback_sql`
- `sql_generator` system prompt: rule 15 instructs LLM to use FQN from DDL context headers; `_build_fallback_sql` uses `_extract_fqn_from_context`
- `context_builder`: after resolving hints → expands to 1-hop FK neighbours via JOIN_PATH (cap 10 tables); step 4b SIMILAR_TO expansion (score≥0.85) if no JOIN_PATH neighbors; fallback (step 5) uses `importance_rank` → JP degree → row_count
- `pipeline.py build_pipeline(graph, config, llm)`:
  - Pipeline: `enrich_query → classify_intent → extract_entities → retrieve_schema → check_clarification → [generate_sql | END] → ...`
  - `check_clarification` node (after `retrieve_schema`): uses LLM to detect ambiguity; if `need_clarification=True` routes to END; skipped entirely when `conversation_history` is non-empty
  - `agent/nodes/clarification_agent.py` — `make_clarification_agent(llm)` factory; emits JSON `{needs_clarification, question, options}`; fallback `_default_clarify` pass-through when no LLM
  - `AgentState` has three new fields: `need_clarification: bool`, `clarification_question: str`, `clarification_options: List[str]`
- `query_enricher.make_query_enricher(llm, knowledge_file)`:
  - Reads `kyc_business_knowledge.txt` (or `KYC_KNOWLEDGE_FILE` env var)
  - System message pre-formatted at factory creation time (not per query)
  - Enriches `user_input` → `enriched_query`; system prompt explicitly states knowledge is NOT exhaustive
  - `entity_extractor` and `sql_generator` both prefer `enriched_query` over `user_input`
  - Enable/disable via `QUERY_ENRICHER_ENABLED` env var (default true); pipeline respects this flag
- `knowledge_graph/knowledge_generator.py` — `generate_knowledge_file(graph, llm, output_path, max_tables=30)`:
  - Called at startup if `kyc_business_knowledge.txt` is empty (via `_maybe_generate_knowledge_file` in app.py)
  - Selects top N tables by `importance_rank` + FK degree; batches 10/LLM call; +1 call for common patterns
  - Output: NOT exhaustive — key tables only (PURPOSE, KEY TERMS, JOINS per table)
  - Atomic write (.tmp → os.replace); after generation clears `_load_knowledge.cache_clear()` + clears pipeline cache
  - Session state `knowledge_file_checked` prevents re-checking on every Streamlit rerun
  - `kyc_business_knowledge.txt` cleared to empty to trigger first-time generation from real Oracle schema

## LLM Graph Enhancer
- **File**: `knowledge_graph/llm_enhancer.py` — `enhance_graph_with_llm(graph, llm)` → report dict
- **Step 1 `_assign_table_importance`**: batches all tables (≤50/call), asks LLM to rank 1..N by business centrality; stores `importance_rank` (int, 1=best), `importance_tier` (core/reference/audit/utility), `importance_reason` on Table nodes; fallback: structural rank for tables LLM misses
- **Step 2 `_infer_missing_relationships`**: finds tables with no JOIN_PATH edges; identifies FK-candidate columns (suffix `_ID/_CODE/_KEY/...`); asks LLM to confirm pairs; adds JOIN_PATH edges (source="llm_inferred") in both directions if confidence HIGH/MEDIUM
- **Step 3 `_fill_missing_descriptions`**: generates one-line descriptions for tables without Oracle comments; stored as `llm_description` (NOT overwriting `comments`)
- **When called**: in `app.py main()` once after graph+pipeline init, only when LLM credentials are present; guarded by `graph_llm_enhanced` session state flag; shows `st.spinner` while running

## app.py UI Notes
- `_render_schema_explorer`: uses `schema=None, limit=200` (not hardcoded `schema="KYC"`); expander label shows `SCHEMA.TABLE_NAME` prefix based on actual graph data
- Graph tab "No JOIN_PATH" diagnostic: shows FK count (`HAS_FOREIGN_KEY` edges) + table count; warns "no FK constraints" vs "not yet computed"
- "Open in Editor" button: sets `st.session_state["editor_sql_input"] = sql` then `st.rerun()` (not just `selected_sql`)
- `st.dataframe`: use `use_container_width=True, hide_index=True` — `width="stretch"` is invalid and silently ignored
- App has 4 tabs: Chat | SQL Editor | Knowledge Graph | Relationships
- `render_relationships_tab()`: FK constraint table + join path explorer (select 2 tables → join columns + ON clause) + column browser

## Graph Cache (Persistence)
- **File**: `knowledge_graph/graph_cache.py` — `save_graph(graph, path, llm_enhanced)`, `load_graph(path, max_age_hours)`, `get_cache_path(config)`, `invalidate_cache(path)`, `cache_info(path)`
- **Format**: `pickle` dict `{version, created_at, graph, llm_enhanced}`. Atomic write via `.tmp` + `os.replace`.
- **Cache key**: SHA1 of `ORACLE_DSN|ORACLE_USER|TARGET_SCHEMAS|FORMAT_VERSION|GRAPH_CACHE_VERSION` → 12-char hex → `graph_{hash}.pkl`
- **Default path**: `/data/graph_cache` (Docker volume) or `~/.cache/knowledgeql` (local dev); override with `GRAPH_CACHE_PATH` env var
- **TTL**: `GRAPH_CACHE_TTL_HOURS=0` (default) = no expiry; set hours to auto-rebuild stale cache
- **IMPORTANT**: `KnowledgeGraph._out_idx/_in_idx` use `defaultdict(_dict_of_lists)` NOT `defaultdict(lambda: ...)` — lambdas inside methods are NOT picklable; module-level `_dict_of_lists()` factory is required
- **app.py flow**: `get_knowledge_graph()` tries disk cache first → Oracle build on miss → saves to disk; after LLM enhancement re-saves with `llm_enhanced=True`; cache loaded with `llm_enhanced=True` skips re-enhancement
- **Return type**: `get_knowledge_graph()` returns `_GraphBundle` (mutable object); all callers access `.graph` and `.llm_enhanced`
- **`_GraphBundle`**: mutable class with `__slots__ = ("graph", "llm_enhanced")`; cached by `@st.cache_resource` by reference — mutating `bundle.llm_enhanced = True` after LLM enhancement is immediately visible to all sessions; prevents multi-session re-enhancement
- **Force Rebuild**: sidebar "Force Rebuild Graph" button calls `invalidate_cache`, `get_knowledge_graph.clear()`, resets session state
- **Docker**: named volume `graph_cache_data` mounted at `/data/graph_cache` in app service; `GRAPH_CACHE_PATH=/data/graph_cache`; `/data/graph_cache` dir created in Dockerfile
- **Version bump**: set `GRAPH_CACHE_VERSION=2` (or any new value) in `.env` to force full rebuild + LLM re-enhancement; changes the cache filename → automatic miss

## oracle_extractor.py FK Notes
- `_extract_foreign_keys`: `AND a.status = 'ENABLED'` removed — includes DISABLED FKs so JOIN_PATHs are computed even when FK enforcement is off
- Uses `ALL_CONSTRAINTS` always (not DBA_CONSTRAINTS) — portable for any schema owner

## oracle_extractor.py Known Bugs Fixed
- `_extract_procedures`: `ALL_PROCEDURES` in Oracle 23c has no `STATUS` column — use LEFT JOIN with `ALL_OBJECTS` for status
- `_extract_synonyms`: `_bind_schemas() + _bind_schemas()` TypeError (dicts can't be +) — fixed to use single dict (both IN clauses use same named binds `:s0, :s1, ...`)
- **LONG buffer handler**: `cursor.var(DB_TYPE_LONG, size)` creates arraysize=1 by default (NOT cursor.arraysize) — must pass `cursor.arraysize` explicitly as 3rd arg or thin mode raises DPY-2016 on every query that returns a LONG column (ALL_TAB_COLUMNS.DATA_DEFAULT, ALL_VIEWS.TEXT, etc.)
- **Thick/thin mode conflict**: `init_oracle_client()` fails with DPY-2019 if thin mode was already used (e.g. by `_oracle_reachable()` in test fixtures). Wrapped in try/except — falls back to thin mode silently
- **No `oracle_lib_dir`**: `init_oracle_client()` called with no `lib_dir` — oracledb discovers Oracle Instant Client via `LD_LIBRARY_PATH`/`PATH` automatically. `ORACLE_LIB_DIR` env var and `oracle_lib_dir` config field have been removed.
- **No demo/mock mode**: `demo_mode` field, `DEMO_MODE` env var, `_mock_execute` function, and all hardcoded KYC schema fallbacks have been removed. App always uses live Oracle.
## Dockerised App Container
- `Dockerfile` (project root) — python:3.11-slim, installs requirements.txt + watchdog, EXPOSE 8501
- `.dockerignore` — excludes tests/, .git, __pycache__, .env, docs/, *.docx
- `docker/app/entrypoint.sh` — startup sequence: wait Oracle → init schema → build graph → start Streamlit
- `docker/app/wait_for_oracle.py` — polls oracledb until reachable (5 min timeout)
- `docker/app/init_schema.py` — checks user_tables count; if <8 runs 01_create_tables.sql + 02_load_data.sql via oracledb (no sqlplus needed in app container)
- `docker/docker-compose.yml` — now has two services: `oracle` + `app`
  - `app` depends_on oracle (service_healthy), env_file: ../.env, overrides ORACLE_DSN=oracle:1521/FREEPDB1
  - Start full stack: `docker compose -f docker/docker-compose.yml up`
  - DB only: `docker compose -f docker/docker-compose.yml up oracle`

## Frontend Chat Notes
- **Scroll fix**: `MessageList` uses `containerRef.scrollTop = scrollHeight` on the scrollable div — NOT `scrollIntoView` (caused page-level scroll jank)
- **Grid height**: `SqlResultCard` grid = `Math.max(200, Math.min(500, 48 + rows*40))` — minimum 200px
- **Clarification flow**: SSE `event: clarification` → `ClarificationCard` bubble; clicking option calls `markClarificationAnswered(id)` + submits answer as new user query with history; clarification agent receives full conversation history in prompt so LLM decides context-aware whether to ask again
- **Clarification agent** (`agent/nodes/clarification_agent.py`): passes up to 8 turns of history to LLM prompt; LLM decides whether query is ambiguous even in multi-turn conversation; does NOT hardcode "skip if history non-empty" — uses LLM judgment
- **New Chat button**: in ChatPanel header bar; calls `saveSession(messages, history)` (chatHistoryStore) then `clearMessages()`
- **Chat History tab**: 6th tab in AppShell (`TabId = ... | 'history'`); `HistoryPage` shows list of sessions from `chatHistoryStore`; "Resume" button calls `restoreSession(messages, history)` on chatStore + switches to Chat tab
- **`chatHistoryStore`** (`store/chatHistoryStore.ts`): Zustand persist store (`knowledgeql-chat-history` in localStorage); `saveSession`, `deleteSession`, `clearAllSessions`; max 50 sessions; revives `timestamp` Date objects on rehydration
- `ChatMessage` type: `question?`, `options?`, `answered?` fields for clarification messages
- `ChatSession` type: `{id, title, createdAt: string (ISO), messages, history}`
- `chatStore.restoreSession(messages, history)` — replaces current chat state wholesale

## Prompts System
- All LLM prompts stored as files in `prompts/` directory (project root)
- Loader: `agent/prompts.py` — `load_prompt(name, default)`, `save_prompt(name, content)`, `list_prompts()`
- Files: `query_enricher_system.txt`, `query_enricher_human.txt`, `intent_classifier_system.txt`, `entity_extractor_system.txt`, `clarification_agent_system.txt`, `clarification_agent_human.txt`, `sql_generator_system.txt`
- All nodes load prompts at factory creation: `load_prompt("name", default=INLINE_DEFAULT)` — inline default used if file missing
- Prompts API: `GET /api/prompts`, `PUT /api/prompts/{name}`, `GET /api/prompts/export` (ZIP)
- `backend/routers/prompts.py` registered in `backend/main.py`

## Trace / Investigate System
- `agent/trace.py` — `TraceStep` class: records node, duration, llm_call (system/human/raw/parsed), graph_ops, output_summary, error
- **`_trace: List[Any]`** added to `AgentState` — all nodes copy and append their trace step
- SSE: each node emits `event: trace` as soon as it completes; final `event: result` also includes `_trace`
- Frontend: `store/traceStore.ts` — Zustand store; `startQuery(query)→id`, `addLiveStep(step)`, `finalizeTrace(id, steps)`
- `ChatPanel.tsx` wires: `startQuery` on submit, `onTrace→addLiveStep`, `finalizeTrace` on result
- `api/query.ts` — `onTrace?: (step: TraceStep) => void` added to `streamQuery()`; handles `event: trace`

## Investigate Tab
- **7th tab**: `🔬 Investigate` in `AppShell.tsx` (TabId: `'investigate'`)
- `pages/InvestigatePage.tsx`: left panel = query history, right = step accordion
- Each step card: system prompt (editable via PUT /api/prompts/{name}), actual user message sent, raw LLM response, graph ops table, parsed output, output summary
- "Edit/Save" inline for each prompt — saves to file immediately
- Shows diff warning if file was edited after the query
- "⬇ Export Prompts (ZIP)" button in left panel footer

## Context Builder Token Budget
- `_DEFAULT_TOKEN_BUDGET = 200000` tokens (~700k chars) — effectively no truncation for normal schemas
- Truncation code still exists as safety net but won't trigger unless schema exceeds ~700k chars

## Importance Ranking (llm_enhancer.py)
- `_oracle_fk_count(fqn)`: counts `HAS_FOREIGN_KEY` edges both FROM and TO a table's columns
- `in_knowledge_file`: True if table name appears in `kyc_business_knowledge.txt`
- Sort order: `in_knowledge_file` → `oracle_fk_count` → `fk_degree` → `row_count` → `name`
- LLM prompt updated to reflect new criteria

## Logging
- `LOG_LEVEL` env var (default: `INFO`) controls log level in `backend/main.py`
- Set `LOG_LEVEL=DEBUG` to get full LLM input/output dumps in all nodes
- All nodes log raw LLM responses at DEBUG level
