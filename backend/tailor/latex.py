"""Backwards-compatible shim over resume.compiler."""
from __future__ import annotations
from pathlib import Path
from resume.compiler import compile_document, page_count, available_engines  # noqa: F401


def find_compiler() -> list[str] | None:
    e = available_engines()
    return [e[0]] if e else None


async def compile_tex(tex: str, out_pdf: Path, timeout: float = 120, assets_dir: Path | None = None) -> tuple[bool, str]:
    r = await compile_document(tex, out_pdf, assets_dir=assets_dir, timeout=timeout)
    return r.ok, r.summary() if not r.ok else ""
