"""
マイソク(物件概要書)解析エージェント

PDF/画像のマイソクからテキストを抽出し、土地物件情報を構造化する。

抽出フロー:
  1. PDF → pdfplumber でテキスト抽出（優先）
  2. 画像 → Tesseract OCR（利用可能な場合のみ）
  3. テキストから正規表現で各フィールドを解析
  4. 不足フィールドは不動産情報ライブラリAPIで補完（座標がある場合）

対応フィールド:
  住所, 土地面積, 建蔽率/容積率, 用途地域, 前面道路幅員,
  接道, 価格, 最寄駅/徒歩分数, 所有権/借地権, 間口/奥行
"""
import re
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List

from .base_agent import BaseAgent

logger = logging.getLogger(__name__)

# PDF抽出ライブラリ
_PDFPLUMBER_AVAILABLE = False
try:
    import pdfplumber
    _PDFPLUMBER_AVAILABLE = True
except ImportError:
    logger.info("pdfplumber未インストール: pip install pdfplumber")

# 画像OCR
_OCR_AVAILABLE = False
try:
    from PIL import Image
    import pytesseract
    _OCR_AVAILABLE = True
except ImportError:
    logger.info("OCR無効: pip install Pillow pytesseract + Tesseract本体が必要")


class MaisokuAgent(BaseAgent):
    """
    マイソクPDF/画像から土地物件情報を抽出するエージェント

    使い方:
        agent = MaisokuAgent()
        result = agent.run(file_path="/path/to/maisoku.pdf")
    """

    # 用途地域の正規表現パターン
    ZONING_NAMES = [
        "第一種低層住居専用地域",
        "第二種低層住居専用地域",
        "第一種中高層住居専用地域",
        "第二種中高層住居専用地域",
        "第一種住居地域",
        "第二種住居地域",
        "準住居地域",
        "田園住居地域",
        "近隣商業地域",
        "商業地域",
        "準工業地域",
        "工業地域",
        "工業専用地域",
        # 短縮形
        "1低専", "2低専", "1中高", "2中高",
        "1住居", "2住居", "準住居",
        "近商", "商業", "準工", "工業", "工専",
    ]

    # 短縮形→正式名変換
    ZONING_SHORT_MAP = {
        "1低専": "第一種低層住居専用地域",
        "2低専": "第二種低層住居専用地域",
        "1中高": "第一種中高層住居専用地域",
        "2中高": "第二種中高層住居専用地域",
        "1住居": "第一種住居地域",
        "2住居": "第二種住居地域",
        "準住居": "準住居地域",
        "近商": "近隣商業地域",
        "商業": "商業地域",
        "準工": "準工業地域",
        "工業": "工業地域",
        "工専": "工業専用地域",
    }

    def __init__(self):
        super().__init__("MaisokuAgent")

    def run(self, file_path: str, lat: float = None, lng: float = None,
            enrich_from_api: bool = True) -> Dict[str, Any]:
        """
        マイソクファイルを解析して土地物件情報を返す

        Args:
            file_path: PDF or 画像ファイルパス
            lat: 緯度（API補完用、任意）
            lng: 経度（API補完用、任意）
            enrich_from_api: 不足フィールドをAPIで補完するか

        Returns:
            dict: LandListing互換のフィールド辞書
                  + "_raw_text": 抽出テキスト全文
                  + "_extraction_method": 抽出方法
                  + "_confidence": 各フィールドの信頼度
                  + "_rejected": True if 借地権
        """
        self.logger.info(f"マイソク解析開始: {file_path}")

        path = Path(file_path)
        if not path.exists():
            self.logger.error(f"ファイルが見つかりません: {file_path}")
            return {"error": f"ファイルが見つかりません: {file_path}"}

        # テキスト抽出
        text, method = self._extract_text(path)
        if not text or len(text.strip()) < 10:
            self.logger.warning("テキスト抽出失敗または空")
            return {
                "error": "テキスト抽出失敗",
                "_raw_text": text or "",
                "_extraction_method": method,
            }

        self.logger.info(f"  抽出方法: {method}, テキスト長: {len(text)}文字")

        # フィールド解析
        result = self._parse_fields(text)
        result["_raw_text"] = text
        result["_extraction_method"] = method
        result["maisoku_pdf_path"] = str(path.resolve())
        result["source"] = "マイソク"

        # 所有権チェック（借地権なら除外フラグ）
        if result.get("_rejected"):
            self.logger.warning("  借地権物件のため除外対象")
            return result

        # API補完（座標があり、不足フィールドがある場合）
        if enrich_from_api and lat and lng:
            result = self._enrich_from_api(result, lat, lng)

        # 抽出結果サマリー
        filled = sum(1 for k, v in result.items()
                     if not k.startswith("_") and v is not None and v != "")
        self.logger.info(f"  解析完了: {filled}フィールド抽出")

        return result

    # ===== テキスト抽出 =====

    def _extract_text(self, path: Path) -> tuple:
        """ファイルからテキストを抽出。(text, method)を返す"""
        suffix = path.suffix.lower()

        # PDF
        if suffix == ".pdf":
            text = self._extract_from_pdf(path)
            if text and len(text.strip()) > 10:
                return text, "pdfplumber"
            # PDFからテキスト取れない場合、画像OCRにフォールバック
            if _OCR_AVAILABLE:
                text = self._ocr_pdf_as_image(path)
                if text:
                    return text, "pdf_ocr"
            return text or "", "pdfplumber_empty"

        # 画像
        if suffix in (".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"):
            if _OCR_AVAILABLE:
                text = self._ocr_image(path)
                return text or "", "tesseract"
            return "", "ocr_unavailable"

        # テキストファイル
        if suffix in (".txt", ".text"):
            text = path.read_text(encoding="utf-8", errors="replace")
            return text, "text_file"

        return "", "unsupported_format"

    def _extract_from_pdf(self, path: Path) -> Optional[str]:
        """pdfplumberでPDFからテキスト抽出"""
        if not _PDFPLUMBER_AVAILABLE:
            self.logger.warning("pdfplumber未インストール")
            return None
        try:
            texts = []
            with pdfplumber.open(str(path)) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        texts.append(page_text)
                    # テーブルも抽出
                    tables = page.extract_tables()
                    for table in tables:
                        for row in table:
                            if row:
                                cells = [str(c).strip() for c in row if c]
                                if cells:
                                    texts.append(" ".join(cells))
            return "\n".join(texts)
        except Exception as e:
            self.logger.error(f"PDF抽出エラー: {e}")
            return None

    def _ocr_pdf_as_image(self, path: Path) -> Optional[str]:
        """PDFを画像に変換してOCR（pdf2imageが必要）"""
        try:
            from pdf2image import convert_from_path
            images = convert_from_path(str(path), dpi=300)
            texts = []
            for img in images:
                gray = img.convert("L")
                bw = gray.point(lambda x: 255 if x > 170 else 0)
                text = pytesseract.image_to_string(bw, lang="jpn+eng", config="--psm 6")
                if text:
                    texts.append(text)
            return "\n".join(texts) if texts else None
        except ImportError:
            self.logger.debug("pdf2image未インストール: PDFの画像OCRスキップ")
            return None
        except Exception as e:
            self.logger.error(f"PDF画像OCRエラー: {e}")
            return None

    def _ocr_image(self, path: Path) -> Optional[str]:
        """画像ファイルをOCR"""
        if not _OCR_AVAILABLE:
            return None
        try:
            img = Image.open(str(path))
            gray = img.convert("L")
            bw = gray.point(lambda x: 255 if x > 170 else 0)
            t1 = pytesseract.image_to_string(bw, lang="jpn+eng", config="--psm 6")
            t2 = pytesseract.image_to_string(bw, lang="jpn+eng", config="--psm 11")
            return t1 if len(t1 or "") >= len(t2 or "") else t2
        except Exception as e:
            self.logger.error(f"画像OCRエラー: {e}")
            return None

    # ===== フィールド解析 =====

    def _parse_fields(self, text: str) -> Dict[str, Any]:
        """テキストから各フィールドを正規表現で抽出"""
        result = {
            "address": None,
            "land_area_sqm": None,
            "building_coverage_ratio": None,
            "floor_area_ratio": None,
            "zoning": None,
            "road_width_m": None,
            "road_legal_type": None,
            "frontage_m": None,
            "depth_m": None,
            "land_price": None,
            "station": None,
            "walk_minutes": None,
            "railway_line": None,
            "land_shape": None,
            "corner_lot": False,
            "setback_required": False,
            "_rejected": False,
            "_confidence": {},
        }

        # 正規化: 全角数字→半角、全角スペース→半角
        normalized = self._normalize_text(text)

        result["address"] = self._parse_address(normalized)
        result["land_area_sqm"] = self._parse_land_area(normalized)
        bcr, far = self._parse_coverage_ratios(normalized)
        result["building_coverage_ratio"] = bcr
        result["floor_area_ratio"] = far
        result["zoning"] = self._parse_zoning(normalized)
        result["road_width_m"] = self._parse_road_width(normalized)
        result["road_legal_type"] = self._parse_road_legal_type(normalized)
        frontage, depth = self._parse_frontage_depth(normalized)
        result["frontage_m"] = frontage
        result["depth_m"] = depth
        result["land_price"] = self._parse_price(normalized)
        station, walk, line = self._parse_station(normalized)
        result["station"] = station
        result["walk_minutes"] = walk
        result["railway_line"] = line
        result["land_shape"] = self._parse_land_shape(normalized)
        result["corner_lot"] = self._parse_corner_lot(normalized)
        result["setback_required"] = self._parse_setback(normalized)

        # 所有権チェック
        rejected, ownership = self._check_ownership(normalized)
        result["_rejected"] = rejected
        result["_ownership"] = ownership

        return result

    @staticmethod
    def _normalize_text(text: str) -> str:
        """全角→半角変換等のテキスト正規化"""
        # 全角数字→半角
        table = str.maketrans("０１２３４５６７８９．，", "0123456789.,")
        text = text.translate(table)
        # 全角スペース→半角
        text = text.replace("\u3000", " ")
        # 連続空白をまとめる
        text = re.sub(r"[ \t]+", " ", text)
        return text

    def _parse_address(self, text: str) -> Optional[str]:
        """住所を抽出"""
        # 「所在地」「住所」「所在」ラベル付き
        patterns = [
            r"(?:所在地|物件所在地|住所|所在)[:\s：]*([^\n]{5,50})",
            # 都道府県から始まる住所パターン
            r"((?:東京都|北海道|(?:京都|大阪)府|.{2,3}県)[^\n]{3,40})",
        ]
        for pat in patterns:
            m = re.search(pat, text)
            if m:
                addr = m.group(1).strip()
                # 余計な後続テキストを切る
                addr = re.split(r"[　\t](?:地積|面積|交通|価格|用途)", addr)[0]
                return addr.strip()
        return None

    def _parse_land_area(self, text: str) -> Optional[float]:
        """土地面積(㎡)を抽出"""
        patterns = [
            # 「土地面積」「地積」「敷地面積」ラベル付き
            r"(?:土地面積|地積|敷地面積|面積)[:\s：]*約?(\d+[.,]?\d*)\s*(?:㎡|m2|m²|平米)",
            # 「○○.○○㎡」（ラベルなし）
            r"(\d{2,5}[.,]\d{1,2})\s*(?:㎡|m2|m²)",
            # 坪表記（変換）
            r"(?:土地面積|地積|敷地面積|面積)[:\s：]*約?(\d+[.,]?\d*)\s*坪",
        ]
        for i, pat in enumerate(patterns):
            m = re.search(pat, text)
            if m:
                val = float(m.group(1).replace(",", ""))
                # 坪→㎡変換
                if i == 2:
                    val = round(val * 3.30579, 2)
                if 10 <= val <= 10000:  # 妥当性チェック
                    return val
        return None

    def _parse_coverage_ratios(self, text: str) -> tuple:
        """建蔽率・容積率を抽出（小数で返す: 60%→0.60）"""
        bcr = None
        far = None

        # 建蔽率
        bcr_patterns = [
            r"建[蔽ぺペ]率[:\s：]*(\d{2,3})%?",
            r"建[蔽ぺペ]率[:\s：/／]*(\d{2,3})",
        ]
        for pat in bcr_patterns:
            m = re.search(pat, text)
            if m:
                val = int(m.group(1))
                if 20 <= val <= 100:
                    bcr = val / 100.0
                break

        # 容積率
        far_patterns = [
            r"容積率[:\s：]*(\d{2,3})%?",
            r"容積率[:\s：/／]*(\d{2,3})",
        ]
        for pat in far_patterns:
            m = re.search(pat, text)
            if m:
                val = int(m.group(1))
                if 50 <= val <= 1300:
                    far = val / 100.0
                break

        # 「建蔽率/容積率: 60%/200%」のような複合パターン
        combined = re.search(
            r"建[蔽ぺペ]率[/／]容積率[:\s：]*(\d{2,3})[%/／](\d{2,3})", text
        )
        if combined:
            b, f = int(combined.group(1)), int(combined.group(2))
            if 20 <= b <= 100:
                bcr = b / 100.0
            if 50 <= f <= 1300:
                far = f / 100.0

        return bcr, far

    def _parse_zoning(self, text: str) -> Optional[str]:
        """用途地域を抽出"""
        # 正式名称を長い順にマッチ（部分一致防止）
        sorted_names = sorted(self.ZONING_NAMES, key=len, reverse=True)
        for name in sorted_names:
            if name in text:
                # 短縮形なら正式名に変換
                return self.ZONING_SHORT_MAP.get(name, name)

        # 「用途地域」ラベルの後にある文字列を取得
        m = re.search(r"用途地域[:\s：]*([^\n]{2,20})", text)
        if m:
            zone_text = m.group(1).strip()
            # 既知の用途地域名にマッチするか
            for name in sorted_names:
                if name in zone_text:
                    return self.ZONING_SHORT_MAP.get(name, name)
            # そのまま返す（不明な用途地域かもしれない）
            if len(zone_text) >= 3:
                return zone_text

        return None

    def _parse_road_width(self, text: str) -> Optional[float]:
        """前面道路幅員(m)を抽出"""
        patterns = [
            r"(?:前面道路|道路幅員|幅員|接面道路)[:\s：]*約?(\d+[.,]?\d*)\s*m",
            r"(?:前面道路|道路幅員|幅員)[:\s：]*約?(\d+[.,]?\d*)\s*メートル",
            r"(\d+[.,]?\d*)\s*m\s*(?:公道|私道|市道|県道|国道)",
        ]
        for pat in patterns:
            m = re.search(pat, text)
            if m:
                val = float(m.group(1).replace(",", ""))
                if 1.0 <= val <= 50.0:  # 妥当性チェック
                    return val
        return None

    def _parse_road_legal_type(self, text: str) -> Optional[str]:
        """道路種別を抽出"""
        patterns = [
            r"(42条\s*[12]項\s*[1-5]号)",
            r"(42条\s*[12]項)",
            r"(建築基準法\s*第?42条[^\n]{0,20})",
        ]
        for pat in patterns:
            m = re.search(pat, text)
            if m:
                return m.group(1).replace(" ", "")

        # 公道/私道
        if re.search(r"(?:接面|前面|接道).*?公道", text):
            return "公道"
        if re.search(r"(?:接面|前面|接道).*?私道", text):
            return "私道"

        return None

    def _parse_frontage_depth(self, text: str) -> tuple:
        """間口・奥行を抽出"""
        frontage = None
        depth = None

        # 間口
        m = re.search(r"間口[:\s：]*約?(\d+[.,]?\d*)\s*m", text)
        if m:
            val = float(m.group(1).replace(",", ""))
            if 2.0 <= val <= 100.0:
                frontage = val

        # 奥行
        m = re.search(r"奥行[きぎ]?[:\s：]*約?(\d+[.,]?\d*)\s*m", text)
        if m:
            val = float(m.group(1).replace(",", ""))
            if 2.0 <= val <= 200.0:
                depth = val

        return frontage, depth

    def _parse_price(self, text: str) -> Optional[int]:
        """価格(円)を抽出"""
        # 億円パターン
        m = re.search(r"(?:価格|売買価格|販売価格|土地価格)[:\s：]*(\d+[.,]?\d*)\s*億円", text)
        if m:
            return int(float(m.group(1).replace(",", "")) * 100_000_000)

        # 万円パターン
        m = re.search(r"(?:価格|売買価格|販売価格|土地価格)[:\s：]*(\d[\d,]*)\s*万円", text)
        if m:
            return int(float(m.group(1).replace(",", "")) * 10_000)

        # 「X億Y万円」パターン
        m = re.search(r"(\d+)\s*億\s*(\d[\d,]*)\s*万円", text)
        if m:
            oku = int(m.group(1)) * 100_000_000
            man = int(m.group(2).replace(",", "")) * 10_000
            return oku + man

        # 億円のみ（ラベルなし）
        m = re.search(r"(\d+[.,]?\d*)\s*億円", text)
        if m:
            return int(float(m.group(1).replace(",", "")) * 100_000_000)

        # 万円のみ（ラベルなし、大きい数字のみ）
        m = re.search(r"(\d[\d,]+)\s*万円", text)
        if m:
            val = int(m.group(1).replace(",", ""))
            if val >= 100:  # 100万円以上
                return val * 10_000

        return None

    def _parse_station(self, text: str) -> tuple:
        """最寄駅・徒歩分数・路線名を抽出"""
        station = None
        walk = None
        line = None

        # 「○○線 ○○駅 徒歩○分」パターン
        m = re.search(
            r"([^\s\n]{2,10}線)\s*[「]?([^\s\n「」]{2,10}?)駅[」]?\s*(?:徒歩|歩)\s*(\d{1,3})\s*分",
            text,
        )
        if m:
            line = m.group(1)
            station = m.group(2)
            walk = int(m.group(3))
            return station, walk, line

        # 「○○駅 徒歩○分」パターン
        m = re.search(r"([^\s\n]{2,10}?)駅\s*(?:徒歩|歩)\s*(\d{1,3})\s*分", text)
        if m:
            station = m.group(1)
            walk = int(m.group(2))

        # 「最寄駅: ○○」パターン
        if not station:
            m = re.search(r"(?:最寄駅|最寄り駅)[:\s：]*([^\s\n]{2,10}?)(?:駅|$)", text)
            if m:
                station = m.group(1)

        # 路線名（単独）
        if not line:
            m = re.search(r"([^\s\n]{2,10}線)", text)
            if m:
                line = m.group(1)

        # 徒歩分数（単独）
        if not walk:
            m = re.search(r"徒歩\s*(\d{1,3})\s*分", text)
            if m:
                walk = int(m.group(1))

        return station, walk, line

    def _parse_land_shape(self, text: str) -> Optional[str]:
        """地形を抽出"""
        if re.search(r"旗竿|旗竿地|路地状", text):
            return "旗竿地"
        if re.search(r"不整形", text):
            return "不整形地"
        if re.search(r"整形", text):
            return "整形地"
        if re.search(r"(?:長方形|正方形|ほぼ整形)", text):
            return "整形地"
        return None

    def _parse_corner_lot(self, text: str) -> bool:
        """角地判定"""
        return bool(re.search(r"角地|角?2方[向面]?道路", text))

    def _parse_setback(self, text: str) -> bool:
        """セットバック有無"""
        return bool(re.search(r"セットバック|ｾｯﾄﾊﾞｯｸ|set\s*back", text, re.IGNORECASE))

    def _check_ownership(self, text: str) -> tuple:
        """所有権チェック。(rejected, ownership_type)を返す"""
        if re.search(r"借地権|定期借地|地上権|旧法賃借", text):
            return True, "借地権"
        if re.search(r"所有権", text):
            return False, "所有権"
        # 明記なしの場合はrejectedにしない
        return False, "不明"

    # ===== API補完 =====

    def _enrich_from_api(self, result: Dict, lat: float, lng: float) -> Dict:
        """不動産情報ライブラリAPIで不足フィールドを補完"""
        try:
            from data.reinfolib_client import ReinfolibClient
            client = ReinfolibClient()
            if not client.is_configured():
                self.logger.debug("APIキー未設定: API補完スキップ")
                return result

            api_data = client.enrich_land_listing(lat, lng)
            if not api_data:
                return result

            # 用途地域が未取得ならAPIから補完
            if not result.get("zoning") and api_data.get("zoning"):
                result["zoning"] = api_data["zoning"]
                result.setdefault("_confidence", {})["zoning"] = "api"

            # 建蔽率
            if not result.get("building_coverage_ratio") and api_data.get("building_coverage_ratio"):
                try:
                    raw = str(api_data["building_coverage_ratio"]).replace("%", "").strip()
                    val = float(raw)
                    result["building_coverage_ratio"] = val / 100 if val > 1 else val
                    result.setdefault("_confidence", {})["building_coverage_ratio"] = "api"
                except (ValueError, TypeError):
                    pass

            # 容積率
            if not result.get("floor_area_ratio") and api_data.get("floor_area_ratio"):
                try:
                    raw = str(api_data["floor_area_ratio"]).replace("%", "").strip()
                    val = float(raw)
                    result["floor_area_ratio"] = val / 100 if val > 1 else val
                    result.setdefault("_confidence", {})["floor_area_ratio"] = "api"
                except (ValueError, TypeError):
                    pass

            # 準防火地域
            if api_data.get("quasi_fireproof") is not None:
                result["quasi_fireproof"] = api_data["quasi_fireproof"]

            self.logger.info("  API補完完了")
        except Exception as e:
            self.logger.debug(f"API補完エラー: {e}")

        return result

    # ===== ユーティリティ =====

    def parse_text_only(self, text: str) -> Dict[str, Any]:
        """
        テキストを直接解析（ファイル不要）。
        バッチ処理やテスト用。
        """
        normalized = self._normalize_text(text)
        result = self._parse_fields(normalized)
        result["_extraction_method"] = "direct_text"
        result["source"] = "マイソク"
        return result

    def to_land_listing_dict(self, parsed: Dict) -> Dict:
        """
        解析結果をLandListing.from_dict()互換のdictに変換。
        内部キー（_で始まる）を除外。
        """
        listing_fields = {
            "address", "land_area_sqm", "building_coverage_ratio",
            "floor_area_ratio", "zoning", "road_width_m", "road_legal_type",
            "frontage_m", "depth_m", "land_price", "station", "walk_minutes",
            "railway_line", "land_shape", "corner_lot", "setback_required",
            "quasi_fireproof", "maisoku_pdf_path", "source", "source_url",
            "latitude", "longitude",
        }
        return {k: v for k, v in parsed.items() if k in listing_fields and v is not None}
