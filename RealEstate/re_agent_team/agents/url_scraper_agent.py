"""
URL指定物件スクレイパー - 1件のURLから物件情報を構造化

対応サイト:
- 楽待 (rakumachi.jp)
- アットホーム (athome.co.jp)
- SUUMO (suumo.jp)
- HOME'S (homes.co.jp)
- 健美家 (kenbiya.com)
- 不動産投資★連合隊 (rals.co.jp / fudosan.cbiz.ne.jp)
- 汎用フォールバック

機能:
- HTML解析による構造化データ抽出
- 画像OCR（間取り図・物件概要画像）
- DB保存
"""
import io
import re
import time
import math
import logging
from typing import Optional, Dict, List, Tuple
from urllib.parse import urlparse
from pathlib import Path
from datetime import datetime

import requests
from bs4 import BeautifulSoup, Tag

from .base_agent import BaseAgent
from models.property import Property
from ingest.source_ids import canonicalize_source

logger = logging.getLogger(__name__)


# OCR利用可否チェック
_OCR_AVAILABLE = False
try:
    from PIL import Image
    import pytesseract
    _OCR_AVAILABLE = True
except ImportError:
    logger.info("OCR無効: pip install Pillow pytesseract + Tesseract本体が必要")

# Playwright利用可否チェック
_PLAYWRIGHT_AVAILABLE = False
try:
    from playwright.sync_api import sync_playwright
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    logger.info("Playwright無効: pip install playwright && playwright install chromium")


