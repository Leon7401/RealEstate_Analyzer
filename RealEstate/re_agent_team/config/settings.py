"""
不動産投資判定エージェントチーム - グローバル設定
Real Estate Investment Judgment Agent Team - Global Settings
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# ===== プロジェクトパス =====
BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", str(BASE_DIR / "output")))
CACHE_DIR = OUTPUT_DIR / "cache"
LOG_DIR = OUTPUT_DIR / "logs"
REPORT_DIR = OUTPUT_DIR / "reports"
DATA_DIR = BASE_DIR / "data" / "samples"
DB_PATH = Path(os.environ.get("DB_PATH", str(OUTPUT_DIR / "realestate.db")))

# ===== バッチ処理設定 =====
BATCH_UPDATE_INTERVAL_HOURS = 24    # 自動更新間隔（時間）
BATCH_TARGET_PREFECTURES = ["13", "14", "11", "12"]   # 一都三県
RENTAL_REFRESH_INTERVAL_HOURS = 3   # 賃料データ定期更新間隔
RENTAL_REFRESH_MAX_PAGES = 8        # 賃料スクレイピング深度（県あたり）
RENTAL_REFRESH_GEOCODE_BATCH = 1500 # 賃料ジオコード補完件数/回
PROPERTY_REFRESH_INTERVAL_HOURS = 6  # 既存建物物件の定期スクレイピング間隔
PROPERTY_REFRESH_MAX_PAGES = 5       # 既存建物スクレイピング深度（県あたり）
PROPERTY_ANALYZE_LIMIT = 60          # 定期分析で判定する件数（最新順）
PROPERTY_ANALYZE_INCLUDE_REBUILD = True  # 既存建物の建替え比較を実施
LISTING_VERIFY_INTERVAL_HOURS = 24   # 掲載有無チェック実行間隔（毎日）
LISTING_VERIFY_BATCH = 300           # 1回あたりのURL検証件数（物件/土地 各テーブル）
LISTING_VERIFY_STALE_HOURS = 24      # 再検証までの最小間隔
LISTING_VERIFY_CONFIRM_FAILURES = 2  # 連続失敗何回で抹消確定にするか

# ===== 駅紐づけ設定 =====
STATION_MAX_DISTANCE_KM = 2.0      # 最寄り駅として紐づける最大距離(km)
STATION_MAX_WALK_MIN = 25           # 最寄り駅として紐づける最大徒歩分数

# .env読込
load_dotenv(BASE_DIR / ".env")

# ===== 不動産情報ライブラリ API =====
# https://www.reinfolib.mlit.go.jp/help/apiManual/
REINFOLIB_API_BASE = "https://www.reinfolib.mlit.go.jp/ex-api/external"
REINFOLIB_API_KEY = os.environ.get("REINFOLIB_API_KEY", "")

# ===== データ取得設定 =====
# 取引価格情報
TRANSACTION_YEARS_BACK = 5          # 過去何年分の取引データを取得
PRICE_INFO_CATEGORY = ""            # 01=取引価格, 02=成約価格, 空=両方

# 対象エリア（デフォルト: 東京23区）
DEFAULT_PREFECTURE_CODE = "13"      # 東京都
DEFAULT_CITY_CODES = [
    "13101",  # 千代田区
    "13102",  # 中央区
    "13103",  # 港区
    "13104",  # 新宿区
    "13105",  # 文京区
    "13106",  # 台東区
    "13107",  # 墨田区
    "13108",  # 江東区
    "13109",  # 品川区
    "13110",  # 目黒区
    "13111",  # 大田区
    "13112",  # 世田谷区
    "13113",  # 渋谷区
    "13114",  # 中野区
    "13115",  # 杉並区
    "13116",  # 豊島区
    "13117",  # 北区
    "13118",  # 荒川区
    "13119",  # 板橋区
    "13120",  # 練馬区
    "13121",  # 足立区
    "13122",  # 葛飾区
    "13123",  # 江戸川区
]

# ===== 投資判定パラメータ =====
# 利回り基準
MIN_GROSS_YIELD = 0.04              # 最低表面利回り 4%
TARGET_GROSS_YIELD = 0.06           # 目標表面利回り 6%
MIN_NET_YIELD = 0.03                # 最低実質利回り 3%
TARGET_NET_YIELD = 0.045            # 目標実質利回り 4.5%

# 土地値比率（物件価格に対する土地価格の割合）
MIN_LAND_VALUE_RATIO = 0.40         # 最低土地値比率 40%
IDEAL_LAND_VALUE_RATIO = 0.60       # 理想土地値比率 60%

# キャッシュフロー想定
VACANCY_RATE = 0.05                 # 空室率 5%
MANAGEMENT_FEE_RATE = 0.05          # 管理費率 5%
REPAIR_RESERVE_RATE = 0.05          # 修繕積立率 5%
INSURANCE_RATE = 0.003              # 火災保険料率 0.3%
PROPERTY_TAX_RATE = 0.014           # 固定資産税率 1.4%（標準税率）
CITY_PLANNING_TAX_RATE = 0.003      # 都市計画税率 0.3%

# ローン想定
DEFAULT_LOAN_RATE = 0.02            # 金利 2.0%
DEFAULT_LOAN_TERM = 35              # 返済期間 35年
DEFAULT_LTV = 0.80                  # LTV 80%

# 投資シミュレーション
SIMULATION_YEARS = 30               # シミュレーション期間
LAND_APPRECIATION_RATE = 0.005      # 地価上昇率（年） 0.5%
BUILDING_DEPRECIATION_RATE = 0.02   # 建物減価率（年） 2%
RENT_DECLINE_RATE = 0.005           # 賃料下落率（年） 0.5%
DISCOUNT_RATE = 0.03                # 割引率（NPV計算用）

# ===== 判定グレード =====
GRADE_THRESHOLDS = {
    "S": {"net_yield": 0.06, "land_ratio": 0.70, "irr": 0.08},    # 最優秀
    "A": {"net_yield": 0.05, "land_ratio": 0.60, "irr": 0.06},    # 優秀
    "B": {"net_yield": 0.04, "land_ratio": 0.50, "irr": 0.04},    # 良好
    "C": {"net_yield": 0.03, "land_ratio": 0.40, "irr": 0.02},    # 普通
    "D": {"net_yield": 0.02, "land_ratio": 0.30, "irr": 0.01},    # 要注意
    "F": {"net_yield": 0.00, "land_ratio": 0.00, "irr": -999},    # 不可
}

# ===== 土地スクレイピング設定 =====
LAND_SCRAPE_RATE_LIMIT = 3.0           # リクエスト間隔（秒）
LAND_SCRAPE_DEFAULT_MAX_PAGES = 5      # デフォルト最大ページ数

# ===== 建築プラン設定 =====
PLAN_UNIT_SIZES = [20, 25, 30, 35]     # 間取りサイズ（㎡）
PLAN_STRUCTURE_TYPES = ["木造", "重量鉄骨"]

# 構造別建築単価（円/㎡）
CONSTRUCTION_COST_PER_SQM = {
    "木造": 220_000,           # 木造アパート: 約22万/㎡
    "重量鉄骨": 300_000,       # 重量鉄骨: 約30万/㎡
}

# 構造別可能階数
STRUCTURE_MAX_FLOORS = {
    "木造": [2, 3],
    "重量鉄骨": [3, 4, 5],
}

# 階数別共用部率
COMMON_AREA_RATIO = {
    2: 0.15,
    3: 0.17,
    4: 0.18,
    5: 0.20,
}

# 用途地域別の階数制限
ZONING_FLOOR_LIMITS = {
    "第一種低層住居専用地域": 3,
    "第二種低層住居専用地域": 3,
    "第一種中高層住居専用地域": None,   # 制限なし
    "第二種中高層住居専用地域": None,
    "第一種住居地域": None,
    "第二種住居地域": None,
    "準住居地域": None,
    "近隣商業地域": None,
    "商業地域": None,
    "準工業地域": None,
    "工業地域": None,
    "工業専用地域": None,
}

# アパート建築不可の用途地域
ZONING_APARTMENT_NG = {"工業専用地域"}

# 低層住居専用地域での3F建築条件
LOW_RISE_3F_CONDITIONS = {
    "height_limit_m": 12,           # 絶対高さ12m指定なら可（10mなら半地下検討）
    "min_far": 1.0,                 # 容積率100%以上
    "min_area_sqm": 70,             # 敷地面積70㎡以上
    "prefer_north_road": True,      # 北側道路が望ましい
}

# ===== 建築規制パラメータ =====
# セットバック
SETBACK_THRESHOLD_ROAD_WIDTH_M = 4.0   # 4m未満でセットバック必要
SETBACK_NARROW_ROAD_COST_PREMIUM = 4000  # セットバック前3m未満で+0.4万円/㎡

# 間口閾値
MIN_FRONTAGE_FOR_MULTI_UNIT_M = 6.5    # 1層2戸に必要な最低間口(m)

# 斜線制限パラメータ
SLOPE_RESTRICTION = {
    "north": {"ratio": 1.25, "base_height_m": 5.0},    # 北側斜線: 5m + 1.25×距離
    "road": {"ratio": 1.25},                             # 道路斜線: 1.25×道路幅員
    "shadow": {                                          # 日影規制
        "第一種低層住居専用地域": {"hours_4h": 3.0, "hours_2_5h": 2.0},
        "第二種低層住居専用地域": {"hours_4h": 3.0, "hours_2_5h": 2.0},
    },
}

# 建蔽率・容積率の最低基準
MIN_BUILDING_COVERAGE = 0.60    # 60%以上
MIN_FLOOR_AREA_RATIO = 2.0     # 200%以上
CORNER_LOT_COVERAGE_BONUS = 0.10  # 角地緩和+10%

# ===== ワンルーム条例（自治体別規制） =====
WARD_ORDINANCE_RULES = {
    "13104": {"name": "新宿区", "min_unit_sqm": 25, "min_total_units_trigger": 30, "family_ratio": 0.0},
    "13112": {"name": "世田谷区", "min_unit_sqm": 25, "min_total_units_trigger": 15},
    "13113": {"name": "渋谷区", "min_unit_sqm": 25, "min_total_units_trigger": 15},
    "13116": {"name": "豊島区", "min_unit_sqm": 25, "min_total_units_trigger": 15, "family_ratio": 0.15},
    "13103": {"name": "港区", "min_unit_sqm": 25, "min_total_units_trigger": 30},
    "13108": {"name": "江東区", "min_unit_sqm": 25, "min_total_units_trigger": 15},
    "13109": {"name": "品川区", "min_unit_sqm": 20, "min_total_units_trigger": 15},
    "13111": {"name": "大田区", "min_unit_sqm": 20, "min_total_units_trigger": 15},
    "13117": {"name": "北区", "min_unit_sqm": 20, "min_total_units_trigger": 15},
    "13121": {"name": "足立区", "min_unit_sqm": 20, "min_total_units_trigger": 20},
}

# ===== 設備グレード別賃料プレミアム =====
EQUIPMENT_PREMIUM = {
    "standard": {
        "label": "標準仕様",
        "factor": 1.00,
        "items": ["エアコン", "IH2口", "セパレート水回り", "室内洗濯機置場", "フローリング"],
    },
    "premium": {
        "label": "プレミアム仕様",
        "factor": 1.05,
        "items": ["standard全部", "食洗機", "宅配BOX", "浴室乾燥", "追焚き", "独立洗面台三面鏡",
                  "防犯カメラ", "高速ネット1G", "複層ガラス", "ダウンライト"],
    },
    "premium_loft": {
        "label": "プレミアム+ロフト",
        "factor": 1.08,
        "items": ["premium全部", "収納付き固定階段ロフト", "デスクカウンター", "床暖房"],
    },
}

# ===== 新駅・再開発ボーナス =====
REDEVELOPMENT_BONUS_STATIONS = {
    "橋本": 15,
    "村岡新駅": 20,
    "多摩モノレール町田": 15,
    "愛甲石田": 10,
    "海老名": 10,
    "辻堂": 10,
    "鷺沼": 10,
    "南町田": 10,
}

# ===== 駅力分析: 路線数マスタ =====
# 実際の乗り入れ路線数（JR・地下鉄・私鉄）
STATION_LINE_COUNT = {
    # ターミナル（8路線以上）
    "東京": 14, "新宿": 12, "渋谷": 9, "池袋": 8, "横浜": 11,
    "大宮": 12, "北千住": 5, "上野": 7, "品川": 6,
    # 主要乗換（4-7路線）
    "秋葉原": 4, "御茶ノ水": 3, "飯田橋": 5, "市ヶ谷": 4,
    "四ツ谷": 4, "赤坂見附": 2, "溜池山王": 2, "永田町": 4,
    "表参道": 3, "六本木": 2, "目黒": 4, "恵比寿": 3,
    "中目黒": 2, "自由が丘": 2, "二子玉川": 2, "武蔵小杉": 5,
    "日暮里": 4, "西日暮里": 3, "王子": 2, "赤羽": 3,
    "大井町": 3, "蒲田": 2, "五反田": 3, "大崎": 3,
    "田町": 2, "浜松町": 2, "新橋": 5, "有楽町": 3,
    "中野": 3, "荻窪": 2, "吉祥寺": 2, "三鷹": 2,
    "立川": 4, "八王子": 3, "町田": 2, "橋本": 3,
    "川崎": 3, "藤沢": 3, "大船": 3, "戸塚": 2,
    "関内": 2, "桜木町": 2, "東神奈川": 2,
    "浦和": 3, "川口": 2, "所沢": 2, "川越": 3,
    "春日部": 2, "越谷": 2, "草加": 2, "朝霞台": 2,
    "千葉": 4, "船橋": 3, "柏": 2, "松戸": 2,
    "津田沼": 2, "西船橋": 4, "市川": 2, "本八幡": 3,
    # 3路線
    "練馬": 3, "小竹向原": 2, "和光市": 2, "志木": 2,
    "押上": 4, "錦糸町": 3, "亀戸": 2, "新木場": 3,
    "豊洲": 2, "月島": 2, "門前仲町": 2, "清澄白河": 2,
    "住吉": 2, "森下": 2, "両国": 2, "浅草": 3,
    "神田": 3, "水道橋": 2, "後楽園": 2, "茗荷谷": 2,
    "巣鴨": 2, "駒込": 2, "田端": 2, "高田馬場": 3,
    "目白": 1, "大塚": 2, "代々木": 2, "原宿": 2,
    "明治神宮前": 2, "外苑前": 1, "青山一丁目": 2,
    "赤坂": 1, "乃木坂": 1, "広尾": 1, "白金高輪": 2,
    "麻布十番": 2, "六本木一丁目": 1, "神谷町": 1,
    "虎ノ門": 1, "虎ノ門ヒルズ": 1, "大手町": 5,
    "日本橋": 3, "三越前": 2, "人形町": 2, "茅場町": 2,
    "八丁堀": 2, "築地": 1, "銀座": 4, "東銀座": 2,
    "新富町": 1, "勝どき": 1, "汐留": 2,
    "二重橋前": 1, "九段下": 3, "半蔵門": 1, "麹町": 1,
    "竹橋": 1, "小川町": 3, "淡路町": 2,
    "湯島": 1, "上野広小路": 1, "仲御徒町": 1, "御徒町": 2,
    "稲荷町": 1, "入谷": 1, "三ノ輪": 1, "南千住": 2,
    "綾瀬": 2, "亀有": 1, "金町": 1, "新小岩": 1,
    "小岩": 1, "平井": 1, "葛西": 1, "西葛西": 1,
    "浦安": 2, "新浦安": 1, "海浜幕張": 1, "舞浜": 1,
    "下北沢": 3, "三軒茶屋": 2, "駒沢大学": 1,
    "溝の口": 2, "たまプラーザ": 1, "あざみ野": 2,
    "センター北": 2, "センター南": 2, "日吉": 2,
    "綱島": 1, "菊名": 2, "新横浜": 3, "鶴見": 2,
    "海老名": 3, "厚木": 2, "本厚木": 2, "伊勢原": 1,
    "秦野": 1, "小田原": 4, "平塚": 1, "茅ヶ崎": 2,
    "辻堂": 1, "鎌倉": 2, "逗子": 2, "久里浜": 2,
    "横須賀": 2, "金沢文庫": 1, "金沢八景": 2,
    "上大岡": 2, "弘明寺": 1, "井土ヶ谷": 1,
    "相模大野": 2, "中央林間": 2, "長津田": 2,
    "青葉台": 1, "鷺沼": 1, "宮前平": 1,
    "登戸": 2, "向ヶ丘遊園": 2, "生田": 1,
    "百合ヶ丘": 1, "新百合ヶ丘": 2, "稲田堤": 2,
}

# 駅力スコアウェイト
STATION_POWER_WEIGHTS = {
    "terminal": 0.20,     # ターミナル近接度
    "lines": 0.20,        # 路線数（重要度UP）
    "land_price": 0.15,   # 地価水準
    "rent": 0.15,         # 賃料水準
    "transactions": 0.15, # 取引活性度
    "population": 0.15,   # 人口密度
}

# データ収集優先度
DATA_COLLECTION_PRIORITY = {
    "rental": {
        "sources": ["SUUMO賃貸"],
        "description": "賃料相場（1K帯重点）",
    },
    "transactions": {
        "sources": ["reinfolib_API"],
        "description": "取引実績（XIT001）",
    },
    "land_price": {
        "sources": ["reinfolib_API"],
        "description": "公示地価（XPT002）",
    },
    "properties": {
        "sources": ["athome", "楽待", "建美家", "SUUMO"],
        "description": "売出物件",
    },
}

# ===== ハザード判定閾値 =====
HAZARD_FLOOD_MAX_DEPTH_M = 2.0         # 浸水2m以内なら検討対象
HAZARD_LANDSLIDE_SPECIAL_ZONE_NG = True # 土砂災害特別警戒区域は完全NG
HAZARD_BUILDING_COLLAPSE_ZONE_NG = True # 家屋倒壊等氾濫想定区域は完全NG

# ===== 資産性分析設定 =====
# OpenRouteService API（アイソクロン用、無料枠あり）
ORS_API_KEY = os.environ.get("ORS_API_KEY", "")
ORS_API_BASE = "https://api.openrouteservice.org"

# ハザードマップタイルURL（国土地理院ハザードマップポータル）
HAZARD_TILE_URLS = {
    "flood": "https://disaportaldata.gsi.go.jp/raster/01_flood_l2_shinsuishin_kuni_data/{z}/{x}/{y}.png",
    "flood_planned": "https://disaportaldata.gsi.go.jp/raster/01_flood_l2_shinsuishin_seibi_data/{z}/{x}/{y}.png",
    "landslide": "https://disaportaldata.gsi.go.jp/raster/05_dosekiryukeikaikuiki/{z}/{x}/{y}.png",
    "landslide_steep": "https://disaportaldata.gsi.go.jp/raster/05_kyukeishatihoukai/{z}/{x}/{y}.png",
    "tsunami": "https://disaportaldata.gsi.go.jp/raster/04_tsunami_newlegend_data/{z}/{x}/{y}.png",
    "storm_surge": "https://disaportaldata.gsi.go.jp/raster/03_hightide_l2_shinsuishin_data/{z}/{x}/{y}.png",
    "terrain_class": "https://cyberjapandata.gsi.go.jp/xyz/lcmfc2/{z}/{x}/{y}.png",
}

# 国土地理院 標高API
GSI_ELEVATION_URL = "https://cyberjapandata2.gsi.go.jp/general/dem/scripts/getelevation.php"

# 資産性スコア配点ウェイト
ASSET_SCORE_WEIGHTS = {
    "road": 0.20,       # 接道状況
    "hazard": 0.25,     # ハザードリスク
    "elevation": 0.10,  # 標高・地形
    "lot_shape": 0.15,  # 敷地形状
    "population": 0.15, # 人口動態
    "station": 0.15,    # 駅距離
}

# ===== ログ設定 =====
LOG_LEVEL = "INFO"

# ===== Web設定 =====
WEB_HOST = os.environ.get("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.environ.get("WEB_PORT", os.environ.get("PORT", "8080")))
MAP_DEFAULT_CENTER = [35.6812, 139.7671]  # 東京駅
MAP_DEFAULT_ZOOM = 12

# ===== 分析入力の自動補完係数 =====
# ヒートマップ/メッシュ連動時に、相場値を物件分析へ取り込む係数
ANALYZE_AUTOFILL_LAND_PRICE_FACTOR = 1.00
ANALYZE_AUTOFILL_RENT_BASE_FACTOR = 0.85
ANALYZE_AUTOFILL_RENT_MIN_FACTOR = 0.70
ANALYZE_AUTOFILL_RENT_MAX_FACTOR = 0.98
# 駅距離による賃料係数調整（近いほど上振れ、遠いほど下振れ）
ANALYZE_AUTOFILL_RENT_NEAR_BONUS = 0.06
ANALYZE_AUTOFILL_RENT_FAR_PENALTY = 0.08
