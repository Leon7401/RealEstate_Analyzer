"""ポータル別 Source Adapter"""

from .base import SourceAdapter
from .registry import get_adapter, list_adapters, ADAPTERS

__all__ = ["SourceAdapter", "get_adapter", "list_adapters", "ADAPTERS"]
