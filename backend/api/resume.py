"""Base résumé API: upload (.tex / .pdf / .zip) → draft, analyse, save, previews."""
from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from config.profile import load_resume
from resume import texparse
from resume.compiler import available_engines, compile_document
from resume.ingest import (ASSETS_DIR, MAX_UPLOAD, RESUME_PDF, SOURCE_PDF, STAGED_ASSETS, STAGED_SOURCE_PDF, STAGING_DIR,
                           IngestError, ingest, list_assets, save_base)

router = APIRouter(prefix="/api/resume")


class TexBody(BaseModel):
    tex: str
    promote_staged: bool = False


def _err(status: int, message: str, compile: dict | None = None):
    raise HTTPException(status, {"message": message, "compile": compile})


@router.get("")
def get_resume():
    tex = load_resume()
    return {"tex": tex, "analysis": texparse.analyze(tex, list_assets(ASSETS_DIR)) if tex else None,
            "has_pdf": RESUME_PDF.exists(), "has_source_pdf": SOURCE_PDF.exists(),
            "assets": sorted(list_assets(ASSETS_DIR)), "engines": available_engines()}


@router.post("/upload")
async def upload(file: UploadFile = File(...)):
    data = await file.read(MAX_UPLOAD + 1)
    try:
        d = await ingest(file.filename or "", data)
    except IngestError as e:
        _err(422, str(e))
    return d.__dict__


@router.post("/analyze")
async def analyze(body: TexBody, compile: bool = False):
    try:
        out: dict = {"analysis": texparse.analyze(body.tex, list_assets(STAGED_ASSETS if body.promote_staged else ASSETS_DIR))}
    except Exception as e:                         # parser must never 500 on odd input
        _err(422, f"could not analyse: {e}")
    if compile:
        assets = STAGED_ASSETS if body.promote_staged and STAGED_ASSETS.exists() else ASSETS_DIR
        out["compile"] = (await compile_document(body.tex, STAGING_DIR / "check.pdf", assets_dir=assets)).to_dict()
    return out


async def _save(tex: str, promote: bool):
    if "\\documentclass" not in tex: _err(422, "not a LaTeX document (no \\documentclass)")
    res = await save_base(tex, promote)
    if not res.ok: _err(422, f"résumé does not compile — {res.summary()}", res.to_dict())
    return {"ok": True, "compile": res.to_dict(), "analysis": texparse.analyze(tex, list_assets(ASSETS_DIR))}


@router.put("")
async def put_resume(request: Request, file: UploadFile | None = File(None)):
    """JSON {tex, promote_staged} — or legacy multipart 'file' with raw .tex."""
    if file is not None:
        return await _save(texparse.decode_tex(await file.read(MAX_UPLOAD)), False)
    try:
        body = TexBody.model_validate(await request.json())
    except Exception:
        _err(400, "send JSON {tex, promote_staged} or a multipart 'file'")
    return await _save(body.tex, body.promote_staged)


@router.get("/pdf")
def pdf():
    if not RESUME_PDF.exists(): raise HTTPException(404)
    return FileResponse(RESUME_PDF, media_type="application/pdf")


@router.get("/draft.pdf")
def draft_pdf():
    p = STAGING_DIR / "draft.pdf"
    if not p.exists(): raise HTTPException(404)
    return FileResponse(p, media_type="application/pdf")


@router.get("/source.pdf")
def source_pdf(staged: bool = False):
    p = STAGED_SOURCE_PDF if staged else SOURCE_PDF
    if not p.exists(): raise HTTPException(404)
    return FileResponse(p, media_type="application/pdf")
