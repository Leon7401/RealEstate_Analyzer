"""既存 ScraperAgent / UrlScraperAgent を Source Adapter 化するラッパ"""
from __future__ import annotations

from typing import List, Optional

from models.property import Property
from .base import SourceAdapter


class PortalListAdapter(SourceAdapter):
    """ScraperAgent のソース別一覧取得を Adapter 化"""

    def __init__(self, name: str, scraper_agent, url_scraper=None):
        self.name = name
        self._scraper = scraper_agent
        self._url_scraper = url_scraper

    def fetch_list(
        self,
        prefecture_code: str,
        *,
        max_pages: int = 5,
        split_by_price: bool = False,
        **kwargs,
    ) -> List[Property]:
        props = self._scraper.run(
            prefecture_code=prefecture_code,
            sources=[self.name],
            max_pages=max_pages,
            split_by_price=split_by_price,
        ) or []
        # 呼び出し側が参照できるよう直近エラーを保持
        self.last_errors = dict(getattr(self._scraper, "last_source_errors", {}) or {})
        return props

    def parse_detail(
        self,
        url: str,
        *,
        use_ocr: bool = True,
        use_browser: bool = False,
        **kwargs,
    ) -> Optional[Property]:
        if self._url_scraper is None:
            return None
        # OCR は HTML 取得失敗時のみ（UrlScraperAgent 側で制御）
        return self._url_scraper.run(url=url, use_ocr=use_ocr, use_browser=use_browser)


class UrlDetailAdapter(SourceAdapter):
    """URL指定取込専用 Adapter"""

    name = "url"

    def __init__(self, url_scraper):
        self._url_scraper = url_scraper

    def fetch_list(self, prefecture_code: str, *, max_pages: int = 5, **kwargs) -> List[Property]:
        return []

    def parse_detail(
        self,
        url: str,
        *,
        use_ocr: bool = True,
        use_browser: bool = False,
        **kwargs,
    ) -> Optional[Property]:
        return self._url_scraper.run(url=url, use_ocr=use_ocr, use_browser=use_browser)
