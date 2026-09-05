""" /api/ingest/* — スクレイプ・重複統合・URL取込 """
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .deps import app_deps as D

router = APIRouter(prefix="/api/ingest", tags=["ingest"])


@router.get("/scrape")
async def ingest_scrape(
    prefecture_code: str = "13",
    sources: str = "rakumachi,kenbiya,rals",
    max_pages: int = 10,
    split_by_price: bool = False,
    auto_judge: bool = True,
    analyze_limit: int = 80,
):
    """スクレイプ → 重複統合 → 座標検証 → 自動判定"""
    try:
        pref_codes = D._expand_prefecture_codes(prefecture_code) or ["13"]
        source_list = [s.strip() for s in sources.split(",") if s.strip()]
        result = D.ingest_pipeline.scrape_and_process(
            prefecture_codes=pref_codes,
            sources=source_list,
            max_pages=max_pages,
            split_by_price=split_by_price,
            auto_judge=auto_judge,
            analyze_limit=analyze_limit,
        )
        return JSONResponse(content=result)
    except Exception as e:
        return JSONResponse(
            content={"error": str(e), "count": 0, "properties": []},
            status_code=500,
        )


@router.post("/scrape-url")
async def ingest_scrape_url(request: Request):
    data = await request.json()
    url = (data.get("url") or "").strip()
    if not url:
        return JSONResponse(content={"error": "URLが指定されていません"}, status_code=400)
    try:
        result = D.ingest_pipeline.ingest_url(
            url,
            use_ocr=data.get("use_ocr", True),
            use_browser=data.get("use_browser", False),
            auto_analyze=data.get("auto_analyze", False),
        )
        if result.get("error") and result.get("status") != "ok":
            code = 422 if "取得できません" in str(result.get("error")) else 500
            return JSONResponse(content=result, status_code=code)
        return JSONResponse(content=result)
    except Exception as e:
        import traceback
        return JSONResponse(
            content={"error": str(e), "traceback": traceback.format_exc()},
            status_code=500,
        )


@router.post("/dedupe")
async def ingest_dedupe(request: Request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    dry_run = bool(data.get("dry_run", False))
    result = D.db.merge_duplicate_properties(
        dry_run=dry_run,
        min_group_size=int(data.get("min_group_size", 2) or 2),
        max_groups=int(data.get("max_groups", 5000) or 5000),
    )
    return JSONResponse(content=result)
