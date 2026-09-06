"""投資グレード色パレット（地図・一覧・結果パネルの単一ソース）"""
from typing import Dict

# 投資判定グレード専用。UI/地図/API はすべてここを参照する。
GRADE_PALETTE: Dict[str, str] = {
    "S": "#1a9641",
    "A": "#4dac26",
    "B": "#b8e186",
    "C": "#fdb863",
    "D": "#e66101",
    "F": "#d7191c",
}

# 資産性スコア用（投資グレードと視覚的に分離）
ASSET_GRADE_PALETTE: Dict[str, str] = {
    "S": "#0277bd",
    "A": "#0288d1",
    "B": "#4fc3f7",
    "C": "#81d4fa",
    "D": "#b0bec5",
    "F": "#78909c",
}

_VALID_GRADES = frozenset(GRADE_PALETTE.keys())


def normalize_grade(grade) -> str:
    g = str(grade or "").strip().upper()
    return g if g in _VALID_GRADES else ""


def grade_color(grade, *, asset: bool = False, fallback: str = "#546e7a") -> str:
    g = normalize_grade(grade)
    palette = ASSET_GRADE_PALETTE if asset else GRADE_PALETTE
    return palette.get(g, fallback)
