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
        while not self._stop_event.is_set():
            try:
                self._check_and_run()
            except Exception as e:
                logger.error(f"スケジューラエラー: {e}")

            # 次のチェックまで待機（stop_eventで即座に抜けられる）
            self._stop_event.wait(self.CHECK_INTERVAL_SEC)

        self._running = False
        logger.info("スケジューラループ終了")

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
                interval_hours = config.get("run_interval_hours", 24)
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

                for pref in pref_codes:
                    bp.run_land_pipeline(
                        source=config.get("source", "suumo"),
                        pref=pref,
                        price_min=config.get("price_min"),
                        price_max=config.get("price_max"),
                        area_min=config.get("area_min"),
                        walk_max=config.get("walk_max"),
                        max_pages=config.get("max_pages", 3),
                    )

                # 重複検出
                dupes = db.detect_duplicates()
                if dupes > 0:
                    logger.info(f"  重複検出: {dupes}件マーク")

                # 最終実行時刻を更新
                db.update_scrape_config_last_run(config["id"])

                logger.info(f"定期スクレイピング完了: {config.get('name', '')}")

            except Exception as e:
                logger.error(
                    f"定期スクレイピングエラー ({config.get('name', '')}): {e}"
                )


# シングルトンインスタンス
scheduler = ScrapeScheduler()
