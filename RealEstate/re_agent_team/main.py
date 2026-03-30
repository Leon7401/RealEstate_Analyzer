"""
不動産投資判定エージェントチーム - メインエントリポイント

Usage:
    # 単体物件分析
    python main.py --mode analyze --name "テスト物件" --address "東京都渋谷区..." \
        --price 5000 --land-area 100 --building-area 200 --structure RC --age 10

    # Webサーバー起動
    python main.py --mode web

    # API接続テスト
    python main.py --mode test-api
"""
import sys
import logging
import argparse
from pathlib import Path

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config.settings import LOG_DIR, LOG_LEVEL, WEB_HOST, WEB_PORT
from agents.orchestrator_agent import OrchestratorAgent
from models.property import Property
from data.reinfolib_client import ReinfolibClient


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(
                LOG_DIR / "re_agent.log", encoding="utf-8"
            ),
        ],
    )


def mode_analyze(args):
    """単体物件分析モード"""
    prop = Property(
        name=args.name,
        address=args.address,
        prefecture_code=args.pref,
        city_code=args.city,
        asking_price=int(args.price * 10000) if args.price else None,
        land_area=args.land_area,
        building_area=args.building_area,
        structure=args.structure,
        building_age=args.age,
        station_distance_min=args.station,
        current_rent_annual=(
            int(args.rent * 10000) if args.rent else None
        ),
    )

    orchestrator = OrchestratorAgent()
    judgment = orchestrator.run(
        property=prop,
        loan_rate=args.loan_rate,
        loan_term=args.loan_term,
        ltv=args.ltv,
    )

    print("\n" + judgment.summary_text)
    return judgment


def mode_web(args):
    """Webサーバー起動モード"""
    import uvicorn
    from web.app import app

    print(f"不動産投資判定マップを起動: http://{WEB_HOST}:{WEB_PORT}")
    uvicorn.run(app, host=WEB_HOST, port=WEB_PORT)


def mode_test_api(args):
    """API接続テストモード"""
    client = ReinfolibClient()

    if not client.is_configured():
        print("⚠ APIキーが未設定です")
        print("  環境変数 REINFOLIB_API_KEY を設定するか、")
        print("  config/settings.py の REINFOLIB_API_KEY を設定してください。")
        print("  APIキー申請: https://www.reinfolib.mlit.go.jp/api/request/")
        print("\n参考テーブルによるオフライン分析は利用可能です。")
        return

    print("API接続テスト中...")
    if client.test_connection():
        print("✓ API接続成功")

        # サンプルデータ取得
        cities = client.get_cities("13")
        print(f"✓ 東京都 市区町村数: {len(cities)}")

        prices = client.get_land_prices("13", "13101")
        print(f"✓ 千代田区 公示地価ポイント数: {len(prices)}")
    else:
        print("✗ API接続失敗")


def mode_batch(args):
    """バッチ処理モード"""
    from engine.batch_processor import BatchProcessor
    from engine.area_analyzer import AreaAnalyzer
    from storage.database import Database

    processor = BatchProcessor()
    analyzer = AreaAnalyzer()
    db = Database()

    prefectures = args.batch_pref.split(",") if args.batch_pref else ["13"]
    print(f"バッチ処理開始: 対象={prefectures}")

    processor.run_full_update(prefectures)

    # 分析結果表示
    for pref in prefectures:
        results = analyzer.analyze_all_areas(pref)
        if results:
            print(f"\n--- 投資妙味ランキング (pref={pref}) ---")
            for r in results[:10]:
                print(
                    f"  #{r.distortion_rank:2d} {r.city_name:6s} "
                    f"Score={r.distortion_score:5.1f} "
                    f"CapRate={r.implied_cap_rate*100:4.1f}% "
                    f"Land=Y{r.avg_land_price/10000:6.0f}万/m2 "
                    f"Rent=Y{r.avg_rent:,.0f}/m2 "
                    f"[{r.nearby_comparison}]"
                )

    stats = db.get_db_stats()
    print(f"\nDB統計: {stats}")


def main():
    parser = argparse.ArgumentParser(
        description="不動産投資判定エージェントチーム"
    )
    parser.add_argument(
        "--mode", choices=["analyze", "web", "test-api", "batch"],
        default="web", help="実行モード"
    )

    # 物件パラメータ
    parser.add_argument("--name", default="分析物件", help="物件名")
    parser.add_argument("--address", default="", help="住所")
    parser.add_argument("--pref", default="13", help="都道府県コード")
    parser.add_argument("--city", default="13113", help="市区町村コード")
    parser.add_argument("--price", type=float, help="売出価格(万円)")
    parser.add_argument("--land-area", type=float, help="土地面積(㎡)")
    parser.add_argument("--building-area", type=float, help="建物面積(㎡)")
    parser.add_argument("--structure", default="RC", help="構造")
    parser.add_argument("--age", type=int, help="築年数")
    parser.add_argument("--station", type=int, default=5, help="駅徒歩(分)")
    parser.add_argument("--rent", type=float, help="現行年間賃料(万円)")

    # ローンパラメータ
    parser.add_argument("--loan-rate", type=float, help="金利")
    parser.add_argument("--loan-term", type=int, help="返済期間(年)")
    parser.add_argument("--ltv", type=float, help="LTV比率")

    # バッチパラメータ
    parser.add_argument("--batch-pref", default="13", help="バッチ対象都道府県(カンマ区切り)")

    args = parser.parse_args()
    setup_logging()

    if args.mode == "analyze":
        mode_analyze(args)
    elif args.mode == "web":
        mode_web(args)
    elif args.mode == "test-api":
        mode_test_api(args)
    elif args.mode == "batch":
        mode_batch(args)


if __name__ == "__main__":
    main()
