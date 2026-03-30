"""資産性スコアリングエージェント - 接道・ハザード・形状・標高・人口動態を総合評価"""
import math
import logging
import time
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, field, asdict

import requests

from .base_agent import BaseAgent
from config.settings import (
    HAZARD_FLOOD_MAX_DEPTH_M,
    MIN_FRONTAGE_FOR_MULTI_UNIT_M,
    REDEVELOPMENT_BONUS_STATIONS,
)


@dataclass
class RoadInfo:
    """接道情報"""
    fronting_roads: List[Dict] = field(default_factory=list)  # [{direction, width_m, road_type}]
    is_corner_lot: bool = False         # 角地
    is_flag_lot: bool = False           # 旗竿地
    road_count: int = 0                 # 接道数
    max_road_width: float = 0.0         # 最大前面道路幅員(m)
    min_road_width: float = 0.0         # 最小前面道路幅員(m)
    has_setback: bool = False           # セットバック必要
    road_score: float = 0.0            # 接道スコア(0-100)

    def to_dict(self):
        return asdict(self)


@dataclass
class HazardInfo:
    """ハザード情報"""
    flood_depth_m: Optional[float] = None       # 洪水浸水想定深(m)
    flood_risk_level: str = "unknown"            # low/medium/high/very_high/unknown
    landslide_risk: bool = False                 # 土砂災害警戒区域内
    tsunami_risk: bool = False                   # 津波浸水想定区域内
    liquefaction_risk: str = "unknown"           # low/medium/high/unknown
    hazard_score: float = 0.0                    # ハザードスコア(0-100, 100=安全)

    def to_dict(self):
        return asdict(self)


@dataclass
class ElevationInfo:
    """標高・地形情報"""
    elevation_m: Optional[float] = None          # 標高(m)
    slope_degree: Optional[float] = None         # 傾斜(度)
    is_fill_land: bool = False                   # 盛土推定
    is_cut_land: bool = False                    # 切土推定
    relative_elevation: Optional[float] = None   # 周辺平均との差(m)
    terrain_score: float = 0.0                  # 地形スコア(0-100)

    def to_dict(self):
        return asdict(self)


@dataclass
class LotShapeInfo:
    """敷地形状情報"""
    shape_type: str = "unknown"                  # regular/irregular/flag/narrow/unknown
    shape_label: str = "不明"                    # 整形地/不整形地/旗竿地/間口狭小
    is_corner: bool = False                      # 角地
    has_retaining_wall: bool = False             # 擁壁あり（検討対象外）
    has_step_retaining_wall: bool = False         # 階段擁壁（完全NG）
    frontage_m: Optional[float] = None           # 間口(m)
    depth_m: Optional[float] = None              # 奥行(m)
    frontage_depth_ratio: Optional[float] = None # 間口/奥行比
    multi_unit_per_floor: bool = True            # 1層2戸可能か
    shape_score: float = 0.0                     # 形状スコア(0-100)

    def to_dict(self):
        return asdict(self)


@dataclass
class PopulationInfo:
    """人口動態情報"""
    current_population: Optional[int] = None
    population_2020: Optional[int] = None
    population_2025: Optional[int] = None
    population_2030: Optional[int] = None
    population_2040: Optional[int] = None
    change_rate_5y: Optional[float] = None       # 5年間変化率
    change_rate_10y: Optional[float] = None      # 10年間変化率
    young_ratio: Optional[float] = None          # 若年層比率
    elderly_ratio: Optional[float] = None        # 高齢者比率
    population_score: float = 0.0                # 人口スコア(0-100)

    def to_dict(self):
        return asdict(self)


@dataclass
class AssetScoreResult:
    """資産性総合スコア"""
    overall_score: float = 0.0                   # 総合スコア(0-100)
    road_info: RoadInfo = field(default_factory=RoadInfo)
    hazard_info: HazardInfo = field(default_factory=HazardInfo)
    elevation_info: ElevationInfo = field(default_factory=ElevationInfo)
    lot_shape: LotShapeInfo = field(default_factory=LotShapeInfo)
    population: PopulationInfo = field(default_factory=PopulationInfo)
    station_distance_score: float = 0.0
    grade: str = "?"                             # S/A/B/C/D/F
    summary: str = ""

    def to_dict(self):
        return {
            "overall_score": self.overall_score,
            "grade": self.grade,
            "summary": self.summary,
            "station_distance_score": self.station_distance_score,
            "road_info": self.road_info.to_dict(),
            "hazard_info": self.hazard_info.to_dict(),
            "elevation_info": self.elevation_info.to_dict(),
            "lot_shape": self.lot_shape.to_dict(),
            "population": self.population.to_dict(),
        }