class UrlScraperAgent(BaseAgent):
    """
    URLを指定して1件の物件情報をクロール＋OCR→構造化

    使い方:
        agent = UrlScraperAgent()
        prop = agent.run(url="https://www.rakumachi.jp/syuuekibukken/detail/xxxxx")
    """

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    }

    # サイト判定用パターン
    SITE_PATTERNS = {
        "rakumachi": r"rakumachi\.jp",
        "athome": r"athome\.co\.jp|atbb\.athome\.co\.jp",
        "suumo": r"suumo\.jp",
        "homes": r"homes\.co\.jp",
        "kenbiya": r"kenbiya\.com",
        "rals": r"rals\.co\.jp|fudosan\.cbiz\.ne\.jp|rals\.net",
        "fudousan_japan": r"fudousan\.or\.jp",
    }

    def __init__(self):
        super().__init__("UrlScraperAgent")
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)
        self._rate_limit = 3.0
        self._last_request = 0.0
        self._image_dir = Path(__file__).parent.parent / "output" / "cache" / "images"
        self._image_dir.mkdir(parents=True, exist_ok=True)
        self._geocode_cache: Dict[str, Tuple[float, float]] = {}

    def run(self, url: str, use_ocr: bool = True, use_browser: bool = False) -> Optional[Property]:
        """
        URLから物件情報を取得して構造化

        Args:
            url: 物件詳細ページのURL
            use_ocr: 画像OCRを使用するか
            use_browser: Playwrightブラウザを使用するか（JS描画対応）

        Returns:
            Property or None
        """
        self.logger.info(f"URL取込開始: {url}")

        # サイト判定
        site = self._detect_site(url)
        self.logger.info(f"  サイト判定: {site}")

        # HTML取得
        html, images_data = self._fetch_page(url, use_browser)
        if not html:
            self.logger.error("  HTML取得失敗")
            return None

        # サイト別パース
        prop_data = self._parse_by_site(site, html, url)

        # OCR（有効かつ画像あれば）
        ocr_texts = []
        if use_ocr and _OCR_AVAILABLE:
            ocr_texts = self._extract_images_and_ocr(html, url, images_data)
            if ocr_texts:
                self.logger.info(f"  OCR抽出: {len(ocr_texts)}件")
                # OCR結果で補完
                self._supplement_from_ocr(prop_data, ocr_texts)

        if not prop_data.get("name"):
            self.logger.warning("  物件名が取得できませんでした")
            return None

        # Property生成
        prop = Property(
            name=prop_data.get("name", "名称不明"),
            address=prop_data.get("address", ""),
            prefecture_code=prop_data.get("prefecture_code", ""),
            city_code=prop_data.get("city_code", ""),
            asking_price=prop_data.get("asking_price"),
            land_area=prop_data.get("land_area"),
            building_area=prop_data.get("building_area"),
            structure=prop_data.get("structure"),
            built_year=prop_data.get("built_year"),
            building_age=prop_data.get("building_age"),
            gross_yield=prop_data.get("gross_yield"),
            nearest_station=prop_data.get("nearest_station"),
            station_distance_min=prop_data.get("station_distance_min"),
            current_rent_annual=prop_data.get("current_rent_annual"),
            land_use_zone=prop_data.get("land_use_zone"),
            building_coverage=prop_data.get("building_coverage"),
            floor_area_ratio=prop_data.get("floor_area_ratio"),
            floors=prop_data.get("floors"),
            units=prop_data.get("units"),
            occupancy_rate=prop_data.get("occupancy_rate"),
            source=canonicalize_source(site) or site,
            source_url=url,
        )

        self.logger.info(
            f"  取込完了: {prop.name} | "
            f"価格: {prop.asking_price:,}円" if prop.asking_price else "価格不明"
        )
        return prop

    # ===== サイト判定 =====

    def _detect_site(self, url: str) -> str:
        for site, pattern in self.SITE_PATTERNS.items():
            if re.search(pattern, url):
                return site
        return "generic"

    # ===== ページ取得 =====

    def _throttle(self):
        elapsed = time.time() - self._last_request
        if elapsed < self._rate_limit:
            time.sleep(self._rate_limit - elapsed)
        self._last_request = time.time()

    def _fetch_page(
        self, url: str, use_browser: bool = False
    ) -> Tuple[Optional[str], List[bytes]]:
        """ページHTMLと画像バイナリを取得"""
        self._throttle()

        # Playwright（JS描画が必要な場合）
        if use_browser and _PLAYWRIGHT_AVAILABLE:
            return self._fetch_with_playwright(url)

        # requests（通常）
        try:
            resp = self.session.get(url, timeout=20)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "utf-8"
            return resp.text, []
        except requests.RequestException as e:
            self.logger.error(f"HTTP取得エラー: {e}")
            # requestsで失敗 → Playwright試行
            if _PLAYWRIGHT_AVAILABLE:
                self.logger.info("Playwrightにフォールバック")
                return self._fetch_with_playwright(url)
            return None, []

    def _fetch_with_playwright(self, url: str) -> Tuple[Optional[str], List[bytes]]:
        """Playwrightでブラウザ描画後のHTMLを取得"""
        try:
            from .browser_fetch import get_browser_fetcher, looks_blocked

            fetcher = get_browser_fetcher()
            html, status, err = fetcher.fetch(url, wait_ms=2000)
            if not html or looks_blocked(html, status):
                self.logger.error(f"Playwright取得拒否: {err or status}")
                return None, []
            return html, []
        except Exception as e:
            self.logger.error(f"Playwright取得エラー: {e}")
            return None, []

    # ===== サイト別パーサー =====

    def _parse_by_site(self, site: str, html: str, url: str) -> Dict:
        """サイトに応じたパーサーを呼び出し"""
        parsers = {
            "rakumachi": self._parse_rakumachi,
            "athome": self._parse_athome,
            "suumo": self._parse_suumo,
            "homes": self._parse_homes,
            "kenbiya": self._parse_kenbiya,
            "rals": self._parse_rals,
        }
        parser = parsers.get(site, self._parse_generic)
        data = parser(html, url)
        # 共通後処理
        self._postprocess(data)
        return data

    def _parse_rakumachi(self, html: str, url: str) -> Dict:
        """楽待パーサー"""
        soup = BeautifulSoup(html, "html.parser")
        data = {}

        # 物件名
        title = soup.select_one("h1, .detail_title, .property-title, .bukken-name")
        data["name"] = title.get_text(strip=True) if title else ""

        # テーブル形式のデータ抽出（楽待は <th>/<td> のペア）
        table_data = self._extract_table_pairs(soup)

        # テーブルからマッピング
        data["address"] = (
            table_data.get("所在地", "")
            or table_data.get("住所", "")
        )
        data["asking_price"] = self._parse_price(
            table_data.get("価格", "")
            or table_data.get("販売価格", "")
        )
        data["gross_yield"] = self._parse_yield(
            table_data.get("利回り", "")
            or table_data.get("表面利回り", "")
            or table_data.get("想定利回り", "")
        )
        data["land_area"] = self._parse_area(table_data.get("土地面積", ""))
        data["building_area"] = self._parse_area(
            table_data.get("建物面積", "")
            or table_data.get("延床面積", "")
        )
        data["structure"] = self._parse_structure(table_data.get("構造", ""))
        data["built_year"], data["building_age"] = self._parse_built_year(
            table_data.get("築年月", "")
            or table_data.get("築年数", "")
        )
        data["nearest_station"], data["station_distance_min"] = self._parse_station(
            table_data.get("交通", "")
            or table_data.get("最寄り駅", "")
        )
        data["land_use_zone"] = table_data.get("用途地域", "")
        data["building_coverage"] = self._parse_ratio(table_data.get("建蔽率", ""))
        data["floor_area_ratio"] = self._parse_ratio(table_data.get("容積率", ""))
        data["units"] = self._parse_int(table_data.get("総戸数", ""))
        data["current_rent_annual"] = self._parse_annual_rent(
            table_data.get("年間収入", "")
            or table_data.get("満室想定年収", "")
            or table_data.get("現行年収", "")
        )
        data["occupancy_rate"] = self._parse_ratio(table_data.get("稼働率", ""))

        return data

    def _parse_athome(self, html: str, url: str) -> Dict:
        """アットホームパーサー"""
        soup = BeautifulSoup(html, "html.parser")
        data = {}

        # 物件名
        title = soup.select_one(
            "h1, .property_view-heading, .property-name, .detail-name"
        )
        data["name"] = title.get_text(strip=True) if title else ""

        # テーブル形式データ抽出
        table_data = self._extract_table_pairs(soup)

        data["address"] = (
            table_data.get("所在地", "")
            or table_data.get("住所", "")
        )
        data["asking_price"] = self._parse_price(
            table_data.get("価格", "")
            or table_data.get("販売価格", "")
        )
        data["gross_yield"] = self._parse_yield(
            table_data.get("利回り", "")
            or table_data.get("想定利回り", "")
        )
        data["land_area"] = self._parse_area(
            table_data.get("土地面積", "")
            or table_data.get("敷地面積", "")
        )
        data["building_area"] = self._parse_area(
            table_data.get("建物面積", "")
            or table_data.get("延床面積", "")
            or table_data.get("専有面積", "")
        )
        data["structure"] = self._parse_structure(
            table_data.get("構造", "")
            or table_data.get("建物構造", "")
        )
        data["built_year"], data["building_age"] = self._parse_built_year(
            table_data.get("築年月", "")
            or table_data.get("築年数", "")
        )
        data["nearest_station"], data["station_distance_min"] = self._parse_station(
            table_data.get("交通", "")
            or table_data.get("最寄り駅", "")
        )
        data["land_use_zone"] = table_data.get("用途地域", "")
        data["building_coverage"] = self._parse_ratio(table_data.get("建蔽率", ""))
        data["floor_area_ratio"] = self._parse_ratio(table_data.get("容積率", ""))
        data["units"] = self._parse_int(table_data.get("総戸数", ""))

        return data

    def _parse_suumo(self, html: str, url: str) -> Dict:
        """SUUMOパーサー"""
        soup = BeautifulSoup(html, "html.parser")
        data = {}

        # 物件名
        title = soup.select_one(
            "h1, .section_h1-header-title, .property_view_main-title"
        )
        data["name"] = title.get_text(strip=True) if title else ""

        table_data = self._extract_table_pairs(soup)

        data["address"] = table_data.get("所在地", "")
        data["asking_price"] = self._parse_price(
            table_data.get("販売価格", "")
            or table_data.get("価格", "")
        )
        data["gross_yield"] = self._parse_yield(table_data.get("利回り", ""))
        data["land_area"] = self._parse_area(table_data.get("土地面積", ""))
        data["building_area"] = self._parse_area(
            table_data.get("建物面積", "")
            or table_data.get("専有面積", "")
        )
        data["structure"] = self._parse_structure(table_data.get("構造", ""))
        data["built_year"], data["building_age"] = self._parse_built_year(
            table_data.get("築年月", "")
        )
        data["nearest_station"], data["station_distance_min"] = self._parse_station(
            table_data.get("交通", "")
        )
        data["land_use_zone"] = table_data.get("用途地域", "")
        data["building_coverage"] = self._parse_ratio(table_data.get("建蔽率", ""))
        data["floor_area_ratio"] = self._parse_ratio(table_data.get("容積率", ""))

        return data

    def _parse_homes(self, html: str, url: str) -> Dict:
        """HOME'Sパーサー"""
        soup = BeautifulSoup(html, "html.parser")
        data = {}

        title = soup.select_one("h1, .bukkenName, .heading--h1")
        data["name"] = title.get_text(strip=True) if title else ""

        table_data = self._extract_table_pairs(soup)

        data["address"] = table_data.get("所在地", "")
        data["asking_price"] = self._parse_price(table_data.get("価格", ""))
        data["gross_yield"] = self._parse_yield(table_data.get("利回り", ""))
        data["land_area"] = self._parse_area(table_data.get("土地面積", ""))
        data["building_area"] = self._parse_area(table_data.get("建物面積", ""))
        data["structure"] = self._parse_structure(table_data.get("構造", ""))
        data["built_year"], data["building_age"] = self._parse_built_year(
            table_data.get("築年月", "")
        )
        data["nearest_station"], data["station_distance_min"] = self._parse_station(
            table_data.get("交通", "")
        )

        return data

    def _parse_kenbiya(self, html: str, url: str) -> Dict:
        """健美家パーサー（表構造 + 本文フォールバック）"""
        soup = BeautifulSoup(html, "html.parser")
        data = {}

        title = soup.select_one("h1, .propertyTitle, .detailTitle")
        data["name"] = title.get_text(strip=True) if title else ""

        table_data = self._extract_table_pairs(soup)
        full_text = soup.get_text(" ", strip=True)

        data["address"] = table_data.get("所在地", "") or table_data.get("住所", "")
        data["asking_price"] = self._parse_price(
            table_data.get("価格", "") or table_data.get("販売価格", "")
        ) or self._extract_price_from_text(full_text)
        data["gross_yield"] = self._parse_yield(
            table_data.get("利回り", "") or table_data.get("表面利回り", "")
        ) or self._extract_yield_from_text(full_text)
        data["land_area"] = self._parse_area(
            table_data.get("土地面積", "") or table_data.get("敷地面積", "")
        )
        data["building_area"] = self._parse_area(
            table_data.get("建物面積", "") or table_data.get("延床面積", "") or table_data.get("専有面積", "")
        )
        data["structure"] = self._parse_structure(table_data.get("構造", "") or table_data.get("建物構造", "")) \
            or self._extract_structure_from_text(full_text)
        data["built_year"], data["building_age"] = self._parse_built_year(
            table_data.get("築年月", "") or table_data.get("築年数", "")
        )
        st_text = table_data.get("交通", "") or table_data.get("最寄り駅", "")
        data["nearest_station"], data["station_distance_min"] = self._parse_station(st_text or full_text)
        data["land_use_zone"] = table_data.get("用途地域", "")
        data["building_coverage"] = self._parse_ratio(table_data.get("建蔽率", ""))
        data["floor_area_ratio"] = self._parse_ratio(table_data.get("容積率", ""))
        data["units"] = self._parse_int(table_data.get("総戸数", ""))
        data["current_rent_annual"] = self._parse_annual_rent(
            table_data.get("年間収入", "") or table_data.get("満室想定年収", "")
        )
        return data

    def _parse_rals(self, html: str, url: str) -> Dict:
        """不動産投資★連合隊パーサー（汎用 + 投資向け項目補強）"""
        soup = BeautifulSoup(html, "html.parser")
        data = self._parse_generic(html, url)
        full_text = soup.get_text(" ", strip=True)
        table_data = self._extract_table_pairs(soup)

        if not data.get("gross_yield"):
            data["gross_yield"] = self._parse_yield(table_data.get("利回り", "")) or self._extract_yield_from_text(full_text)
        if not data.get("land_area"):
            data["land_area"] = self._parse_area(table_data.get("土地面積", ""))
        if not data.get("building_area"):
            data["building_area"] = self._parse_area(
                table_data.get("建物面積", "") or table_data.get("専有面積", "")
            )
        if not data.get("units"):
            data["units"] = self._parse_int(table_data.get("総戸数", ""))
        if not data.get("current_rent_annual"):
            data["current_rent_annual"] = self._parse_annual_rent(
                table_data.get("満室年収", "") or table_data.get("年間収入", "")
            )
        return data

    def _parse_generic(self, html: str, url: str) -> Dict:
        """汎用パーサー（テーブルペア＋全文テキスト解析）"""
        soup = BeautifulSoup(html, "html.parser")
        data = {}

        # タイトル
        title = soup.select_one("h1")
        data["name"] = title.get_text(strip=True) if title else urlparse(url).path

        # テーブルデータ
        table_data = self._extract_table_pairs(soup)

        # テーブルから取得試行
        data["address"] = table_data.get("所在地", "") or table_data.get("住所", "")
        data["asking_price"] = self._parse_price(
            table_data.get("価格", "") or table_data.get("販売価格", "")
        )

        # テーブルで取れなければ全文テキストからフォールバック
        if not data["asking_price"]:
            full_text = soup.get_text(" ", strip=True)
            data["asking_price"] = self._extract_price_from_text(full_text)
            data["gross_yield"] = self._extract_yield_from_text(full_text)
            data["structure"] = self._extract_structure_from_text(full_text)
            data["built_year"], data["building_age"] = self._extract_age_from_text(full_text)
            data["nearest_station"], data["station_distance_min"] = self._extract_station_from_text(full_text)
        else:
            data["gross_yield"] = self._parse_yield(table_data.get("利回り", ""))
            data["structure"] = self._parse_structure(table_data.get("構造", ""))
            data["built_year"], data["building_age"] = self._parse_built_year(
                table_data.get("築年月", "")
            )
            data["nearest_station"], data["station_distance_min"] = self._parse_station(
                table_data.get("交通", "")
            )

        data["land_area"] = self._parse_area(table_data.get("土地面積", ""))
        data["building_area"] = self._parse_area(table_data.get("建物面積", ""))
        data["land_use_zone"] = table_data.get("用途地域", "")
        data["building_coverage"] = self._parse_ratio(table_data.get("建蔽率", ""))
        data["floor_area_ratio"] = self._parse_ratio(table_data.get("容積率", ""))

        return data

    # ===== テーブルデータ抽出 =====

    def _extract_table_pairs(self, soup: BeautifulSoup) -> Dict[str, str]:
        """
        HTML内の <th>/<td> ペアや <dt>/<dd> ペアからkey-valueを抽出

        不動産サイトは物件概要をテーブルで表示する傾向が強い
        """
        pairs = {}

        # <th>/<td> ペア
        for th in soup.find_all("th"):
            td = th.find_next_sibling("td")
            if td:
                key = th.get_text(strip=True)
                val = td.get_text(" ", strip=True)
                if key and val:
                    pairs[key] = val

        # <dt>/<dd> ペア
        for dt in soup.find_all("dt"):
            dd = dt.find_next_sibling("dd")
            if dd:
                key = dt.get_text(strip=True)
                val = dd.get_text(" ", strip=True)
                if key and val:
                    pairs[key] = val

        # label/value風のdivペア
        for label in soup.select(".label, .item-label, .detail-label, [class*='label']"):
            value = label.find_next_sibling()
            if value:
                key = label.get_text(strip=True)
                val = value.get_text(" ", strip=True)
                if key and val and len(key) < 20:
                    pairs[key] = val

        return pairs

    # ===== OCR処理 =====

    def _extract_images_and_ocr(
        self, html: str, url: str, extra_images: List[bytes]
    ) -> List[str]:
        """ページ内画像をダウンロードしてOCR"""
        if not _OCR_AVAILABLE:
            return []

        ocr_results = []
        soup = BeautifulSoup(html, "html.parser")
        base_url = f"{urlparse(url).scheme}://{urlparse(url).netloc}"

        # 物件概要画像っぽいものを選別
        img_urls = set()
        for img in soup.find_all("img"):
            src = img.get("src", "") or img.get("data-src", "")
            alt = (img.get("alt", "") or "").lower()

            # 物件概要・間取り関連の画像を優先
            is_relevant = any(kw in alt for kw in [
                "間取", "概要", "物件", "外観", "内観", "図面", "madori",
            ])
            # またはサイズが大きめの画像
            width = img.get("width", "")
            if not is_relevant and width:
                try:
                    is_relevant = int(width) >= 300
                except (ValueError, TypeError):
                    pass

            if src and is_relevant:
                if src.startswith("//"):
                    src = "https:" + src
                elif src.startswith("/"):
                    src = base_url + src
                elif not src.startswith("http"):
                    continue
                img_urls.add(src)

        # 最大5枚までOCR
        for i, img_url in enumerate(list(img_urls)[:5]):
            try:
                self._throttle()
                resp = self.session.get(img_url, timeout=15)
                if resp.status_code == 200:
                    text = self._ocr_image(resp.content)
                    if text and len(text.strip()) > 5:
                        ocr_results.append(text)
                        self.logger.debug(f"  OCR画像{i+1}: {len(text)}文字")
            except Exception as e:
                self.logger.debug(f"  画像取得/OCRエラー: {e}")

        # Playwrightスクリーンショット等の追加画像
        for img_bytes in extra_images:
            text = self._ocr_image(img_bytes)
            if text and len(text.strip()) > 5:
                ocr_results.append(text)

        return ocr_results

    def _ocr_image(self, image_bytes: bytes) -> str:
        """画像バイナリからOCRテキスト抽出"""
        if not _OCR_AVAILABLE:
            return ""
        try:
            img = Image.open(io.BytesIO(image_bytes))
            # OCR前処理: グレースケール→2値化で文字認識率を上げる
            gray = img.convert("L")
            bw = gray.point(lambda x: 255 if x > 170 else 0)
            # PSM6（ブロック）とPSM11（疎テキスト）を併用し、長い方を採用
            t1 = pytesseract.image_to_string(bw, lang="jpn+eng", config="--psm 6")
            t2 = pytesseract.image_to_string(bw, lang="jpn+eng", config="--psm 11")
            text = t1 if len(t1 or "") >= len(t2 or "") else t2
            return (text or "").strip()
        except Exception as e:
            self.logger.debug(f"OCRエラー: {e}")
            return ""

    def _supplement_from_ocr(self, data: Dict, ocr_texts: List[str]) -> None:
        """OCR結果でHTML解析結果を補完"""
        combined = " ".join(ocr_texts)

        # 未取得の項目のみ補完
        if not data.get("asking_price"):
            price = self._extract_price_from_text(combined)
            if price:
                data["asking_price"] = price
                self.logger.info("  OCR補完: 価格")

        if not data.get("gross_yield"):
            y = self._extract_yield_from_text(combined)
            if y:
                data["gross_yield"] = y
                self.logger.info("  OCR補完: 利回り")

        if not data.get("land_area"):
            m = re.search(r"土地[面積]?\s*[:：]?\s*([\d,.]+)\s*(?:m2|㎡|平米)", combined)
            if m:
                data["land_area"] = float(m.group(1).replace(",", ""))
                self.logger.info("  OCR補完: 土地面積")

        if not data.get("building_area"):
            m = re.search(r"(?:建物|延床)[面積]?\s*[:：]?\s*([\d,.]+)\s*(?:m2|㎡|平米)", combined)
            if m:
                data["building_area"] = float(m.group(1).replace(",", ""))
                self.logger.info("  OCR補完: 建物面積")

        if not data.get("structure"):
            s = self._extract_structure_from_text(combined)
            if s:
                data["structure"] = s
                self.logger.info("  OCR補完: 構造")

        if not data.get("built_year"):
            by, ba = self._extract_age_from_text(combined)
            if by:
                data["built_year"] = by
                data["building_age"] = ba
                self.logger.info("  OCR補完: 築年")

        if not data.get("nearest_station"):
            st, dist = self._extract_station_from_text(combined)
            if st:
                data["nearest_station"] = st
                data["station_distance_min"] = dist
                self.logger.info("  OCR補完: 交通")

        if not data.get("units"):
            m = re.search(r"(?:総戸数|全)\s*[:：]?\s*(\d+)\s*戸", combined)
            if m:
                data["units"] = int(m.group(1))
                self.logger.info("  OCR補完: 総戸数")

    # ===== テキスト解析ヘルパー =====

    def _parse_price(self, text: str) -> Optional[int]:
        """価格テキスト → 円"""
        if not text:
            return None
        return self._extract_price_from_text(text)

    def _extract_price_from_text(self, text: str) -> Optional[int]:
        """テキストから価格抽出（万円→円変換）"""
        # "1億5000万円", "1億5,000万円"
        m = re.search(r"(\d+)億\s*(\d[\d,]*)\s*万円", text)
        if m:
            return int(m.group(1)) * 100_000_000 + int(m.group(2).replace(",", "")) * 10_000

        # "5000万円", "5,000万円"
        m = re.search(r"(\d[\d,]*)\s*万円", text)
        if m:
            return int(m.group(1).replace(",", "")) * 10_000

        # "1.5億円"
        m = re.search(r"([\d.]+)\s*億円", text)
        if m:
            return int(float(m.group(1)) * 100_000_000)

        return None

    def _parse_yield(self, text: str) -> Optional[float]:
        """利回りテキスト → 小数"""
        if not text:
            return None
        return self._extract_yield_from_text(text)

    def _extract_yield_from_text(self, text: str) -> Optional[float]:
        m = re.search(r"([\d.]+)\s*[%％]", text)
        if m:
            val = float(m.group(1))
            if 0.5 < val < 50:
                return val / 100
        return None

    def _parse_area(self, text: str) -> Optional[float]:
        """面積テキスト → ㎡"""
        if not text:
            return None
        m = re.search(r"([\d,.]+)\s*(?:m2|㎡|平米|m²)", text)
        if m:
            return float(m.group(1).replace(",", ""))
        # 数値のみ
        m = re.search(r"([\d,.]+)", text)
        if m:
            val = float(m.group(1).replace(",", ""))
            if val > 5:  # 5㎡以上なら面積と判断
                return val
        return None

    def _parse_structure(self, text: str) -> Optional[str]:
        if not text:
            return None
        return self._extract_structure_from_text(text)

    def _extract_structure_from_text(self, text: str) -> Optional[str]:
        for code, label in [
            ("SRC", "SRC"), ("SRC", "鉄骨鉄筋"),
            ("RC", "RC"), ("RC", "鉄筋コンクリート"), ("RC", "鉄筋"),
            ("鉄骨", "鉄骨"), ("鉄骨", "S造"),
            ("軽量鉄骨", "軽量鉄骨"),
            ("木造", "木造"), ("木造", "W造"),
        ]:
            if label in text:
                return code
        return None

    def _parse_built_year(self, text: str) -> Tuple[Optional[int], Optional[int]]:
        if not text:
            return None, None
        return self._extract_age_from_text(text)

    def _extract_age_from_text(self, text: str) -> Tuple[Optional[int], Optional[int]]:
        now_year = datetime.now().year

        # "1995年3月" or "1995年"
        m = re.search(r"(\d{4})\s*年", text)
        if m:
            year = int(m.group(1))
            if 1900 <= year <= now_year:
                return year, now_year - year

        # "築30年"
        m = re.search(r"築\s*(\d+)\s*年", text)
        if m:
            age = int(m.group(1))
            return now_year - age, age

        # 和暦: "令和3年", "平成10年", "昭和60年"
        wareki = {
            "令和": 2018, "平成": 1988, "昭和": 1925, "大正": 1911,
        }
        for era, base in wareki.items():
            m = re.search(era + r"(\d+)\s*年", text)
            if m:
                year = base + int(m.group(1))
                return year, now_year - year

        return None, None

    def _parse_station(self, text: str) -> Tuple[Optional[str], Optional[int]]:
        if not text:
            return None, None
        return self._extract_station_from_text(text)

    def _extract_station_from_text(self, text: str) -> Tuple[Optional[str], Optional[int]]:
        def _clean_station(raw: str) -> str:
            s = str(raw or "").strip()
            s = re.sub(r"(?:最寄り?駅|交通|アクセス)[:：\s]*", "", s)
            s = re.sub(r"^.*?線[／/\s]*", "", s)
            s = s.replace("「", "").replace("」", "").replace("駅", "").strip()
            s = re.split(r"[、,／/・\s]", s)[0].strip()
            # 住所/路線記法の誤爆を除外
            if not (1 <= len(s) <= 12):
                return ""
            if any(x in s for x in ("都", "道", "府", "県", "市", "区", "町", "丁目", "番地")):
                return ""
            if "線" in s:
                return ""
            return s

        candidates: List[Tuple[str, int]] = []
        patterns = [
            r"[「『]?\s*([^「」『』\n]{1,14}?)\s*[」』]?\s*駅?\s*(?:徒歩|歩)\s*(\d{1,3})\s*分",
            r"([^\s\n]{1,14}?)駅\s*(?:徒歩|歩)\s*(\d{1,3})\s*分",
            r"(?:最寄り?駅|交通)[:：\s]*([^\s\n]{1,14}?)(?:駅|$).*?(?:徒歩|歩)\s*(\d{1,3})\s*分",
        ]
        for pat in patterns:
            for m in re.finditer(pat, text):
                st = _clean_station(m.group(1))
                if not st:
                    continue
                try:
                    walk = int(m.group(2))
                except (TypeError, ValueError):
                    continue
                if 0 < walk <= 180:
                    candidates.append((st, walk))
            if candidates:
                break

        if candidates:
            # 徒歩分が短い候補を優先（通常は最寄駅）
            candidates.sort(key=lambda x: x[1])
            return candidates[0][0], candidates[0][1]

        # "徒歩5分" のみ
        m = re.search(r"(?:徒歩|歩)\s*(\d{1,3})\s*分", text)
        if m:
            try:
                walk = int(m.group(1))
            except (TypeError, ValueError):
                walk = None
            return None, walk

        return None, None

    @staticmethod
    def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        r = 6371.0
        p1 = math.radians(lat1)
        p2 = math.radians(lat2)
        dp = math.radians(lat2 - lat1)
        dl = math.radians(lon2 - lon1)
        a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return 2 * r * math.asin(math.sqrt(max(0.0, min(1.0, a))))

    def _parse_ratio(self, text: str) -> Optional[float]:
        """建蔽率/容積率（%→小数）"""
        if not text:
            return None
        m = re.search(r"([\d.]+)\s*[%％]", text)
        if m:
            return float(m.group(1)) / 100
        return None

    def _parse_int(self, text: str) -> Optional[int]:
        if not text:
            return None
        m = re.search(r"(\d+)", text)
        return int(m.group(1)) if m else None

    def _parse_annual_rent(self, text: str) -> Optional[int]:
        """年間収入テキスト → 円"""
        if not text:
            return None
        return self._extract_price_from_text(text)

    # ===== 後処理 =====

    def _postprocess(self, data: Dict) -> None:
        """共通後処理: 市区町村コード推定、年間賃料計算等"""
        address = data.get("address", "")

        # 都道府県コード推定
        if not data.get("prefecture_code"):
            data["prefecture_code"] = self._guess_pref_code(address)

        # 市区町村コード推定
        if not data.get("city_code"):
            data["city_code"] = self._guess_city_code(
                address, data.get("prefecture_code", "")
            )

        # 年間賃料が無い場合、価格×利回りで推定
        if not data.get("current_rent_annual"):
            price = data.get("asking_price")
            y = data.get("gross_yield")
            if price and y:
                data["current_rent_annual"] = int(price * y)

        # 駅名整合チェック（OCR/HTML誤抽出の補正）
        try:
            from data.station_master import resolve_station_id, STATION_MAP, find_nearest_station
            from data.geocoder import Geocoder

            pref_code = data.get("prefecture_code") or self._guess_pref_code(address)
            station_text = str(data.get("nearest_station") or "").strip()

            # 住所ジオコード（キャッシュ）
            glat = glon = None
            if address:
                if address in self._geocode_cache:
                    gc = self._geocode_cache[address]
                else:
                    try:
                        gc = Geocoder().geocode(address)
                    except Exception:
                        gc = None
                    self._geocode_cache[address] = gc
                if gc:
                    glat, glon = float(gc[0]), float(gc[1])

            sid = resolve_station_id(
                nearest_station_text=station_text,
                lat=glat,
                lon=glon,
                pref_code=pref_code or None,
            )

            near = None
            if glat is not None and glon is not None:
                near = find_nearest_station(glat, glon, max_distance_km=8.0, pref_code=pref_code or None)
                if (not near) or float(near.get("distance_km") or 999.0) > 20.0:
                    near_any = find_nearest_station(glat, glon, max_distance_km=8.0, pref_code=None)
                    if near_any:
                        near = near_any

            suspicious = False
            if sid and sid in STATION_MAP and glat is not None and glon is not None:
                s = STATION_MAP[sid]
                dkm = self._haversine_km(glat, glon, float(s["lat"]), float(s["lon"]))
                walk = data.get("station_distance_min")
                try:
                    walk = float(walk) if walk is not None else None
                except (TypeError, ValueError):
                    walk = None
                if walk and walk > 0:
                    expected = max(0.08 * walk, 0.2)
                    suspicious = dkm > max(2.0, expected * 4.0)
                else:
                    suspicious = dkm > 8.0
            elif station_text:
                suspicious = True

            if near and (not sid or suspicious):
                data["nearest_station"] = near.get("name")
                data["station_id"] = near.get("station_id")
                dkm2 = float(near.get("distance_km") or 0.0)
                if dkm2 > 0 and (
                    not data.get("station_distance_min")
                    or suspicious
                ):
                    data["station_distance_min"] = max(1, min(120, int(round(dkm2 * 12.5))))
            elif sid and sid in STATION_MAP:
                data["nearest_station"] = STATION_MAP[sid]["name"]
                data["station_id"] = sid
        except Exception:
            pass

    def _guess_pref_code(self, address: str) -> str:
        """住所から都道府県コード推定"""
        prefs = {
            "東京": "13", "神奈川": "14", "埼玉": "11", "千葉": "12",
            "大阪": "27", "愛知": "23", "福岡": "40", "北海道": "01",
            "京都": "26", "兵庫": "28", "広島": "34", "宮城": "04",
        }
        for name, code in prefs.items():
            if name in address:
                return code
        return ""

    def _guess_city_code(self, address: str, pref_code: str) -> str:
        """住所から市区町村コード推定"""
        TOKYO_WARDS = {
            "千代田区": "13101", "中央区": "13102", "港区": "13103",
            "新宿区": "13104", "文京区": "13105", "台東区": "13106",
            "墨田区": "13107", "江東区": "13108", "品川区": "13109",
            "目黒区": "13110", "大田区": "13111", "世田谷区": "13112",
            "渋谷区": "13113", "中野区": "13114", "杉並区": "13115",
            "豊島区": "13116", "北区": "13117", "荒川区": "13118",
            "板橋区": "13119", "練馬区": "13120", "足立区": "13121",
            "葛飾区": "13122", "江戸川区": "13123",
        }
        for ward, code in TOKYO_WARDS.items():
            if ward in address:
                return code
        return f"{pref_code}101" if pref_code else ""
