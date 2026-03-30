"""地価分析エージェント - 公示地価・取引価格の収集と分析"""
import statistics
from datetime import datetime
from typing import List, Optional

from .base_agent import BaseAgent
from data.reinfolib_client import ReinfolibClient
from data.geocoder import Geocoder
from models.land_price import LandPrice, TransactionRecord, AreaLandPriceSummary


class LandPriceAgent(BaseAgent):
    """
    公示地価・基準地価・取引実績データを収集し、
    エリアの地価水準を分析するエージェント
    """

    # APIなし時の参考地価テーブル（円/㎡）
    REFERENCE_LAND_PRICES = {
        # 23区
        "13101": 3_500_000,   # 千代田区
        "13102": 3_200_000,   # 中央区
        "13103": 3_000_000,   # 港区
        "13104": 2_200_000,   # 新宿区
        "13105": 1_500_000,   # 文京区
        "13106": 1_200_000,   # 台東区
        "13107": 800_000,     # 墨田区
        "13108": 900_000,     # 江東区
        "13109": 1_400_000,   # 品川区
        "13110": 1_300_000,   # 目黒区
        "13111": 800_000,     # 大田区
        "13112": 1_000_000,   # 世田谷区
        "13113": 2_000_000,   # 渋谷区
        "13114": 1_100_000,   # 中野区
        "13115": 900_000,     # 杉並区
        "13116": 1_200_000,   # 豊島区
        "13117": 700_000,     # 北区
        "13118": 700_000,     # 荒川区
        "13119": 650_000,     # 板橋区
        "13120": 600_000,     # 練馬区
        "13121": 500_000,     # 足立区
        "13122": 450_000,     # 葛飾区
        "13123": 500_000,     # 江戸川区
        # 多摩地域
        "13201": 200_000,     # 八王子市
        "13202": 400_000,     # 立川市
        "13203": 550_000,     # 武蔵野市
        "13204": 480_000,     # 三鷹市
        "13205": 130_000,     # 青梅市
        "13206": 350_000,     # 府中市
        "13207": 250_000,     # 昭島市
        "13208": 420_000,     # 調布市
        "13209": 220_000,     # 町田市
        "13210": 400_000,     # 小金井市
        "13211": 300_000,     # 小平市
        "13212": 280_000,     # 日野市
        "13213": 280_000,     # 東村山市
        "13214": 350_000,     # 国分寺市
        "13215": 380_000,     # 国立市
        "13218": 200_000,     # 福生市
        "13219": 380_000,     # 狛江市
        "13220": 220_000,     # 東大和市
        "13221": 250_000,     # 清瀬市
        "13222": 270_000,     # 東久留米市
        "13224": 250_000,     # 多摩市
        "13225": 280_000,     # 稲城市
        "13229": 350_000,     # 西東京市
        # 神奈川県主要市
        "14100": 350_000,     # 横浜市
        "14130": 400_000,     # 川崎市
        "14150": 200_000,     # 相模原市
        # 埼玉県主要市
        "11100": 280_000,     # さいたま市
        "11203": 320_000,     # 川口市
        # 千葉県主要市
        "12100": 200_000,     # 千葉市
        "12204": 250_000,     # 船橋市
        "12227": 400_000,     # 浦安市
        "_default": 300_000,
    }

    def __init__(self):
        super().__init__("LandPriceAgent")
        self.client = ReinfolibClient()
        self.geocoder = Geocoder()

    def run(
        self,
        prefecture_code: str,
        city_code: str = "",
        year: int = None,
        target_lat: float = None,
        target_lng: float = None,
        radius_km: float = 1.0,
        land_only: bool = False,
    ) -> AreaLandPriceSummary:
        """
        指定エリアの地価サマリーを生成

        Args:
            prefecture_code: 都道府県コード
            city_code: 市区町村コード
            year: 対象年（Noneで最新）
            target_lat/lng: 中心座標（指定時は周辺のみ抽出）
            radius_km: 抽出半径(km)
            land_only: Trueなら土地のみの取引に絞る（建物付き除外）
        """
        self.logger.info(f"地価データ収集開始: pref={prefecture_code}, city={city_code}")

        # 公示地価取得
        try:
            land_prices = self._fetch_land_prices(prefecture_code, city_code, year)
        except Exception as e:
            self.logger.warning(f"API取得失敗、参考テーブルで代替: {e}")
            land_prices = []
        self.logger.info(f"公示地価ポイント数: {len(land_prices)}")

        # 取引実績取得
        try:
            transactions = self._fetch_transactions(prefecture_code, city_code, land_only=land_only)
        except Exception as e:
            self.logger.warning(f"取引データ取得失敗: {e}")
            transactions = []
        self.logger.info(f"取引実績件数: {len(transactions)}")

        # 座標指定時はフィルタリング
        if target_lat and target_lng:
            land_prices = self._filter_by_distance(
                land_prices, target_lat, target_lng, radius_km
            )
            transactions = self._filter_transactions_by_distance(
                transactions, target_lat, target_lng, radius_km
            )
            self.logger.info(
                f"半径{radius_km}km内: 地価{len(land_prices)}件, 取引{len(transactions)}件"
            )

        # サマリー生成
        summary = self._build_summary(
            city_code, land_prices, transactions, year
        )
        self.logger.info(
            f"地価サマリー完成: 平均㎡単価={summary.avg_price_per_sqm:,.0f}円"
        )
        return summary

    def estimate_land_value(
        self,
        address: str,
        land_area: float,
        prefecture_code: str,
        city_code: str,
    ) -> dict:
        """
        住所と面積から推定土地価格を算出

        Returns:
            {
                "estimated_price": int,
                "price_per_sqm": float,
                "official_price_per_sqm": float,
                "ratio_to_official": float,
                "method": str,
            }
        """
        # 1. ジオコーディング
        coords = self.geocoder.geocode(address)

        # 2. 周辺の土地のみ取引を取得（建物付き取引は㎡単価が合算で高くなるため除外）
        summary = self.run(
            prefecture_code=prefecture_code,
            city_code=city_code,
            target_lat=coords[0] if coords else None,
            target_lng=coords[1] if coords else None,
            radius_km=1.0,
            land_only=True,
        )

        # 3. 推定㎡単価を決定（土地のみ取引から）
        method = ""
        est_per_sqm = 0

        if summary.sample_count > 0:
            if summary.transactions:
                tx_prices = [
                    t.price_per_sqm for t in summary.transactions
                    if t.price_per_sqm and t.price_per_sqm > 0
                ]
                if tx_prices:
                    est_per_sqm = statistics.median(tx_prices)
                    method = "土地取引事例比較法"

            if est_per_sqm <= 0 and summary.median_price_per_sqm > 0:
                est_per_sqm = summary.median_price_per_sqm
                method = "公示地価準拠"

        # 座標フィルタで見つからなかった場合、市区町村全体で再試行
        if est_per_sqm <= 0 and (coords or city_code):
            city_summary = self.run(
                prefecture_code=prefecture_code,
                city_code=city_code,
                land_only=True,
            )
            if city_summary.transactions:
                tx_prices = [
                    t.price_per_sqm for t in city_summary.transactions
                    if t.price_per_sqm and t.price_per_sqm > 0
                ]
                if tx_prices:
                    est_per_sqm = statistics.median(tx_prices)
                    method = "土地取引事例（市区町村）"
            if est_per_sqm <= 0 and city_summary.median_price_per_sqm > 0:
                est_per_sqm = city_summary.median_price_per_sqm
                method = "地価データ（市区町村）"
            if est_per_sqm <= 0:
                summary = city_summary

        # それでもなければ参考テーブル
        if est_per_sqm <= 0:
            est_per_sqm = self.REFERENCE_LAND_PRICES.get(
                city_code, self.REFERENCE_LAND_PRICES.get("_default", 300_000)
            )
            method = "参考テーブル（オフライン）"

        estimated_price = int(est_per_sqm * land_area)

        official = summary.avg_price_per_sqm
        if official <= 0:
            official = self.REFERENCE_LAND_PRICES.get(
                city_code, self.REFERENCE_LAND_PRICES.get("_default", 300_000)
            )

        sample_total = summary.sample_count + len(summary.transactions)

        return {
            "estimated_price": estimated_price,
            "price_per_sqm": est_per_sqm,
            "official_price_per_sqm": official,
            "ratio_to_official": (
                est_per_sqm / official if official > 0 else 0
            ),
            "method": method,
            "sample_count": sample_total,
        }

    # ===== 内部メソッド =====

    def _fetch_land_prices(
        self, pref: str, city: str, year: int = None
    ) -> List[LandPrice]:
        """DBに蓄積された地価データを取得（API直接呼び出しはバッチで実施）"""
        from storage.database import Database
        db = Database()

        # DBから取得
        with db._conn() as conn:
            sql = "SELECT * FROM land_prices WHERE prefecture_code=?"
            params = [pref]
            if city:
                sql += " AND city_code=?"
                params.append(city)
            if year:
                sql += " AND year=?"
                params.append(year)
            sql += " ORDER BY year DESC LIMIT 500"
            rows = conn.execute(sql, params).fetchall()

        results = []
        for row in [dict(r) for r in rows]:
            try:
                lp = LandPrice(
                    address=row.get("address", ""),
                    price_per_sqm=int(row.get("price_per_sqm", 0)),
                    year=int(row.get("year", 0)),
                    latitude=row.get("latitude"),
                    longitude=row.get("longitude"),
                    land_use_zone=row.get("land_use_zone", ""),
                    nearest_station=row.get("nearest_station", ""),
                    station_distance_min=row.get("station_distance_min"),
                    price_change_rate=row.get("price_change_rate"),
                    price_type=row.get("price_type", ""),
                    prefecture_code=pref,
                    city_code=city or row.get("city_code", ""),
                )
                if lp.price_per_sqm > 0:
                    results.append(lp)
            except (ValueError, TypeError):
                continue

        # DBにデータが無ければ参考テーブルから
        if not results:
            ref_price = self.REFERENCE_LAND_PRICES.get(
                city, self.REFERENCE_LAND_PRICES.get("_default", 300_000)
            )
            results.append(LandPrice(
                address=f"参考値 ({city})",
                price_per_sqm=ref_price,
                year=datetime.now().year,
                price_type="参考値",
                prefecture_code=pref,
                city_code=city,
            ))

        return results

    def _fetch_transactions(
        self, pref: str, city: str, land_only: bool = False
    ) -> List[TransactionRecord]:
        """DBに蓄積された取引データを取得（API直接呼び出しはバッチで実施）"""
        from storage.database import Database
        db = Database()

        with db._conn() as conn:
            sql = "SELECT * FROM transactions WHERE prefecture_code=?"
            params = [pref]
            if city:
                sql += " AND city_code=?"
                params.append(city)
            if land_only:
                # 土地値推定用：「宅地(土地)」のみ（建物付き取引は㎡単価が合算で高くなる）
                sql += " AND property_type = '宅地(土地)'"
            sql += " ORDER BY transaction_date DESC LIMIT 500"
            rows = conn.execute(sql, params).fetchall()

        results = []
        for row in [dict(r) for r in rows]:
            try:
                tr = TransactionRecord(
                    address=row.get("address", ""),
                    transaction_price=int(row.get("transaction_price", 0)),
                    price_per_sqm=row.get("price_per_sqm"),
                    transaction_date=row.get("transaction_date", ""),
                    land_area=row.get("land_area"),
                    land_shape=row.get("land_shape", ""),
                    land_use_zone=row.get("land_use_zone", ""),
                    building_area=row.get("building_area"),
                    structure=row.get("structure", ""),
                    built_year=row.get("built_year"),
                    use=row.get("use", ""),
                    nearest_station=row.get("nearest_station", ""),
                    station_distance_min=row.get("station_distance_min"),
                    property_type=row.get("property_type", ""),
                    prefecture_code=pref,
                    city_code=city or row.get("city_code", ""),
                )
                if tr.transaction_price > 0:
                    results.append(tr)
            except (ValueError, TypeError):
                continue

        return results

    def _filter_by_distance(
        self, prices: List[LandPrice], lat: float, lng: float, radius_km: float
    ) -> List[LandPrice]:
        from math import radians, cos, sin, asin, sqrt

        def haversine(lat1, lon1, lat2, lon2):
            lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
            dlat = lat2 - lat1
            dlon = lon2 - lon1
            a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
            return 6371 * 2 * asin(sqrt(a))

        return [
            p for p in prices
            if p.latitude and p.longitude
            and haversine(lat, lng, p.latitude, p.longitude) <= radius_km
        ]

    def _filter_transactions_by_distance(
        self, txs: List[TransactionRecord], lat: float, lng: float, radius_km: float
    ) -> List[TransactionRecord]:
        # 取引データには座標がないことが多いためフィルタなしで返す
        # TODO: ジオコーディングして距離フィルタ
        return txs

    def _build_summary(
        self,
        city_code: str,
        land_prices: List[LandPrice],
        transactions: List[TransactionRecord],
        year: int = None,
    ) -> AreaLandPriceSummary:
        prices = [lp.price_per_sqm for lp in land_prices if lp.price_per_sqm > 0]

        if prices:
            avg_price = statistics.mean(prices)
            median_price = statistics.median(prices)
            min_price = min(prices)
            max_price = max(prices)
        else:
            avg_price = median_price = min_price = max_price = 0

        change_rates = [
            lp.price_change_rate for lp in land_prices
            if lp.price_change_rate is not None
        ]

        from datetime import datetime
        return AreaLandPriceSummary(
            city_code=city_code,
            city_name="",  # TODO: 市区町村名取得
            year=year or datetime.now().year,
            avg_price_per_sqm=avg_price,
            median_price_per_sqm=median_price,
            min_price_per_sqm=min_price,
            max_price_per_sqm=max_price,
            sample_count=len(prices),
            avg_change_rate=(
                statistics.mean(change_rates) if change_rates else None
            ),
            land_prices=land_prices,
            transactions=transactions,
        )

    @staticmethod
    def _safe_int(val) -> Optional[int]:
        if val is None or val == "":
            return None
        try:
            return int(val)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _safe_float(val) -> Optional[float]:
        if val is None or val == "":
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _parse_built_year(s: str) -> Optional[int]:
        """'令和5年' や '平成20年' などを西暦に変換"""
        if not s:
            return None
        import re
        m = re.search(r"(令和|平成|昭和)(\d+)年", s)
        if m:
            era, y = m.group(1), int(m.group(2))
            if era == "令和":
                return 2018 + y
            elif era == "平成":
                return 1988 + y
            elif era == "昭和":
                return 1925 + y
        m2 = re.search(r"(\d{4})年?", s)
        if m2:
            return int(m2.group(1))
        return None
