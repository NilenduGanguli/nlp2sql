"""
Entity Extractor Node (Agentic)
================================
Implements a tool-calling agent loop that exposes the knowledge graph as live
tools.  Instead of a single one-shot LLM call the agent:

  1. Receives a hierarchical schema tree (grouped by importance tier) as part
     of its system prompt, giving it a structured map of the entire schema.
  2. Issues zero-or-more knowledge-graph tool calls to explore tables, columns,
     FK relationships, and business terms — driven by its own reasoning about
     the user's *intent*, not just keyword matching.
  3. Calls submit_entities once it has gathered enough evidence, providing
     both the entity dict and confirmed table FQNs for the context builder.

The loop runs for up to MAX_TOOL_CALLS iterations.  If the LLM has not
submitted by then a final "force-submit" call is made.  Keyword fallback is
a last resort.

State produced:
  entities           – standard entity dict (tables, columns, conditions, …)
  entity_table_fqns  – List[str] of SCHEMA.TABLE FQNs the agent confirmed;
                       the context builder uses these directly, skipping its
                       own name-resolution pass.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from agent.prompts import load_prompt
from agent.state import AgentState
from agent.trace import TraceStep

logger = logging.getLogger(__name__)

# Maximum tool invocations before a forced submit
MAX_TOOL_CALLS = 8
# Tables shown in the hierarchical schema tree (grouped by tier)
_MAX_TREE_TABLES = 60
# Maximum chars for a single tool result injected back into the conversation


def _safe_format(template: str, **kwargs) -> str:
    """
    Like str.format() but treats ALL literal braces in the template as safe.

    Prompt files can contain raw JSON examples with { } without escaping.
    Only keys that match kwargs are substituted; every other `{...}` is left
    as-is.  This means Prompt Studio users never have to write {{ or }}.

    Algorithm:
      1. Escape all { → {{ and } → }}
      2. Un-escape only the known placeholder keys: {{key}} → {key}
      3. Call .format(**kwargs)
    """
    safe = template.replace("{", "{{").replace("}", "}}")
    for key in kwargs:
        safe = safe.replace("{{" + key + "}}", "{" + key + "}")
    return safe.format(**kwargs)
_MAX_TOOL_RESULT_CHARS = 3000


# ── Tool result formatters ────────────────────────────────────────────────────

def _fmt_search_results(results: List[Dict]) -> str:
    if not results:
        return "No matches found."
    lines = []
    for r in results:
        label = r.get("label", "?")
        fqn   = r.get("fqn", "")
        desc  = (r.get("description") or "")[:80]
        score = r.get("score", 0)
        match = r.get("match_type", "")
        lines.append(f"  [{label}] {fqn}  score={score:.2f} ({match})"
                     + (f"\n    → {desc}" if desc else ""))
    return "\n".join(lines)


def _fmt_table_detail(detail: Optional[Dict]) -> str:
    if detail is None:
        return "Table not found."
    t = detail.get("table", {})
    cols = detail.get("columns", [])
    fks  = detail.get("foreign_keys", [])
    name = t.get("fqn") or t.get("name", "?")
    desc = (t.get("comments") or t.get("llm_description") or "").strip()
    rows = t.get("row_count")

    lines = [f"{name}" + (f"  ({rows:,} rows)" if rows else "")]
    if desc:
        lines.append(f"  Description: {desc[:120]}")

    # Columns
    col_parts = []
    for c in cols:
        tag = ""
        if c.get("is_pk"):   tag += " PK"
        if c.get("is_fk"):   tag += " FK"
        if c.get("is_indexed"): tag += " IDX"
        dt = c.get("data_type") or c.get("data_type_full") or ""
        col_parts.append(f"{c['name']} {dt}{tag}")
    if col_parts:
        lines.append("  Columns: " + ", ".join(col_parts))

    # Foreign keys
    if fks:
        fk_parts = [f"{f['fk_col']} → {f['ref_table']}.{f['ref_col']}" for f in fks[:8]]
        lines.append("  FK refs: " + " | ".join(fk_parts))

    return "\n".join(lines)


def _fmt_join_path(path: Optional[Dict]) -> str:
    if path is None:
        return "No join path found between these tables."
    src = path.get("source", "?")
    if src == "precomputed":
        cols = path.get("join_columns", [])
        jtype = path.get("join_type", "INNER")
        card  = path.get("cardinality", "")
        return f"JOIN ({jtype}, {card}): " + " AND ".join(str(c) for c in cols)
    else:
        nodes = path.get("path_nodes", [])
        edges = path.get("path_edges", [])
        hops  = path.get("hops", len(nodes) - 1)
        details = " → ".join(
            f"{e.get('src', '?')}.{e.get('constraint', '')}" for e in edges
        )
        return f"{hops}-hop traversal path: {' → '.join(nodes)}\n  via: {details}"


def _fmt_related_tables(graph, table_fqn: str) -> str:
    table_fqn = table_fqn.upper()
    edges = graph.get_out_edges("JOIN_PATH", table_fqn)
    if not edges:
        # Try HAS_FOREIGN_KEY via columns
        from knowledge_graph.traversal import get_table_detail
        detail = get_table_detail(graph, table_fqn)
        if detail:
            fks = detail.get("foreign_keys", [])
            if fks:
                lines = [f"FK relationships from {table_fqn}:"]
                for f in fks:
                    lines.append(f"  {f['fk_col']} → {f['ref_table']}.{f['ref_col']}")
                return "\n".join(lines)
        return f"No FK relationships found from {table_fqn}."

    lines = [f"Tables reachable from {table_fqn}:"]
    for e in edges[:12]:
        dest   = e.get("_to", "?")
        cols   = e.get("join_columns", [])
        jtype  = e.get("join_type", "")
        card   = e.get("cardinality", "")
        line   = f"  → {dest}"
        if jtype:
            line += f" ({jtype}, {card})"
        if cols:
            line += f" via {cols}"
        lines.append(line)
    return "\n".join(lines)


def _fmt_business_terms(results: List[Dict]) -> str:
    if not results:
        return "No business term matches found."
    lines = []
    for r in results:
        term   = r.get("term") or r.get("name", "?")
        defn   = (r.get("definition") or r.get("description") or "")[:80]
        target = r.get("target_fqn") or r.get("fqn", "?")
        label  = r.get("target_labels", [None])[0] if r.get("target_labels") else r.get("label", "?")
        lines.append(f"  '{term}' → [{label}] {target}" + (f": {defn}" if defn else ""))
    return "\n".join(lines)


# ── Graph tool dispatcher ─────────────────────────────────────────────────────

def _call_graph_tool(
    graph,
    action: str,
    args: Dict,
    trace: TraceStep,
) -> Tuple[str, List]:
    """
    Execute a graph tool and return (formatted_result_str, raw_results).
    Adds a graph_op entry to the trace.
    """
    from knowledge_graph.traversal import (
        find_join_path,
        get_table_detail,
        resolve_business_term,
        search_schema,
    )

    try:
        if action == "search_schema":
            query  = args.get("query", "")
            limit  = int(args.get("limit", 8))
            raw    = search_schema(graph, query, limit=limit)
            result = _fmt_search_results(raw)
            trace.add_graph_op("search_schema", args, raw)
            return result, raw

        elif action == "get_table_detail":
            fqn    = args.get("table_fqn", "").upper()
            raw    = get_table_detail(graph, fqn)
            result = _fmt_table_detail(raw)
            trace.add_graph_op("get_table_detail", args, [raw] if raw else [])
            return result, [raw] if raw else []

        elif action == "find_join_path":
            f1  = args.get("from_fqn", "").upper()
            f2  = args.get("to_fqn", "").upper()
            raw = find_join_path(graph, f1, f2, max_hops=4)
            result = _fmt_join_path(raw)
            trace.add_graph_op("find_join_path", args, [raw] if raw else [])
            return result, [raw] if raw else []

        elif action == "resolve_business_term":
            term   = args.get("term", "")
            raw    = resolve_business_term(graph, term)
            result = _fmt_business_terms(raw)
            trace.add_graph_op("resolve_business_term", args, raw)
            return result, raw

        elif action == "list_related_tables":
            fqn    = args.get("table_fqn", "").upper()
            result = _fmt_related_tables(graph, fqn)
            trace.add_graph_op("list_related_tables", args, [])
            return result, []

        else:
            return f"Unknown tool: {action}", []

    except Exception as exc:
        logger.warning("Tool %s failed: %s", action, exc)
        trace.add_graph_op(action, args, [])
        return f"Tool error: {exc}", []


# ── JSON action parser ────────────────────────────────────────────────────────

def _parse_action(content: str) -> Optional[Dict]:
    """
    Extract the first valid JSON object from an LLM response.
    Handles markdown code blocks and leading/trailing prose.
    """
    # Strip markdown fences
    content = re.sub(r"```(?:json)?\s*", "", content, flags=re.IGNORECASE)
    content = content.replace("```", "")

    # Find outermost JSON object
    start = content.find("{")
    if start == -1:
        return None
    depth, end = 0, -1
    for i in range(start, len(content)):
        if content[i] == "{":
            depth += 1
        elif content[i] == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        return None
    try:
        return json.loads(content[start:end + 1])
    except json.JSONDecodeError:
        return None


# ── Hierarchical schema tree builder ─────────────────────────────────────────

def _build_schema_tree(graph) -> str:
    """
    Build a hierarchical schema tree grouped by importance_tier.
    Returns formatted text ready for injection into the system prompt.
    """
    try:
        from knowledge_graph.traversal import get_columns_for_table
    except Exception:
        return "(schema tree unavailable)"

    all_tables = graph.get_all_nodes("Table")
    if not all_tables:
        return "(no tables in graph)"

    def _sort_key(t: Dict) -> Tuple:
        tier_order = {"core": 0, "reference": 1, "audit": 2, "utility": 3}
        tier  = tier_order.get(t.get("importance_tier", ""), 4)
        rank  = t.get("importance_rank") or 999
        jp    = len(graph.get_out_edges("JOIN_PATH", t.get("fqn", "")))
        return (tier, rank, -jp)

    sorted_tables = sorted(all_tables, key=_sort_key)
    total = len(sorted_tables)

    # Group by tier; cap each tier to keep tree readable
    tier_caps = {"core": 25, "reference": 20, "audit": 10, "utility": 10}
    tiers: Dict[str, List] = {"core": [], "reference": [], "audit": [], "utility": [], "unranked": []}
    counts: Dict[str, int] = {k: 0 for k in tiers}

    for t in sorted_tables:
        tier = t.get("importance_tier") or ("unranked" if not t.get("importance_rank") else "utility")
        cap  = tier_caps.get(tier, 10)
        if counts.get(tier, 0) < cap:
            tiers.setdefault(tier, []).append(t)
            counts[tier] = counts.get(tier, 0) + 1

    lines = [f"DATABASE SCHEMA — {', '.join(sorted({t.get('schema','?') for t in all_tables if t.get('schema')}))}",
             f"Total tables: {total}",
             ""]

    tier_labels = [
        ("core",     "CORE TABLES (business-critical, highest connectivity)"),
        ("reference","REFERENCE / LOOKUP TABLES"),
        ("audit",    "AUDIT / HISTORY TABLES"),
        ("utility",  "UTILITY TABLES"),
        ("unranked", "OTHER TABLES"),
    ]

    shown = 0
    for tier_key, tier_label in tier_labels:
        group = tiers.get(tier_key, [])
        if not group:
            continue
        lines.append(f"## {tier_label}")
        for t in group:
            fqn     = t.get("fqn", "")
            name    = t.get("name", fqn)
            desc    = (t.get("comments") or t.get("llm_description") or "").strip()
            rows    = t.get("row_count")

            # Columns: PKs first, then FKs, then up to 6 data columns
            try:
                cols = get_columns_for_table(graph, fqn)
            except Exception:
                cols = []

            pk_names: List[str] = []
            fk_parts: List[str] = []
            data_cols: List[str] = []
            for c in cols:
                dt = c.get("data_type") or ""
                entry = f"{c['name']}({dt})"
                if c.get("is_pk"):
                    pk_names.append(c["name"])
                elif c.get("is_fk"):
                    # Find what it references via HAS_FOREIGN_KEY edge
                    fk_edges = graph.get_out_edges("HAS_FOREIGN_KEY", c.get("fqn", ""))
                    if fk_edges:
                        ref_fqn = fk_edges[0].get("_to", "")
                        ref_table = ref_fqn.rsplit(".", 1)[0] if ref_fqn.count(".") >= 2 else ref_fqn
                        fk_parts.append(f"{c['name']}→{ref_table}")
                    else:
                        data_cols.append(entry)
                else:
                    if len(data_cols) < 5:
                        data_cols.append(entry)

            line = f"• {fqn}"
            if rows:
                line += f" ({rows:,} rows)"
            if desc:
                line += f"\n  Purpose: {desc[:100]}"
            meta_parts = []
            if pk_names:
                meta_parts.append(f"PKs: {', '.join(pk_names)}")
            if fk_parts:
                meta_parts.append("FKs: " + ", ".join(fk_parts[:5]))
            if data_cols:
                meta_parts.append("Cols: " + ", ".join(data_cols))
            if meta_parts:
                line += "\n  " + " | ".join(meta_parts)

            lines.append(line)
            shown += 1

        lines.append("")

    if total > shown:
        lines.append(
            f"[Showing {shown} of {total} tables. "
            "Use search_schema, resolve_business_term, or get_table_detail to explore the rest.]"
        )

    # Always append Oracle data dictionary section — these views are available on every Oracle DB
    lines.append("")
    lines.append("## ORACLE DATA DICTIONARY VIEWS (always available — query DB metadata)")
    lines.append("• ALL_TABLES — all tables accessible to current user (OWNER, TABLE_NAME, NUM_ROWS, etc.)")
    lines.append("• ALL_COLUMNS — all columns for accessible tables (TABLE_NAME, COLUMN_NAME, DATA_TYPE, NULLABLE)")
    lines.append("• ALL_CONSTRAINTS — constraint definitions (CONSTRAINT_NAME, CONSTRAINT_TYPE, TABLE_NAME, STATUS)")
    lines.append("• ALL_INDEXES — index info (INDEX_NAME, TABLE_NAME, UNIQUENESS, STATUS)")
    lines.append("• ALL_IND_COLUMNS — columns included in each index (INDEX_NAME, COLUMN_NAME, COLUMN_POSITION)")
    lines.append("• ALL_VIEWS — view definitions accessible to user (VIEW_NAME, TEXT)")
    lines.append("• ALL_PROCEDURES — stored procedures and packages (OBJECT_NAME, PROCEDURE_NAME, OBJECT_TYPE)")
    lines.append("• ALL_SYNONYMS — synonym definitions (SYNONYM_NAME, TABLE_OWNER, TABLE_NAME)")
    lines.append("• ALL_SEQUENCES — sequence objects (SEQUENCE_NAME, MIN_VALUE, MAX_VALUE, INCREMENT_BY)")
    lines.append("• USER_TABLES — tables owned by current user (subset of ALL_TABLES)")
    lines.append("• USER_SEGMENTS — storage/size info per segment (SEGMENT_NAME, SEGMENT_TYPE, BYTES)")
    lines.append("• DBA_TABLES — ALL tables in DB (requires DBA role; cols = ALL_TABLES)")
    lines.append("• DBA_USERS — database user accounts (USERNAME, ACCOUNT_STATUS, CREATED)")
    lines.append("Use these when the user asks about schema structure, table counts, column lists,")
    lines.append("constraint definitions, index usage, or any database metadata question.")

    return "\n".join(lines)


# ── System prompt template ────────────────────────────────────────────────────

_AGENT_SYSTEM_TEMPLATE = """\
You are a schema-expert entity extraction agent for an Oracle database (schemas: {schemas}).

