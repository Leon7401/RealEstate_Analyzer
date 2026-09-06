"""ドメイン別 API ルータ（段階分割）。既存URLは互換エイリアスで維持。"""

from .ingest import router as ingest_router
from .listings import router as listings_router
from .analysis import router as analysis_router
from .map_api import router as map_router

__all__ = [
    "ingest_router",
    "listings_router",
    "analysis_router",
    "map_router",
]
