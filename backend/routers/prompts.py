"""
Prompts CRUD API
=================
GET  /api/prompts           → list all prompt files with their content
PUT  /api/prompts/{name}    → update a prompt file
GET  /api/prompts/export    → download all prompt files as a ZIP
"""
from __future__ import annotations

import io
import zipfile

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from agent.prompts import list_prompts, load_prompt, save_prompt

router = APIRouter(tags=["prompts"])


class PromptUpdate(BaseModel):
    content: str


@router.get("/prompts")
def get_prompts():
    return {"prompts": list_prompts()}


@router.put("/prompts/{name}")
def update_prompt(name: str, body: PromptUpdate):
    # Validate name (alphanumeric, underscores, hyphens only)
    import re
    if not re.match(r"^[a-z0-9_\-]+$", name):
        raise HTTPException(status_code=400, detail="Invalid prompt name")
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
