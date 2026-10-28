"""Reverse-geocode property coordinates with Nominatim (English), cached forever.

Nominatim's usage policy: at most 1 request/second and an identifying User-Agent.
Properties don't move, so each (rounded) coordinate is looked up once and kept in
scraper/.cache/geocode.json, which CI persists with actions/cache.
"""

from __future__ import annotations

from typing import Any

from common import CACHE_DIR, TOOL_UA, PoliteClient, log, read_json, write_json

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
CACHE_FILE = CACHE_DIR / "geocode.json"


def _key(lat: float, lng: float) -> str:
    return f"{lat:.6f},{lng:.6f}"


def _summarise(addr: dict[str, str]) -> dict[str, Any]:
    """Keep the parts that make up a Japanese address; drop building/road names."""
    return {
        "postcode": addr.get("postcode"),
        "chome": addr.get("neighbourhood") or addr.get("block"),       # "Higashi Nakano 1"
        "town": addr.get("quarter") or addr.get("suburb"),             # "Higashi Nakano"
        # Tokyo's 23 wards come back as `city`; designated cities (Yokohama…) put the ward in `city_district`
        "ward": addr.get("city_district") or addr.get("city"),
        "city": addr.get("city") or addr.get("town") or addr.get("village"),
        "prefecture": addr.get("province") or addr.get("state"),
    }


class Geocoder:
    def __init__(self, max_new: int | None = None):
        self.cache: dict[str, Any] = read_json(CACHE_FILE, {})
        self.client: PoliteClient | None = None
        self.max_new = max_new
        self.new = 0

    def lookup(self, lat: float, lng: float) -> dict[str, Any] | None:
        key = _key(lat, lng)
        if key in self.cache:
            return self.cache[key]
        if self.max_new is not None and self.new >= self.max_new:
            return None
        if self.client is None:
            self.client = PoliteClient(delay=1.1, user_agent=TOOL_UA)
        try:
            resp = self.client.get(NOMINATIM_URL, params={
                "lat": f"{lat:.7f}", "lon": f"{lng:.7f}", "format": "jsonv2",
                "accept-language": "en", "zoom": 18, "addressdetails": 1,
            })
            resp.raise_for_status()
            result = _summarise(resp.json().get("address", {}))
        except Exception as exc:
            log.warning("geocode %s failed: %s", key, exc)
            return None
        self.cache[key] = result
        self.new += 1
        if self.new % 25 == 0:
            self.save()
            log.info("geocoded %d new coordinates", self.new)
        return result

    def save(self) -> None:
        write_json(CACHE_FILE, self.cache, pretty=True)