class AssetScoreAgent(BaseAgent):
    """
    物件の資産性を多角的に評価するエージェント
    - 接道状況（Overpass API: 道路幅員・角地・旗竿地）
    - ハザードリスク（国土地理院ハザードマップ）
    - 標高・盛土分析（国土地理院標高API）
    - 敷地形状（OSMデータ+推定）
    - 人口動態（e-Stat API）
    - 駅距離スコア
    """

    OVERPASS_URL = "https://overpass-api.de/api/interpreter"
    ELEVATION_URL = "https://cyberjapandata2.gsi.go.jp/general/dem/scripts/getelevation.php"

    # 配点ウェイト
    WEIGHTS = {
        "road": 0.20,
        "hazard": 0.25,
        "elevation": 0.10,
        "lot_shape": 0.15,
        "population": 0.15,
        "station": 0.15,
    }

    def __init__(self):
        super().__init__("AssetScoreAgent")
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "RealEstateAgentTeam/1.0"})

    def run(
        self,
        lat: float,
        lng: float,
        land_area_sqm: float = None,
        station_distance_min: int = None,
        station_name: str = None,
        city_code: str = None,
        prefecture_code: str = None,
        road_frontage: str = None,
        land_shape: str = None,
        has_retaining_wall: bool = False,
    ) -> AssetScoreResult:
        """資産性分析を実行"""
        self.logger.info(f"資産性分析開始: ({lat}, {lng})")

        result = AssetScoreResult()

        # 1. 接道分析
        try:
            result.road_info = self._analyze_road(lat, lng, road_frontage)
        except Exception as e:
            self.logger.warning(f"接道分析エラー: {e}")

        # 2. 標高分析
        try:
            result.elevation_info = self._analyze_elevation(lat, lng)
        except Exception as e:
            self.logger.warning(f"標高分析エラー: {e}")

        # 3. 敷地形状推定
        try:
            result.lot_shape = self._analyze_lot_shape(
                lat, lng, land_area_sqm, road_frontage, land_shape,
                result.road_info
            )
            # 擁壁フラグ反映
            if has_retaining_wall:
                result.lot_shape.has_retaining_wall = True
                result.lot_shape.shape_score = 0.0
        except Exception as e:
            self.logger.warning(f"形状分析エラー: {e}")

        # 4. 人口動態
        try:
            result.population = self._analyze_population(city_code, prefecture_code)
        except Exception as e:
            self.logger.warning(f"人口分析エラー: {e}")

        # 5. 駅距離スコア（再開発ボーナス込み）
        result.station_distance_score = self._calc_station_score(
            station_distance_min, station_name
        )

        # 6. ハザードスコア（標高情報を使って補完）
        try:
            result.hazard_info = self._analyze_hazard(lat, lng, result.elevation_info)
        except Exception as e:
            self.logger.warning(f"ハザード分析エラー: {e}")

        # 総合スコア計算
        result.overall_score = self._calc_overall_score(result)
        result.grade = self._determine_grade(result.overall_score)
        result.summary = self._build_summary(result)

        self.logger.info(f"資産性分析完了: スコア{result.overall_score:.1f} グレード{result.grade}")
        return result

    # ===== 接道分析 (Overpass API) =====

    def _analyze_road(self, lat: float, lng: float, road_frontage: str = None) -> RoadInfo:
        """OSM Overpass APIで物件周辺の道路を取得し接道状況を分析"""
        info = RoadInfo()

        # Overpass APIクエリ: 半径50m以内の道路
        query = f"""
        [out:json][timeout:10];
        (
          way["highway"](around:50,{lat},{lng});
        );
        out body;
        >;
        out skel qt;
        """

        try:
            resp = self._session.post(
                self.OVERPASS_URL,
                data={"data": query},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            self.logger.warning(f"Overpass APIエラー: {e}")
            # テキスト接道情報からフォールバック推定
            return self._fallback_road_info(road_frontage)

        # ノード座標マップ
        nodes = {}
        for el in data.get("elements", []):
            if el["type"] == "node":
                nodes[el["id"]] = (el["lat"], el["lon"])

        # 道路解析
        roads = []
        for el in data.get("elements", []):
            if el["type"] != "way":
                continue
            tags = el.get("tags", {})
            hw = tags.get("highway", "")
            if hw in ("footway", "cycleway", "path", "steps", "pedestrian", "service"):
                continue

            # 道路幅推定
            width = self._estimate_road_width(tags, hw)
            # 道路方向（物件からの相対方位）
            direction = self._calc_road_direction(lat, lng, el.get("nodes", []), nodes)

            roads.append({
                "direction": direction,
                "width_m": width,
                "road_type": hw,
                "name": tags.get("name", ""),
            })

        if not roads:
            return self._fallback_road_info(road_frontage)

        info.fronting_roads = roads
        info.road_count = len(roads)
        info.max_road_width = max(r["width_m"] for r in roads)
        info.min_road_width = min(r["width_m"] for r in roads)
        info.has_setback = info.max_road_width < 4.0

        # 角地判定: 異なる方向から2本以上の道路
        directions = set(r["direction"] for r in roads)
        if len(directions) >= 2:
            info.is_corner_lot = True

        # 旗竿地判定: 近くの道路が1本で幅員が狭い
        if len(roads) == 1 and roads[0]["width_m"] < 3.0:
            info.is_flag_lot = True

        # スコア計算
        info.road_score = self._calc_road_score(info)

        return info

    def _estimate_road_width(self, tags: dict, highway_type: str) -> float:
        """OSMタグから道路幅員を推定"""
        # 明示的なwidthタグ
        w = tags.get("width")
        if w:
            try:
                return float(w.replace("m", "").strip())
            except ValueError:
                pass

        # lanesから推定
        lanes = tags.get("lanes")
        if lanes:
            try:
                return float(lanes) * 3.0
            except ValueError:
                pass

        # highway typeから推定
        width_defaults = {
            "motorway": 14.0,
            "trunk": 12.0,
            "primary": 10.0,
            "secondary": 8.0,
            "tertiary": 6.0,
            "unclassified": 5.0,
            "residential": 4.5,
            "living_street": 4.0,
            "track": 3.0,
        }
        return width_defaults.get(highway_type, 4.0)

    def _calc_road_direction(
        self, lat: float, lng: float, node_ids: list, nodes: dict
    ) -> str:
        """道路の物件に対する方位を計算"""
        if not node_ids or not nodes:
            return "N"

        # 道路の中点を計算
        coords = [nodes[nid] for nid in node_ids if nid in nodes]
        if not coords:
            return "N"

        mid_lat = sum(c[0] for c in coords) / len(coords)
        mid_lng = sum(c[1] for c in coords) / len(coords)

        dlat = mid_lat - lat
        dlng = mid_lng - lng

        angle = math.degrees(math.atan2(dlng, dlat)) % 360

        if angle < 45 or angle >= 315:
            return "N"
        elif angle < 135:
            return "E"
        elif angle < 225:
            return "S"
        else:
            return "W"

    def _fallback_road_info(self, road_frontage: str = None) -> RoadInfo:
        """テキスト接道情報からフォールバック推定"""
        info = RoadInfo()
        if not road_frontage:
            info.road_score = 50.0  # 不明
            return info

        text = road_frontage
        # 幅員抽出
        import re
        widths = re.findall(r'(\d+\.?\d*)\s*[mM]', text)
        if widths:
            widths = [float(w) for w in widths]
            info.max_road_width = max(widths)
            info.min_road_width = min(widths)
            info.road_count = len(widths)

        # 角地
        if "角地" in text or "二方" in text or "三方" in text:
            info.is_corner_lot = True
            info.road_count = max(info.road_count, 2)

        # 旗竿
        if "旗竿" in text or "路地" in text or "専用通路" in text:
            info.is_flag_lot = True

        # セットバック
        if "セットバック" in text or info.max_road_width < 4.0:
            info.has_setback = True

        info.road_score = self._calc_road_score(info)
        return info

    def _calc_road_score(self, info: RoadInfo) -> float:
        """接道スコア計算(0-100)"""
        score = 50.0  # ベース

        # 前面道路幅員
        w = info.max_road_width
        if w >= 8.0:
            score += 30
        elif w >= 6.0:
            score += 20
        elif w >= 4.0:
            score += 10
        elif w > 0:
            score -= 20  # 4m未満はセットバック必要

        # 角地ボーナス
        if info.is_corner_lot:
            score += 15

        # 旗竿ペナルティ
        if info.is_flag_lot:
            score -= 25

        # 複数接道ボーナス
        if info.road_count >= 2:
            score += 5

        return max(0, min(100, score))

    # ===== 標高・盛土分析 =====

    def _analyze_elevation(self, lat: float, lng: float) -> ElevationInfo:
        """国土地理院標高APIで標高取得＋周辺との比較で盛土/切土推定"""
        info = ElevationInfo()

        # 中心点の標高
        center_elev = self._get_elevation(lat, lng)
        if center_elev is None:
            info.terrain_score = 50.0
            return info

        info.elevation_m = center_elev

        # 周辺8点の標高（約100m間隔）
        offset = 0.001  # 約111m
        surrounding = []
        for dlat in [-offset, 0, offset]:
            for dlng in [-offset, 0, offset]:
                if dlat == 0 and dlng == 0:
                    continue
                elev = self._get_elevation(lat + dlat, lng + dlng)
                if elev is not None:
                    surrounding.append(elev)
                time.sleep(0.1)  # API負荷軽減

        if surrounding:
            avg_surrounding = sum(surrounding) / len(surrounding)
            info.relative_elevation = center_elev - avg_surrounding

            # 傾斜計算（最大高低差 / 距離）
            max_diff = max(abs(center_elev - s) for s in surrounding)
            distance_m = offset * 111000  # 約111m
            info.slope_degree = math.degrees(math.atan(max_diff / distance_m))

            # 盛土/切土推定
            # 周辺より2m以上高い→盛土の可能性
            if info.relative_elevation > 2.0:
                info.is_fill_land = True
            # 周辺より2m以上低い→切土/窪地
            elif info.relative_elevation < -2.0:
                info.is_cut_land = True

        # スコア
        info.terrain_score = self._calc_terrain_score(info)
        return info

    def _get_elevation(self, lat: float, lng: float) -> Optional[float]:
        """国土地理院標高API"""
        try:
            resp = self._session.get(
                self.ELEVATION_URL,
                params={"lon": lng, "lat": lat, "outtype": "JSON"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            elev = data.get("elevation")
            if elev is not None and elev != "-----":
                return float(elev)
        except Exception:
            pass
        return None

    def _calc_terrain_score(self, info: ElevationInfo) -> float:
        """地形スコア(0-100)"""
        score = 70.0  # ベース

        # 標高が高めは良い（水害リスク低）
        if info.elevation_m is not None:
            if info.elevation_m >= 30:
                score += 15
            elif info.elevation_m >= 10:
                score += 5
            elif info.elevation_m < 3:
                score -= 20  # 低地は水害リスク

        # 盛土はマイナス
        if info.is_fill_land:
            score -= 20

        # 急傾斜はマイナス
        if info.slope_degree is not None and info.slope_degree > 15:
            score -= 15

        # 周辺より高い位置はプラス（水はけ良好）
        if info.relative_elevation is not None:
            if info.relative_elevation > 1.0:
                score += 10
            elif info.relative_elevation < -1.0:
                score -= 10

        return max(0, min(100, score))

    # ===== ハザード分析 =====

    def _analyze_hazard(
        self, lat: float, lng: float, elevation: ElevationInfo
    ) -> HazardInfo:
        """標高・位置情報からハザードリスクを推定"""
        info = HazardInfo()

        # 標高ベースの洪水リスク推定
        if elevation.elevation_m is not None:
            elev = elevation.elevation_m
            if elev < 2:
                info.flood_risk_level = "very_high"
                info.flood_depth_m = 3.0
            elif elev < 5:
                info.flood_risk_level = "high"
                info.flood_depth_m = 1.5
            elif elev < 10:
                info.flood_risk_level = "medium"
                info.flood_depth_m = 0.5
            else:
                info.flood_risk_level = "low"
                info.flood_depth_m = 0.0

        # 盛土地は液状化リスク
        if elevation.is_fill_land:
            info.liquefaction_risk = "high"
        elif elevation.elevation_m is not None and elevation.elevation_m < 5:
            info.liquefaction_risk = "medium"
        else:
            info.liquefaction_risk = "low"

        # 急傾斜地は土砂リスク
        if elevation.slope_degree is not None and elevation.slope_degree > 20:
            info.landslide_risk = True

        # ハザードスコア計算（100=安全）
        info.hazard_score = self._calc_hazard_score(info)
        return info

    def _calc_hazard_score(self, info: HazardInfo) -> float:
        """ハザードスコア(0-100, 100=安全)

        基準（CLAUDE.md）:
        - 浸水2m以内なら検討対象内（スコア減はあるが0にはしない）
        - 浸水2m超は原則NG（スコア大幅減）
        - 土砂災害特別警戒区域・家屋倒壊等氾濫想定区域は完全NG
        """
        score = 100.0

        # 洪水リスク（2m基準）
        if info.flood_depth_m is not None:
            if info.flood_depth_m > HAZARD_FLOOD_MAX_DEPTH_M:
                score -= 50  # 2m超: ほぼNG
            elif info.flood_depth_m > 1.0:
                score -= 25  # 1-2m: 要注意だが検討可
            elif info.flood_depth_m > 0.5:
                score -= 10  # 0.5-1m: 軽微
            # 0.5m以下: ペナルティなし
        elif info.flood_risk_level != "low":
            flood_penalty = {"very_high": 50, "high": 25, "medium": 10, "unknown": 5}
            score -= flood_penalty.get(info.flood_risk_level, 5)

        # 土砂リスク
        if info.landslide_risk:
            score -= 30  # 土砂警戒区域

        # 津波リスク
        if info.tsunami_risk:
            score -= 20

        # 液状化リスク
        liq_penalty = {"high": 20, "medium": 10, "low": 0, "unknown": 5}
        score -= liq_penalty.get(info.liquefaction_risk, 5)

        return max(0, min(100, score))

    # ===== 敷地形状分析 =====

    def _analyze_lot_shape(
        self,
        lat: float,
        lng: float,
        land_area_sqm: float = None,
        road_frontage: str = None,
        land_shape: str = None,
        road_info: RoadInfo = None,
    ) -> LotShapeInfo:
        """敷地形状を推定"""
        info = LotShapeInfo()

        # 既存データからの判定
        if land_shape:
            text = land_shape
            if "整形" in text:
                info.shape_type = "regular"
                info.shape_label = "整形地"
            elif "不整形" in text:
                info.shape_type = "irregular"
                info.shape_label = "不整形地"
            elif "旗竿" in text or "旗" in text:
                info.shape_type = "flag"
                info.shape_label = "旗竿地"

        # road_infoから角地判定
        if road_info and road_info.is_corner_lot:
            info.is_corner = True
        if road_info and road_info.is_flag_lot and info.shape_type == "unknown":
            info.shape_type = "flag"
            info.shape_label = "旗竿地"

        # 接道情報から間口推定
        if road_frontage:
            import re
            frontage_m = re.findall(r'間口[約]?(\d+\.?\d*)', road_frontage)
            if frontage_m:
                info.frontage_m = float(frontage_m[0])

        # 間口と面積から奥行推定
        if info.frontage_m and land_area_sqm:
            info.depth_m = land_area_sqm / info.frontage_m
            info.frontage_depth_ratio = info.frontage_m / info.depth_m

            # 間口狭小判定
            if info.frontage_m < 4.0:
                info.shape_type = "narrow"
                info.shape_label = "間口狭小"
            elif info.frontage_depth_ratio < 0.3:
                info.shape_type = "narrow"
                info.shape_label = "間口狭小（うなぎの寝床）"

        # デフォルト: 面積から推定
        if info.shape_type == "unknown" and land_area_sqm:
            # 小さい土地は不整形の可能性が高い
            if land_area_sqm < 40:
                info.shape_type = "irregular"
                info.shape_label = "狭小地"
            else:
                info.shape_type = "regular"
                info.shape_label = "整形地（推定）"

        info.shape_score = self._calc_shape_score(info)
        return info

    def _calc_shape_score(self, info: LotShapeInfo) -> float:
        """形状スコア(0-100)

        基準（CLAUDE.md）:
        - 擁壁・階段擁壁は例外なく除外 → スコア0
        - 間口6.5m以上で1層2戸可能 → 加点
        - 整形地（南北奥行）が最良
        - 角地は建蔽率+10%緩和で加点
        """
        # 擁壁は完全NG
        if info.has_retaining_wall or info.has_step_retaining_wall:
            return 0.0

        base_scores = {
            "regular": 80,
            "irregular": 50,
            "flag": 35,
            "narrow": 30,
            "unknown": 50,
        }
        score = float(base_scores.get(info.shape_type, 50))

        # 角地ボーナス（建蔽率+10%緩和 + 斜線制限緩和）
        if info.is_corner:
            score += 15

        # 間口6.5m以上で1層2戸可能
        if info.frontage_m is not None:
            if info.frontage_m >= MIN_FRONTAGE_FOR_MULTI_UNIT_M:
                score += 10
                info.multi_unit_per_floor = True
            elif info.frontage_m < 4.0:
                score -= 15  # 間口狭小: 建築困難
                info.multi_unit_per_floor = False
            else:
                score -= 5   # 6.5m未満: 1層2戸が困難
                info.multi_unit_per_floor = False

        # 間口/奥行比が良好（南北に奥行きある正方形が最良）
        if info.frontage_depth_ratio:
            if 0.5 <= info.frontage_depth_ratio <= 1.5:
                score += 10
            elif info.frontage_depth_ratio < 0.3:
                score -= 10

        return max(0, min(100, score))

    # ===== 人口動態分析 =====

    def _analyze_population(
        self, city_code: str = None, prefecture_code: str = None
    ) -> PopulationInfo:
        """人口動態を分析（参照テーブルベース）"""
        info = PopulationInfo()

        if not city_code:
            info.population_score = 50.0
            return info

        # 東京23区+主要市の人口動態参照テーブル
        # 2020国勢調査実績 → 2025/2030/2040推計（社人研データベース）
        POP_DATA = {
            "13101": {"name": "千代田区",  "pop2020": 66680,  "rate_5y": 0.08,  "rate_10y": 0.12, "young": 0.12, "old": 0.18},
            "13102": {"name": "中央区",    "pop2020": 169179, "rate_5y": 0.12,  "rate_10y": 0.18, "young": 0.12, "old": 0.15},
            "13103": {"name": "港区",      "pop2020": 260486, "rate_5y": 0.08,  "rate_10y": 0.12, "young": 0.11, "old": 0.16},
            "13104": {"name": "新宿区",    "pop2020": 346235, "rate_5y": 0.02,  "rate_10y": 0.01, "young": 0.11, "old": 0.20},
            "13105": {"name": "文京区",    "pop2020": 240069, "rate_5y": 0.05,  "rate_10y": 0.07, "young": 0.11, "old": 0.19},
            "13106": {"name": "台東区",    "pop2020": 211444, "rate_5y": 0.04,  "rate_10y": 0.05, "young": 0.10, "old": 0.22},
            "13107": {"name": "墨田区",    "pop2020": 272085, "rate_5y": 0.03,  "rate_10y": 0.03, "young": 0.11, "old": 0.22},
            "13108": {"name": "江東区",    "pop2020": 524310, "rate_5y": 0.05,  "rate_10y": 0.08, "young": 0.12, "old": 0.20},
            "13109": {"name": "品川区",    "pop2020": 422488, "rate_5y": 0.04,  "rate_10y": 0.06, "young": 0.11, "old": 0.19},
            "13110": {"name": "目黒区",    "pop2020": 288088, "rate_5y": 0.02,  "rate_10y": 0.02, "young": 0.10, "old": 0.19},
            "13111": {"name": "大田区",    "pop2020": 748081, "rate_5y": 0.01,  "rate_10y": 0.00, "young": 0.10, "old": 0.23},
            "13112": {"name": "世田谷区",  "pop2020": 943664, "rate_5y": 0.02,  "rate_10y": 0.02, "young": 0.10, "old": 0.19},
            "13113": {"name": "渋谷区",    "pop2020": 243883, "rate_5y": 0.03,  "rate_10y": 0.04, "young": 0.10, "old": 0.18},
            "13114": {"name": "中野区",    "pop2020": 344880, "rate_5y": 0.01,  "rate_10y": 0.00, "young": 0.10, "old": 0.20},
            "13115": {"name": "杉並区",    "pop2020": 591108, "rate_5y": 0.01,  "rate_10y": 0.00, "young": 0.09, "old": 0.21},
            "13116": {"name": "豊島区",    "pop2020": 301599, "rate_5y": 0.02,  "rate_10y": 0.02, "young": 0.11, "old": 0.20},
            "13117": {"name": "北区",      "pop2020": 355213, "rate_5y": 0.01,  "rate_10y": -0.01, "young": 0.10, "old": 0.24},
            "13118": {"name": "荒川区",    "pop2020": 217475, "rate_5y": 0.02,  "rate_10y": 0.01, "young": 0.11, "old": 0.23},
            "13119": {"name": "板橋区",    "pop2020": 584483, "rate_5y": 0.01,  "rate_10y": -0.01, "young": 0.10, "old": 0.23},
            "13120": {"name": "練馬区",    "pop2020": 752608, "rate_5y": 0.01,  "rate_10y": -0.01, "young": 0.10, "old": 0.23},
            "13121": {"name": "足立区",    "pop2020": 695043, "rate_5y": -0.01, "rate_10y": -0.04, "young": 0.10, "old": 0.26},
            "13122": {"name": "葛飾区",    "pop2020": 453093, "rate_5y": -0.01, "rate_10y": -0.03, "young": 0.10, "old": 0.25},
            "13123": {"name": "江戸川区",  "pop2020": 697932, "rate_5y": -0.01, "rate_10y": -0.04, "young": 0.11, "old": 0.22},
            # 主要市（神奈川・埼玉・千葉）
            "14101": {"name": "横浜市鶴見区",  "pop2020": 295340, "rate_5y": 0.01, "rate_10y": -0.01, "young": 0.11, "old": 0.22},
            "14102": {"name": "横浜市神奈川区","pop2020": 246640, "rate_5y": 0.01, "rate_10y": 0.00, "young": 0.11, "old": 0.21},
            "14103": {"name": "横浜市西区",    "pop2020": 105031, "rate_5y": 0.05, "rate_10y": 0.08, "young": 0.12, "old": 0.18},
            "14104": {"name": "横浜市中区",    "pop2020": 151960, "rate_5y": 0.02, "rate_10y": 0.02, "young": 0.10, "old": 0.22},
            "11101": {"name": "さいたま市西区","pop2020": 92804,  "rate_5y": 0.00, "rate_10y": -0.02, "young": 0.10, "old": 0.25},
            "12101": {"name": "千葉市中央区",  "pop2020": 212690, "rate_5y": 0.00, "rate_10y": -0.02, "young": 0.10, "old": 0.24},
        }

        pop = POP_DATA.get(city_code)
        if not pop:
            # 都道府県デフォルト
            pref_defaults = {
                "13": {"rate_5y": 0.02, "rate_10y": 0.01, "young": 0.10, "old": 0.22, "pop2020": 500000},
                "14": {"rate_5y": 0.00, "rate_10y": -0.02, "young": 0.10, "old": 0.25, "pop2020": 300000},
                "11": {"rate_5y": -0.01, "rate_10y": -0.04, "young": 0.10, "old": 0.27, "pop2020": 200000},
                "12": {"rate_5y": -0.01, "rate_10y": -0.04, "young": 0.10, "old": 0.27, "pop2020": 200000},
            }
            pop = pref_defaults.get(prefecture_code, {
                "rate_5y": -0.02, "rate_10y": -0.06, "young": 0.09, "old": 0.30, "pop2020": 100000
            })

        info.population_2020 = pop.get("pop2020")
        info.change_rate_5y = pop.get("rate_5y")
        info.change_rate_10y = pop.get("rate_10y")
        info.young_ratio = pop.get("young")
        info.elderly_ratio = pop.get("old")

        if info.population_2020 and info.change_rate_5y is not None:
            info.population_2025 = int(info.population_2020 * (1 + info.change_rate_5y))
            info.population_2030 = int(info.population_2020 * (1 + (info.change_rate_10y or info.change_rate_5y * 2)))
            info.population_2040 = int(info.population_2030 * (1 + (info.change_rate_10y or -0.03)))

        info.population_score = self._calc_population_score(info)
        return info

    def _calc_population_score(self, info: PopulationInfo) -> float:
        """人口スコア(0-100)"""
        score = 50.0

        # 人口増減率
        if info.change_rate_5y is not None:
            if info.change_rate_5y > 0.05:
                score += 30
            elif info.change_rate_5y > 0.02:
                score += 20
            elif info.change_rate_5y > 0:
                score += 10
            elif info.change_rate_5y > -0.02:
                score += 0
            elif info.change_rate_5y > -0.05:
                score -= 15
            else:
                score -= 30

        # 若年層比率
        if info.young_ratio is not None:
            if info.young_ratio >= 0.12:
                score += 10
            elif info.young_ratio < 0.09:
                score -= 10

        # 高齢者比率
        if info.elderly_ratio is not None:
            if info.elderly_ratio >= 0.30:
                score -= 15
            elif info.elderly_ratio <= 0.18:
                score += 10

        return max(0, min(100, score))

    # ===== 駅距離スコア =====

    def _calc_station_score(
        self, station_distance_min: int = None, station_name: str = None
    ) -> float:
        """駅徒歩分数からスコア算出

        基準（CLAUDE.md）:
        - 徒歩10分以内が検討対象（エリア次第で11-12分も可）
        - 徒歩13分以上は原則対象外
        - 新駅開発・駅前再開発エリアは加点
        """
        if station_distance_min is None:
            return 50.0

        d = station_distance_min
        if d <= 3:
            score = 100.0
        elif d <= 5:
            score = 90.0
        elif d <= 7:
            score = 80.0
        elif d <= 10:
            score = 65.0
        elif d <= 12:
            score = 45.0   # エリア次第で検討可
        elif d <= 15:
            score = 20.0   # 13分以上は原則対象外
        else:
            score = 5.0

        # 新駅・再開発ボーナス
        if station_name:
            for key, bonus in REDEVELOPMENT_BONUS_STATIONS.items():
                if key in station_name:
                    score = min(100, score + bonus)
                    break

        return score

    # ===== 総合スコア =====

    def _calc_overall_score(self, result: AssetScoreResult) -> float:
        """加重平均で総合スコア算出"""
        scores = {
            "road": result.road_info.road_score,
            "hazard": result.hazard_info.hazard_score,
            "elevation": result.elevation_info.terrain_score,
            "lot_shape": result.lot_shape.shape_score,
            "population": result.population.population_score,
            "station": result.station_distance_score,
        }

        total = sum(scores[k] * self.WEIGHTS[k] for k in self.WEIGHTS)
        return round(total, 1)

    def _determine_grade(self, score: float) -> str:
        if score >= 85:
            return "S"
        elif score >= 70:
            return "A"
        elif score >= 55:
            return "B"
        elif score >= 40:
            return "C"
        elif score >= 25:
            return "D"
        else:
            return "F"

    def _build_summary(self, result: AssetScoreResult) -> str:
        """サマリーテキスト生成"""
        parts = []

        # 接道
        ri = result.road_info
        if ri.is_corner_lot:
            parts.append("角地")
        if ri.is_flag_lot:
            parts.append("旗竿地")
        if ri.max_road_width > 0:
            parts.append(f"前面道路{ri.max_road_width:.1f}m")
        if ri.has_setback:
            parts.append("セットバック要")

        # 形状
        ls = result.lot_shape
        if ls.shape_label != "不明":
            parts.append(ls.shape_label)

        # ハザード
        hi = result.hazard_info
        if hi.flood_risk_level in ("high", "very_high"):
            parts.append(f"洪水リスク{hi.flood_risk_level}")
        if hi.landslide_risk:
            parts.append("土砂警戒")
        if hi.liquefaction_risk == "high":
            parts.append("液状化リスク高")

        # 標高
        ei = result.elevation_info
        if ei.elevation_m is not None:
            parts.append(f"標高{ei.elevation_m:.1f}m")
        if ei.is_fill_land:
            parts.append("盛土推定")

        # 人口
        pi = result.population
        if pi.change_rate_5y is not None:
            sign = "+" if pi.change_rate_5y > 0 else ""
            parts.append(f"人口{sign}{pi.change_rate_5y*100:.1f}%/5y")

        return " / ".join(parts)
