"""
定期スクレイピングスケジューラ

起動時に自動開始し、収益物件を IngestPipeline で取得して DB 保存する。
土地用 scrape_configs があれば土地パイプラインも実行する。
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from config.settings import (
    BATCH_TARGET_PREFECTURES,
    RENTAL_REFRESH_INTERVAL_HOURS,
    RENTAL_REFRESH_MAX_PAGES,
    RENTAL_REFRESH_GEOCODE_BATCH,
    PROPERTY_REFRESH_INTERVAL_HOURS,
    PROPERTY_REFRESH_MAX_PAGES,
)

logger = logging.getLogger("Scheduler")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str, default: List[str]) -> List[str]:
    raw = os.getenv(name, "")
    if not raw.strip():
        return list(default)
    return [x.strip() for x in raw.split(",") if x.strip()]


class ScrapeScheduler:
    """バックグラウンドで定期スクレイピングを実行"""

    CHECK_INTERVAL_SEC = int(os.getenv("RE_SCHEDULER_CHECK_SEC", "300"))

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._running = False
        self._lock = threading.Lock()
        self._pipeline = None
        self.status: Dict[str, Any] = {
            "running": False,
            "started_at": None,
            "last_tick_at": None,
            "property": {
                "enabled": True,
                "last_run_at": None,
                "last_result": None,
                "last_error": None,
                "next_run_at": None,
            },
            "land": {
                "last_run_at": None,
                "last_result": None,
                "last_error": None,
                "configs": 0,
            },
            "rental": {"last_run_at": None, "last_error": None},
            "growth": {"last_run_at": None, "last_error": None},
        }

    def set_pipeline(self, pipeline) -> None:
        """app 起動時に IngestPipeline を注入（循環import回避）"""
        self._pipeline = pipeline

    def start(self) -> None:
        if self._running:
            logger.warning("スケジューラは既に実行中")
            return

        self._ensure_default_land_config()
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="scrape-scheduler"
        )
        self._thread.start()
        self._running = True
        self.status["running"] = True
        self.status["started_at"] = datetime.now().isoformat(timespec="seconds")
        logger.info("スケジューラ開始")

    def stop(self) -> None:
        self._stop_event.set()
        self._running = False
        self.status["running"] = False
        logger.info("スケジューラ停止")

    @property
    def is_running(self) -> bool:
        return self._running

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            out = json.loads(json.dumps(self.status, ensure_ascii=False, default=str))
        out["running"] = self._running
        return out

    def _ensure_default_land_config(self) -> None:
        try:
            from storage.database import Database

            db = Database()
            existing = db.get_scrape_configs(active_only=False)
            if existing:
                self.status["land"]["configs"] = len(
                    db.get_scrape_configs(active_only=True)
                )
                return
            db.save_scrape_config(
                {
                    "name": "自動: 一都三県 土地(SUUMO)",
                    "source": "suumo",
                    "prefecture_codes": list(BATCH_TARGET_PREFECTURES),
                    "max_pages": 2,
                    "run_interval_hours": 12,
                }
            )
            self.status["land"]["configs"] = 1
            logger.info("デフォルト土地スクレイプ設定を作成しました")
        except Exception as e:
            logger.warning("デフォルト土地設定の作成に失敗: %s", e)

    def _run_loop(self) -> None:
        property_enabled = _env_bool("RE_AUTO_PROPERTY_SCRAPE", True)
        property_interval_h = float(
            os.getenv(
                "RE_AUTO_PROPERTY_INTERVAL_HOURS",
                str(PROPERTY_REFRESH_INTERVAL_HOURS or 6),
            )
        )
        self.status["property"]["enabled"] = property_enabled

        property_last_run: Optional[datetime] = None
        rental_last_run: Optional[datetime] = None
        growth_last_run: Optional[datetime] = None
        first = True

        while not self._stop_event.is_set():
            now = datetime.now()
            self.status["last_tick_at"] = now.isoformat(timespec="seconds")

            # 1) 収益物件（本命・ボタン不要）
            try:
                due = property_enabled and (
                    first
                    or property_last_run is None
                    or (now - property_last_run)
                    >= timedelta(hours=property_interval_h)
                )
                if due:
                    self._run_property_ingest()
                    property_last_run = datetime.now()
                if property_enabled and property_last_run is not None:
                    self.status["property"]["next_run_at"] = (
                        property_last_run + timedelta(hours=property_interval_h)
                    ).isoformat(timespec="seconds")
            except Exception as e:
                logger.exception("収益物件自動取得エラー: %s", e)
                self.status["property"]["last_error"] = str(e)

            # 2) 土地 scrape_configs
            try:
                self._check_and_run_land_configs()
            except Exception as e:
                logger.error("土地スケジューラエラー: %s", e)
                self.status["land"]["last_error"] = str(e)

            # 3) 賃料・成長は起動直後を避け、後続サイクルで実行
            if not first:
                try:
                    if rental_last_run is None or (
                        now - rental_last_run
                    ) >= timedelta(hours=RENTAL_REFRESH_INTERVAL_HOURS):
                        self._run_rental_refresh()
                        rental_last_run = datetime.now()
                        self.status["rental"]["last_run_at"] = (
                            rental_last_run.isoformat(timespec="seconds")
                        )
                        self.status["rental"]["last_error"] = None
                except Exception as e:
                    logger.error("賃料リフレッシュエラー: %s", e)
                    self.status["rental"]["last_error"] = str(e)

                try:
                    if growth_last_run is None or (
                        now - growth_last_run
                    ) >= timedelta(hours=6):
                        self._run_growth_pipeline()
                        growth_last_run = datetime.now()
                        self.status["growth"]["last_run_at"] = (
                            growth_last_run.isoformat(timespec="seconds")
                        )
                        self.status["growth"]["last_error"] = None
                except Exception as e:
                    logger.error("成長パイプラインエラー: %s", e)
                    self.status["growth"]["last_error"] = str(e)

            first = False
            self._stop_event.wait(self.CHECK_INTERVAL_SEC)

        self._running = False
        self.status["running"] = False
        logger.info("スケジューラループ終了")

    def _get_pipeline(self):
        if self._pipeline is not None:
            return self._pipeline
        from storage.database import Database
        from ingest.pipeline import IngestPipeline
        from ingest.adapters.registry import register_default_adapters
        from agents.scraper_agent import ScraperAgent
        from agents.url_scraper_agent import UrlScraperAgent
        from services.geo_quality import GeoQualityService

        db = Database()
        adapters = register_default_adapters(ScraperAgent(), UrlScraperAgent())
        self._pipeline = IngestPipeline(
            db,
            adapters=adapters,
            geo_service=GeoQualityService(db=db),
        )
        return self._pipeline

    def _run_property_ingest(self) -> None:
        sources = _env_list("RE_AUTO_PROPERTY_SOURCES", ["rals", "rakumachi", "kenbiya", "athome"])
        prefs = _env_list("RE_AUTO_PROPERTY_PREFS", list(BATCH_TARGET_PREFECTURES))
        max_pages = int(
            os.getenv(
                "RE_AUTO_PROPERTY_MAX_PAGES",
                str(max(1, min(int(PROPERTY_REFRESH_MAX_PAGES or 3), 5))),
            )
        )
        analyze_limit = int(os.getenv("RE_AUTO_PROPERTY_ANALYZE_LIMIT", "40"))

        logger.info(
            "=== 自動収益物件スクレイプ開始 sources=%s prefs=%s pages=%s ===",
            sources,
            prefs,
            max_pages,
        )

        pipeline = self._get_pipeline()
        result = pipeline.scrape_and_process(
            prefecture_codes=prefs,
            sources=sources,
            max_pages=max_pages,
            split_by_price=False,
            auto_judge=True,
            analyze_limit=analyze_limit,
        )
        summary = {
            "count": result.get("count", 0),
            "saved": result.get("saved", result.get("count", 0)),
            "auto_judged": result.get("auto_judged", 0),
            "sources": sources,
            "prefecture_codes": prefs,
            "source_errors": result.get("source_errors") or {},
            "message": result.get("message"),
        }
        with self._lock:
            self.status["property"]["last_run_at"] = datetime.now().isoformat(
                timespec="seconds"
            )
            self.status["property"]["last_result"] = summary
            self.status["property"]["last_error"] = None
        logger.info("=== 自動収益物件スクレイプ完了: %s ===", summary)

    def _check_and_run_land_configs(self) -> None:
        from storage.database import Database
        from engine.batch_processor import BatchProcessor

        db = Database()
        configs = db.get_scrape_configs(active_only=True)
        self.status["land"]["configs"] = len(configs)
        if not configs:
            return

        now = datetime.now()
        bp = BatchProcessor()

        for config in configs:
            try:
                interval_hours = int(config.get("run_interval_hours") or 24)
                last_run = config.get("last_run_at")
                if last_run:
                    try:
                        last_dt = datetime.fromisoformat(str(last_run))
                        if now - last_dt < timedelta(hours=interval_hours):
                            continue
                    except ValueError:
                        pass

                logger.info(
                    "定期土地スクレイピング実行: %s (source=%s)",
                    config.get("name", ""),
                    config.get("source", "suumo"),
                )

                pref_codes = config.get("prefecture_codes") or ["13"]
                if isinstance(pref_codes, str):
                    pref_codes = json.loads(pref_codes)

                total_saved = 0
                for pref in pref_codes:
                    out = (
                        bp.run_land_pipeline(
                            source=str(config.get("source") or "suumo").lower(),
                            pref=str(pref),
                            price_min=config.get("price_min"),
                            price_max=config.get("price_max"),
                            area_min=config.get("area_min"),
                            walk_max=config.get("walk_max"),
                            max_pages=int(config.get("max_pages") or 3),
                        )
                        or {}
                    )
                    total_saved += int(
                        out.get("listings_saved")
                        or out.get("saved")
                        or out.get("count")
                        or 0
                    )

                try:
                    db.tune_scrape_config_max_pages(config["id"], total_saved)
                except Exception as e:
                    logger.warning("設定チューニング失敗: %s", e)
                db.update_scrape_config_last_run(config["id"])

                with self._lock:
                    self.status["land"]["last_run_at"] = now.isoformat(timespec="seconds")
                    self.status["land"]["last_result"] = {
                        "config": config.get("name"),
                        "saved": total_saved,
                    }
                    self.status["land"]["last_error"] = None
                logger.info(
                    "定期土地スクレイピング完了: %s saved=%s",
                    config.get("name"),
                    total_saved,
                )
            except Exception as e:
                logger.error(
                    "定期土地スクレイピングエラー (%s): %s",
                    config.get("name"),
                    e,
                )
                self.status["land"]["last_error"] = str(e)

    def _run_growth_pipeline(self) -> None:
        logger.info("=== 定期データ成長パイプライン開始 ===")
        from engine.batch_processor import MeshGrowthPipeline

        MeshGrowthPipeline().run(max_rental_pages=2, geocode_batch=50)

    def _run_rental_refresh(self) -> None:
        logger.info("=== 定期賃料リフレッシュ開始 ===")
        from engine.batch_processor import MeshGrowthPipeline

        MeshGrowthPipeline().run(
            prefectures=BATCH_TARGET_PREFECTURES,
            max_rental_pages=min(3, int(RENTAL_REFRESH_MAX_PAGES or 3)),
            geocode_batch=min(200, int(RENTAL_REFRESH_GEOCODE_BATCH or 200)),
        )


scheduler = ScrapeScheduler()
