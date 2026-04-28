from agent.knowledge_store import KYCKnowledgeStore, VerifiedPattern
from knowledge_graph.graph_store import KnowledgeGraph


def _graph_with(table_fqn: str) -> KnowledgeGraph:
    g = KnowledgeGraph()
    schema, name = table_fqn.split(".")
    g.merge_node("Table", table_fqn, {"name": name, "schema": schema})
    return g


def test_add_pattern_persists_and_reloads(tmp_path):
    persist = str(tmp_path / "ks.json")
    store_a = KYCKnowledgeStore(persist_path=persist)
    p = VerifiedPattern(
        pattern_id="vp_1",
        sql_skeleton="select * from kyc.customers where risk = ?",
        exemplar_query="show high risk customers",
        exemplar_sql="SELECT * FROM KYC.CUSTOMERS WHERE risk = 'HIGH'",
        tables_used=["KYC.CUSTOMERS"],
        accept_count=3,
        consumer_uses=0,
        negative_signals=0,
        score=3.0,
        promoted_at=1000.0,
        source_entry_ids=["e1", "e2", "e3"],
        manual_promotion=False,
    )
    store_a.add_pattern(p)

    store_b = KYCKnowledgeStore(persist_path=persist)
    found = [pp for pp in store_b.patterns if pp.pattern_id == "vp_1"]
    assert len(found) == 1
    assert found[0].score == 3.0


def test_find_verified_pattern_filters_by_table_existence(tmp_path):
    g = _graph_with("KYC.CUSTOMERS")
    store = KYCKnowledgeStore(persist_path=str(tmp_path / "ks.json"))
    store.add_pattern(VerifiedPattern(
        pattern_id="vp_1",
        sql_skeleton="select * from kyc.customers where risk = ?",
        exemplar_query="show me high risk customers",
        exemplar_sql="SELECT * FROM KYC.CUSTOMERS WHERE risk = 'HIGH'",
        tables_used=["KYC.CUSTOMERS"],
        accept_count=3, consumer_uses=0, negative_signals=0,
        score=3.0, promoted_at=1.0, source_entry_ids=["e1"], manual_promotion=False,
    ))
    store.add_pattern(VerifiedPattern(
        pattern_id="vp_2",
        sql_skeleton="select * from kyc.gone where x = ?",
        exemplar_query="dropped table query",
        exemplar_sql="SELECT * FROM KYC.GONE WHERE x = 1",
        tables_used=["KYC.GONE"],  # not in graph
        accept_count=3, consumer_uses=0, negative_signals=0,
        score=3.0, promoted_at=2.0, source_entry_ids=["e2"], manual_promotion=False,
    ))

    matched = store.find_verified_pattern("show high risk customers", g)
    assert matched is not None
    assert matched.pattern_id == "vp_1"


def test_find_verified_pattern_returns_none_when_no_match(tmp_path):
    g = _graph_with("KYC.CUSTOMERS")
    store = KYCKnowledgeStore(persist_path=str(tmp_path / "ks.json"))
    store.add_pattern(VerifiedPattern(
        pattern_id="vp_1",
        sql_skeleton="select * from kyc.customers where risk = ?",
        exemplar_query="show me high risk customers",
        exemplar_sql="SELECT * FROM KYC.CUSTOMERS",
        tables_used=["KYC.CUSTOMERS"],
        accept_count=3, consumer_uses=0, negative_signals=0,
        score=3.0, promoted_at=1.0, source_entry_ids=["e1"], manual_promotion=False,
    ))
    matched = store.find_verified_pattern("the meaning of life", g)
    assert matched is None
