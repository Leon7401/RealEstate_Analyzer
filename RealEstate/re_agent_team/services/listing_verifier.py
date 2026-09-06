"""元ページ（掲載URL）の生存確認サービス"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional, Tuple

import requests

logger = logging.getLogger("ListingVerifier")

# ソフト404・掲載終了を示す文言
_DEAD_MARKERS = (
    "掲載を終了",
    "掲載終了",
    "掲載が終了",
    "この物件は見つかりません",
    "お探しのページは見つかりません",
    "ページが見つかりません",
    "指定されたページは存在しません",
    "削除されました",
    "物件は削除",
    "売止",
    "売り止め",
    "成約済",
    "成約済み",
    "募集終了",
    "取り扱いを終了",
    "not found",
    "page not found",
    "404 not found",
)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


def compute_link_status(
    *,
    source_url: Optional[str],
    listing_status: Optional[str],
    last_verified_at: Optional[str] = None,
    verify_fail_count: Optional[int] = None,
) -> str:
    """
    UI用のリンク状態コードを返す。
      no_url / unchecked / alive / suspect / dead
    """
    url = (source_url or "").strip()
    if not url:
        return "no_url"
    status = (listing_status or "active").strip().lower()
    if status == "delisted":
        return "dead"
    fails = int(verify_fail_count or 0)
    if fails > 0:
        return "suspect"
    if not last_verified_at:
        return "unchecked"
    return "alive"


def link_status_label(code: str) -> str:
    return {
        "no_url": "URLなし",
        "unchecked": "未確認",
        "alive": "掲載中",
        "suspect": "要確認",
        "dead": "リンク切れ",
    }.get(code, code)


def check_source_alive(url: str) -> Tuple[bool, Optional[int], str]:
    """
    掲載URLの生存確認。
    Returns: (alive, http_status, note)
    """
    url = (url or "").strip()
    if not url:
        return False, None, "no_url"

    headers = {
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    }

    try:
        resp = requests.head(url, allow_redirects=True, timeout=12, headers=headers)
        status = int(resp.status_code)
        # HEAD拒否や曖昧な応答は GET で確認
        if status in (403, 405, 400, 501) or status >= 500:
            resp = requests.get(
                url, allow_redirects=True, timeout=18, headers=headers, stream=True
            )
            status = int(resp.status_code)
            body = _peek_body(resp)
        elif 200 <= status < 400:
            # ソフト404検知のため軽く本文も見る
            resp = requests.get(
                url, allow_redirects=True, timeout=18, headers=headers, stream=True
            )
            status = int(resp.status_code)
            body = _peek_body(resp)
        else:
            body = ""

        if status == 404 or status == 410:
            return False, status, f"http_{status}"
        if status >= 400:
            return False, status, f"http_{status}"

        marker = _find_dead_marker(body)
        if marker:
            return False, status, f"soft404:{marker}"

        return True, status, "ok"
    except requests.Timeout:
        return False, None, "timeout"
    except requests.RequestException as e:
        return False, None, f"error:{e.__class__.__name__}"


def _peek_body(resp: requests.Response, limit: int = 80000) -> str:
    try:
        chunks = []
        size = 0
        for chunk in resp.iter_content(chunk_size=4096, decode_unicode=True):
            if not chunk:
                continue
            if isinstance(chunk, bytes):
                chunk = chunk.decode(resp.encoding or "utf-8", errors="ignore")
            chunks.append(chunk)
            size += len(chunk)
            if size >= limit:
                break
        try:
            resp.close()
        except Exception:
            pass
        return "".join(chunks)
    except Exception:
        return ""


def _find_dead_marker(html: str) -> Optional[str]:
    if not html:
        return None
    # script/style を粗く除去して判定ノイズを減らす
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html)
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).lower()
    for marker in _DEAD_MARKERS:
        if marker.lower() in text:
            return marker
    return None


def run_verification_batch(
    db,
    *,
    limit: int = 300,
    stale_hours: int = 24,
    confirm_failures: int = 2,
    tables: tuple = ("properties", "land_listings"),
) -> Dict[str, Any]:
    """DB上の対象URLを走査し、結果を記録する。"""
    out: Dict[str, Dict[str, int]] = {}
    for table in tables:
        rows = db.get_source_verification_targets(
            table=table,
            limit=max(1, int(limit)),
            stale_hours=max(1, int(stale_hours)),
        )
        checked = alive_cnt = failed_cnt = 0
        for row in rows:
            checked += 1
            alive, status, note = check_source_alive(str(row.get("source_url") or ""))
            if alive:
                alive_cnt += 1
            else:
                failed_cnt += 1
            db.record_source_verification_result(
                table=table,
                row_id=row.get("id"),
                is_alive=alive,
                http_status=status,
                note=note,
                confirm_failures=max(1, int(confirm_failures)),
            )
        out[table] = {
            "checked": checked,
            "alive": alive_cnt,
            "failed": failed_cnt,
        }
        logger.info(
            "掲載URL検証 %s: checked=%s alive=%s failed=%s",
            table,
            checked,
            alive_cnt,
            failed_cnt,
        )
    return {
        "limit": max(1, int(limit)),
        "stale_hours": max(1, int(stale_hours)),
        "confirm_failures": max(1, int(confirm_failures)),
        "result": out,
    }
