"""
Agent Nodes Package
====================
Each module in this package exports a ``make_*`` factory function that
returns a LangGraph-compatible node callable:

  ``make_intent_classifier(llm)``    → node function
  ``make_entity_extractor(llm)``     → node function
  ``make_context_builder(graph)``    → node function
  ``make_sql_generator(llm)``        → node function
  ``make_sql_validator()``           → node function
  ``make_query_optimizer()``         → node function
  ``make_query_executor(config)``    → node function
  ``make_result_formatter()``        → node function

Each node function accepts an AgentState dict and returns a (possibly
partial) AgentState dict with updated fields.
"""

from agent.nodes import (
    intent_classifier,
    entity_extractor,
    context_builder,
    sql_generator,
    sql_validator,
    query_optimizer,
    query_executor,
    result_formatter,
)

__all__ = [
    "intent_classifier",
    "entity_extractor",
    "context_builder",
    "sql_generator",
    "sql_validator",
    "query_optimizer",
    "query_executor",
    "result_formatter",
]
