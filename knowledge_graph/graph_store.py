"""
In-Memory Property Graph Store
================================
Replaces Neo4j as the storage backend for the KnowledgeQL knowledge graph.

All nodes and edges are stored in plain Python dicts, keyed for O(1) lookup.
NetworkX is used only for shortest-path computation in find_join_path.

Node model
----------
  _nodes[label][id] = {prop: value, ...}

Edge model
----------
  _edges[rel_type]           = [{_from, _to, **props}, ...]
  _out_idx[rel_type][from_id] = [edge_dict, ...]   # forward lookup
  _in_idx[rel_type][to_id]   = [edge_dict, ...]    # reverse lookup
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterator, List, Optional, Tuple


def _dict_of_lists() -> "defaultdict[str, List[Any]]":
    """Module-level factory for defaultdict(list) — required for pickle compatibility."""
    return defaultdict(list)


class KnowledgeGraph:
    """
    An in-memory property graph with typed node labels and relationship types.

    Usage::

        g = KnowledgeGraph()
        g.merge_node("Table", "KYC.CUSTOMERS", {"name": "CUSTOMERS", "schema": "KYC"})
        g.merge_node("Column", "KYC.CUSTOMERS.CUSTOMER_ID", {"name": "CUSTOMER_ID", ...})
        g.merge_edge("HAS_COLUMN", "KYC.CUSTOMERS", "KYC.CUSTOMERS.CUSTOMER_ID",
                     ordinal_position=1)
        cols = g.get_out_edges("HAS_COLUMN", "KYC.CUSTOMERS")
    """

    def __init__(self) -> None:
        # label -> node_id -> property dict
        self._nodes: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)

        # rel_type -> list of all edge dicts (each has _from and _to)
        self._edges: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

        # Fast lookup: rel_type -> from_id -> [edge_dict]
        self._out_idx: Dict[str, Dict[str, List[Dict[str, Any]]]] = (
            defaultdict(_dict_of_lists)
        )
        # Fast lookup: rel_type -> to_id -> [edge_dict]
        self._in_idx: Dict[str, Dict[str, List[Dict[str, Any]]]] = (
            defaultdict(_dict_of_lists)
        )

    # ------------------------------------------------------------------
    # Node operations
    # ------------------------------------------------------------------

    def merge_node(self, label: str, node_id: str, props: Dict[str, Any]) -> None:
        """Upsert a node. Existing properties are overwritten by *props*."""
        existing = self._nodes[label].get(node_id, {})
        existing.update(props)
        self._nodes[label][node_id] = existing

    def get_node(self, label: str, node_id: str) -> Optional[Dict[str, Any]]:
        """Return the property dict for a node, or None if not found."""
        return self._nodes[label].get(node_id)

    def get_all_nodes(self, label: str) -> List[Dict[str, Any]]:
        """Return all node property dicts for *label*."""
        return list(self._nodes[label].values())

    def count_nodes(self, label: str) -> int:
        return len(self._nodes[label])

    def all_node_ids(self, label: str) -> Iterator[str]:
        return iter(self._nodes[label])

    # ------------------------------------------------------------------
    # Edge operations
    # ------------------------------------------------------------------

    def merge_edge(
        self,
        rel_type: str,
        from_id: str,
        to_id: str,
        merge_key: Optional[str] = None,
        **props: Any,
    ) -> None:
        """
        Upsert an edge between *from_id* and *to_id* of type *rel_type*.

        Merge semantics:
          - If *merge_key* is given, merge on (from_id, to_id, props[merge_key]).
          - Otherwise merge on (from_id, to_id) — only one edge per pair kept.
        """
        existing_list = self._out_idx[rel_type].get(from_id, [])
        for edge in existing_list:
            if edge["_to"] != to_id:
                continue
            if merge_key is None or edge.get(merge_key) == props.get(merge_key):
                edge.update(props)
                return

        # Create a new edge
        new_edge: Dict[str, Any] = {"_from": from_id, "_to": to_id, **props}
        self._edges[rel_type].append(new_edge)
        self._out_idx[rel_type][from_id].append(new_edge)
        self._in_idx[rel_type][to_id].append(new_edge)

    def get_out_edges(self, rel_type: str, from_id: str) -> List[Dict[str, Any]]:
        """Return all edges of *rel_type* leaving *from_id*."""
        return list(self._out_idx[rel_type].get(from_id, []))

    def get_in_edges(self, rel_type: str, to_id: str) -> List[Dict[str, Any]]:
        """Return all edges of *rel_type* arriving at *to_id*."""
        return list(self._in_idx[rel_type].get(to_id, []))

    def get_all_edges(self, rel_type: str) -> List[Dict[str, Any]]:
        """Return all edges of *rel_type* across the whole graph."""
        return list(self._edges[rel_type])

    def count_edges(self, rel_type: str) -> int:
        return len(self._edges[rel_type])

    # ------------------------------------------------------------------
    # Convenience: node update (set a single property on an existing node)
    # ------------------------------------------------------------------

    def set_node_prop(self, label: str, node_id: str, key: str, value: Any) -> None:
        node = self._nodes[label].get(node_id)
        if node is not None:
            node[key] = value

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, int]:
        """Return a dict of {label/rel_type: count} for all populated entries."""
        stats: Dict[str, int] = {}
        for label, nodes in self._nodes.items():
            if nodes:
                stats[label] = len(nodes)
        for rel_type, edges in self._edges.items():
            if edges:
                stats[rel_type] = len(edges)
        return stats
