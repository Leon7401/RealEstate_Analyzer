"""物件情報スクレイピングエージェント - 楽待/SUUMO賃貸から実データを取得"""
import re
import time
from typing import List, Optional, Dict
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .base_agent import BaseAgent
from .browser_fetch import (
    describe_block,
    get_browser_fetcher,
    looks_blocked,
    playwright_available,
)
from .url_scraper_agent import UrlScraperAgent
from models.property import Property
from data.station_master import resolve_station_id, STATION_MAP


class ScraperAgent(BaseAgent):
    """
    不動産ポータルサイトから収益物件・賃料データをスクレイピング

    収益物件: 楽待 (rakumachi.jp) - 1ページ50件、ページネーション有
    賃料データ: SUUMO賃貸 (suumo.jp/chintai/) - 1ページ20件

    注意: robots.txt準拠、レート制限遵守
    """

    RAKUMACHI_BASE = "https://www.rakumachi.jp"
    RAKUMACHI_SEARCH = "https://www.rakumachi.jp/syuuekibukken/area/"
    KENBIYA_SEARCH = "https://www.kenbiya.com/pp0/s/{pref_slug}/"
    RALS_SEARCH = "https://www.rals.co.jp/invest/index.php"
    ATHOME_BASE = "https://www.athome.co.jp"
    ATHOME_BUY_OTHER = "https://www.athome.co.jp/buy_other/{pref_slug}/list/"
    ATHOME_PREF_MAP = {
        "13": "tokyo", "14": "kanagawa", "11": "saitama", "12": "chiba",
    }
    ATHOME_LIST_KEYWORDS = (
        "一棟売アパート", "一棟売マンション", "売ビル", "一棟アパート",
        "一棟マンション", "一棟売", "投資",
    )
    ATHOME_LIST_EXCLUDE = (
        "売駐車場", "中古一戸建て", "売倉庫", "戸建て", "区分", "借地",
    )

    # 対象物件種別（タイトル先頭で判定）
    ALLOWED_PROPERTY_TYPES = [
        "1棟アパート", "1棟マンション", "一棟アパート", "一棟マンション",
        "1棟商業ビル", "土地",
        "一棟売アパート", "一棟売マンション", "売ビル", "一棟売",
    ]
    # 除外キーワード（借地権・区分・PR広告等）
    EXCLUDED_KEYWORDS = ["借地", "定借", "定期借地", "地上権", "区分"]
    EXCLUDED_PREFIXES = ["PR"]  # PR広告物件

    SUUMO_BASE = "https://suumo.jp"
    # SUUMO賃貸のエリア別URL
    SUUMO_CHINTAI_AREAS = {
        "13": {
            "shinjuku": "sc_shinjuku", "shibuya": "sc_shibuya",
            "minato": "sc_minato", "chiyoda": "sc_chiyoda",
            "chuo": "sc_chuo", "shinagawa": "sc_shinagawa",
            "meguro": "sc_meguro", "setagaya": "sc_setagaya",
            "suginami": "sc_suginami", "nakano": "sc_nakano",
            "toshima": "sc_toshima", "bunkyo": "sc_bunkyo",
            "taito": "sc_taito", "sumida": "sc_sumida",
            "koto": "sc_koto", "ota": "sc_ota",
            "kita": "sc_kita", "arakawa": "sc_arakawa",
            "itabashi": "sc_itabashi", "nerima": "sc_nerima",
            "adachi": "sc_adachi", "katsushika": "sc_katsushika",
            "edogawa": "sc_edogawa",
            # 多摩地区
            "hachioji": "sc_hachioji", "machida": "sc_machida",
            "tachikawa": "sc_tachikawa", "musashino": "sc_musashino",
            "mitaka": "sc_mitaka", "fuchu": "sc_fuchu",
            "chofu": "sc_chofu", "koganei": "sc_koganei",
            "kodaira": "sc_kodaira", "hino": "sc_hino",
            "tama": "sc_tama", "kokubunji": "sc_kokubunji",
        },
        "14": {
            "yokohama_tsurumi": "sc_yokohamashitsurumi",
            "yokohama_naka": "sc_yokohamashinaka",
            "yokohama_kanagawa": "sc_yokohamashikanagawa",
            "yokohama_nishi": "sc_yokohamashinishi",
            "yokohama_hodogaya": "sc_yokohamashihodogaya",
            "yokohama_kohoku": "sc_yokohamashikohoku",
            "kawasaki_kawasaki": "sc_kawasakishikawasaki",
            "kawasaki_nakahara": "sc_kawasakishinakahara",
            "kawasaki_takatsu": "sc_kawasakishitakatsu",
            "fujisawa": "sc_fujisawa",
        },
        "11": {
            "kawaguchi": "sc_kawaguchi",
            "kawagoe": "sc_kawagoe",
            "tokorozawa": "sc_tokorozawa",
            "saitama_omiya": "sc_saitamashiomiya",
            "saitama_urawa": "sc_saitamashiurawa",
            "saitama_minami": "sc_saitamashiminami",
            "saitama_chuo": "sc_saitamashichuo",
            "koshigaya": "sc_koshigaya",
            "soka": "sc_soka",
            "yashio": "sc_yashio",
        },
        "12": {
            "funabashi": "sc_funabashi",
            "ichikawa": "sc_ichikawa",
            "matsudo": "sc_matsudo",
            "chiba_chuo": "sc_chibashichuo",
            "kashiwa": "sc_kashiwa",
            "urayasu": "sc_urayasu",
            "chiba_inage": "sc_chibashiinage",
            "chiba_hanamigawa": "sc_chibashihanamigawa",
            "nagareyama": "sc_nagareyama",
            "narashino": "sc_narashino",
            "noda": "sc_noda",
        },
    }

    RAKUMACHI_PREF_MAP = {
        "13": "tokyo", "14": "kanagawa", "11": "saitama", "12": "chiba",
    }
    KENBIYA_PREF_MAP = {
        "13": "tokyo", "14": "kanagawa", "11": "saitama", "12": "chiba",
    }

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24", "Google Chrome";v="131"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    }

    def __init__(self):
        super().__init__("ScraperAgent")
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)
        self._rate_limit = 4.0
        self._last_request = 0.0
        self.url_scraper = UrlScraperAgent()
        self.last_source_errors: Dict[str, str] = {}
        self._browser = get_browser_fetcher()

    def _warm_session(self, url: str):
        """一覧取得前にトップへアクセスして Cookie を温める（403対策）"""
        try:
            self.session.get(url, timeout=12)
        except requests.RequestException:
            pass

    def _fetch_html(
        self,
        url: str,
        *,
        params: Optional[Dict[str, str]] = None,
        headers: Optional[Dict[str, str]] = None,
        warm_url: Optional[str] = None,
        prefer_browser: bool = False,
        timeout: int = 20,
    ) -> tuple[Optional[str], Optional[str]]:
        """
        HTML取得。requests →（拒否時）Playwright の順。
        Returns: (html, error_message)
        """
        full_url = url
        if params:
            from urllib.parse import urlencode

            qs = urlencode(params)
            full_url = f"{url}?{qs}" if "?" not in url else f"{url}&{qs}"

        html: Optional[str] = None
        status: Optional[int] = None
        err: Optional[str] = None

        if not prefer_browser:
            self._throttle()
            try:
                resp = self.session.get(url, params=params, timeout=timeout, headers=headers or {})
                status = resp.status_code
                resp.encoding = resp.apparent_encoding or "utf-8"
                html = resp.text
                if resp.status_code == 200 and not looks_blocked(html, status):
                    return html, None
                err = describe_block(html or "", status)
            except requests.RequestException as e:
                err = f"HTTP error: {e}"
                html = None

        if playwright_available():
            self.logger.info("ブラウザ取得にフォールバック: %s", full_url[:120])
            b_html, b_status, b_err = self._browser.fetch(
                full_url, warm_url=warm_url, wait_ms=2200
            )
            if b_html and not looks_blocked(b_html, b_status):
                return b_html, None
            err = b_err or describe_block(b_html or "", b_status) or err

        return None, err or "取得失敗"

    # ================================================================
    # 収益物件スクレイピング (楽待)
    # ================================================================

    def run(
        self,
        prefecture_code: str = "13",
        sources: List[str] = None,
        price_min: int = None,
        price_max: int = None,
        yield_min: float = None,
        max_pages: int = 10,
        split_by_price: bool = False,
    ) -> List[Property]:
        """
        複数ポータルから収益物件をスクレイピング

        Args:
            prefecture_code: 都道府県コード
            sources: 対象ソース（rakumachi/kenbiya/rals）
            max_pages: 最大ページ数
        """
        _ = (price_min, price_max, yield_min, split_by_price)
        srcs = [s.lower() for s in (sources or ["rakumachi"])]
        self.logger.info(
            f"収益物件スクレイピング開始: pref={prefecture_code}, pages={max_pages}, sources={srcs}"
        )

        all_properties = []
        seen_urls = set()
        self.last_source_errors = {}
        source_handlers = {
            "rakumachi": self._scrape_rakumachi_page,
            "kenbiya": self._scrape_kenbiya_page,
            "rals": self._scrape_rals_page,
            "athome": self._scrape_athome_page,
        }
        warm_urls = {
            "rakumachi": self.RAKUMACHI_BASE + "/",
            "kenbiya": "https://www.kenbiya.com/",
            "rals": "https://www.rals.co.jp/",
            "athome": self.ATHOME_BASE + "/",
        }

        for src in srcs:
            handler = source_handlers.get(src)
            if not handler:
                self.logger.info(f"  [{src}] 未対応ソースのためスキップ")
                continue
            if warm_urls.get(src):
                self._warm_session(warm_urls[src])
            got_any = False
            for page in range(1, max_pages + 1):
                self.logger.info(f"  [{src}] ページ {page}/{max_pages}...")
                try:
                    props = handler(prefecture_code, page)
                    if not props:
                        if page == 1 and src not in self.last_source_errors:
                            self.last_source_errors[src] = "取得0件（ブロックまたはHTML構造変更の可能性）"
                            self.logger.info(f"  [{src}] 取得0件")
                        break
                    got_any = True
                    for p in props:
                        url = getattr(p, "source_url", None) or ""
                        if url and url not in seen_urls:
                            seen_urls.add(url)
                            all_properties.append(p)
                        elif not url:
                            all_properties.append(p)
                    self.logger.info(f"  [{src}] ページ {page}: {len(props)}件")
                except Exception as e:
                    self.last_source_errors[src] = str(e)
                    self.logger.warning(f"  [{src}] ページ {page} エラー: {e}")
                    break
            if got_any and src in self.last_source_errors:
                self.last_source_errors.pop(src, None)

        self.logger.info(f"収益物件スクレイピング完了: {len(all_properties)}件")
        return all_properties

    def _throttle(self):
        elapsed = time.time() - self._last_request
        if elapsed < self._rate_limit:
            time.sleep(self._rate_limit - elapsed)
        self._last_request = time.time()

    def _scrape_rakumachi_page(
        self, pref_code: str, page: int = 1,
    ) -> List[Property]:
        """楽待の収益物件一覧をスクレイピング（403時はブラウザフォールバック）"""
        pref_name = self.RAKUMACHI_PREF_MAP.get(pref_code, "tokyo")
        url = f"{self.RAKUMACHI_SEARCH}{pref_name}/"
        params = {
            "page": str(page),
            "sort": "property_created_at",
            "sort_type": "desc",
        }
        headers = {
            "Referer": f"{self.RAKUMACHI_BASE}/",
            "Sec-Fetch-Site": "same-origin",
        }
        html, err = self._fetch_html(
            url,
            params=params,
            headers=headers,
            warm_url=f"{self.RAKUMACHI_BASE}/",
        )
        if not html:
            self.last_source_errors["rakumachi"] = err or "取得失敗"
            self.logger.error("楽待取得失敗: %s", err)
            return []

        props = self._parse_rakumachi(html, pref_code)
        if not props and looks_blocked(html):
            self.last_source_errors["rakumachi"] = describe_block(html)
        return props

    def _parse_rakumachi(self, html: str, pref_code: str) -> List[Property]:
        """楽待HTMLを構造的にパース"""
        soup = BeautifulSoup(html, "html.parser")
        properties = []

        cards = soup.select(".propertyBlock")
        for card in cards:
            try:
                prop = self._parse_rakumachi_card(card, pref_code)
                if prop:
                    properties.append(prop)
            except Exception as e:
                self.logger.debug(f"楽待パースエラー: {e}")
                continue

        return properties

    def _parse_rakumachi_card(self, card, pref_code: str) -> Optional[Property]:
        """楽待の .propertyBlock カードを構造的にパース"""
        # タイトル（物件種別 + 物件名）
        title_el = card.select_one(".propertyBlock__title")
        if not title_el:
            return None
        name = title_el.get_text(strip=True)
        if not name or len(name) < 3:
            return None

        # PR広告除外
        if any(name.startswith(prefix) for prefix in self.EXCLUDED_PREFIXES):
            return None

        # 物件種別フィルタ: 1棟もの・土地のみ通す
        full_text = card.get_text(" ", strip=True)
        is_allowed = any(t in name for t in self.ALLOWED_PROPERTY_TYPES)
        if not is_allowed:
            return None

        # 借地権・区分除外
        if any(kw in name or kw in full_text[:200] for kw in self.EXCLUDED_KEYWORDS):
            return None

        # 一都三県以外の物件除外
        pref_names = {"13": "東京都", "14": "神奈川県", "11": "埼玉県", "12": "千葉県"}
        target_pref = pref_names.get(pref_code, "")
        address_text = ""
        for dt in card.select("dt"):
            if "所在地" in dt.get_text(strip=True):
                dd = dt.find_next_sibling("dd")
                if dd:
                    address_text = dd.get_text(strip=True)
                    break
        if not address_text:
            import re as _re
            m = _re.search(r"所在地\s*((?:東京都|神奈川県|埼玉県|千葉県)\S+)", full_text)
            address_text = m.group(1) if m else ""
        # 一都三県のいずれかを含まない場合は除外
        if address_text and not any(p in address_text for p in pref_names.values()):
            return None

        # リンク
        link = card.select_one("a[href*='/syuueki']")
        source_url = ""
        if link:
            href = link.get("href", "")
            source_url = href if href.startswith("http") else self.RAKUMACHI_BASE + href

        # dt/dd構造から情報抽出
        fields = {}
        for dt in card.select("dt"):
            label = dt.get_text(strip=True)
            dd = dt.find_next_sibling("dd")
            if dd:
                fields[label] = dd.get_text(strip=True)

        # offerエリア（価格・利回り）
        offer = card.select_one(".propertyBlock__offer")
        if offer:
            for dt in offer.select("dt"):
                label = dt.get_text(strip=True)
                dd = dt.find_next_sibling("dd")
                if dd:
                    fields[label] = dd.get_text(strip=True)

        # 価格
        price_text = fields.get("価格", "")
        price = self._extract_price(price_text)

        # 利回り
        yield_text = fields.get("利回り", "")
        gross_yield = self._extract_yield_val(yield_text)

        # 所在地
        address = fields.get("所在地", "")

        # 交通
        transport = fields.get("交通", "")
        station, distance, station_id = self._extract_station_from_transport(transport, pref_code=pref_code)

        # 構造
        structure = self._extract_structure(
            fields.get("構造", "") + " " + fields.get("建物構造", "")
        )

        # 築年
        age_text = fields.get("築年数", "") or fields.get("築年月", "")
        built_year, building_age = self._extract_age(age_text)

        # 面積
        land_text = fields.get("土地面積", "")
        land_area = self._extract_area_val(land_text)
        bldg_text = (
            fields.get("建物面積", "")
            or fields.get("建物延床面積", "")
            or fields.get("延床面積", "")
            or fields.get("専有面積", "")
        )
        building_area = self._extract_area_val(bldg_text)
        floors = self._extract_floor_count(
            fields.get("階数", "") + " " + fields.get("建物階数", "") + " " + fields.get("建物", "")
        )

        # フォールバック: フルテキストから不足情報を補完
        full_text = card.get_text(" ", strip=True)
        if not price:
            # "価格 4180万円" パターン
            m = re.search(r"価格\s*([\d,]+万円|[\d.]+億円|[\d]+億[\d,]+万円)", full_text)
            if m:
                price = self._extract_price(m.group(1))
        if not address:
            m = re.search(
                r"所在地\s*((?:東京都|神奈川県|埼玉県|千葉県)\S+?(?:区|市|町|村)\S*?\d)",
                full_text
            )
            if m:
                address = m.group(1)
        if not gross_yield:
            m = re.search(r"利回り\s*([\d.]+)\s*%", full_text)
            if m:
                gross_yield = float(m.group(1)) / 100
        if not structure:
            # "建物構造 RC造" or "SRC造" etc
            m = re.search(r"(?:建物構造|構造)\s*(SRC|RC|鉄骨|木造|鉄筋)", full_text)
            if m:
                s = m.group(1)
                structure = "SRC" if s == "鉄骨鉄筋" else ("RC" if s == "鉄筋" else s)
            else:
                structure = self._extract_structure(full_text)
        if not station:
            station, distance, station_id = self._extract_station_from_transport(full_text, pref_code=pref_code)
        if not land_area:
            m = re.search(r"土地\s*([\d,.]+)\s*(?:m²|㎡|m2)", full_text)
            if m:
                land_area = float(m.group(1).replace(",", ""))
        if not building_area:
            m = re.search(r"(?:建物|延床|建物延床|専有)\s*(?:面積)?\s*[:：]?\s*([\d,.]+)\s*(?:m²|㎡|m2|平米)", full_text)
            if m:
                building_area = float(m.group(1).replace(",", ""))
        if not building_area:
            m = re.search(r"([\d,.]+)\s*(?:m²|㎡|m2|平米)\s*(?:建物|延床|専有)", full_text)
            if m:
                building_area = float(m.group(1).replace(",", ""))
        if not building_age:
            m = re.search(r"築(\d+)年", full_text)
            if m:
                building_age = int(m.group(1))
                from datetime import datetime
                built_year = datetime.now().year - building_age
        if not floors:
            floors = self._extract_floor_count(full_text)

        if not price and not gross_yield:
            return None

        city_code = self._guess_city_code(address, pref_code)

        return Property(
            name=name[:100],
            address=address,
            prefecture_code=pref_code,
            city_code=city_code,
            asking_price=price,
            land_area=land_area,
            building_area=building_area,
            structure=structure,
            floors=floors,
            built_year=built_year,
            building_age=building_age,
            gross_yield=gross_yield,
            nearest_station=station,
            station_distance_min=distance,
            station_id=station_id,
            current_rent_annual=(
                int(price * gross_yield) if price and gross_yield else None
            ),
            source="rakumachi",
            source_url=source_url,
        )

    def _scrape_kenbiya_page(self, pref_code: str, page: int = 1) -> List[Property]:
        """健美家の一覧から詳細URLを収集して構造化"""
        self._throttle()
        pref_slug = self.KENBIYA_PREF_MAP.get(pref_code, "tokyo")
        url = self.KENBIYA_SEARCH.format(pref_slug=pref_slug)
        params = {"page": str(page)}
        return self._crawl_detail_urls(
            source_name="kenbiya",
            list_url=url,
            params=params,
            url_pattern=r"kenbiya\.com/.+/(?:detail|view|bukken|estate)",
        )

    def _scrape_rals_page(self, pref_code: str, page: int = 1) -> List[Property]:
        """不動産投資★連合隊の一覧から詳細URLを収集して構造化"""
        self._throttle()
        pref_map = {"13": "tokyo", "14": "kanagawa", "11": "saitama", "12": "chiba"}
        params = {
            "p": str(page),
            "area": pref_map.get(pref_code, "tokyo"),
        }
        return self._crawl_detail_urls(
            source_name="rals",
            list_url=self.RALS_SEARCH,
            params=params,
            url_pattern=r"fudosan\.cbiz\.ne\.jp/detailPage/",
        )


    def _scrape_athome_page(self, pref_code: str, page: int = 1) -> List[Property]:
        """アットホーム事業用（一棟売等）一覧から詳細URLを収集して構造化"""
        self._throttle()
        pref_slug = self.ATHOME_PREF_MAP.get(pref_code, "tokyo")
        if page <= 1:
            list_url = self.ATHOME_BUY_OTHER.format(pref_slug=pref_slug)
        else:
            list_url = (
                self.ATHOME_BUY_OTHER.format(pref_slug=pref_slug).rstrip("/")
                + f"/page{page}/"
            )
        return self._crawl_detail_urls(
            source_name="athome",
            list_url=list_url,
            params={},
            url_pattern=r"athome\.co\.jp/buy_other/\d+",
            prefer_browser=True,
            list_text_include=self.ATHOME_LIST_KEYWORDS,
            list_text_exclude=self.ATHOME_LIST_EXCLUDE,
        )

    def _crawl_detail_urls(
        self,
        source_name: str,
        list_url: str,
        params: Dict[str, str],
        url_pattern: str,
        prefer_browser: bool = False,
        list_text_include=None,
        list_text_exclude=None,
    ) -> List[Property]:
        """一覧ページから候補URLを抽出し、詳細ページを構造化"""
        warm = f"{urlparse_base(list_url)}/"
        html, err = self._fetch_html(
            list_url,
            params=params,
            warm_url=warm,
            prefer_browser=prefer_browser,
        )
        if not html:
            key = {
                "kenbiya": "kenbiya",
                "健美家": "kenbiya",
                "rals": "rals",
                "不動産投資連合隊": "rals",
                "athome": "athome",
                "アットホーム": "athome",
            }.get(source_name, source_name)
            # 属性名の揺れに対応
            if not hasattr(self, "last_source_errors") or self.last_source_errors is None:
                self.last_source_errors = {}
            self.last_source_errors[key] = err or "一覧取得失敗"
            self.logger.warning("[%s] 一覧取得失敗: %s", source_name, err)
            return []

        soup = BeautifulSoup(html, "html.parser")
        include = tuple(list_text_include or ())
        exclude = tuple(list_text_exclude or ())
        urls = []
        for a in soup.select("a[href]"):
            href = (a.get("href") or "").strip()
            if not href:
                continue
            if href.startswith("//"):
                href = "https:" + href
            elif href.startswith("/"):
                href = urljoin(list_url, href)
            elif not href.startswith("http"):
                href = urljoin(list_url, href)
            if not re.search(url_pattern, href):
                continue
            if "login" in href or "member" in href:
                continue
            # 一覧カード文言で投資物件を優先フィルタ（athome混在対策）
            card_text = ""
            parent = a.find_parent(["article", "li", "div", "tr"])
            if parent is not None:
                card_text = parent.get_text(" ", strip=True)[:240]
            else:
                card_text = a.get_text(" ", strip=True)[:240]
            if exclude and any(k in card_text for k in exclude):
                continue
            if include and not any(k in card_text for k in include):
                continue
            urls.append(href.split("#")[0])

        # 重複除去 + 上限（一覧1pあたりの過剰アクセスを抑える）
        uniq_urls = list(dict.fromkeys(urls))[:25]
        # 一覧フィルタで0件なら、フィルタ無しのURLへフォールバック（最大12件）
        if not uniq_urls and include:
            raw = []
            for a in soup.select("a[href]"):
                href = (a.get("href") or "").strip()
                if not href:
                    continue
                if href.startswith("//"):
                    href = "https:" + href
                elif href.startswith("/"):
                    href = urljoin(list_url, href)
                elif not href.startswith("http"):
                    href = urljoin(list_url, href)
                if re.search(url_pattern, href) and "login" not in href:
                    raw.append(href.split("#")[0])
            uniq_urls = list(dict.fromkeys(raw))[:12]

        properties: List[Property] = []
        use_browser_detail = bool(prefer_browser)
        for detail_url in uniq_urls:
            try:
                prop = self.url_scraper.run(
                    url=detail_url,
                    use_ocr=False,
                    use_browser=use_browser_detail,
                )
                if not prop and not use_browser_detail and playwright_available():
                    prop = self.url_scraper.run(
                        url=detail_url, use_ocr=False, use_browser=True
                    )
                    if prop:
                        use_browser_detail = True
                if not prop:
                    continue
                title = (prop.name or "").strip()
                excluded = getattr(self, "EXCLUDED_KEYWORDS", []) or []
                if any(kw in title for kw in excluded):
                    continue
                allowed = self.ALLOWED_PROPERTY_TYPES
                if not any(t in title for t in allowed):
                    if not getattr(prop, "gross_yield", None):
                        continue
                prop.source = source_name
                properties.append(prop)
            except Exception as e:
                self.logger.debug(f"[{source_name}] 詳細解析失敗: {detail_url} ({e})")
                continue
        return properties

    # ================================================================
    # 賃料スクレイピング (SUUMO賃貸)
    # ================================================================

    def scrape_rentals(
        self,
        prefecture_code: str = "13",
        city_code: str = "",
        max_pages: int = 10,
    ) -> List[Dict]:
        """
        SUUMO賃貸から賃料データをスクレイピング
        区ごとに巡回して大量取得
        """
        self.logger.info(
            f"賃料スクレイピング開始: pref={prefecture_code}, pages={max_pages}"
        )

        areas = self.SUUMO_CHINTAI_AREAS.get(prefecture_code, {})
        if not areas:
            self.logger.warning(f"賃貸エリア未定義: pref={prefecture_code}")
            return []

        all_rentals = []
        pages_per_area = max(1, max_pages // len(areas))

        for area_key, sc_code in areas.items():
            for page in range(1, pages_per_area + 1):
                try:
                    rentals = self._scrape_suumo_chintai_page(
                        prefecture_code, sc_code, page
                    )
                    if not rentals:
                        break
                    all_rentals.extend(rentals)
                    self.logger.info(
                        f"  [{area_key}] p{page}: {len(rentals)}件"
                    )
                except Exception as e:
                    self.logger.warning(f"  [{area_key}] p{page} エラー: {e}")
                    break

        self.logger.info(f"賃料スクレイピング完了: {len(all_rentals)}件")
        return all_rentals

    def _scrape_suumo_chintai_page(
        self, pref_code: str, sc_code: str, page: int
    ) -> List[Dict]:
        """SUUMO賃貸の1ページをスクレイピング"""
        self._throttle()

        pref_map = {"13": "tokyo", "14": "kanagawa", "11": "saitama", "12": "chiba"}
        pref_name = pref_map.get(pref_code, "tokyo")
        url = f"{self.SUUMO_BASE}/chintai/{pref_name}/{sc_code}/"
        params = {"page": str(page)}

        try:
            resp = self.session.get(url, params=params, timeout=15)
            resp.raise_for_status()
            resp.encoding = "utf-8"
        except requests.RequestException as e:
            self.logger.error(f"SUUMO賃貸 HTTP error: {e}")
            return []

        return self._parse_suumo_chintai(resp.text, pref_code)

    def _parse_suumo_chintai(self, html: str, pref_code: str) -> List[Dict]:
        """SUUMO賃貸HTMLをパース（.cassetteitem構造）"""
        soup = BeautifulSoup(html, "html.parser")
        rentals = []

        cards = soup.select(".cassetteitem")
        for card in cards:
            try:
                # 建物情報
                title_el = (
                    card.select_one(".cassetteitem_content-title")
                    or card.select_one(".cassetteitem_content-label")
                    or card.select_one(".cassetteitem_title")
                )
                building_name = title_el.get_text(strip=True) if title_el else ""

                addr_el = card.select_one(".cassetteitem_detail-col1")
                address = addr_el.get_text(strip=True) if addr_el else ""
                if not address:
                    # 稀に構造違いで所在地が別クラスに入る
                    alt_addr = card.select_one(".cassetteitem_detail-text")
                    address = alt_addr.get_text(strip=True) if alt_addr else ""

                station_el = card.select_one(".cassetteitem_detail-col2")
                station_text = station_el.get_text(strip=True) if station_el else ""

                col3 = card.select_one(".cassetteitem_detail-col3")
                col3_text = col3.get_text(strip=True) if col3 else ""

                # 築年・階数
                built_year = None
                m = re.search(r"築(\d+)年", col3_text)
                if m:
                    from datetime import datetime
                    built_year = datetime.now().year - int(m.group(1))
                floors_total = None
                m_total = re.search(r"(\d+)階建", col3_text)
                if m_total:
                    floors_total = int(m_total.group(1))
                if floors_total is None:
                    floors_total = self._extract_floor_count(col3_text)

                # 構造
                structure = self._extract_structure(col3_text + " " + building_name)

                # 駅・徒歩
                station, distance, station_id = self._extract_station_from_transport(station_text, pref_code=pref_code)

                city_code = self._guess_city_code(address, pref_code)

                # 各部屋（建物内の複数ユニット）
                units = card.select("table.cassetteitem_other tbody tr")
                for unit in units:
                    tds = unit.select("td")
                    if len(tds) < 6:
                        continue

                    texts = [td.get_text(strip=True) for td in tds]

                    unit_text = " ".join(texts)

                    # 賃料 (例: "15万円12000円" → 150000 + 12000 = rent, 管理費は別)
                    rent_text = texts[3] if len(texts) > 3 else unit_text
                    rent = self._extract_rent(rent_text)
                    if not rent or rent < 10000:
                        continue

                    # 面積 (例: "1SK34.7m2")
                    area_text = texts[5] if len(texts) > 5 else unit_text
                    area = self._extract_area_val(area_text)
                    if not area or area < 5:
                        continue
                    floor = None
                    floor_text = " ".join(texts[:3]) or unit_text
                    m_floor = re.search(r"(\d+)階", floor_text)
                    if m_floor:
                        floor = int(m_floor.group(1))
                    if floor is None:
                        floor = self._extract_unit_floor(unit_text)

                    # 間取り（1R/ワンルーム/1K/1LDK 等を正規化）
                    layout = self._extract_layout(area_text or " ".join(texts))
                    if not layout:
                        # 最低限の品質担保: 間取りが取れない部屋は除外
                        continue
                    if not address or len(address) < 3:
                        # 所在地欠落レコードは蓄積しない
                        continue

                    rentals.append({
                        "building_name": building_name,
                        "address": address,
                        "rent_monthly": rent,
                        "area_sqm": area,
                        "rent_per_sqm": rent / area,
                        "layout": layout,
                        "structure": structure,
                        "built_year": built_year,
                        "floor": floor,
                        "floors_total": floors_total,
                        "nearest_station": station,
                        "station_distance_min": distance,
                        "station_id": station_id,
                        "city_code": city_code,
                        "source": "SUUMO賃貸",
                    })
            except Exception as e:
                self.logger.debug(f"SUUMO賃貸パースエラー: {e}")
                continue

        return rentals

    # ================================================================
    # テキスト抽出ヘルパー
    # ================================================================

    def _extract_price(self, text: str) -> Optional[int]:
        """価格抽出（万円 → 円）"""
        if not text:
            return None
        # "1億5000万円"
        m = re.search(r"(\d+)億(\d+)万", text)
        if m:
            return int(m.group(1)) * 100_000_000 + int(m.group(2)) * 10_000
        # "5000万円"
        m = re.search(r"([\d,]+)万円", text)
        if m:
            return int(m.group(1).replace(",", "")) * 10_000
        # "1.5億円"
        m = re.search(r"([\d.]+)億円", text)
        if m:
            return int(float(m.group(1)) * 100_000_000)
        return None

    def _extract_yield_val(self, text: str) -> Optional[float]:
        """利回り値抽出"""
        if not text:
            return None
        m = re.search(r"([\d.]+)\s*%", text)
        if m:
            val = float(m.group(1))
            if 0.5 < val < 30:
                return val / 100
        return None

    def _extract_rent(self, text: str) -> Optional[int]:
        """賃料抽出"""
        if not text:
            return None
        # "15万円" or "15.5万円"
        m = re.search(r"([\d.]+)万円", text)
        if m:
            return int(float(m.group(1)) * 10000)
        # "150000円"
        m = re.search(r"([\d,]+)円", text)
        if m:
            val = int(m.group(1).replace(",", ""))
            if val > 10000:
                return val
        return None

    def _extract_area_val(self, text: str) -> Optional[float]:
        """面積値抽出"""
        if not text:
            return None
        m = re.search(r"([\d,.]+)\s*(?:m2|m²|㎡|平米)", text)
        if m:
            val = float(m.group(1).replace(",", ""))
            if 3 < val < 50000:
                return val
        return None

    def _extract_floor_count(self, text: str) -> Optional[int]:
        if not text:
            return None
        m = re.search(r"(\d+)\s*階建", text)
        if m:
            return int(m.group(1))
        m = re.search(r"地上\s*(\d+)\s*階", text)
        if m:
            return int(m.group(1))
        return None

    def _extract_unit_floor(self, text: str) -> Optional[int]:
        if not text:
            return None
        # "3階/10階建", "4階", "B1階"
        m = re.search(r"(\d+)\s*階\s*/\s*\d+\s*階建", text)
        if m:
            return int(m.group(1))
        m = re.search(r"(\d+)\s*階", text)
        if m:
            return int(m.group(1))
        return None

    def _extract_structure(self, text: str) -> Optional[str]:
        if not text:
            return None
        for s in ["SRC", "RC", "鉄骨鉄筋", "鉄骨", "鉄筋", "軽量鉄骨", "木造"]:
            if s in text:
                if s == "鉄骨鉄筋":
                    return "SRC"
                if s == "鉄筋":
                    return "RC"
                return s
        return None

    def _extract_age(self, text: str) -> tuple:
        if not text:
            return (None, None)
        from datetime import datetime
        m = re.search(r"築(\d+)年", text)
        if m:
            age = int(m.group(1))
            return (datetime.now().year - age, age)
        m = re.search(r"(\d{4})年", text)
        if m:
            year = int(m.group(1))
            return (year, max(0, datetime.now().year - year))
        return (None, None)

    def _extract_station_from_transport(self, text: str, pref_code: str = None) -> tuple:
        """交通テキストから実在駅名/駅IDと徒歩分数を抽出"""
        if not text:
            return (None, None, None)

        raw = str(text).replace("\n", " ").replace("　", " ")
        raw = re.sub(r"\s+", " ", raw).strip()

        # 候補抽出（「駅」表記、引用符表記、最寄り駅ラベル）
        candidates = []
        for pat in [
            r"([^\s/／()（）,、]+?)駅",
            r"「([^」]+)」",
            r"最寄(?:り)?駅[:：]?\s*([^\s/／()（）,、]+)",
        ]:
            for m in re.finditer(pat, raw):
                name = (m.group(1) or "").strip()
                if not name:
                    continue
                if name in {"徒歩", "バス", "停", "分"}:
                    continue
                candidates.append(name)

        # 近傍距離（徒歩）を先に拾う
        dist = None
        md = re.search(r"(?:歩|徒歩)\s*(\d+)\s*分", raw)
        if md:
            dist = int(md.group(1))
        else:
            md = re.search(r"バス\s*\d+\s*分", raw)
            if md:
                # バスのみは精度が低いので徒歩距離は未設定
                dist = None

        # 候補を実在駅に正規化
        for cand in candidates:
            sid = resolve_station_id(nearest_station_text=cand, pref_code=pref_code)
            if sid and sid in STATION_MAP:
                return (STATION_MAP[sid]["name"], dist, sid)

        # 交通文全体をフォールバックで解決
        sid = resolve_station_id(nearest_station_text=raw, pref_code=pref_code)
        if sid and sid in STATION_MAP:
            return (STATION_MAP[sid]["name"], dist, sid)

        return (None, dist, None)

    def _extract_layout(self, text: str) -> Optional[str]:
        """SUUMO表記ゆれを含む間取り抽出"""
        if not text:
            return None
        normalized = text.replace("ワンルーム", "1R").replace("ﾜﾝﾙｰﾑ", "1R")
        m = re.search(r"((?:\d+)?[SLDK]+|1R|[2-9]R)", normalized)
        if m:
            layout = m.group(1).upper()
            # SUUMOの "R" 単独を "1R" に寄せる
            if layout == "R":
                layout = "1R"
            return layout
        return None

    def _extract_area(self, text: str, prefix: str) -> Optional[float]:
        pattern = prefix + r"[面積]?\s*[:：]?\s*([\d,.]+)\s*(?:m2|㎡|平米)"
        m = re.search(pattern, text)
        if m:
            return float(m.group(1).replace(",", ""))
        return None

    def _extract_yield(self, text: str) -> Optional[float]:
        m = re.search(r"利回り[^\d]*([\d.]+)\s*%", text)
        if m:
            return float(m.group(1)) / 100
        return None

    def _guess_city_code(self, address: str, pref_code: str) -> str:
        """住所から市区町村コードを推定"""
        TOKYO_MUNICIPALITIES = {
            "千代田区": "13101", "中央区": "13102", "港区": "13103",
            "新宿区": "13104", "文京区": "13105", "台東区": "13106",
            "墨田区": "13107", "江東区": "13108", "品川区": "13109",
            "目黒区": "13110", "大田区": "13111", "世田谷区": "13112",
            "渋谷区": "13113", "中野区": "13114", "杉並区": "13115",
            "豊島区": "13116", "北区": "13117", "荒川区": "13118",
            "板橋区": "13119", "練馬区": "13120", "足立区": "13121",
            "葛飾区": "13122", "江戸川区": "13123",
            "八王子市": "13201", "立川市": "13202", "武蔵野市": "13203",
            "三鷹市": "13204", "府中市": "13206", "調布市": "13208",
            "町田市": "13209", "小金井市": "13210", "小平市": "13211",
            "国分寺市": "13214", "国立市": "13215", "西東京市": "13229",
        }
        OTHER = {
            "横浜市": "14100", "川崎市": "14130", "相模原市": "14150",
            "さいたま市": "11100", "川口市": "11203",
            "千葉市": "12100", "船橋市": "12204", "浦安市": "12227",
        }
        lookup = {**TOKYO_MUNICIPALITIES, **OTHER}
        for name, code in lookup.items():
            if name in address:
                return code
        return f"unknown_{pref_code}"


def urlparse_base(url: str) -> str:
    from urllib.parse import urlparse as _urlparse

    p = _urlparse(url)
    return f"{p.scheme}://{p.netloc}"