Your task: identify ALL tables, columns, conditions, and relationships needed to answer the
user's query — not just what is literally named, but everything implied by the INTENT.

{schema_tree}

─────────────────────────────────────────────────────
AVAILABLE TOOLS
─────────────────────────────────────────────────────
{tools_spec}

─────────────────────────────────────────────────────
HOW TO RESPOND
─────────────────────────────────────────────────────
Every response must be a single JSON object (no other text):

  {"thought": "<your reasoning>", "action": "<tool_name>", "args": {...}}

For the final submission:

  {"thought": "<your reasoning>", "action": "submit_entities", "args": {
    "tables":       ["TABLE1", "TABLE2"],
    "columns":      ["COL1"],
    "conditions":   ["col = 'VALUE'"],
    "time_range":   null,
    "aggregations": ["COUNT"],
    "sort_by":      null,
    "limit":        null,
    "table_fqns":   ["SCHEMA.TABLE1", "SCHEMA.TABLE2"]
  }}

RULES:
- Investigate thoroughly — search for every concept in the query, not just obvious matches
- Include ALL tables needed for JOINs (not just the primary answer table)
- If a term could refer to multiple tables, use get_table_detail to check columns and decide
- Use list_related_tables or find_join_path to discover required joins
- Prefer Oracle UPPERCASE names. table_fqns must be SCHEMA.TABLE format
- You have up to {max_calls} tool calls; use them wisely but do not stop early
─────────────────────────────────────────────────────
"""

_TOOLS_SPEC = """\
1. search_schema
   Find tables/columns by name or keyword. Good for initial discovery.
   Args: {"query": "customer risk", "limit": 8}

