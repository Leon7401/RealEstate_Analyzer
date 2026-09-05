""" /api/analysis/* — 判定・自動分析・クイック診断 """
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .deps import app_deps as D

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


@router.post("/run")
async def analysis_run(request: Request):
    """単体判定（既存 /api/analyze 互換ラッパ）"""
    return await D.analyze_property(request)


@router.post("/auto")
async def analysis_auto(request: Request):
    """自動分析→ランク反映（既存 /api/properties/auto-analyze）"""
    return await D.auto_analyze_properties(request)


@router.post("/quick-land")
async def analysis_quick_land(request: Request):
    """住所+面積のクイック診断"""
    return await D.quick_evaluate_land(request)


@router.get("/grade-palette")
async def analysis_grade_palette():
    from contracts import GRADE_PALETTE, ASSET_GRADE_PALETTE
    return JSONResponse(content={
        "investment": GRADE_PALETTE,
        "asset": ASSET_GRADE_PALETTE,
    })
