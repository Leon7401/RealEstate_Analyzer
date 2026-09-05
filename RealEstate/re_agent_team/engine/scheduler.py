"""
定期スクレイピングスケジューラ

scrape_configs テーブルのアクティブな設定を定期チェックし、
前回実行から一定時間経過したものを自動実行する。
"""
import json
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Optional
import requests
from config.settings import (
    BATCH_TARGET_PREFECTURES,
    RENTAL_REFRESH_INTERVAL_HOURS,
    RENTAL_REFRESH_MAX_PAGES,
    RENTAL_REFRESH_GEOCODE_BATCH,
    PROPERTY_REFRESH_INTERVAL_HOURS,
    PROPERTY_REFRESH_MAX_PAGES,
    PROPERTY_ANALYZE_LIMIT,
    PROPERTY_ANALYZE_INCLUDE_REBUILD,
    LISTING_VERIFY_INTERVAL_HOURS,
    LISTING_VERIFY_BATCH,
    LISTING_VERIFY_STALE_HOURS,
    LISTING_VERIFY_CONFIRM_FAILURES,
)

logger = logging.getLogger("Scheduler")


class ScrapeScheduler:
    """バックグラウンドで定期スクレイピングを実行"""

    CHECK_INTERVAL_SEC = 300  # 5分ごとにチェック

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._running = False

    def start(self):
        """スケジューラをバックグラウンドで開始"""
        if self._running:
            logger.warning("スケジューラは既に実行中")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._running = True
        logger.info("スケジューラ開始")

    def stop(self):
        """スケジューラを停止"""
        self._stop_event.set()
        self._running = False
        logger.info("スケジューラ停止")

    @property
    def is_running(self) -> bool:
        return self._running

    def _run_loop(self):
        """メインループ: CHECK_INTERVAL_SEC ごとに設定をチェック"""
        growth_last_run = None
        GROWTH_INTERVAL_HOURS = 6  # 6時間ごとにデータ成長パイプライン
        rental_last_run = None
        property_last_run = None
        listing_verify_last_run = None

        while not self._stop_event.is_set():
            try:
                self._check_and_run()
            except Exception as e:
                logger.error(f"スケジューラエラー: {e}")

            # 賃料データ高頻度リフレッシュ（品質維持）
            try:
                now = datetime.now()
                if rental_last_run is None or (now - rental_last_run) >= timedelta(hours=RENTAL_REFRESH_INTERVAL_HOURS):
                    self._run_rental_refresh()
                    rental_last_run = now
            except Exception as e:
                logger.error(f"賃料リフレッシュエラー: {e}")

            # 既存建物物件スクレイピング + 現況/建替え比較分析
            try:
                now = datetime.now()
                if property_last_run is None or (now - property_last_run) >= timedelta(hours=PROPERTY_REFRESH_INTERVAL_HOURS):
                    self._run_property_refresh_and_analysis()
                    property_last_run = now
            except Exception as e:
                logger.error(f"既存建物分析パイプラインエラー: {e}")

            # データ成長パイプライン（6時間ごと）
            try:
                now = datetime.now()
                if growth_last_run is None or (now - growth_last_run) >= timedelta(hours=GROWTH_INTERVAL_HOURS):
                    self._run_growth_pipeline()
                    growth_last_run = now
            except Exception as e:
                logger.error(f"成長パイプラインエラー: {e}")

            # 掲載有無チェック（消失物件をdelisted化）
            try:
                now = datetime.now()
                if listing_verify_last_run is None or (now - listing_verify_last_run) >= timedelta(hours=LISTING_VERIFY_INTERVAL_HOURS):
                    self._run_listing_source_verification()
                    listing_verify_last_run = now
            except Exception as e:
                logger.error(f"掲載有無チェックエラー: {e}")

            self._stop_event.wait(self.CHECK_INTERVAL_SEC)

        self._running = False
        logger.info("スケジューラループ終了")

    @staticmethod
    def _check_source_alive(url: str) -> tuple[bool, Optional[int], str]:
        if not url:
            return False, None, "no_url"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            )
        }
        try:
            r = requests.head(url, allow_redirects=True, timeout=12, headers=headers)
            status = int(r.status_code)
            if status in (403, 405):
                r = requests.get(url, allow_redirects=True, timeout=15, headers=headers, stream=True)
                status = int(r.status_code)
            alive = 200 <= status < 400
            return alive, status, ""
        except Exception as e:
            return False, None, str(e)

    def _run_listing_source_verification(self):
        """掲載URLの生存確認を定期実行し、消失物件をdelisted化"""
        from storage.database import Database

        db = Database()
        logger.info("=== 定期掲載有無チェック開始 ===")
        summary = {}
        for table in ("properties", "land_listings"):
            rows = db.get_source_verification_targets(
                table=table,
                limit=max(1, int(LISTING_VERIFY_BATCH)),
                stale_hours=max(1, int(LISTING_VERIFY_STALE_HOURS)),
            )
            checked = 0
            alive_cnt = 0
            fail_cnt = 0
            for row in rows:
                checked += 1
                alive, status, note = self._check_source_alive(str(row.get("source_url") or ""))
                if alive:
                    alive_cnt += 1
                else:
                    fail_cnt += 1
                db.record_source_verification_result(
                    table=table,
                    row_id=row.get("id"),
                    is_alive=alive,
                    http_status=status,
                    note=note,
                    confirm_failures=max(1, int(LISTING_VERIFY_CONFIRM_FAILURES)),
                )
            summary[table] = {"checked": checked, "alive": alive_cnt, "failed": fail_cnt}
            logger.info(
                f"  {table}: checked={checked}, alive={alive_cnt}, failed={fail_cnt}"
            )
        logger.info(f"=== 定期掲載有無チェック完了: {summary} ===")

    def _run_growth_pipeline(self):
        """メッシュデータ成長パイプラインを実行"""
        logger.info("=== 定期データ成長パイプライン開始 ===")
        try:
            from engine.batch_processor import MeshGrowthPipeline
            pipeline = MeshGrowthPipeline()
            pipeline.run(max_rental_pages=3, geocode_batch=100)
        except Exception as e:
            logger.error(f"成長パイプラインエラー: {e}")

    def _run_rental_refresh(self):
        """賃料収集・ジオコードを高頻度で回す"""
        logger.info("=== 定期賃料リフレッシュ開始 ===")
        try:
            from engine.batch_processor import MeshGrowthPipeline
            pipeline = MeshGrowthPipeline()
            pipeline.run(
                prefectures=BATCH_TARGET_PREFECTURES,
                max_rental_pages=RENTAL_REFRESH_MAX_PAGES,
                geocode_batch=RENTAL_REFRESH_GEOCODE_BATCH,
            )
        except Exception as e:
            logger.error(f"定期賃料リフレッシュエラー: {e}")

    @staticmethod
    def _build_rebuild_candidate(base: dict) -> Optional[dict]:
        """既存建物物件から建替えシナリオ入力を生成"""
        try:
            land_area = float(base.get("land_area") or 0)
            current_bldg = float(base.get("building_area") or 0)
            age = int(base.get("building_age") or 0)
            if land_area <= 0 or current_bldg <= 0 or age < 10:
                return None

            far_ratio = 2.0
            if base.get("floor_area_ratio"):
                far_raw = float(base.get("floor_area_ratio"))
                far_ratio = far_raw if far_raw <= 5 else far_raw / 100
            far_ratio = max(0.8, min(4.5, far_ratio))

            target_area = max(current_bldg * 1.12, land_area * min(1.8, far_ratio * 0.90))
            target_area = max(current_bldg, target_area)

            structure = base.get("structure") or "RC"
            unit_cost = {"木造": 220000, "鉄骨": 280000, "RC": 300000, "SRC": 340000}.get(structure, 280000)
            demolition = int(current_bldg * 35000)
            rebuild_cost = int(target_area * unit_cost + demolition)

            rebuilt = dict(base)
            rebuilt["name"] = f"{base.get('name') or '物件'} (建替想定)"
            rebuilt["built_year"] = datetime.now().year
            rebuilt["building_age"] = 0
            rebuilt["building_area"] = target_area
            rebuilt["asking_price"] = int((base.get("asking_price") or 0) + rebuild_cost)
            if base.get("current_rent_annual"):
                rebuilt["current_rent_annual"] = int(float(base["current_rent_annual"]) * 1.15)
            return rebuilt
        except Exception:
            return None

    @staticmethod
    def _choose_scenario(as_is: dict, rebuild: Optional[dict]) -> dict:
        """出口性を重視して現況/建替えの採用シナリオを決定"""
        if not rebuild:
            return as_is

        as_score = as_is["judgment"].overall_score
        rb_score = rebuild["judgment"].overall_score
        as_sim = as_is.get("simulation")

        # 出口が見えやすく、数年保有が有利なら現況を優先
        if as_sim:
            hold_roi = getattr(as_sim, "hold_sell_roi_65", 0.0) or 0.0
            if hold_roi >= 0.25 and as_score + 3.0 >= rb_score:
                return as_is

        return rebuild if rb_score > as_score else as_is

    def _run_property_refresh_and_analysis(self):
        """既存建物スクレイピングを定期実行し、現況/建替え比較で自動判定"""
        logger.info("=== 定期既存建物スクレイピング + 比較分析開始 ===")
        try:
            from agents.scraper_agent import ScraperAgent
            from agents.orchestrator_agent import OrchestratorAgent
            from models.property import Property
            from storage.database import Database

            scraper = ScraperAgent()
            orchestrator = OrchestratorAgent()
            db = Database()

            all_props = []
            for pref in BATCH_TARGET_PREFECTURES:
                try:
                    props = scraper.run(prefecture_code=pref, max_pages=PROPERTY_REFRESH_MAX_PAGES)
                    all_props.extend(props)
                    logger.info(f"  既存建物収集: pref={pref}, {len(props)}件")
                except Exception as e:
                    logger.warning(f"  既存建物収集エラー: pref={pref}, {e}")

            if not all_props:
                logger.info("  既存建物収集: 0件")
                return

            # 最新物件を保存
            for p in all_props:
                try:
                    db.upsert_property(p.to_dict())
                except Exception:
                    continue

            # スクレイピング直後に重複統合してから判定
            dedupe_result = db.merge_duplicate_properties(
                dry_run=False,
                min_group_size=2,
                max_groups=5000,
            )
            logger.info(
                f"  重複統合: groups={dedupe_result.get('group_count',0)} "
                f"merged={dedupe_result.get('merged_records',0)} "
                f"relinked={dedupe_result.get('relinked_judgments',0)}"
            )

            # 座標品質検証（住所ジオコード優先・都県境界チェック）
            try:
                from services.geo_quality import GeoQualityService
                geo = GeoQualityService(db=db)
                with db._conn() as conn:
                    geo_rows = [dict(r) for r in conn.execute("""
                        SELECT * FROM properties
                        WHERE prefecture_code IN ('13','14','11','12')
                        ORDER BY updated_at DESC LIMIT 500
                    """).fetchall()]
                geo_stats = geo.enrich_properties(
                    geo_rows, persist_updates=True, geocode_budget=80
                )
                logger.info(
                    f"  座標検証: updated={geo_stats.get('updated',0)} "
                    f"corrected={geo_stats.get('corrected',0)} "
                    f"estimated={geo_stats.get('estimated',0)}"
                )
            except Exception as e:
                logger.warning(f"  座標検証スキップ: {e}")

            # 価格がある物件を優先して判定（統合後のDBから取得）
            with db._conn() as conn:
                rows = conn.execute("""
                    SELECT *
                    FROM properties
                    WHERE prefecture_code IN ('13','14','11','12')
                      AND COALESCE(listing_status, 'active') = 'active'
                      AND source IN ('楽待', '健美家', '不動産投資連合隊', 'rakumachi', 'kenbiya', 'rals')
                      AND asking_price IS NOT NULL
                      AND asking_price > 0
                    ORDER BY asking_price DESC, updated_at DESC
                    LIMIT ?
                """, (max(1, PROPERTY_ANALYZE_LIMIT),)).fetchall()
            candidates = [Property.from_dict(dict(r)) for r in rows]

            judged = 0
            for prop in candidates:
                try:
                    as_is = orchestrator.run(prop)
                    selected = as_is

                    if PROPERTY_ANALYZE_INCLUDE_REBUILD:
                        rebuild_input = self._build_rebuild_candidate(prop.to_dict())
                        if rebuild_input:
                            rebuild_prop = Property.from_dict(rebuild_input)
                            rebuild = orchestrator.run(rebuild_prop)
                            selected = self._choose_scenario(as_is, rebuild)

                    judged += 1
                    logger.info(
                        f"  自動判定: {prop.name} -> {selected['judgment'].grade} "
                        f"({selected['judgment'].overall_score:.1f})"
                    )
                except Exception as e:
                    logger.warning(f"  自動判定エラー: {prop.name}, {e}")

            logger.info(f"=== 定期既存建物スクレイピング + 比較分析完了: 収集{len(all_props)}件 / 判定{judged}件 ===")
        except Exception as e:
            logger.error(f"定期既存建物処理エラー: {e}")

    def _check_and_run(self):
        """アクティブな設定をチェックし、実行タイミングのものを処理"""
        from storage.database import Database
        from engine.batch_processor import BatchProcessor

        db = Database()
        configs = db.get_scrape_configs(active_only=True)

        if not configs:
            return

        now = datetime.now()
        bp = BatchProcessor()

        for config in configs:
            try:
                interval_hours = int(config.get("run_interval_hours") or 24)
                last_run = config.get("last_run_at")

                # 前回実行からの経過チェック
                if last_run:
                    last_dt = datetime.fromisoformat(last_run)
                    if now - last_dt < timedelta(hours=interval_hours):
                        continue  # まだ実行タイミングでない

                logger.info(
                    f"定期スクレイピング実行: {config.get('name', '')} "
                    f"(source={config.get('source', 'suumo')})"
                )

                # パイプライン実行
                pref_codes = config.get("prefecture_codes", ["13"])
                if isinstance(pref_codes, str):
                    pref_codes = json.loads(pref_codes)

                total_saved = 0
                for pref in pref_codes:
                    out = bp.run_land_pipeline(
                        source=config.get("source", "suumo"),
                        pref=pref,
                        price_min=config.get("price_min"),
                        price_max=config.get("price_max"),
                        area_min=config.get("area_min"),
                        walk_max=config.get("walk_max"),
                        max_pages=config.get("max_pages", 3),
                    ) or {}
                    total_saved += int(out.get("listings_saved") or 0)

                # 重複検出
                dupes = db.detect_duplicates()
                if dupes > 0:
                    logger.info(f"  重複検出: {dupes}件マーク")

                # 結果が少なければページ数を増やし、多すぎれば抑える（自己改善）
                try:
                    db.tune_scrape_config_max_pages(config["id"], total_saved)
                except Exception as e:
                    logger.warning(f"  設定チューニング失敗: {e}")

                # 最終実行時刻を更新
                db.update_scrape_config_last_run(config["id"])

                logger.info(f"定期スクレイピング完了: {config.get('name', '')}")

            except Exception as e:
                logger.error(
                    f"定期スクレイピングエラー ({config.get('name', '')}): {e}"
                )


# シングルトンインスタンス
scheduler = ScrapeScheduler()
