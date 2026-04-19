"""
Business Document Loader
=========================
Parses business knowledge documents from the ``kyc_business_knowledge_agentic/``
directory and converts them into :class:`~agent.knowledge_store.KnowledgeEntry`
instances for the KYC Business Agent.

Three parsers:
  1. ``load_business_json(path)`` — ``business_json_template.txt``
  2. ``load_business_relations(path)`` — ``business_table_relation.txt``
  3. ``load_text_documents(docs_dir)`` — any other ``.txt`` files in the directory
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from agent.knowledge_store import KnowledgeEntry

logger = logging.getLogger(__name__)


def _make_id(source: str, key: str) -> str:
    """Deterministic ID from source + key so reloads deduplicate."""
    return hashlib.md5(f"{source}:{key}".encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# 1. business_json_template.txt — quasi-JSON with comments
# ---------------------------------------------------------------------------

def load_business_json(path: str) -> List[KnowledgeEntry]:
    """Parse ``business_json_template.txt`` into KnowledgeEntry objects.

    The file is quasi-JSON (has Python-style ``#`` comments and ``...`` placeholders).
    We strip those before parsing, then extract:
    - Global value sets (STATUS_VALUES, CLIENT_TYPE_VALUES, etc.)
    - Table column descriptions and value references
    - Relationship definitions
    """
    if not os.path.exists(path):
        logger.warning("Business JSON template not found: %s", path)
        return []

    try:
        with open(path, "r") as f:
            raw = f.read()
    except Exception as exc:
        logger.error("Failed to read %s: %s", path, exc)
        return []

    entries: List[KnowledgeEntry] = []

    # --- Extract global value sets ---
    # Look for patterns like "STATUS_VALUES": { "description": ..., "values": [...] }
    _extract_value_sets(raw, entries)

    # --- Extract table info ---
    _extract_tables(raw, entries)

    # --- Extract relationships ---
    _extract_relationships(raw, entries)

    logger.info("Loaded %d entries from business JSON template: %s", len(entries), path)
    return entries


def _extract_value_sets(raw: str, entries: List[KnowledgeEntry]) -> None:
    """Extract global value sets like STATUS_VALUES, CLIENT_TYPE_VALUES."""
    # Match quoted key followed by a block containing description and values
    pattern = r'"(\w+_VALUES)"\s*:\s*\{([^}]+)\}'
    for match in re.finditer(pattern, raw):
        name = match.group(1)
        block = match.group(2)

        desc_match = re.search(r'"description"\s*:\s*"([^"]*)"', block)
        description = desc_match.group(1) if desc_match else name

        # Extract values array
        values: List[str] = []
        vals_match = re.search(r'"values"\s*:\s*\[([^\]]*)\]', block)
        if vals_match:
            for v in re.findall(r'"([^"]*)"', vals_match.group(1)):
                if v and v != "...":
                    values.append(v)

        content = f"Value set: {name}\nDescription: {description}"
        if values:
            content += f"\nKnown values: {', '.join(values)}"

        entries.append(KnowledgeEntry(
            id=_make_id("business_json", f"value_set:{name}"),
            source="business_json_template",
            content=content,
            category="column_values",
            metadata={"value_set_name": name, "values": values, "description": description},
        ))


def _extract_tables(raw: str, entries: List[KnowledgeEntry]) -> None:
    """Extract table definitions with column descriptions."""
    # Find the "tables" section
    tables_match = re.search(r'"tables"\s*:\s*\{', raw)
    if not tables_match:
        return

    # Look for table entries: "TABLE_NAME": { "full_name": ..., "columns": [...], ... }
    # Parse table names and their full_name fields
    table_pattern = r'"(\w+)"\s*:\s*\{[^{]*?"full_name"\s*:\s*"([^"]*)"'
    for match in re.finditer(table_pattern, raw[tables_match.start():]):
        table_name = match.group(1)
        full_name = match.group(2)

        # Extract columns list
        cols_text = ""
        # Find the block for this table
        block_start = match.start() + tables_match.start()
        block_text = raw[block_start:block_start + 2000]  # reasonable window

        cols_match = re.search(r'"columns"\s*:\s*\[([^\]]*)\]', block_text)
        if cols_match:
            cols = [c.strip().strip('"') for c in cols_match.group(1).split(',')
                    if c.strip().strip('"') and c.strip().strip('"') != '...']
            cols_text = ", ".join(cols)

        # Extract primary key
        pk_text = ""
        pk_match = re.search(r'"primary_key"\s*:\s*\[([^\]]*)\]', block_text)
        if pk_match:
            pks = [c.strip().strip('"') for c in pk_match.group(1).split(',')
                   if c.strip().strip('"') and c.strip().strip('"') != '...']
            pk_text = ", ".join(pks)

        # Extract column descriptions from the block
        col_descs: List[str] = []
        col_desc_pattern = r'"(\w+)"\s*:\s*\{[^}]*?"description"\s*:\s*"([^"]*)"'
        for cd in re.finditer(col_desc_pattern, block_text):
            col_name = cd.group(1)
            if col_name in ("full_name", "columns", "primary_key"):
                continue
            col_desc = cd.group(2)
            col_descs.append(f"  {col_name}: {col_desc}")

        content = f"Table: {table_name} ({full_name})"
        if pk_text:
            content += f"\nPrimary key: {pk_text}"
        if cols_text:
            content += f"\nColumns: {cols_text}"
        if col_descs:
            content += "\nColumn descriptions:\n" + "\n".join(col_descs)

        entries.append(KnowledgeEntry(
            id=_make_id("business_json", f"table:{table_name}"),
            source="business_json_template",
            content=content,
            category="table_info",
            metadata={"table_name": table_name, "full_name": full_name, "pk": pk_text},
        ))


def _extract_relationships(raw: str, entries: List[KnowledgeEntry]) -> None:
    """Extract relationship definitions."""
    rel_pattern = re.compile(
        r'\{\s*"parent"\s*:\s*"(\w+)"\s*,\s*'
        r'"child"\s*:\s*"(\w+)"\s*,\s*'
        r'"parent_key"\s*:\s*\[([^\]]*)\]\s*,\s*'
        r'"child_key"\s*:\s*\[([^\]]*)\]\s*,\s*'
        r'"join_type"\s*:\s*"([^"]*)"',
        re.DOTALL,
    )
    for match in rel_pattern.finditer(raw):
        parent = match.group(1)
        child = match.group(2)
        parent_keys = [k.strip().strip('"') for k in match.group(3).split(',') if k.strip().strip('"')]
        child_keys = [k.strip().strip('"') for k in match.group(4).split(',') if k.strip().strip('"')]
        join_type = match.group(5)

        content = (
            f"Relationship: {parent} → {child}\n"
            f"Join type: {join_type}\n"
            f"Parent key: {', '.join(parent_keys)}\n"
            f"Child key: {', '.join(child_keys)}"
        )
        entries.append(KnowledgeEntry(
            id=_make_id("business_json", f"rel:{parent}:{child}"),
            source="business_json_template",
            content=content,
            category="relationships",
            metadata={
                "parent": parent,
                "child": child,
                "parent_keys": parent_keys,
                "child_keys": child_keys,
                "join_type": join_type,
            },
        ))


# ---------------------------------------------------------------------------
# 2. business_table_relation.txt — tree hierarchy
# ---------------------------------------------------------------------------

def load_business_relations(path: str) -> List[KnowledgeEntry]:
    """Parse ``business_table_relation.txt`` tree into KnowledgeEntry objects.

    Format:
        **TABLE_NAME** (join_keys)
        ├── **CHILD_TABLE** (join_keys)
        │   ├── **GRANDCHILD** (join_keys)
    """
    if not os.path.exists(path):
        logger.warning("Business table relations not found: %s", path)
        return []

    try:
        with open(path, "r") as f:
            lines = f.readlines()
    except Exception as exc:
        logger.error("Failed to read %s: %s", path, exc)
        return []

    entries: List[KnowledgeEntry] = []
    # Stack: [(indent_level, table_name)]
    parent_stack: List[tuple] = []

    for line in lines:
        stripped = line.rstrip()
        if not stripped or stripped.startswith("#"):
            continue

        # Extract table name and join keys
        match = re.search(r'\*\*(\w+)\*\*\s*\(([^)]*)\)', stripped)
        if not match:
            continue

        table_name = match.group(1)
        join_keys_raw = match.group(2).strip()
        join_keys = [k.strip() for k in join_keys_raw.split(",") if k.strip()]

        # Determine indent level (count leading non-alpha chars)
        indent = 0
        for ch in stripped:
            if ch.isalpha() or ch == '*':
                break
            indent += 1

        # Pop stack until we find the parent at a lesser indent
        while parent_stack and parent_stack[-1][0] >= indent:
            parent_stack.pop()

        parent_name = parent_stack[-1][1] if parent_stack else None

        content = f"Table hierarchy: {table_name}"
        if parent_name:
            content += f"\nParent: {parent_name}"
        content += f"\nJoin keys: {', '.join(join_keys)}"
        if parent_name:
            # Check for -> notation indicating different key names
            fk_parts = []
            for k in join_keys:
                if "->" in k:
                    local, remote = k.split("->", 1)
                    fk_parts.append(f"{table_name}.{local.strip()} = {parent_name}.{remote.strip()}")
                else:
                    fk_parts.append(f"{table_name}.{k} = {parent_name}.{k}")
            content += f"\nJoin condition: {' AND '.join(fk_parts)}"

        entries.append(KnowledgeEntry(
            id=_make_id("business_relations", f"hierarchy:{table_name}"),
            source="business_table_relation",
            content=content,
            category="relationships",
            metadata={
                "table_name": table_name,
                "parent": parent_name,
                "join_keys": join_keys,
            },
        ))

        parent_stack.append((indent, table_name))

    logger.info("Loaded %d hierarchy entries from: %s", len(entries), path)
    return entries


# ---------------------------------------------------------------------------
# 3. Generic text documents
# ---------------------------------------------------------------------------

def load_text_documents(docs_dir: str) -> List[KnowledgeEntry]:
    """Load any .txt files in docs/ not already handled by the specialized parsers."""
    _SKIP = {"business_json_template.txt", "business_table_relation.txt"}

    if not os.path.isdir(docs_dir):
        return []

    entries: List[KnowledgeEntry] = []
    for fname in sorted(os.listdir(docs_dir)):
        if not fname.endswith(".txt") or fname in _SKIP:
            continue
        fpath = os.path.join(docs_dir, fname)
        try:
            with open(fpath, "r") as f:
                content = f.read().strip()
            if not content:
                continue
            entries.append(KnowledgeEntry(
                id=_make_id("document", fname),
                source="document",
                content=content,
                category="business_rule",
                metadata={"filename": fname},
            ))
        except Exception as exc:
            logger.warning("Failed to load document %s: %s", fpath, exc)

    if entries:
        logger.info("Loaded %d text documents from: %s", len(entries), docs_dir)
    return entries


# ---------------------------------------------------------------------------
# Main loader: all sources at once
# ---------------------------------------------------------------------------

def load_all_business_knowledge(docs_dir: str = "docs") -> List[KnowledgeEntry]:
    """Load all business knowledge from the docs directory."""
    all_entries: List[KnowledgeEntry] = []

    json_path = os.path.join(docs_dir, "business_json_template.txt")
    all_entries.extend(load_business_json(json_path))

    rel_path = os.path.join(docs_dir, "business_table_relation.txt")
    all_entries.extend(load_business_relations(rel_path))

    all_entries.extend(load_text_documents(docs_dir))

    logger.info("Total business knowledge entries loaded: %d", len(all_entries))
    return all_entries
