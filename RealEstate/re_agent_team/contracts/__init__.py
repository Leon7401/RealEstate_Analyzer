"""再設計の単一ソース契約（Listing / Analysis / Map / Grade Palette）"""

from .palette import (
    GRADE_PALETTE,
    ASSET_GRADE_PALETTE,
    grade_color,
    normalize_grade,
)
from .listing import ListingDTO, QualityFlags
from .analysis import AnalysisResultDTO, ScenarioResultDTO, SelectedJudgmentDTO
from .map_feature import MapFeatureDTO, MapGeometryDTO

# 実装クラス名との互換エイリアス
ListingDTO = ListingDTO
QualityFlags = QualityFlags
ScenarioResultDTO = ScenarioResultDTO
SelectedJudgmentDTO = SelectedJudgmentDTO

__all__ = [
    "GRADE_PALETTE",
    "ASSET_GRADE_PALETTE",
    "grade_color",
    "normalize_grade",
    "ListingDTO",
    "QualityFlags",
    "AnalysisResultDTO",
    "ScenarioResultDTO",
    "SelectedJudgmentDTO",
    "MapFeatureDTO",
    "MapGeometryDTO",
]
