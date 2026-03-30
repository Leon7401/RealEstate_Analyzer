"""
大量データ収集エンジン - 全ソース×全エリア×全価格帯でスクレイピング

使い方:
    python -m engine.data_collector

使い方:
    python -m engine.data_collector
"""
import sys
import logging
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.batch_processor import BatchProcessor
from storage.database import Database

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("DataCollector")


# 収集設定
SOURCES = ["suumo"]  # athome, homes, rakumachi are less reliable
PREFECTURES = ["13", "14", "11", "12"]  # 一都三県
PRICE_RANGES = [
    (None, 1000),
    (1000, 2000),
    (2000, 3000),
    (3000, 4000),
    (4000, 5000),
    (5000, 7000),
    (7000, 10000),
    # (min万円, max万円)
    (10000, None),
]
MAX_PAGES_PER_SEARCH = 5


def run_collection():
    """全パターンでスクレイピングを実行"""
    bp = BatchProcessor()
    db = Database()

    initial_count = db.count_land_listings()
    logger.info(f"=== データ大量収集開始 (現在: {initial_count}件) ===")

    total_new = 0

    for source in SOURCES:
        for pref in PREFECTURES:
            for price_min, price_max in PRICE_RANGES:
                label = f"{source}/{pref}/{price_min or 0}-{price_max or '∞'}万"
                logger.info(f"  収集中: {label}")

                try:
                    result = bp.run_land_pipeline(
                        source=source,
                        pref=pref,
                        price_min=price_min,
                        price_max=price_max,
                        max_pages=MAX_PAGES_PER_SEARCH,
                    )
                    new = result.get("listings_saved", 0)
                    total_new += new
                    logger.info(f"    => {new}件取得")
                except Exception as e:
                    logger.error(f"    エラー: {e}")

                # Rate limiting between searches
                time.sleep(2)

    # API実データ取得（キーがある場合）
    if bp.api.is_configured():
        logger.info("=== API実データ取得 ===")
        tx = bp.ingest_real_transactions()
        lp = bp.ingest_real_land_prices()
        logger.info(f"API実データ: 取引{tx}件, 地価{lp}件")

        # メトリクス再計算
        for pref in PREFECTURES:
            bp.compute_station_metrics(pref)

    # 重複検出
    dupes = db.detect_duplicates()
    logger.info(f"重複検出: {dupes}件マーク")

    # 駅統計データ収集（乗降客数・空室率）
    try:
        from data.station_stats_collector import StationStatsCollector
        collector = StationStatsCollector()
        collector.collect_all(db)
    except Exception as e:
        logger.error(f"駅統計収集エラー: {e}")

    final_count = db.count_land_listings()
    logger.info(f"=== 収集完了: {initial_count} → {final_count}件 (+{total_new}) ===")

    return total_new


if __name__ == "__main__":
    run_collection()
