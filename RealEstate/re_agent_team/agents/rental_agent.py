"""賃料分析エージェント - 賃貸市場データの収集と分析"""
import statistics
from typing import List, Optional, Dict

from .base_agent import BaseAgent
from models.rental import RentalComp, AreaRentalSummary


class RentalAgent(BaseAgent):
    """
    賃料データを収集・分析し、エリアの賃料水準を把握するエージェント

    データソース:
    - 手動入力/CSVインポート（初期実装）
    - SUUMO等のスクレイピング（将来拡張）
    - LIFULL HOME'S API（パートナー契約後）
    """

    # エリア別・構造別の参考賃料単価（円/㎡・月）
    # 2026年3月 SUUMO賃貸実績に基づき更新
    REFERENCE_RENT_TABLE = {
        # city_code: {structure: rent_per_sqm}
        "13101": {"RC": 6100, "SRC": 6400, "鉄骨": 5200, "木造": 4500},   # 千代田区
        "13102": {"RC": 5800, "SRC": 6100, "鉄骨": 4900, "木造": 4200},   # 中央区
        "13103": {"RC": 7200, "SRC": 7500, "鉄骨": 6100, "木造": 5200},   # 港区
        "13104": {"RC": 5400, "SRC": 5700, "鉄骨": 4600, "木造": 3900},   # 新宿区
        "13105": {"RC": 4800, "SRC": 5100, "鉄骨": 4100, "木造": 3500},   # 文京区
        "13106": {"RC": 4600, "SRC": 4900, "鉄骨": 3900, "木造": 3300},   # 台東区
        "13107": {"RC": 4200, "SRC": 4500, "鉄骨": 3600, "木造": 3000},   # 墨田区
        "13108": {"RC": 4500, "SRC": 4800, "鉄骨": 3800, "木造": 3200},   # 江東区
        "13109": {"RC": 5500, "SRC": 5800, "鉄骨": 4700, "木造": 4000},   # 品川区
        "13110": {"RC": 5700, "SRC": 6000, "鉄骨": 4900, "木造": 4100},   # 目黒区
        "13111": {"RC": 4200, "SRC": 4500, "鉄骨": 3600, "木造": 3000},   # 大田区
        "13112": {"RC": 4400, "SRC": 4700, "鉄骨": 3700, "木造": 3100},   # 世田谷区
        "13113": {"RC": 6000, "SRC": 6300, "鉄骨": 5100, "木造": 4300},   # 渋谷区
        "13114": {"RC": 4200, "SRC": 4500, "鉄骨": 3600, "木造": 3000},   # 中野区
        "13115": {"RC": 3500, "SRC": 3700, "鉄骨": 2900, "木造": 2500},   # 杉並区
        "13116": {"RC": 4700, "SRC": 5000, "鉄骨": 4000, "木造": 3400},   # 豊島区
        "13117": {"RC": 3800, "SRC": 4000, "鉄骨": 3200, "木造": 2700},   # 北区
        "13118": {"RC": 3800, "SRC": 4000, "鉄骨": 3200, "木造": 2700},   # 荒川区
        "13119": {"RC": 3600, "SRC": 3800, "鉄骨": 3000, "木造": 2500},   # 板橋区
        "13120": {"RC": 3600, "SRC": 3800, "鉄骨": 3000, "木造": 2500},   # 練馬区
        "13121": {"RC": 3200, "SRC": 3400, "鉄骨": 2700, "木造": 2300},   # 足立区
        "13122": {"RC": 3100, "SRC": 3300, "鉄骨": 2600, "木造": 2200},   # 葛飾区
        "13123": {"RC": 3200, "SRC": 3400, "鉄骨": 2700, "木造": 2300},   # 江戸川区
        "_default": {"RC": 3500, "SRC": 3700, "鉄骨": 3000, "木造": 2400},
    }

    # 築年数による減価係数
    AGE_ADJUSTMENT = {
        (0, 5): 1.00,
        (6, 10): 0.95,
        (11, 15): 0.90,
        (16, 20): 0.85,
        (21, 25): 0.78,
        (26, 30): 0.72,
        (31, 40): 0.65,
        (41, 999): 0.55,
    }

    # 駅距離による調整係数
    STATION_DISTANCE_ADJUSTMENT = {
        (0, 3): 1.05,
        (4, 5): 1.00,
        (6, 7): 0.97,
        (8, 10): 0.93,
        (11, 15): 0.88,
        (16, 999): 0.80,
    }

    def __init__(self):
        super().__init__("RentalAgent")
        self._comps_db: List[RentalComp] = []
        self._load_db_comps()

    def _load_db_comps(self):
        """DBから賃料事例を読み込み"""
        try:
            from storage.database import Database
            db = Database()
            rows = db.get_rental_comps(limit=5000)
            for r in rows:
                try:
                    rent = r.get("rent_monthly", 0)
                    area = r.get("area_sqm", 0)
                    if rent > 0 and area > 0:
                        comp = RentalComp(
                            address=r.get("address", ""),
                            rent_monthly=rent,
                            area_sqm=area,
                            rent_per_sqm=r.get("rent_per_sqm", rent / area),
                            layout=r.get("layout"),
                            structure=r.get("structure"),
                            built_year=r.get("built_year"),
                            nearest_station=r.get("nearest_station"),
                            station_distance_min=r.get("station_distance_min"),
                            city_code=r.get("city_code"),
                        )
                        self._comps_db.append(comp)
                except Exception:
                    continue
            if self._comps_db:
                self.logger.info(f"DB賃料事例読込: {len(self._comps_db)}件")
        except Exception as e:
            self.logger.debug(f"DB賃料事例読込エラー: {e}")

    def run(
        self,
        city_code: str,
        structure: str = "RC",
        building_age: int = None,
        station_distance_min: int = None,
        area_sqm: float = None,
    ) -> AreaRentalSummary:
        """
        指定条件でのエリア賃料サマリーを生成

        Args:
            city_code: 市区町村コード
            structure: 構造 (RC/SRC/鉄骨/木造)
            building_age: 築年数
            station_distance_min: 駅徒歩分数
            area_sqm: 専有面積（㎡）
        """
        self.logger.info(f"賃料分析開始: city={city_code}, structure={structure}")

        # 蓄積データをcity_codeでフィルタ
        comps = [c for c in self._comps_db if c.city_code == city_code]

        # city_codeで見つからない場合は住所の区名で絞込
        if len(comps) < 5:
            # city_codeから区名を逆引き
            ward_name = self._city_code_to_name(city_code)
            if ward_name:
                comps = [
                    c for c in self._comps_db
                    if ward_name in (c.address or "")
                ]

        if len(comps) >= 5:
            self.logger.info(f"DB賃料事例{len(comps)}件を使用 (city={city_code})")
            return self._build_summary_from_comps(city_code, comps)

        # 参考テーブルからの推定
        return self._estimate_from_reference(
            city_code, structure, building_age, station_distance_min
        )

    def estimate_rent(
        self,
        city_code: str,
        structure: str,
        building_age: int,
        area_sqm: float,
        station_distance_min: int = 5,
        floor: int = None,
    ) -> dict:
        """
        条件指定で推定賃料を算出

        Returns:
            {
                "monthly_rent": int,
                "rent_per_sqm": float,
                "annual_rent": int,
                "confidence": str,  # "high"/"medium"/"low"
                "method": str,
            }
        """
        # 基準賃料取得
        ref_table = self.REFERENCE_RENT_TABLE.get(
            city_code, self.REFERENCE_RENT_TABLE["_default"]
        )
        base_rent = ref_table.get(structure, ref_table.get("RC", 3500))

        # 築年数調整
        age_factor = self._get_age_factor(building_age)

        # 駅距離調整
        dist_factor = self._get_distance_factor(station_distance_min)

        # 階数調整（高層ほど高い）
        floor_factor = 1.0
        if floor and floor > 1:
            floor_factor = 1.0 + min(floor - 1, 20) * 0.005

        # 最終㎡賃料
        adjusted_rent = base_rent * age_factor * dist_factor * floor_factor
        monthly_rent = int(adjusted_rent * area_sqm)
        annual_rent = monthly_rent * 12

        # 確信度判定
        if city_code in self.REFERENCE_RENT_TABLE:
            confidence = "medium"
        else:
            confidence = "low"

        # 蓄積データがあればconfidenceを上げる
        matching_comps = [
            c for c in self._comps_db
            if c.structure == structure
            and abs((c.area_sqm or 0) - area_sqm) < 10
        ]
        if len(matching_comps) >= 5:
            confidence = "high"

        return {
            "monthly_rent": monthly_rent,
            "rent_per_sqm": adjusted_rent,
            "annual_rent": annual_rent,
            "confidence": confidence,
            "method": "参考テーブル＋補正" if not matching_comps else "事例比較法",
            "adjustments": {
                "base_rent_per_sqm": base_rent,
                "age_factor": age_factor,
                "distance_factor": dist_factor,
                "floor_factor": floor_factor,
            },
        }

    def add_comps(self, comps: List[RentalComp]):
        """賃貸比較事例を追加"""
        self._comps_db.extend(comps)
        self.logger.info(f"賃貸事例 {len(comps)}件 追加 (合計: {len(self._comps_db)}件)")

    def load_comps_from_csv(self, csv_path: str):
        """CSVから賃貸事例を読込"""
        import csv
        comps = []
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    rent = int(row.get("rent_monthly", 0))
                    area = float(row.get("area_sqm", 0))
                    if rent > 0 and area > 0:
                        comp = RentalComp(
                            address=row.get("address", ""),
                            rent_monthly=rent,
                            area_sqm=area,
                            rent_per_sqm=rent / area,
                            layout=row.get("layout"),
                            structure=row.get("structure"),
                            built_year=self._safe_int(row.get("built_year")),
                            nearest_station=row.get("nearest_station"),
                            station_distance_min=self._safe_int(
                                row.get("station_distance_min")
                            ),
                        )
                        comps.append(comp)
                except (ValueError, TypeError):
                    continue
        self.add_comps(comps)
        return len(comps)

    # ===== 内部メソッド =====

    def _get_age_factor(self, age: int = None) -> float:
        if age is None:
            return 0.90  # 不明時はデフォルト
        for (lo, hi), factor in self.AGE_ADJUSTMENT.items():
            if lo <= age <= hi:
                return factor
        return 0.55

    def _get_distance_factor(self, dist: int = None) -> float:
        if dist is None:
            return 1.00
        for (lo, hi), factor in self.STATION_DISTANCE_ADJUSTMENT.items():
            if lo <= dist <= hi:
                return factor
        return 0.80

    def _build_summary_from_comps(
        self, city_code: str, comps: List[RentalComp]
    ) -> AreaRentalSummary:
        rents = [c.rent_per_sqm for c in comps if c.rent_per_sqm > 0]
        if not rents:
            rents = [0]

        # 構造別集計
        by_structure: Dict[str, List[float]] = {}
        for c in comps:
            if c.structure and c.rent_per_sqm > 0:
                by_structure.setdefault(c.structure, []).append(c.rent_per_sqm)

        return AreaRentalSummary(
            city_code=city_code,
            city_name="",
            avg_rent_per_sqm=statistics.mean(rents),
            median_rent_per_sqm=statistics.median(rents),
            min_rent_per_sqm=min(rents),
            max_rent_per_sqm=max(rents),
            sample_count=len(rents),
            rent_by_structure={
                k: statistics.mean(v) for k, v in by_structure.items()
            },
            comps=comps,
        )

    def _estimate_from_reference(
        self,
        city_code: str,
        structure: str = "RC",
        building_age: int = None,
        station_distance_min: int = None,
    ) -> AreaRentalSummary:
        ref = self.REFERENCE_RENT_TABLE.get(
            city_code, self.REFERENCE_RENT_TABLE["_default"]
        )
        base = ref.get(structure, 3500)
        age_f = self._get_age_factor(building_age)
        dist_f = self._get_distance_factor(station_distance_min)
        estimated = base * age_f * dist_f

        return AreaRentalSummary(
            city_code=city_code,
            city_name="",
            avg_rent_per_sqm=estimated,
            median_rent_per_sqm=estimated,
            min_rent_per_sqm=estimated * 0.8,
            max_rent_per_sqm=estimated * 1.2,
            sample_count=0,
            rent_by_structure={s: v * age_f * dist_f for s, v in ref.items()},
        )

    @staticmethod
    def _city_code_to_name(city_code: str) -> Optional[str]:
        """市区町村コードから名前を逆引き"""
        CODE_TO_NAME = {
            "13101": "千代田区", "13102": "中央区", "13103": "港区",
            "13104": "新宿区", "13105": "文京区", "13106": "台東区",
            "13107": "墨田区", "13108": "江東区", "13109": "品川区",
            "13110": "目黒区", "13111": "大田区", "13112": "世田谷区",
            "13113": "渋谷区", "13114": "中野区", "13115": "杉並区",
            "13116": "豊島区", "13117": "北区", "13118": "荒川区",
            "13119": "板橋区", "13120": "練馬区", "13121": "足立区",
            "13122": "葛飾区", "13123": "江戸川区",
        }
        return CODE_TO_NAME.get(city_code)

    @staticmethod
    def _safe_int(val) -> Optional[int]:
        if val is None or val == "":
            return None
        try:
            return int(val)
        except (ValueError, TypeError):
            return None