2. get_table_detail
   Full column list with data types, PKs, FK references for ONE table.
   Args: {"table_fqn": "SCHEMA.TABLE_NAME"}

3. find_join_path
   Find the JOIN columns between two specific tables.
   Args: {"from_fqn": "SCHEMA.TABLE1", "to_fqn": "SCHEMA.TABLE2"}

4. resolve_business_term
   Map business/domain language to schema objects (e.g. "KYC check" → table).
   Args: {"term": "know your customer check"}

5. list_related_tables
   List all tables reachable from a given table via FK relationships.
   Args: {"table_fqn": "SCHEMA.TABLE_NAME"}

6. submit_entities
   Finalise your findings. MUST include table_fqns (fully-qualified).
   See response format above."""


# ── Fallback keyword extractor ────────────────────────────────────────────────

def _fallback_extract(user_input: str, all_table_names: Optional[List[str]] = None) -> Dict[str, Any]:
    text = user_input.upper()
    found: List[str] = []
    if all_table_names:
        for name in all_table_names:
            nu = name.upper()
            if nu in text or nu.rstrip("S") in text or text.rstrip("S") in nu:
                found.append(name)
        found = found[:5]

    time_range: Optional[str] = None
    for kw, val in {
        "LAST MONTH": "last month", "LAST QUARTER": "last quarter",
        "LAST YEAR": "last year", "THIS YEAR": "this year",
        "THIS MONTH": "this month", "PAST YEAR": "past year",
        "PAST MONTH": "past month",
    }.items():
        if kw in text:
            time_range = val
            break

    aggs: List[str] = []
    if any(kw in text for kw in ("HOW MANY", "COUNT", "TOTAL NUMBER", "NUMBER OF")):
        aggs.append("COUNT")
    if any(kw in text for kw in ("SUM", "TOTAL AMOUNT", "TOTAL VALUE")):
        aggs.append("SUM")
    if any(kw in text for kw in ("AVERAGE", "AVG", "MEAN")):
        aggs.append("AVG")

    return {
        "tables": found, "columns": [], "conditions": [],
        "time_range": time_range, "aggregations": aggs, "sort_by": None, "limit": None,
    }


# ── Node factory ──────────────────────────────────────────────────────────────

def _build_schema_summary(graph) -> Tuple[str, List[str], List[str]]:
    """
    Kept for backward compatibility with callers that unpack 3 values.
    Returns (table_list_text, all_table_names, all_schemas).
    """
    if graph is None:
        return "(schema not loaded)", [], []

    all_tables = graph.get_all_nodes("Table")
    names = [t.get("name", "") for t in all_tables if t.get("name")]
    schemas = sorted({t.get("schema", "") for t in all_tables if t.get("schema")})
    tree = _build_schema_tree(graph)
    return tree, names, schemas


def make_entity_extractor(llm, graph=None) -> Callable[[AgentState], AgentState]:
    """
    Factory: returns a LangGraph node that runs an agentic entity-extraction loop.

    The LLM receives a hierarchical schema tree and can call knowledge-graph
    tools (search_schema, get_table_detail, find_join_path, …) before settling
    on the final list of entities and confirmed table FQNs.
    """
    all_table_names: List[str] = []
    system_prompt: str = ""

    if graph is not None:
        all_tables = graph.get_all_nodes("Table")
        all_table_names = [t.get("name", "") for t in all_tables if t.get("name")]
        all_schemas = sorted({t.get("schema", "") for t in all_tables if t.get("schema")})
        schema_str  = ", ".join(all_schemas) if all_schemas else "unknown"

        schema_tree = _build_schema_tree(graph)
        template    = load_prompt("entity_extractor_system", default=_AGENT_SYSTEM_TEMPLATE)
        system_prompt = _safe_format(
            template,
            schemas    = schema_str,
            schema_tree= schema_tree,
            tools_spec = _TOOLS_SPEC,
            max_calls  = MAX_TOOL_CALLS,
        )
    else:
        system_prompt = (
            "You are an entity extractor for an Oracle database. "
            "Extract tables, columns, conditions from the user query as JSON."
        )

    logger.info("Entity extractor (agentic) initialised: %d tables", len(all_table_names))

    def extract_entities(state: AgentState) -> AgentState:
        user_input = state.get("enriched_query") or state.get("user_input", "")
        _trace = list(state.get("_trace", []))
        trace  = TraceStep("extract_entities", "investigating")

        logger.debug("Agentic entity extraction for: %r", user_input[:120])

        # Accumulate all LLM raw exchanges for the trace
        all_raw_responses: List[str] = []
        entity_table_fqns: List[str] = []

        entities: Dict[str, Any] = {
            "tables": [], "columns": [], "conditions": [],
            "time_range": None, "aggregations": [], "sort_by": None, "limit": None,
        }

        try:
            from langchain_core.messages import HumanMessage, SystemMessage

            # Build conversation: system prompt + first human turn
            sys_msg      = SystemMessage(content=system_prompt)
            conversation  = [
                HumanMessage(
                    content=f"Query: {user_input}\n\nBegin your investigation."
                )
            ]

            submitted = False

            for iteration in range(MAX_TOOL_CALLS):
                response = llm.invoke([sys_msg] + conversation)
                content  = response.content if hasattr(response, "content") else str(response)
                all_raw_responses.append(f"[Iteration {iteration + 1}] {content}")
                logger.debug("Entity agent iter %d raw: %s", iteration + 1, content[:300])

                action_dict = _parse_action(content)
                if action_dict is None:
                    logger.warning(
                        "Entity agent iter %d: could not parse JSON — content: %r",
                        iteration + 1, content[:300],
                    )
                    conversation.append(HumanMessage(
                        content="Your response was not valid JSON. Respond with ONLY a JSON object."
                    ))
                    continue

                action = action_dict.get("action", "")
                args   = action_dict.get("args", {})
                thought = action_dict.get("thought", "")
                logger.debug("Entity agent iter %d: action=%s thought=%r", iteration + 1, action, thought[:100])

                if action == "submit_entities":
                    # Extract final entities
                    for key in ("tables", "columns", "conditions", "aggregations"):
                        val = args.get(key)
                        if isinstance(val, list):
                            entities[key] = val
                    for key in ("time_range", "sort_by", "limit"):
                        val = args.get(key)
                        if val is not None:
                            entities[key] = val

                    entity_table_fqns = [
                        fqn.upper() for fqn in args.get("table_fqns", [])
                        if isinstance(fqn, str)
                    ]
                    # Normalise table names to uppercase
                    entities["tables"]       = [t.upper() for t in entities["tables"] if isinstance(t, str)]
                    entities["columns"]      = [c.upper() for c in entities["columns"] if isinstance(c, str)]
                    entities["aggregations"] = [a.upper() for a in entities["aggregations"] if isinstance(a, str)]

                    trace.add_graph_op("submit_entities", {"table_fqns": entity_table_fqns, "tables": entities["tables"]}, [])
                    submitted = True
                    break

                # Execute graph tool
                tool_result_str, raw = _call_graph_tool(graph, action, args, trace)
                if len(tool_result_str) > _MAX_TOOL_RESULT_CHARS:
                    tool_result_str = tool_result_str[:_MAX_TOOL_RESULT_CHARS] + "\n… (truncated)"

                # Append AI turn + tool result turn
                from langchain_core.messages import AIMessage
                conversation.append(AIMessage(content=content))
                prompt_suffix = (
                    f"\n\nTool result for '{action}':\n{tool_result_str}"
                    "\n\nContinue your investigation or call submit_entities when ready."
                    f" Remaining tool calls: {MAX_TOOL_CALLS - iteration - 1}."
                )
                conversation.append(HumanMessage(content=prompt_suffix))

            # Force submit if loop exhausted without a submit
            if not submitted:
                logger.warning("Entity agent did not submit within %d iterations; forcing submit", MAX_TOOL_CALLS)
                from langchain_core.messages import AIMessage
                force_msg = HumanMessage(
                    content=(
                        "You have reached the tool call limit. "
                        "You MUST now call submit_entities with your best findings. "
                        "Output ONLY the JSON submit_entities action."
                    )
                )
                force_response = llm.invoke([sys_msg] + conversation + [force_msg])
                force_content  = force_response.content if hasattr(force_response, "content") else str(force_response)
                all_raw_responses.append(f"[Force submit] {force_content}")
                logger.debug("Force submit response: %s", force_content[:300])

                action_dict = _parse_action(force_content)
                if action_dict and action_dict.get("action") == "submit_entities":
                    args = action_dict.get("args", {})
                    for key in ("tables", "columns", "conditions", "aggregations"):
                        val = args.get(key)
                        if isinstance(val, list):
                            entities[key] = val
                    for key in ("time_range", "sort_by", "limit"):
                        val = args.get(key)
                        if val is not None:
                            entities[key] = val

                    entity_table_fqns = [
                        fqn.upper() for fqn in args.get("table_fqns", [])
                        if isinstance(fqn, str)
                    ]
                    entities["tables"]       = [t.upper() for t in entities["tables"] if isinstance(t, str)]
                    entities["columns"]      = [c.upper() for c in entities["columns"] if isinstance(c, str)]
                    entities["aggregations"] = [a.upper() for a in entities["aggregations"] if isinstance(a, str)]
                    submitted = True

            if not submitted:
                logger.warning("Force submit also failed; running keyword fallback")
                entities = _fallback_extract(user_input, all_table_names)

            combined_raw = "\n\n".join(all_raw_responses)
            trace.set_llm_call(system_prompt, f"Query: {user_input}", combined_raw, entities)

        except Exception as exc:
            logger.error("Agentic entity extraction failed: %s", exc, exc_info=True)
            entities = _fallback_extract(user_input, all_table_names)
            trace.error = str(exc)

        if not entities.get("tables"):
            logger.info("No tables extracted — context builder will apply connectivity fallback")

        logger.info(
            "Entities extracted [agentic]: tables=%s fqns=%s conditions=%d",
            entities.get("tables"),
            entity_table_fqns,
            len(entities.get("conditions", [])),
        )

        trace.output_summary = {
            "tables":           entities.get("tables"),
            "entity_table_fqns": entity_table_fqns,
            "conditions":       entities.get("conditions"),
            "columns":          entities.get("columns"),
            "iterations":       len(all_raw_responses),
        }
        _trace.append(trace.finish().to_dict())

        return {
            **state,
            "entities":          entities,
            "entity_table_fqns": entity_table_fqns,
            "step":              "entities_extracted",
            "_trace":            _trace,
        }

    return extract_entities
