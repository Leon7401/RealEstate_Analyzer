""" /api/map/* — メッシュ・座標再検証・レイヤー """
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .deps import app_deps as D

router = APIRouter(prefix="/api/map", tags=["map"])


@router.post("/revalidate-coords")
async def map_revalidate_coords(request: Request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    limit = int(data.get("limit", 2000) or 2000)
    geocode_budget = int(data.get("geocode_budget", 200) or 200)
    dry_run = bool(data.get("dry_run", False))
    prefs = data.get("prefecture_codes") or data.get("prefectures")
    if isinstance(prefs, str):
        prefs = [p.strip() for p in prefs.split(",") if p.strip()]
    result = D.geo_service.revalidate_all(
        limit=limit,
        geocode_budget=geocode_budget,
        dry_run=dry_run,
        prefecture_codes=prefs,
    )
    return JSONResponse(content=result)


@router.get("/grade-palette")
async def map_grade_palette():
    from contracts import GRADE_PALETTE, ASSET_GRADE_PALETTE
    return JSONResponse(content={
        "investment": GRADE_PALETTE,
        "asset": ASSET_GRADE_PALETTE,
    })
