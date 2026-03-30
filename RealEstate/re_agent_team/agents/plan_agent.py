"""建築プラン生成エージェント - 土地情報から最適な建築プランを算出"""
import math
from typing import List, Optional

from .base_agent import BaseAgent
from .rental_agent import RentalAgent
from models.land_listing import LandListing
from models.building_plan import BuildingPlan, LandPlanSummary
from config.settings import (
    PLAN_UNIT_SIZES,
    CONSTRUCTION_COST_PER_SQM,
    STRUCTURE_MAX_FLOORS,
    COMMON_AREA_RATIO,
    ZONING_FLOOR_LIMITS,
    ZONING_APARTMENT_NG,
    LOW_RISE_3F_CONDITIONS,
    SETBACK_THRESHOLD_ROAD_WIDTH_M,
    SETBACK_NARROW_ROAD_COST_PREMIUM,
    MIN_FRONTAGE_FOR_MULTI_UNIT_M,
    SLOPE_RESTRICTION,
    CORNER_LOT_COVERAGE_BONUS,
    WARD_ORDINANCE_RULES,
    EQUIPMENT_PREMIUM,
)


class PlanAgent(BaseAgent):
    """
    土地情報から建築可能なプランを算出するエージェント

    構造タイプ: 木造 / 重量鉄骨
    算出項目: 各構造×階数×間取りサイズでの戸数・収益試算
    """

    # 準防火地域での木造コスト増加率
    FIREPROOF_COST_FACTOR = 1.10  # 10%増

    # 土地取得諸費用率（仲介手数料3% + 登記費用1% + 不動産取得税3%）
    LAND_ACQUISITION_COST_RATE = 0.07

    # 建築付帯費用率（設計費3% + 外構工事5% + 地盤改良3% + 各種申請2% + 予備2%）
    CONSTRUCTION_OVERHEAD_RATE = 0.15

    # 最低2戸以上のプランのみ有効
    MIN_VIABLE_UNITS = 2

    def __init__(self):
        super().__init__("PlanAgent")
        self.rental_agent = RentalAgent()

    def run(
        self,
        land_listing: LandListing,
        rent_per_sqm: float = None,
        city_code: str = None,
        equipment_grade: str = "premium",
    ) -> LandPlanSummary:
        """
        土地物件に対して全プランを算出

        Args:
            land_listing: 土地物件データ
            rent_per_sqm: 想定㎡賃料（月額）。Noneなら自動推定
            city_code: 市区町村コード（賃料推定用）
            equipment_grade: 設備グレード（standard/premium/premium_loft）
        """
        self.logger.info(
            f"プラン生成開始: {land_listing.address} "
            f"({land_listing.land_area_sqm}㎡, "
            f"建蔽率{(land_listing.building_coverage_ratio or 0)*100:.0f}%, "
            f"容積率{(land_listing.floor_area_ratio or 0)*100:.0f}%)"
        )

        empty_summary = LandPlanSummary(
            land_listing_id=land_listing.id or 0,
            address=land_listing.address,
            land_price=land_listing.land_price or 0,
            land_area_sqm=land_listing.land_area_sqm or 0,
        )

        if not land_listing.land_area_sqm or not land_listing.building_coverage_ratio:
            self.logger.warning("面積または建蔽率が未設定、プラン生成不可")
            return empty_summary

        # 検討対象外チェック（擁壁・工業専用地域）
        if land_listing.is_disqualified:
            self.logger.warning(f"検討対象外: {land_listing.address} (擁壁/用途地域NG)")
            return empty_summary

        # セットバック推定
        land_listing.estimate_setback()

        # 設備プレミアム係数
        eq_config = EQUIPMENT_PREMIUM.get(equipment_grade, EQUIPMENT_PREMIUM["premium"])
        eq_factor = eq_config["factor"]

        # ワンルーム条例
        listing_city = city_code or self._guess_city_code(land_listing.address)
        ward_rule = WARD_ORDINANCE_RULES.get(listing_city)

        plans = []
        land_area = land_listing.effective_land_area or land_listing.land_area_sqm
        coverage = land_listing.effective_coverage or land_listing.building_coverage_ratio
        far = land_listing.floor_area_ratio or 1.0

        # 前面道路幅員による容積率制限
        if land_listing.road_width_m:
            road_far = land_listing.road_width_m * 0.4  # 住居系: 幅員×4/10
            if land_listing.zoning and ("商業" in land_listing.zoning or "工業" in land_listing.zoning):
                road_far = land_listing.road_width_m * 0.6  # 商業系: 幅員×6/10
            far = min(far, road_far)

        max_footprint = land_area * coverage
        max_total_floor = land_area * far

        # 用途地域による階数制限
        zoning_limit = self._get_zoning_floor_limit(land_listing)

        # 間口チェック（1層2戸の可否）
        frontage = land_listing.frontage_m
        narrow_frontage = frontage is not None and frontage < MIN_FRONTAGE_FOR_MULTI_UNIT_M

        for structure_type, allowed_floors in STRUCTURE_MAX_FLOORS.items():
            cost_per_sqm = CONSTRUCTION_COST_PER_SQM[structure_type]

            # 準防火地域での木造コスト増
            if structure_type == "木造" and land_listing.quasi_fireproof:
                cost_per_sqm = round(cost_per_sqm * self.FIREPROOF_COST_FACTOR)

            for num_floors in allowed_floors:
                # 用途地域チェック
                if zoning_limit and num_floors > zoning_limit:
                    continue

                # 斜線制限によるボリューム減
                volume_reduction = self._calc_volume_reduction(
                    land_listing, num_floors, structure_type
                )

                # 面積計算
                actual_total = min(max_footprint * num_floors, max_total_floor)
                actual_total *= (1 - volume_reduction)  # 斜線制限減
                common_ratio = COMMON_AREA_RATIO.get(num_floors, 0.18)
                effective_area = actual_total * (1 - common_ratio)

                for unit_size in PLAN_UNIT_SIZES:
                    # 間口が狭い場合、1層1戸制限
                    if narrow_frontage:
                        units_per_floor = 1
                        max_units = units_per_floor * num_floors
                    else:
                        max_units = math.floor(effective_area / unit_size)

                    if max_units < self.MIN_VIABLE_UNITS:
                        continue

                    # ワンルーム条例チェック
                    ordinance_ok = True
                    ordinance_note = ""
                    if ward_rule and unit_size < ward_rule.get("min_unit_sqm", 0):
                        if max_units >= ward_rule.get("min_total_units_trigger", 999):
                            ordinance_ok = False
                            ordinance_note = f"{ward_rule['name']}条例: {unit_size}㎡ < 最低{ward_rule['min_unit_sqm']}㎡"

                    # 賃料推定
                    unit_rent_per_sqm = rent_per_sqm
                    if unit_rent_per_sqm is None:
                        unit_rent_per_sqm = self._estimate_rent(
                            land_listing, structure_type, listing_city
                        )

                    # 設備プレミアム適用
                    monthly_rent_per_unit = int(unit_rent_per_sqm * unit_size * eq_factor)
                    annual_income = monthly_rent_per_unit * 12 * max_units
                    construction_cost = int(actual_total * cost_per_sqm)

                    # セットバック施工費増分
                    setback_premium = 0
                    if land_listing.road_width_m and land_listing.road_width_m < 3.0:
                        setback_premium = int(actual_total * SETBACK_NARROW_ROAD_COST_PREMIUM)

                    # 総投資額 = 土地代 + 土地取得諸費用 + 建築費 + 付帯費用 + セットバック増分
                    land_price = land_listing.land_price or 0
                    land_acq_cost = int(land_price * self.LAND_ACQUISITION_COST_RATE)
                    construction_overhead = int(construction_cost * self.CONSTRUCTION_OVERHEAD_RATE)
                    total_inv = (land_price + land_acq_cost + construction_cost
                                 + construction_overhead + setback_premium)
                    est_yield = annual_income / total_inv if total_inv > 0 else 0

                    if est_yield > 0.30:
                        self.logger.warning(f"異常利回り {est_yield*100:.1f}% (capped to 30%): {land_listing.address}")
                        est_yield = 0.30

                    plan = BuildingPlan(
                        land_listing_id=land_listing.id or 0,
                        structure_type=structure_type,
                        floors=num_floors,
                        unit_size_sqm=unit_size,
                        max_footprint_sqm=max_footprint,
                        max_total_floor_area_sqm=max_total_floor,
                        actual_total_floor_area_sqm=actual_total,
                        common_area_ratio=common_ratio,
                        effective_floor_area_sqm=effective_area,
                        max_units=max_units,
                        estimated_rent_per_sqm=unit_rent_per_sqm,
                        equipment_grade=equipment_grade,
                        equipment_premium_factor=eq_factor,
                        estimated_monthly_rent_per_unit=monthly_rent_per_unit,
                        estimated_annual_income=annual_income,
                        estimated_construction_cost=construction_cost,
                        land_acquisition_cost=land_acq_cost,
                        construction_overhead=construction_overhead,
                        setback_cost_premium=setback_premium,
                        total_investment=total_inv,
                        estimated_yield=est_yield,
                        volume_reduction_ratio=volume_reduction,
                        ward_ordinance_compliant=ordinance_ok,
                        ward_ordinance_note=ordinance_note,
                    )
                    plans.append(plan)

        # 多角的評価で最適プランをランク付け
        for plan in plans:
            yield_score = min(plan.estimated_yield / 0.10, 1.0) * 35
            units_score = min(plan.max_units / 12, 1.0) * 15
            eff = (plan.estimated_annual_income / plan.total_investment
                   if plan.total_investment > 0 else 0)
            efficiency_score = min(eff / 0.10, 1.0) * 15
            struct_score = 15 if plan.structure_type == "重量鉄骨" else 10
            # 条例不適合ペナルティ
            compliance_score = 0 if plan.ward_ordinance_compliant else -20
            # 3F優先ボーナス（指示書: 第一優先は3F建て）
            floor_bonus = 10 if plan.floors >= 3 else 0
            plan._rank_score = (yield_score + units_score + efficiency_score
                                + struct_score + compliance_score + floor_bonus)

        plans.sort(key=lambda p: getattr(p, '_rank_score', 0), reverse=True)

        best = plans[0] if plans else None
        recommendation = ""
        if best:
            reasons = []
            if best.estimated_yield >= 0.05:
                reasons.append(f"利回り{best.estimated_yield*100:.1f}%")
            if best.max_units >= 6:
                reasons.append(f"{best.max_units}戸で安定稼働")
            if best.structure_type == "重量鉄骨":
                reasons.append("重鉄で資産性・耐用年数に優位")
            elif best.structure_type == "木造" and best.floors >= 3:
                reasons.append("木造3Fで戸数最大化")
            if not best.ward_ordinance_compliant:
                reasons.append(f"⚠ {best.ward_ordinance_note}")
            recommendation = " / ".join(reasons) if reasons else "最高スコアプラン"

        summary = LandPlanSummary(
            land_listing_id=land_listing.id or 0,
            address=land_listing.address,
            land_price=land_listing.land_price or 0,
            land_area_sqm=land_listing.land_area_sqm,
            plans=plans,
        )

        self.logger.info(
            f"プラン生成完了: {len(plans)}プラン, "
            f"推奨: {best.plan_label if best else 'なし'} ({recommendation}), "
            f"最高利回り {summary.best_yield*100:.2f}%"
        )
        return summary

    def run_batch(
        self,
        listings: List[LandListing],
        rent_per_sqm: float = None,
    ) -> List[LandPlanSummary]:
        """複数土地のプラン一括算出"""
        results = []
        for listing in listings:
            try:
                summary = self.run(listing, rent_per_sqm=rent_per_sqm)
                results.append(summary)
            except Exception as e:
                self.logger.warning(f"プラン生成エラー ({listing.address}): {e}")
        return results

    def _get_zoning_floor_limit(self, listing: LandListing) -> Optional[int]:
        """用途地域による階数制限を取得（低層地域の3F条件も考慮）"""
        zoning = listing.zoning
        if not zoning:
            return None

        # アパート建築不可
        if zoning in ZONING_APARTMENT_NG:
            return 0

        limit = ZONING_FLOOR_LIMITS.get(zoning)
        if limit is None:
            return None

        # 低層住居専用地域での3F条件チェック
        if "低層" in zoning and limit == 3:
            cond = LOW_RISE_3F_CONDITIONS
            area = listing.effective_land_area or listing.land_area_sqm or 0
            far = listing.floor_area_ratio or 0
            height = listing.height_limit_m

            # 高さ制限10mなら2Fまで（12mなら3Fまで可）
            if height and height <= 10:
                return 2
            # 容積率100%未満なら3Fのボリューム確保困難
            if far < cond["min_far"]:
                return 2
            # 敷地面積70㎡未満なら3Fでも戸数確保困難
            if area < cond["min_area_sqm"]:
                return 2

        return limit

    def _calc_volume_reduction(
        self, listing: LandListing, floors: int, structure: str
    ) -> float:
        """斜線制限（北側・道路・日影）による建築ボリューム減率を推定"""
        reduction = 0.0

        # 概算建物高さ
        floor_height = 3.0 if structure == "木造" else 3.3
        building_height = floors * floor_height

        # 北側斜線制限（低層・中高層住居専用地域）
        zoning = listing.zoning or ""
        if "住居" in zoning and "商業" not in zoning:
            ns = SLOPE_RESTRICTION["north"]
            # 北側道路なら斜線の影響は大幅に軽減
            if not listing.north_road:
                # 隣地境界からの距離を推定（奥行の半分程度）
                depth = listing.depth_m or 10.0
                boundary_dist = min(depth * 0.3, 3.0)  # 北側境界まで約30%
                allowed_height = ns["base_height_m"] + ns["ratio"] * boundary_dist
                if building_height > allowed_height:
                    # 最上階の一部が制限される
                    overshoot_ratio = (building_height - allowed_height) / building_height
                    reduction += overshoot_ratio * 0.15  # 最上階面積の約15%減

        # 道路斜線制限
        road_width = listing.road_width_m or 4.0
        rs = SLOPE_RESTRICTION["road"]
        road_allowed_height = road_width * rs["ratio"]
        if building_height > road_allowed_height:
            overshoot_ratio = (building_height - road_allowed_height) / building_height
            reduction += overshoot_ratio * 0.10  # 上層部のセットバック

        return min(reduction, 0.30)  # 最大30%減

    def _estimate_rent(
        self, listing: LandListing, structure: str, city_code: str = None
    ) -> float:
        """DB賃料データ → 駅メトリクス → RentalAgent参考テーブルの順で推定"""
        from storage.database import Database

        structure_map = {"木造": "木造", "重量鉄骨": "鉄骨"}
        struct = structure_map.get(structure, structure)

        if not city_code:
            city_code = self._guess_city_code(listing.address)

        # 構造別補正係数（RC基準）
        structure_factor = {"木造": 0.88, "鉄骨": 0.93, "重量鉄骨": 0.93}.get(structure, 0.95)

        try:
            db = Database()

            # 1. 駅名で賃料事例を検索
            if listing.station:
                comps = db.get_rental_comps(station=listing.station, limit=50)
                rents = [r["rent_per_sqm"] for r in comps if r.get("rent_per_sqm") and r["rent_per_sqm"] > 0]
                if len(rents) >= 3:
                    import statistics
                    base = statistics.median(rents)
                    return base * structure_factor

            # 2. 駅メトリクスから推定
            from data.station_master import resolve_station_id
            station_text = listing.station or ""
            pref = listing._guess_pref_code() if hasattr(listing, '_guess_pref_code') else "13"
            sid = resolve_station_id(station_text, listing.latitude, listing.longitude, pref)
            if sid:
                metrics = db.get_station_metrics(prefecture_code=pref)
                for m in metrics:
                    if m.get("station_id") == sid and m.get("avg_rent_per_sqm") and m["avg_rent_per_sqm"] > 0:
                        return m["avg_rent_per_sqm"] * structure_factor
        except Exception:
            pass

        # 3. フォールバック: RentalAgent参考テーブル
        result = self.rental_agent.estimate_rent(
            city_code=city_code,
            structure=struct,
            building_age=0,
            area_sqm=25.0,
            station_distance_min=listing.walk_minutes or 7,
        )
        return result.get("rent_per_sqm", 3000)

    @staticmethod
    def _guess_city_code(address: str) -> str:
        """住所から市区町村コードを推定"""
        WARDS = {
            "千代田区": "13101", "中央区": "13102", "港区": "13103",
            "新宿区": "13104", "文京区": "13105", "台東区": "13106",
            "墨田区": "13107", "江東区": "13108", "品川区": "13109",
            "目黒区": "13110", "大田区": "13111", "世田谷区": "13112",
            "渋谷区": "13113", "中野区": "13114", "杉並区": "13115",
            "豊島区": "13116", "北区": "13117", "荒川区": "13118",
            "板橋区": "13119", "練馬区": "13120", "足立区": "13121",
            "葛飾区": "13122", "江戸川区": "13123",
        }
        for ward, code in WARDS.items():
            if ward in address:
                return code
        return "_default"
