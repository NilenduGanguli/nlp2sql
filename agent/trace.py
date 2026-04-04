"""
Pipeline Trace Collector
========================
Each node appends a TraceStep to state["_trace"]. The SSE router streams
trace steps as `event: trace` and the final result includes the full list.
"""
from __future__ import annotations
import time
from typing import Any, Dict, List, Optional


class TraceStep:
    """Data captured for one pipeline node execution."""

    def __init__(self, node: str, step_label: str):
        self.node = node
        self.step_label = step_label
        self._start = time.monotonic()
        self.duration_ms: float = 0.0
        self.llm_call: Optional[Dict[str, Any]] = None   # {system, human, raw_response, parsed}
        self.graph_ops: List[Dict[str, Any]] = []         # [{op, params, result_count, sample}]
        self.output_summary: Dict[str, Any] = {}
        self.error: Optional[str] = None

    def finish(self) -> "TraceStep":
        self.duration_ms = round((time.monotonic() - self._start) * 1000, 1)
        return self

    def set_llm_call(self, system: str, human: str, raw_response: str, parsed: Any = None):
        self.llm_call = {
            "system_prompt": system,
            "user_prompt": human,
            "raw_response": raw_response,
            "parsed_output": parsed,
        }

    def add_graph_op(self, op: str, params: Dict, results: List):
        self.graph_ops.append({
            "op": op,
            "params": params,
            "result_count": len(results),
            "result_sample": results[:3],
        })

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node": self.node,
            "step_label": self.step_label,
            "duration_ms": self.duration_ms,
            "llm_call": self.llm_call,
            "graph_ops": self.graph_ops,
            "output_summary": self.output_summary,
            "error": self.error,
        }
