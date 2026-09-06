""" /api/listings/* — 一覧・詳細・エクスポート """
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .deps import app_deps as D

router = APIRouter(prefix="/api/listings", tags=["listings"])


@router.get("/properties")
async def listings_properties(
    include_land: bool = True,
    include_delisted: bool = False,
    sort_by: str = "updated_at",
    prefecture_code: str = "metro",
    station_filter: str = "",
    min_price: int = None,
    max_price: int = None,
    min_yield: float = None,
):
    """収益物件一覧（既存 /api/sample-properties 互換）"""
    return await D.get_sample_properties(
        include_land=include_land,
        include_delisted=include_delisted,
        sort_by=sort_by,
        prefecture_code=prefecture_code,
        station_filter=station_filter,
        min_price=min_price,
        max_price=max_price,
        min_yield=min_yield,
    )


@router.get("/scraped")
async def listings_scraped(limit: int = 200):
    props = D.db.get_properties(limit=limit)
    cleaned = []
    for p in props:
        p = dict(p)
        p.pop("data_json", None)
        cleaned.append(p)
    return JSONResponse(content={"count": len(cleaned), "properties": cleaned})
