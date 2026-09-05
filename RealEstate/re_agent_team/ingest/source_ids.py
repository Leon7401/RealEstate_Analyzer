"""ポータル出典の正規化（英語キーへ統一）"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Set

# DB / API / 自動判定で使う正規キー
CANONICAL_SOURCES = ("rakumachi", "kenbiya", "rals", "athome", "suumo")

# 表記ゆれ → 正規キー
_SOURCE_ALIASES: Dict[str, str] = {
    "rakumachi": "rakumachi",
    "楽待": "rakumachi",
    "kenbiya": "kenbiya",
    "健美家": "kenbiya",
    "rals": "rals",
    "不動産投資連合隊": "rals",
    "不動産投資★連合隊": "rals",
    "連合隊": "rals",
    "athome": "athome",
    "アットホーム": "athome",
    "homes": "homes",
    "HOME'S": "homes",
    "ホームズ": "homes",
    "toushi-athome": "athome",
    "suumo": "suumo",
    "SUUMO": "suumo",
    "スーモ": "suumo",
}

# 正規キー → DB照合用の別名（過去データ互換）
_SOURCE_QUERY_ALIASES: Dict[str, List[str]] = {
    "rakumachi": ["rakumachi", "楽待"],
    "kenbiya": ["kenbiya", "健美家"],
    "rals": ["rals", "不動産投資連合隊", "不動産投資★連合隊", "連合隊"],
    "athome": ["athome", "アットホーム"],
    "homes": ["homes", "HOME'S", "ホームズ"],
    "suumo": ["suumo", "SUUMO", "スーモ"],
}


def canonicalize_source(source: Optional[str]) -> Optional[str]:
    if source is None:
        return None
    raw = str(source).strip()
    if not raw:
        return None
    if raw in _SOURCE_ALIASES:
        return _SOURCE_ALIASES[raw]
    low = raw.lower()
    if low in _SOURCE_ALIASES:
        return _SOURCE_ALIASES[low]
    return low


def expand_sources_for_query(sources: Iterable[str]) -> List[str]:
    """auto_judge 等の SQL IN 用に別名を展開する。"""
    out: List[str] = []
    seen: Set[str] = set()
    for src in sources or []:
        key = canonicalize_source(src) or str(src).strip()
        aliases = _SOURCE_QUERY_ALIASES.get(key, [key])
        for a in aliases:
            if a and a not in seen:
                seen.add(a)
                out.append(a)
    return out or ["rakumachi", "楽待"]
