"""住所→緯度経度変換（国土地理院API）"""
import requests
import time
from typing import Optional, Tuple


class Geocoder:
    """
    国土地理院ジオコーディングAPI
    https://msearch.gsi.go.jp/address-search/AddressSearch?q={address}
    無料・認証不要
    """

    BASE_URL = "https://msearch.gsi.go.jp/address-search/AddressSearch"

    def __init__(self):
        self._last_request = 0.0

    def geocode(self, address: str) -> Optional[Tuple[float, float]]:
        """
        住所から緯度経度を取得

        Returns:
            (latitude, longitude) or None
        """
        elapsed = time.time() - self._last_request
        if elapsed < 0.5:
            time.sleep(0.5 - elapsed)
        self._last_request = time.time()

        try:
            resp = requests.get(
                self.BASE_URL,
                params={"q": address},
                timeout=10,
            )
            resp.raise_for_status()
            results = resp.json()
            if results and len(results) > 0:
                geometry = results[0].get("geometry", {})
                coords = geometry.get("coordinates", [])
                if len(coords) == 2:
                    lng, lat = coords  # GeoJSON: [lng, lat]
                    return (lat, lng)
        except Exception:
            pass
        return None
