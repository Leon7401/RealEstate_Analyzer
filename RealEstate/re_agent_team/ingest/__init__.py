"""物件取得パイプライン（Source Adapter → normalize → dedupe → geo → judge）"""

from .pipeline import IngestPipeline

__all__ = ["IngestPipeline"]
