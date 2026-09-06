"""Source Adapter 共通IF"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from models.property import Property


class SourceAdapter(ABC):
    """ポータル別取得アダプタ。fetch_list / parse_detail を実装する。"""

    name: str = "base"

    @abstractmethod
    def fetch_list(
        self,
        prefecture_code: str,
        *,
        max_pages: int = 5,
        **kwargs,
    ) -> List[Property]:
        """一覧取得 → 正規化 Property リスト"""

    @abstractmethod
    def parse_detail(
        self,
        url: str,
        *,
        use_ocr: bool = True,
        use_browser: bool = False,
        **kwargs,
    ) -> Optional[Property]:
        """詳細URLから1件を構造化。OCRは HTML 失敗時の補完専用。"""

    def source_label(self) -> str:
        return self.name
