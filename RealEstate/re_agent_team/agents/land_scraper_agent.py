"""土地物件スクレイピングエージェント - SUUMO/楽待/athome/HOME'Sから土地情報を取得"""
import re
import time
import logging
from typing import List, Optional, Dict
from urllib.parse import urlencode, urljoin

import requests
from bs4 import BeautifulSoup

from .base_agent import BaseAgent
from models.land_listing import LandListing


class LandScraperAgent(BaseAgent):
    """
    土地物件をスクレイピングするエージェント

    対応サイト:
    - SUUMO 土地 (suumo.jp/jj/bukken/ichiran/ bs=030)
    - 楽待 土地 (rakumachi.com)
    - athome 土地 (athome.co.jp/tochi/)
    - HOME'S 土地 (homes.co.jp/tochi/)

    注意: robots.txt準拠、レート制限遵守
    """

    SUUMO_BASE = "https://suumo.jp"
    SUUMO_TOCHI_SEARCH = "https://suumo.jp/jj/bukken/ichiran/JJ010FJ001/"

    # SUUMOエリアコード
    SUUMO_AREA_MAP = {
        "13": "030",  # 東京都
        "14": "040",  # 神奈川県
        "11": "020",  # 埼玉県
        "12": "010",  # 千葉県
    }

    # 楽待
    RAKUMACHI_BASE = "https://www.rakumachi.com"
    RAKUMACHI_TOCHI_SEARCH = "https://www.rakumachi.com/syuuekibukken/tochi/area/"

    # athome
    ATHOME_BASE = "https://www.athome.co.jp"
    ATHOME_TOCHI_SEARCH = "https://www.athome.co.jp/tochi/"
    ATHOME_PREF_MAP = {
        "13": "tokyo", "14": "kanagawa", "11": "saitama", "12": "chiba",
    }

    # HOME'S
    HOMES_BASE = "https://www.homes.co.jp"
    HOMES_TOCHI_SEARCH = "https://www.homes.co.jp/tochi/"
    HOMES_PREF_MAP = {
        "13": "tokyo", "14": "kanagawa", "11": "saitama", "12": "chiba",
    }

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    }

    def __init__(self):
        super().__init__("LandScraperAgent")
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)
        self._rate_limit = 3.0
        self._last_request = 0.0

    def run(
        self,
        source: str = "suumo",
        prefecture_code: str = "13",
        price_min: int = None,
        price_max: int = None,
        area_min: float = None,
        area_max: float = None,
        walk_max: int = None,
        max_pages: int = 10,
    ) -> List[LandListing]:
        """
        土地物件を検索・スクレイピング

        Args:
            source: "suumo", "rakumachi", "athome", or "homes"
            prefecture_code: 都道府県コード
            price_min: 最低価格（万円）
            price_max: 最高価格（万円）
            area_min: 最低面積（㎡）
            area_max: 最高面積（㎡）
            walk_max: 最大徒歩分数
            max_pages: 最大取得ページ数
        """
        self.logger.info(
            f"土地スクレイピング開始: source={source}, pref={prefecture_code}, "
            f"pages={max_pages}"
        )

        listings = []
        for page in range(1, max_pages + 1):
            self.logger.info(f"  ページ {page}/{max_pages} 取得中...")
            try:
                if source == "rakumachi":
                    page_listings = self._scrape_rakumachi_page(
                        prefecture_code, price_min, price_max,
                        area_min, walk_max, page
                    )
                elif source == "athome":
                    page_listings = self._scrape_athome_page(
                        prefecture_code, price_min, price_max,
                        area_min, walk_max, page
                    )
                elif source == "homes":
                    page_listings = self._scrape_homes_page(
                        prefecture_code, price_min, price_max,
                        area_min, walk_max, page
                    )
                else:
                    page_listings = self._scrape_suumo_tochi_page(
                        prefecture_code, price_min, price_max,
                        area_min, walk_max, page
                    )

                if not page_listings:
                    self.logger.info(f"  ページ {page}: 物件なし、終了")
                    break

                # SUUMO: 詳細ページから建蔽率・容積率を取得
                if source == "suumo":
                    for i, pl in enumerate(page_listings):
                        if not pl.building_coverage_ratio and pl.source_url:
                            self.logger.info(
                                f"    詳細取得 {i+1}/{len(page_listings)}: {pl.address}"
                            )
                            page_listings[i] = self._fetch_suumo_detail(pl)

                listings.extend(page_listings)
                self.logger.info(f"  ページ {page}: {len(page_listings)}件取得")
            except Exception as e:
                self.logger.warning(f"  ページ {page} エラー: {e}")
                break

        self.logger.info(f"土地スクレイピング完了: 合計 {len(listings)}件")
        return listings

    def _throttle(self):
        elapsed = time.time() - self._last_request
        if elapsed < self._rate_limit:
            time.sleep(self._rate_limit - elapsed)
        self._last_request = time.time()

    # ===== SUUMO 土地 =====

    def _scrape_suumo_tochi_page(
        self,
        pref_code: str,
        price_min: int = None,
        price_max: int = None,
        area_min: float = None,
        walk_max: int = None,
        page: int = 1,
    ) -> List[LandListing]:
        self._throttle()

        params = {
            "ar": self.SUUMO_AREA_MAP.get(pref_code, "030"),
            "bs": "030",    # 土地 (030が正しいコード)
            "ta": pref_code,
            "pc": str(page),
        }
        if price_min:
            params["pn"] = str(price_min)
        if price_max:
            params["px"] = str(price_max)

        try:
            resp = self.session.get(
                self.SUUMO_TOCHI_SEARCH,
                params=params,
                timeout=15,
            )
            resp.raise_for_status()
            resp.encoding = "utf-8"
        except requests.RequestException as e:
            self.logger.error(f"HTTP error: {e}")
            return []

        return self._parse_suumo_tochi_listing(resp.text, pref_code)

    def _parse_suumo_tochi_listing(
        self, html: str, pref_code: str
    ) -> List[LandListing]:
        """SUUMOの土地一覧ページをパース（.property_unit構造）"""
        soup = BeautifulSoup(html, "html.parser")
        listings = []

        # SUUMOの土地物件は .property_unit 内にdt/ddペアで構造化されている
        cards = soup.select(".property_unit")
        self.logger.debug(f"  .property_unit cards found: {len(cards)}")

        for card in cards:
            try:
                listing = self._parse_suumo_property_unit(card, pref_code)
                if listing:
                    listings.append(listing)
            except Exception as e:
                self.logger.debug(f"カードパースエラー: {e}")
                continue

        return listings

    def _parse_suumo_property_unit(
        self, card, pref_code: str
    ) -> Optional[LandListing]:
        """SUUMOの .property_unit カードをパース"""
        # dt/dd ペアから構造化データを取得
        fields = {}
        for dt in card.select("dt"):
            label = dt.get_text(strip=True)
            dd = dt.find_next_sibling("dd")
            if dd:
                fields[label] = dd.get_text(strip=True)

        # 物件名 (必須チェック用)
        name = fields.get("物件名", "")

        # ソースURL
        source_url = None
        title_el = card.select_one(".property_unit-title a")
        if title_el and title_el.get("href"):
            href = title_el["href"]
            source_url = href if href.startswith("http") else self.SUUMO_BASE + href

        # 全テキスト（フォールバック用）
        full_text = card.get_text(" ", strip=True)

        # 所在地
        address = fields.get("所在地", "")
        if not address:
            address = self._extract_address(full_text, pref_code)

        # 価格
        price_text = fields.get("販売価格", "") or fields.get("価格", "")
        price = self._extract_price(price_text) if price_text else self._extract_price(full_text)

        # 土地面積
        area_text = fields.get("土地面積", "") or fields.get("面積", "")
        area = self._extract_land_area_from_field(area_text) if area_text else self._extract_land_area(full_text)

        # 沿線・駅
        station_text = fields.get("沿線・駅", "") or fields.get("交通", "")
        station, line, walk = self._extract_station_info(station_text or full_text)

        # 建蔽率・容積率（フルテキストから抽出）
        coverage = self._extract_ratio(full_text, "建蔽率", "建ぺい率")
        far = self._extract_ratio(full_text, "容積率")
        zoning = self._extract_zoning(full_text)

        # 準防火・道路情報
        fireproof = "準防火" in full_text
        two_way = "2方向" in full_text or "二方向" in full_text or "角地" in full_text
        north_road = "北道路" in full_text or "北側道路" in full_text

        # 最低限の情報がなければスキップ
        if not address and not name:
            return None

        if not address:
            address = name  # 物件名を住所として使用

        return LandListing(
            address=address,
            railway_line=line,
            station=station,
            walk_minutes=walk,
            land_price=price,
            land_area_sqm=area,
            building_coverage_ratio=coverage,
            floor_area_ratio=far,
            zoning=zoning,
            quasi_fireproof=fireproof,
            two_way_road=two_way,
            north_road=north_road,
            source="SUUMO",
            source_url=source_url,
        )

    def _fetch_suumo_detail(self, listing: LandListing) -> LandListing:
        """個別物件ページから建蔽率・容積率・用途地域を取得"""
        if not listing.source_url:
            return listing

        self._throttle()

        try:
            resp = self.session.get(listing.source_url, timeout=15)
            resp.raise_for_status()
            resp.encoding = "utf-8"
        except requests.RequestException as e:
            self.logger.debug(f"詳細ページ取得エラー: {e}")
            return listing

        soup = BeautifulSoup(resp.text, "html.parser")

        # dt/th から建蔽率・容積率・用途地域を抽出
        for el in soup.select("dt, th"):
            txt = el.get_text(strip=True)
            sibling = el.find_next_sibling("dd") or el.find_next_sibling("td")
            if not sibling:
                continue
            val = sibling.get_text(strip=True)

            if ("建蔽" in txt or "建ぺい" in txt) and not listing.building_coverage_ratio:
                # "建ペい率：40％・80％" → 最初の値が建蔽率
                nums = re.findall(r"(\d+)\s*[%％]", val)
                if nums:
                    cov = float(nums[0]) / 100.0
                    if cov <= 1.0:  # sanity: 建蔽率は100%以下
                        listing.building_coverage_ratio = cov
                # 容積率も同じフィールドにある場合 "40%・80%" → "容積"を含み2値なら2番目が容積率
                if "容積" in val and len(nums) >= 2 and not listing.floor_area_ratio:
                    far_val = float(nums[1]) / 100.0
                    if far_val <= 10.0:  # sanity: 容積率は1000%以下
                        listing.floor_area_ratio = far_val

            if "容積" in txt and not listing.floor_area_ratio:
                m = re.search(r"(\d+)\s*[%％]", val)
                if m:
                    far_val = float(m.group(1)) / 100.0
                    if far_val <= 10.0:  # sanity: 容積率は1000%以下
                        listing.floor_area_ratio = far_val

            if "用途" in txt and not listing.zoning:
                listing.zoning = self._normalize_zoning(val)

            if "防火" in txt:
                if "準防火" in val:
                    listing.quasi_fireproof = True

        return listing

    def _normalize_zoning(self, text: str) -> Optional[str]:
        """用途地域の略称を正式名に変換"""
        mapping = {
            "１種低層": "第一種低層住居専用地域",
            "１低層": "第一種低層住居専用地域",
            "1種低層": "第一種低層住居専用地域",
            "２種低層": "第二種低層住居専用地域",
            "2種低層": "第二種低層住居専用地域",
            "１中高": "第一種中高層住居専用地域",
            "1中高": "第一種中高層住居専用地域",
            "２中高": "第二種中高層住居専用地域",
            "2中高": "第二種中高層住居専用地域",
            "１住居": "第一種住居地域",
            "1住居": "第一種住居地域",
            "２住居": "第二種住居地域",
            "2住居": "第二種住居地域",
            "準住居": "準住居地域",
            "近商": "近隣商業地域",
            "商業": "商業地域",
            "準工": "準工業地域",
        }
        for abbr, full in mapping.items():
            if abbr in text:
                return full
        return self._extract_zoning(text)

    # ===== 楽待 =====

    def _scrape_rakumachi_page(
        self,
        pref_code: str,
        price_min: int = None,
        price_max: int = None,
        area_min: float = None,
        walk_max: int = None,
        page: int = 1,
    ) -> List[LandListing]:
        self._throttle()

        pref_names = {
            "13": "tokyo", "14": "kanagawa",
            "11": "saitama", "12": "chiba",
        }
        pref_name = pref_names.get(pref_code, "tokyo")

        url = f"{self.RAKUMACHI_TOCHI_SEARCH}{pref_name}/"
        params = {"page": str(page)}
        if price_min:
            params["price_from"] = str(price_min * 10000)
        if price_max:
            params["price_to"] = str(price_max * 10000)

        try:
            resp = self.session.get(url, params=params, timeout=15)
            resp.raise_for_status()
            resp.encoding = "utf-8"
        except requests.RequestException as e:
            self.logger.error(f"HTTP error: {e}")
            return []

        return self._parse_rakumachi_listing(resp.text, pref_code)

    def _parse_rakumachi_listing(
        self, html: str, pref_code: str
    ) -> List[LandListing]:
        soup = BeautifulSoup(html, "html.parser")
        listings = []

        cards = soup.select(
            ".propertyBlock, .property-list-item, "
            ".js-bukkenList, .full-width-list-item"
        )

        for card in cards:
            try:
                text = card.get_text(" ", strip=True)
                if len(text) < 20:
                    continue

                address = self._extract_address(text, pref_code)
                if not address:
                    continue

                source_url = None
                link_el = card.select_one("a[href]")
                if link_el and link_el.get("href"):
                    href = link_el["href"]
                    source_url = href if href.startswith("http") else urljoin(self.RAKUMACHI_BASE, href)

                price = self._extract_price(text)
                area = self._extract_land_area(text)
                coverage = self._extract_ratio(text, "建蔽率", "建ぺい率")
                far = self._extract_ratio(text, "容積率")
                zoning = self._extract_zoning(text)
                station, line, walk = self._extract_station_info(text)

                if not price and not area:
                    continue

                listing = LandListing(
                    address=address,
                    railway_line=line,
                    station=station,
                    walk_minutes=walk,
                    land_price=price,
                    land_area_sqm=area,
                    building_coverage_ratio=coverage,
                    floor_area_ratio=far,
                    zoning=zoning,
                    quasi_fireproof="準防火" in text,
                    two_way_road="2方向" in text or "角地" in text,
                    north_road="北道路" in text,
                    source="楽待",
                    source_url=source_url,
                )
                listings.append(listing)
            except Exception as e:
                self.logger.debug(f"楽待パースエラー: {e}")
                continue

        return listings

    # ===== athome =====

    def _scrape_athome_page(
        self,
        pref_code: str,
        price_min: int = None,
        price_max: int = None,
        area_min: float = None,
        walk_max: int = None,
        page: int = 1,
    ) -> List[LandListing]:
        self._throttle()
        pref_name = self.ATHOME_PREF_MAP.get(pref_code, "tokyo")
        url = f"{self.ATHOME_TOCHI_SEARCH}{pref_name}/"
        params = {"page": str(page)}
        if price_min:
            params["priceFrom"] = str(price_min)  # 万円
        if price_max:
            params["priceTo"] = str(price_max)

        try:
            resp = self.session.get(url, params=params, timeout=15)
            resp.raise_for_status()
            resp.encoding = "utf-8"
        except requests.RequestException as e:
            self.logger.error(f"athome HTTP error: {e}")
            return []

        return self._parse_athome_listing(resp.text, pref_code)

    def _parse_athome_listing(
        self, html: str, pref_code: str
    ) -> List[LandListing]:
        soup = BeautifulSoup(html, "html.parser")
        listings = []

        # athome uses various card structures
        cards = soup.select(
            ".property-card, .p-property, .newArrangement-item, "
            ".data_table, table.dataTable tr, .js-cassetteItem"
        )

        for card in cards:
            try:
                text = card.get_text(" ", strip=True)
                if len(text) < 30:
                    continue

                address = self._extract_address(text, pref_code)
                if not address:
                    continue

                source_url = None
                link_el = card.select_one("a[href]")
                if link_el and link_el.get("href"):
                    href = link_el["href"]
                    source_url = href if href.startswith("http") else urljoin(self.ATHOME_BASE, href)

                price = self._extract_price(text)
                area = self._extract_land_area(text)
                coverage = self._extract_ratio(text, "建蔽率", "建ぺい率")
                far = self._extract_ratio(text, "容積率")
                zoning = self._extract_zoning(text)
                station, line, walk = self._extract_station_info(text)

                if not price and not area:
                    continue

                listings.append(LandListing(
                    address=address,
                    railway_line=line,
                    station=station,
                    walk_minutes=walk,
                    land_price=price,
                    land_area_sqm=area,
                    building_coverage_ratio=coverage,
                    floor_area_ratio=far,
                    zoning=zoning,
                    quasi_fireproof="準防火" in text,
                    two_way_road="2方向" in text or "角地" in text,
                    north_road="北道路" in text,
                    source="athome",
                    source_url=source_url,
                ))
            except Exception as e:
                self.logger.debug(f"athomeパースエラー: {e}")

        return listings

    # ===== HOME'S =====

    def _scrape_homes_page(
        self,
        pref_code: str,
        price_min: int = None,
        price_max: int = None,
        area_min: float = None,
        walk_max: int = None,
        page: int = 1,
    ) -> List[LandListing]:
        self._throttle()
        pref_name = self.HOMES_PREF_MAP.get(pref_code, "tokyo")
        url = f"{self.HOMES_TOCHI_SEARCH}{pref_name}/list/"
        params = {"page": str(page)}
        if price_min:
            params["plb"] = str(price_min)
        if price_max:
            params["pub"] = str(price_max)

        try:
            resp = self.session.get(url, params=params, timeout=15)
            resp.raise_for_status()
            resp.encoding = "utf-8"
        except requests.RequestException as e:
            self.logger.error(f"HOME'S HTTP error: {e}")
            return []

        return self._parse_homes_listing(resp.text, pref_code)

    def _parse_homes_listing(
        self, html: str, pref_code: str
    ) -> List[LandListing]:
        soup = BeautifulSoup(html, "html.parser")
        listings = []

        cards = soup.select(
            ".mod-mergeBuilding, .prg-building, .bukkenList-item, "
            ".p-searchResult__item, .mod-bukkenList--item"
        )

        for card in cards:
            try:
                text = card.get_text(" ", strip=True)
                if len(text) < 30:
                    continue

                address = self._extract_address(text, pref_code)
                if not address:
                    continue

                source_url = None
                link_el = card.select_one("a[href]")
                if link_el and link_el.get("href"):
                    href = link_el["href"]
                    source_url = href if href.startswith("http") else urljoin(self.HOMES_BASE, href)

                price = self._extract_price(text)
                area = self._extract_land_area(text)
                coverage = self._extract_ratio(text, "建蔽率", "建ぺい率")
                far = self._extract_ratio(text, "容積率")
                zoning = self._extract_zoning(text)
                station, line, walk = self._extract_station_info(text)

                if not price and not area:
                    continue

                listings.append(LandListing(
                    address=address,
                    railway_line=line,
                    station=station,
                    walk_minutes=walk,
                    land_price=price,
                    land_area_sqm=area,
                    building_coverage_ratio=coverage,
                    floor_area_ratio=far,
                    zoning=zoning,
                    quasi_fireproof="準防火" in text,
                    two_way_road="2方向" in text or "角地" in text,
                    north_road="北道路" in text,
                    source="HOME'S",
                    source_url=source_url,
                ))
            except Exception as e:
                self.logger.debug(f"HOME'Sパースエラー: {e}")

        return listings

    # ===== テキスト抽出ヘルパー =====

    def _extract_address(self, text: str, pref_code: str) -> str:
        pref_map = {"13": "東京都", "14": "神奈川県", "11": "埼玉県", "12": "千葉県"}
        pref_name = pref_map.get(pref_code, "")
        # "東京都国分寺市戸倉4" のようなパターン
        m = re.search(
            rf"({pref_name}\S+?[市区町村郡]\S*?)(?:\s|　|,|沿線|交通|$)",
            text
        )
        if m:
            return m.group(1).strip()
        # "渋谷区神宮前3丁目" のようなパターン
        m = re.search(r"(\S+?[区市町村]\S*?(?:\d|丁目|番))", text)
        if m:
            return m.group(0).strip()
        return ""

    def _extract_price(self, text: str) -> Optional[int]:
        # "3220万円～4840万円" → 下限を取得
        m = re.search(r"(\d+)億(\d+)万", text)
        if m:
            return int(m.group(1)) * 100_000_000 + int(m.group(2)) * 10_000
        m = re.search(r"([\d,]+)万円", text)
        if m:
            return int(m.group(1).replace(",", "")) * 10_000
        m = re.search(r"([\d.]+)億円", text)
        if m:
            return int(float(m.group(1)) * 100_000_000)
        return None

    def _extract_land_area_from_field(self, text: str) -> Optional[float]:
        """dt/ddフィールドの面積テキストをパース: "125m 2 ～135.01m 2" """
        # SUUMO特有: "125m 2" のように m と 2 が分離
        m = re.search(r"([\d,.]+)\s*m\s*2", text)
        if m:
            return float(m.group(1).replace(",", ""))
        m = re.search(r"([\d,.]+)\s*(?:㎡|平米)", text)
        if m:
            return float(m.group(1).replace(",", ""))
        return None

    def _extract_land_area(self, text: str) -> Optional[float]:
        """フルテキストから土地面積を抽出"""
        for pattern in [
            r"土地面積\s*([\d,.]+)\s*m\s*2",
            r"(?:土地|敷地)[面積]?\s*[:：]?\s*([\d,.]+)\s*(?:m2|㎡|平米)",
            r"([\d,.]+)\s*(?:m2|㎡)\s*",
        ]:
            m = re.search(pattern, text)
            if m:
                val = float(m.group(1).replace(",", ""))
                if 5 < val < 50000:  # 妥当な土地面積範囲
                    return val
        return None

    def _extract_ratio(self, text: str, *keywords: str) -> Optional[float]:
        for kw in keywords:
            m = re.search(rf"{kw}\s*[:：]?\s*([\d.]+)\s*%", text)
            if m:
                return float(m.group(1)) / 100.0
        return None

    def _extract_zoning(self, text: str) -> Optional[str]:
        zones = [
            "第一種低層住居専用地域", "第二種低層住居専用地域",
            "第一種中高層住居専用地域", "第二種中高層住居専用地域",
            "第一種住居地域", "第二種住居地域",
            "準住居地域", "近隣商業地域", "商業地域",
            "準工業地域", "工業地域", "工業専用地域",
        ]
        for z in zones:
            if z in text:
                return z
        return None

    def _extract_station_info(self, text: str) -> tuple:
        """(駅名, 路線名, 徒歩分) を抽出"""
        # "西武国分寺線「恋ヶ窪」徒歩13分～15分"
        m = re.search(r"(.+?線)「(.+?)」.*?歩\s*(\d+)\s*分", text)
        if m:
            return (m.group(2).strip(), m.group(1).strip(), int(m.group(3)))
        # "ＪＲ中央線「国立」徒歩22分"
        m = re.search(r"(.+?線)\s*[/／「]\s*(.+?)[」駅].*?歩\s*(\d+)\s*分", text)
        if m:
            return (m.group(2).strip(), m.group(1).strip(), int(m.group(3)))
        # "「狭山市」駅 徒歩7分"
        m = re.search(r"「(.+?)」.*?歩\s*(\d+)\s*分", text)
        if m:
            return (m.group(1), None, int(m.group(2)))
        # "狭山市駅 徒歩7分"
        m = re.search(r"(.+?駅)\s*(?:まで)?.*?徒歩\s*(\d+)\s*分", text)
        if m:
            return (m.group(1), None, int(m.group(2)))
        return (None, None, None)

    # ===== CSV取込 =====

    def import_from_csv(self, csv_path: str) -> List[LandListing]:
        """CSVファイルから土地物件を取込（参照シート形式対応）"""
        import csv
        listings = []

        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = list(reader)

        # ヘッダー行を探す（"住所" を含む行）
        header_idx = None
        for i, row in enumerate(rows):
            joined = ",".join(row)
            if "住所" in joined:
                header_idx = i
                break

        if header_idx is None:
            self.logger.warning("ヘッダー行が見つかりません")
            return []

        # データ行を探す（最初の数値Noがある行）
        data_start = header_idx + 1
        for i in range(header_idx + 1, min(header_idx + 5, len(rows))):
            if len(rows[i]) > 1 and rows[i][0].strip().isdigit():
                data_start = i
                break

        for row in rows[data_start:]:
            if len(row) < 10 or not row[1].strip():
                continue
            try:
                address = row[1].strip()
                line_station = row[2].strip() if len(row) > 2 else ""
                walk = self._safe_int(row[3]) if len(row) > 3 else None
                price_str = row[4].strip().replace(",", "") if len(row) > 4 else ""
                area = self._safe_float(row[5]) if len(row) > 5 else None
                coverage = self._safe_float(row[6]) if len(row) > 6 else None
                far = self._safe_float(row[7]) if len(row) > 7 else None
                zoning = row[8].strip() if len(row) > 8 else None
                fireproof = row[9].strip() == "対象" if len(row) > 9 else False
                two_way = row[10].strip() == "対象" if len(row) > 10 else False
                north = row[11].strip() == "対象" if len(row) > 11 else False
                pdf_link = row[12].strip() if len(row) > 12 else None
                memo = row[-2].strip() if len(row) > 14 else None

                # 路線と駅を分離
                line_name, station_name = None, None
                if "/" in line_station or "／" in line_station:
                    parts = re.split(r"[/／]", line_station)
                    line_name = parts[0].strip()
                    station_name = parts[1].strip() if len(parts) > 1 else None

                price = int(price_str) if price_str.isdigit() else None
                if coverage and coverage > 1:
                    coverage = coverage / 100.0
                if far and far > 1:
                    far = far / 100.0

                listing = LandListing(
                    address=address,
                    railway_line=line_name,
                    station=station_name,
                    walk_minutes=walk,
                    land_price=price,
                    land_area_sqm=area,
                    building_coverage_ratio=coverage,
                    floor_area_ratio=far,
                    zoning=zoning,
                    quasi_fireproof=fireproof,
                    two_way_road=two_way,
                    north_road=north,
                    source="CSV",
                    maisoku_pdf_path=pdf_link,
                    memo=memo,
                )
                listings.append(listing)
            except Exception as e:
                self.logger.debug(f"CSV行パースエラー: {e}")
                continue

        self.logger.info(f"CSV取込完了: {len(listings)}件")
        return listings

    @staticmethod
    def _safe_int(val) -> Optional[int]:
        if val is None or str(val).strip() == "":
            return None
        try:
            return int(str(val).strip().replace(",", ""))
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _safe_float(val) -> Optional[float]:
        if val is None or str(val).strip() == "":
            return None
        try:
            return float(str(val).strip().replace(",", ""))
        except (ValueError, TypeError):
            return None
