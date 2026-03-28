"""
Live pipeline smoke test — runs 3 NLP queries against the real Oracle DB.
Run from the project root:  python scripts/smoke_test.py
"""
import logging
import os
import sys

logging.basicConfig(level=logging.WARNING)

# Ensure project root is on the path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))

from app_config import AppConfig
from knowledge_graph.oracle_extractor import OracleMetadataExtractor
from knowledge_graph.graph_builder import GraphBuilder
from knowledge_graph.glossary_loader import InferredGlossaryBuilder
from agent.pipeline import build_pipeline, run_query

# ── Config ────────────────────────────────────────────────────────────────────
cfg = AppConfig()
print(f"\nConfig:")
print(f"  oracle  : {cfg.oracle.dsn}  user={cfg.oracle.user}  schema={cfg.oracle.target_schemas}")
print(f"  llm     : {cfg.llm_provider}/{cfg.llm_model}")

# ── Build knowledge graph from live Oracle ────────────────────────────────────
print("\nExtracting Oracle metadata...")
extractor = OracleMetadataExtractor(cfg.oracle)
if not extractor.check_connectivity():
    print("ERROR: Cannot connect to Oracle", file=sys.stderr)
    sys.exit(1)

meta = extractor.extract()
print(f"  tables={len(meta.tables)}, columns={len(meta.columns)}, "
      f"FKs={len(meta.foreign_keys)}, indexes={len(meta.indexes)}")

print("Building knowledge graph...")
builder = GraphBuilder(cfg.graph)
builder.build(meta)
InferredGlossaryBuilder(builder.graph).build(meta)
graph = builder.graph
stats = graph.get_stats()
print(f"  nodes : { {k:v for k,v in stats.items() if not k.isupper()} }")
print(f"  edges : { {k:v for k,v in stats.items() if k.isupper()} }")

# ── Build pipeline ────────────────────────────────────────────────────────────
print("\nBuilding agent pipeline...")
pipeline = build_pipeline(graph, cfg)
print(f"  pipeline type: {type(pipeline).__name__}")

# ── Run queries ───────────────────────────────────────────────────────────────
queries = [
    "show me all high risk customers",
    "which customers have flagged transactions?",
    "list PEP customers with their risk scores",
    "how many escalated KYC reviews are there?",
    "show beneficial owners with ownership above 50%",
]

PASS = 0
FAIL = 0
print("\n" + "=" * 65)
for q in queries:
    print(f"\nQ: {q}")
    try:
        result = run_query(pipeline, q)
        sql   = result.get("sql", "")
        rows  = result.get("total_rows", 0)
        ms    = result.get("execution_time_ms", 0)
        cols  = result.get("columns", [])
        rtype = result.get("type", "?")
        err   = result.get("error")

        print(f"  sql    : {sql[:90]}{'...' if len(sql) > 90 else ''}")
        print(f"  result : type={rtype}  rows={rows}  time={ms}ms")
        if cols:
            print(f"  cols   : {cols}")
        if result.get("rows"):
            print(f"  row[0] : {result['rows'][0]}")
        if err:
            print(f"  ERROR  : {err}")
            FAIL += 1
        elif rtype == "query_result" and not err:
            PASS += 1
        else:
            FAIL += 1
    except Exception as exc:
        print(f"  EXCEPTION: {exc}")
        FAIL += 1

print("\n" + "=" * 65)
print(f"\nSmoke test: {PASS} passed, {FAIL} failed out of {len(queries)} queries")
sys.exit(0 if FAIL == 0 else 1)
