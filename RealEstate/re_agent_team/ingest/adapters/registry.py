"""Adapter レジストリ"""
from __future__ import annotations

from typing import Dict, List, Optional

from .base import SourceAdapter
from .portal import PortalListAdapter, UrlDetailAdapter

ADAPTERS: Dict[str, SourceAdapter] = {}


def register_default_adapters(scraper_agent, url_scraper) -> Dict[str, SourceAdapter]:
    global ADAPTERS
    ADAPTERS = {
        "rakumachi": PortalListAdapter("rakumachi", scraper_agent, url_scraper),
        "kenbiya": PortalListAdapter("kenbiya", scraper_agent, url_scraper),
        "rals": PortalListAdapter("rals", scraper_agent, url_scraper),
        "athome": PortalListAdapter("athome", scraper_agent, url_scraper),
        "homes": PortalListAdapter("homes", scraper_agent, url_scraper),
        "url": UrlDetailAdapter(url_scraper),
    }
    return ADAPTERS


def get_adapter(name: str) -> Optional[SourceAdapter]:
    return ADAPTERS.get((name or "").lower())


def list_adapters() -> List[str]:
    return sorted(k for k in ADAPTERS.keys() if k != "url")
