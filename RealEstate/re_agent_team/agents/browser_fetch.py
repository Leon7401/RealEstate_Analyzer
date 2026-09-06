"""
ブラウザ相当のHTML取得（Playwright）。

requests が 403/429 になるサイト向けのフォールバック。
レート制限・locale・現実的な UA を使い、過度な並列アクセスはしない。
Cloudflare 等の IP ブロック自体は突破できない点に注意。
"""
from __future__ import annotations

import logging
import os
import random
import threading
import time
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_PLAYWRIGHT_AVAILABLE = False
try:
    from playwright.sync_api import sync_playwright

    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    sync_playwright = None  # type: ignore

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

BLOCK_MARKERS = (
    "アクセスができません",
    "Access Denied",
    "Just a moment",
    "cf-browser-verification",
    "Attention Required",
    "Too Many Requests",
    "429 Too Many Requests",
)


def playwright_available() -> bool:
    if not _PLAYWRIGHT_AVAILABLE:
        return False
    # 明示的に無効化可能
    if os.getenv("SCRAPE_USE_BROWSER", "1").strip().lower() in ("0", "false", "no"):
        return False
    return True


def looks_blocked(html: str, status: Optional[int] = None) -> bool:
    if status in (403, 429, 503):
        return True
    if not html:
        return True
    sample = html[:8000]
    return any(m in sample for m in BLOCK_MARKERS)


def describe_block(html: str, status: Optional[int] = None) -> str:
    if status == 429 or (html and "Too Many Requests" in html):
        return "HTTP 429（レート制限）。間隔を空けて再試行してください"
    if status == 403 or (html and "アクセスができません" in html):
        return (
            "サイト側ブロック（Cloudflare/WAF）。"
            "データセンターIPでは突破できないことがあります。"
            "自宅回線での実行、または連合隊など別ソースを利用してください"
        )
    if html and ("Just a moment" in html or "cf-browser-verification" in html):
        return "Cloudflareチャレンジ中（自動突破は未対応）"
    if status:
        return f"HTTP {status}（取得拒否）"
    return "ブロックまたは空レスポンス"


class BrowserFetcher:
    """
    同一ホストではブラウザコンテキストを再利用し、トップを温めてから一覧へ遷移する。
    スレッドセーフ（1プロセス内の直列利用を想定）。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._last_request = 0.0
        self._min_interval = float(os.getenv("SCRAPE_BROWSER_INTERVAL", "4.0"))

    def _throttle(self):
        elapsed = time.time() - self._last_request
        wait = self._min_interval - elapsed
        if wait > 0:
            time.sleep(wait + random.uniform(0.2, 0.8))
        self._last_request = time.time()

    def fetch(
        self,
        url: str,
        *,
        warm_url: Optional[str] = None,
        wait_ms: int = 2000,
        timeout_ms: int = 45000,
    ) -> tuple[Optional[str], Optional[int], Optional[str]]:
        """
        Returns: (html, status_code, error_message)
        """
        if not playwright_available():
            return None, None, "Playwright未導入または SCRAPE_USE_BROWSER=0"

        self._throttle()
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}/"
        warm = warm_url or origin

        with self._lock:
            try:
                with sync_playwright() as p:
                    browser = p.chromium.launch(
                        headless=True,
                        args=[
                            "--disable-blink-features=AutomationControlled",
                            "--no-sandbox",
                        ],
                    )
                    context = browser.new_context(
                        locale="ja-JP",
                        timezone_id="Asia/Tokyo",
                        viewport={"width": 1366, "height": 768},
                        user_agent=CHROME_UA,
                        extra_http_headers={
                            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
                            "Upgrade-Insecure-Requests": "1",
                        },
                    )
                    page = context.new_page()
                    page.add_init_script(
                        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
                    )

                    # Cookie / セッション温め
                    try:
                        page.goto(warm, wait_until="domcontentloaded", timeout=timeout_ms)
                        page.wait_for_timeout(800 + int(random.uniform(0, 700)))
                    except Exception as e:
                        logger.info("browser warm failed: %s", e)

                    resp = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                    page.wait_for_timeout(wait_ms)
                    status = resp.status if resp else None
                    html = page.content()
                    browser.close()

                    if looks_blocked(html, status):
                        return html, status, describe_block(html, status)
                    return html, status, None
            except Exception as e:
                logger.warning("Playwright fetch error: %s", e)
                return None, None, f"Playwrightエラー: {e}"


_default_fetcher: Optional[BrowserFetcher] = None


def get_browser_fetcher() -> BrowserFetcher:
    global _default_fetcher
    if _default_fetcher is None:
        _default_fetcher = BrowserFetcher()
    return _default_fetcher
