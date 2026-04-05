"""
Prompts CRUD API
=================
GET  /api/prompts                           → list all prompt files with their content
PUT  /api/prompts/{name}                    → update a prompt file (with versioning)
GET  /api/prompts/export                    → download all prompt files as a ZIP
GET  /api/prompts/{name}/history            → list version history (newest first)
GET  /api/prompts/{name}/history/{version}  → get content of a specific version
POST /api/prompts/{name}/restore/{version}  → restore a version (saves + rebuilds)
"""
from __future__ import annotations

import io
import re
import zipfile

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from agent.prompts import (
    get_prompt_version,
    list_prompt_versions,
    list_prompts,
    load_prompt,
    save_prompt,
)

router = APIRouter(tags=["prompts"])

_NAME_RE = re.compile(r"^[a-z0-9_\-]+$")


def _valid_name(name: str) -> None:
    if not _NAME_RE.match(name):
        raise HTTPException(status_code=400, detail="Invalid prompt name")


class PromptUpdate(BaseModel):
    content: str


@router.get("/prompts")
def get_prompts():
    return {"prompts": list_prompts()}


@router.put("/prompts/{name}")
def update_prompt(name: str, body: PromptUpdate):
    _valid_name(name)
    save_prompt(name, body.content)
    return {"ok": True, "name": name}


@router.get("/prompts/export")
def export_prompts():
    prompts = list_prompts()
    if not prompts:
        raise HTTPException(status_code=404, detail="No prompts found")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in prompts:
            zf.writestr(f"prompts/{p['name']}.txt", p["content"])
    buf.seek(0)
    headers = {"Content-Disposition": "attachment; filename=prompts.zip"}
    return Response(content=buf.read(), media_type="application/zip", headers=headers)


@router.get("/prompts/{name}/history")
def get_prompt_history(name: str):
    """Return version history for a prompt (newest first, up to 30 entries)."""
    _valid_name(name)
    versions = list_prompt_versions(name)
    return {"name": name, "versions": versions, "persistence_enabled": len(versions) > 0 or True}


@router.get("/prompts/{name}/history/{version_id}")
def get_prompt_version_content(name: str, version_id: str):
    """Return the content of a specific historical version."""
    _valid_name(name)
    content = get_prompt_version(name, version_id)
    if content is None:
        raise HTTPException(status_code=404, detail=f"Version '{version_id}' not found for prompt '{name}'")
    return {"name": name, "version_id": version_id, "content": content}


@router.post("/prompts/{name}/restore/{version_id}")
async def restore_prompt_version(name: str, version_id: str, request: Request):
    """
    Restore a specific historical version of a prompt.
    Saves it as the current version (creating a new history entry) and triggers
    a pipeline rebuild so the restored prompt takes effect immediately.
    """
    _valid_name(name)
    content = get_prompt_version(name, version_id)
    if content is None:
        raise HTTPException(status_code=404, detail=f"Version '{version_id}' not found for prompt '{name}'")

    # Save as new current (this also creates a new version entry in history)
    save_prompt(name, content)

    # Trigger pipeline rebuild so restored prompt takes effect immediately
    rebuilt = False
    try:
        app = request.app
        if hasattr(app.state, "pipeline") and hasattr(app.state, "graph"):
            from agent.pipeline import build_pipeline
            config = app.state.config
            from agent.llm import get_llm
            try:
                llm = get_llm(config)
            except Exception:
                llm = None
            app.state.pipeline = build_pipeline(app.state.graph.graph, config, llm)
            rebuilt = True
    except Exception:
        pass

    return {"ok": True, "name": name, "restored_version": version_id, "pipeline_rebuilt": rebuilt}
