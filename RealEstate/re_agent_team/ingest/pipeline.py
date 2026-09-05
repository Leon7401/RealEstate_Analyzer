"""scrape → dedupe → geo → judge の単一路線"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from contracts.listing import ListingDTO
from contracts.analysis import AnalysisResultDTO
from models.property import Property

logger = logging.getLogger(__name__)


class IngestPipeline:
    """
    取得パイプラインの単一エントリ。
    必ず: fetch → upsert → reconcile → dedupe → geo → (optional) judge
    """

    def __init__(
        self,
        db,
        *,
        adapters: Optional[dict] = None,
        geo_service=None,
        orchestrator=None,
        diversify_fn=None,
    ):
        self.db = db
        self.adapters = adapters or {}
        self.geo_service = geo_service
        self.orchestrator = orchestrator
        self.diversify_fn = diversify_fn

    def scrape_and_process(
        self,
        *,
        prefecture_codes: List[str],
        sources: List[str],
        max_pages: int = 10,
        split_by_price: bool = False,
        auto_judge: bool = True,
        analyze_limit: int = 80,
        geocode_budget: int = 80,
    ) -> Dict[str, Any]:
        props: List[Property] = []
        source_list = [s.strip().lower() for s in sources if s and s.strip()]
        for pref in prefecture_codes:
            for src in source_list:
                adapter = self.adapters.get(src)
                if adapter is None:
                    logger.warning("unknown source adapter: %s", src)
                    continue
                try:
                    fetched = adapter.fetch_list(
                        pref,
                        max_pages=max_pages,
                        split_by_price=split_by_price,
                    )
                    props.extend(fetched or [])
                except Exception as e:
                    logger.exception("fetch_list failed source=%s pref=%s: %s", src, pref, e)

        saved = 0
        for p in props:
            try:
                self.db.upsert_property(p.to_dict())
                saved += 1
            except Exception:
                pass

        try:
            self.db.reconcile_station_refs("properties", limit=10000)
        except Exception:
            pass

        dedupe_result = self.db.merge_duplicate_properties(
            dry_run=False,
            min_group_size=2,
            max_groups=5000,
        )

        geo_stats = {}
        if self.geo_service is not None:
            try:
                with self.db._conn() as conn:
                    pref_marks = ",".join(["?"] * len(prefecture_codes))
                    rows = [dict(r) for r in conn.execute(
                        f"SELECT * FROM properties WHERE prefecture_code IN ({pref_marks}) "
                        f"ORDER BY updated_at DESC LIMIT ?",
                        list(prefecture_codes) + [max(200, int(analyze_limit) * 3)],
                    ).fetchall()]
                geo_stats = self.geo_service.enrich_properties(
                    rows,
                    persist_updates=True,
                    geocode_budget=geocode_budget,
                )
            except Exception as e:
                logger.exception("geo enrich failed: %s", e)
                geo_stats = {"error": str(e)}

        analyzed = 0
        ranking_rows: List[dict] = []
        errors: List[str] = []
        if auto_judge and self.orchestrator is not None:
            with self.db._conn() as conn:
                qmarks = ",".join(["?"] * len(source_list)) if source_list else "?"
                pref_marks = ",".join(["?"] * len(prefecture_codes))
                params = list(prefecture_codes) + (source_list or ["rakumachi"]) + [max(1, int(analyze_limit))]
                rows = conn.execute(f"""
                    SELECT *
                    FROM properties
                    WHERE prefecture_code IN ({pref_marks})
                      AND source IN ({qmarks})
                      AND asking_price IS NOT NULL
                      AND asking_price > 0
                    ORDER BY updated_at DESC
                    LIMIT ?
                """, params).fetchall()

            analyzed_rows = []
            for r in rows:
                try:
                    prop = Property.from_dict(dict(r))
                    result = self.orchestrator.run(prop)
                    j = result.get("judgment")
                    analyzed += 1
                    dto = AnalysisResultDTO.from_judgment(
                        property_id=prop.id,
                        property_name=prop.name,
                        judgment=j,
                        scenario="as_is",
                    )
                    analyzed_rows.append({
                        "name": prop.name,
                        "grade": dto.selected.grade,
                        "score": dto.selected.score,
                        "recommendation": dto.selected.recommendation,
                        "latitude": prop.latitude,
                        "longitude": prop.longitude,
                        "city_code": prop.city_code,
                        "nearest_station": prop.nearest_station,
                        "address": prop.address,
                        "selected": dto.selected.to_dict(),
                    })
                except Exception as e:
                    errors.append(str(e))
                    continue

            analyzed_rows.sort(key=lambda x: (x.get("score") or 0.0), reverse=True)
            diversified = (
                self.diversify_fn(analyzed_rows)
                if self.diversify_fn
                else analyzed_rows
            )
            ranking_rows = [
                {
                    "rank": i + 1,
                    "name": x.get("name"),
                    "grade": x.get("grade"),
                    "score": x.get("score"),
                    "recommendation": x.get("recommendation"),
                    "selected": x.get("selected"),
                }
                for i, x in enumerate(diversified[:50])
            ]

        return {
            "count": len(props),
            "saved": saved,
            "prefecture_codes": prefecture_codes,
            "sources": source_list,
            "properties": [p.to_dict() for p in props],
            "dedupe": {
                "group_count": dedupe_result.get("group_count", 0),
                "merged_records": dedupe_result.get("merged_records", 0),
                "relinked_judgments": dedupe_result.get("relinked_judgments", 0),
            },
            "geo": geo_stats,
            "auto_judged": analyzed,
            "ranking": ranking_rows,
            "errors": errors[:10],
        }

    def ingest_url(
        self,
        url: str,
        *,
        use_ocr: bool = True,
        use_browser: bool = False,
        auto_analyze: bool = False,
        geocode_budget: int = 5,
    ) -> Dict[str, Any]:
        adapter = self.adapters.get("url")
        if adapter is None:
            return {"error": "url adapter not configured"}
        prop = adapter.parse_detail(url, use_ocr=use_ocr, use_browser=use_browser)
        if not prop:
            return {"error": "物件情報を取得できませんでした", "url": url, "status": "error"}

        listing = ListingDTO.from_property_dict(prop.to_dict())
        listing.quality.parsed_ok = True
        listing.quality.ocr_used = bool(use_ocr)

        self.db.upsert_property(prop.to_dict())
        dedupe_result = self.db.merge_duplicate_properties(
            dry_run=False,
            min_group_size=2,
            max_groups=5000,
        )

        analyze_target = prop
        try:
            with self.db._conn() as conn:
                row = conn.execute("""
                    SELECT * FROM properties
                    WHERE source_url = ?
                    ORDER BY updated_at DESC LIMIT 1
                """, (prop.source_url or "",)).fetchone()
            if row:
                analyze_target = Property.from_dict(dict(row))
                if self.geo_service is not None:
                    self.geo_service.enrich_properties(
                        [dict(row)],
                        persist_updates=True,
                        geocode_budget=geocode_budget,
                    )
        except Exception:
            pass

        result = {
            "status": "ok",
            "property": prop.to_dict(),
            "listing": listing.to_dict(),
            "saved_to_db": True,
            "dedupe": {
                "group_count": dedupe_result.get("group_count", 0),
                "merged_records": dedupe_result.get("merged_records", 0),
                "relinked_judgments": dedupe_result.get("relinked_judgments", 0),
            },
        }
        if auto_analyze and self.orchestrator is not None:
            try:
                analysis = self.orchestrator.run(analyze_target)
                judgment = analysis["judgment"]
                dto = AnalysisResultDTO.from_judgment(
                    property_id=analyze_target.id,
                    property_name=analyze_target.name,
                    judgment=judgment,
                )
                result["judgment"] = judgment.to_dict()
                result["analysis"] = dto.to_dict()
                result["summary"] = judgment.summary_text
                result["critic_review"] = analysis.get("critic_review", {})
            except Exception as e:
                result["analyze_error"] = str(e)
        return result
