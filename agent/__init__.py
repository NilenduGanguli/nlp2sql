"""
KnowledgeQL Agent Package
==========================
LangGraph-based agentic pipeline for NLP-to-SQL translation.

Pipeline stages:
  1. intent_classifier  — Classify user query intent
  2. entity_extractor   — Extract business entities from query
  3. context_builder    — Retrieve schema subgraph from KnowledgeGraph
  4. sql_generator      — Generate Oracle SQL with chain-of-thought
  5. sql_validator      — Validate syntax and check anti-patterns
  6. query_optimizer    — Apply rule-based SQL optimizations
  7. query_executor     — Execute against Oracle (or mock in demo mode)
  8. result_formatter   — Format results for chat UI
"""
