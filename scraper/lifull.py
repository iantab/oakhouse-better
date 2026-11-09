"""LIFULL HOME'S Monthly (monthly.homes.jp): furnished monthly apartments, nationwide.

No HTML scraping: the site's own frontend loads its listings from a JSON API (api.monthly-homes.jp) with a
fixed public key that every visitor's browser sends. robots.txt allows everything; the API host has none.
A full run is ~122 list pages (100 rooms each) plus the 47 prefectures' city lists (refetched weekly).

Facts only (see the README): room photos, building photos, free-text remarks, listing titles and company
phone numbers are never kept. Each room links to its own page on monthly.homes.jp.

    python lifull.py --limit 2    # fetch 2 list pages, parse and print a summary (writes only the cache)
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from common import CACHE_DIR, PoliteClient, log, setup_logging
from xross import _area

SITE_URL = "https://monthly.homes.jp"
API_URL = "https://api.monthly-homes.jp"
API_KEY = "rakuten-lifull-stay"  # public; embedded in every monthly.homes.jp page
PER_PAGE = 100
PAGES_CACHE = CACHE_DIR / "lifull" / "pages"
CITIES_CACHE = CACHE_DIR / "lifull" / "cities"
CITIES_MAX_AGE_D = 7

# JIS prefecture codes 01–47 → the romanised slug Oakhouse's area tree uses
PREFECTURES = (
    "hokkaido aomori iwate miyagi akita yamagata fukushima ibaraki tochigi gunma saitama chiba tokyo kanagawa "
    "niigata toyama ishikawa fukui yamanashi nagano gifu shizuoka aichi mie shiga kyoto osaka hyogo nara wakayama "
    "tottori shimane okayama hiroshima yamaguchi tokushima kagawa ehime kochi fukuoka saga nagasaki kumamoto oita "
    "miyazaki kagoshima okinawa"
).split()

STRUCTURES = {  # building.structure.name → wording amenities.structure() understands
    "RC": "RC (Reinforced Concrete)", "SRC": "SRC (Steel-framed Reinforced Concrete)",
    "鉄骨造": "Steel frame", "軽量鉄骨造": "Light-gauge steel", "木造": "Wood",
    "鉄筋ブロック造": "Reinforced concrete block", "ALC": "ALC",
}
BUILDING_KINDS = {"マンション": "Monthly apartment", "アパート": "Monthly apartment",
                  "戸建て": "Monthly house", "サービスアパートメント": "Serviced apartment"}
FLOOR_PLANS = {"ワンルーム": "Studio"}
# Stay-length plans, as monthly.homes.jp labels them (料金: 3ヶ月未満 / 3ヶ月以上 / 7ヶ月以上)
PLANS = (("short_plan", "Under 3 months"), ("middle_plan", "3 months or more"), ("long_plan", "7 months or more"))
CONTRACT_FORMS = {"定期借家": "Fixed-term lease", "一時使用": "Temporary-use lease"}
BED_TYPES = {"ベッド": "bed", "ソファベッド": "sofa bed", "布団": "futon", "二段ベッド": "bunk bed"}
BED_SIZES = {"シングル": "Single", "セミダブル": "Semi-double", "ダブル": "Double", "クイーン": "Queen", "キング": "King",
             "セミシングル": "Semi-single"}


def _write_gz(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(text.encode("utf-8")))


def _read_json_gz(path: Path) -> Any:
    return json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))


def _api(client: PoliteClient, path: str, **params: Any) -> tuple[Any, dict[str, Any]]:
    """GET an API path; returns (parsed body, X-Pagination). Fails loudly on anything but JSON."""
    resp = client.get(f"{API_URL}{path}", params={"api_key": API_KEY, **params}, headers={"Accept": "application/json"})
    if resp.status_code != 200 or "json" not in resp.headers.get("content-type", ""):
        raise RuntimeError(f"LIFULL API {path} {params}: HTTP {resp.status_code} {resp.headers.get('content-type')}")
    return resp.json(), json.loads(resp.headers.get("X-Pagination") or "{}")


def _city_path(pref: int) -> Path:
    return CITIES_CACHE / f"{pref:02d}.json.gz"


def _fetch_cities(client: PoliteClient) -> None:
    """The 47 prefectures' city lists (Japanese name → romanised name); they change rarely."""
    for pref in range(1, 48):
        path = _city_path(pref)
        if path.exists() and time.time() - path.stat().st_mtime < CITIES_MAX_AGE_D * 86400:
            continue
        body, _ = _api(client, "/v1/master/address/cities", pref_id=f"{pref:02d}")
        rows = ((body or {}).get("result") or {}).get("row_set")
        if not rows:
            raise RuntimeError(f"LIFULL city list for prefecture {pref:02d} is empty; API changed?")
        _write_gz(path, json.dumps(list(rows.values()), ensure_ascii=False))


def fetch_all(delay: float = 1.5, limit: int | None = None) -> list[str]:
    """Download every list page into the cache; returns their paths for the manifest."""
    start = time.monotonic()
    client = PoliteClient(delay=delay)
    try:
        _fetch_cities(client)
        paths, page, total = [], 1, None
        while True:
            body, pagination = _api(client, "/v2/room_types", per=PER_PAGE, page=page)
            if not isinstance(body, list):
                raise RuntimeError(f"LIFULL page {page}: expected a list of rooms, got {type(body).__name__}")
            total = pagination.get("total", total)
            path = PAGES_CACHE / f"{page:04d}.json.gz"
            _write_gz(path, json.dumps(body, ensure_ascii=False))
            paths.append(path.relative_to(CACHE_DIR).as_posix())
            if not pagination.get("more") or (limit and page >= limit):
                break
            page += 1
    finally:
        client.close()
    # Pages from an earlier, longer run would otherwise linger in the cache
    keep = {Path(p).name for p in paths}
    for old in PAGES_CACHE.glob("*.json.gz"):
        if old.name not in keep:
            old.unlink()
    log.info("lifull: fetched %d pages (%s rooms listed) in %.0fs", len(paths), total, time.monotonic() - start)
    return paths


# ------------------------------------------------------------------------------------------- parse

class Cities:
    """Longest-prefix match of a full Japanese address to its city, from the cached city lists."""

    def __init__(self) -> None:
        self.by_pref: dict[int, list[dict[str, Any]]] = {}
        for pref in range(1, 48):
            path = _city_path(pref)
            rows = _read_json_gz(path) if path.exists() else []
            self.by_pref[pref] = sorted(rows, key=lambda r: -len(r["pref_name"] + r["name"]))

    def match(self, address: str) -> dict[str, Any] | None:
        for rows in self.by_pref.values():
            for r in rows:
                if address.startswith(r["pref_name"] + r["name"]):
                    return r
        return None


def _city_slug(city: dict[str, Any]) -> str | None:
    """Designated cities collapse to the city (yokohama, sapporo), like Oakhouse's tree; Tokyo keeps
    its wards (chiyoda); towns drop their district prefix (ashigarashimo_yugawara → yugawara)."""
    major = city.get("major_city_roman")
    if major and major != "23ku":
        return major
    roman = city.get("roman")
    return roman.rsplit("_", 1)[-1] if roman else None


def _title(roman: str | None) -> str | None:
    return roman.replace("_", " ").replace("-", " ").title() if roman else None


def _stations(building: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for a in building.get("accesses") or []:
        station = (a.get("station") or {}).get("roman")
        if not station or a.get("bus_stop"):
            continue
        out.append({"line": _title((a.get("line") or {}).get("roman")), "line_slug": None,
                    "station": _title(station), "station_slug": None, "walk_min": a.get("walk_time")})
    return out


def _room(r: dict[str, Any]) -> dict[str, Any]:
    price = r.get("price") or {}
    per_day = lambda *keys: sum(price.get(k) or 0 for k in keys)  # noqa: E731
    total = r.get("price_30d")
    initial = r.get("price_30d_initial_cost")
    plan = FLOOR_PLANS.get(r.get("floor_plan_text") or "", r.get("floor_plan_text") or "")
    cap = r.get("capacity")
    pay = {m.get("id") for m in r.get("method_of_payments") or []}  # 4 一括支払い (all at once), 5 分割支払い (installments)
    # ~4% of rooms list only how to pay (transfer, card…), not when; only a lone lump-sum option rules monthly out
    upfront_only = 4 in pay and 5 not in pay
    return {
        "id": r["id"],
        "number": plan or "Room",
        "status": "available",  # listings are bookable while published; the API has no vacancy calendar
        "available_from": None,
        "rent": per_day("rent_price") * 30 or None,
        # everything else per day: utilities, management and the operator's own extras (共益費, 光熱費…);
        # rent + this always equals price_30d
        "maintenance": per_day("utility_price", "management_price", "etc1_price", "etc2_price", "etc3_price") * 30,
        "monthly_total": total,
        "contract_fee": initial - total if initial is not None and total is not None else None,
        "size_m2": r.get("exclusive_area"),
        "room_type": f"Up to {cap} people" if cap and cap > 1 else ("1 person" if cap == 1 else None),
        "floor_plan": plan or None,
        "floor_label": None,
        "max_occupancy": cap,
        "tags": [t["tag_id"] for t in r.get("tags") or []],
        "campaign": bool(r.get("campaigns")),
        "pay_monthly": not upfront_only,
        "upfront_only": upfront_only,
        "installments": 5 in pay,  # explicitly offers 分割支払い; rooms that don't say stay False
        "attrs": {},
        "type_key": "lifull",
        "url": f"{SITE_URL}/room_type_id-{r['id']}",
        # [stay length, discount per day, cleaning fee] for each plan the room offers
        "plans": [[label, (r.get(key) or {}).get("discount_price") or 0, (r.get(key) or {}).get("cleaning_price")]
                  for key, label in PLANS if r.get(key)],
        "beds": [_bed(b) for b in r.get("beddings") or [] if b.get("count")],
        "contract_form": CONTRACT_FORMS.get(r.get("contract_form_text") or "", r.get("contract_form_text")),
    }


def _bed(b: dict[str, Any]) -> str:
    """"2 × Double bed" from {type_text: ベッド, size_text: ダブル, count: 2}."""
    kind = BED_TYPES.get(b.get("type_text") or "", "bed")
    size = BED_SIZES.get(b.get("size_text") or "")
    return f"{b['count']} × {f'{size} ' if size else ''}{kind}"


def _building(b: dict[str, Any], cities: Cities, year_now: int) -> dict[str, Any]:
    address = b.get("full_address") or ""
    city = cities.match(address)
    pref_code = int(city["pref_id"]) if city else None
    pref = PREFECTURES[pref_code - 1] if pref_code else None
    slug = _city_slug(city) if city else None
    if pref and slug:
        area_path, prefecture, region, ward = _area(pref, slug.replace("_", "-"))
    else:
        area_path, prefecture, region, ward = ([pref] if pref else []), _title(pref), None, None
    structure = (b.get("structure") or {}).get("name")
    built = re.match(r"(\d{4})", b.get("construction_date") or "")
    outline = " ".join(x for x in (STRUCTURES.get(structure or ""), f"Year Built:{built.group(1)}" if built else None) if x)
    zip_code = b.get("zip_code") or ""
    return {
        "id": int(b["id"]),  # 14-digit building id; far above Oakhouse's and Xross's id ranges
        "operator": "lifull",
        "kind": "apartment",
        "name_missing": False,
        "category": "MONTHLY APARTMENT",
        "name": BUILDING_KINDS.get((b.get("kind") or {}).get("name") or "", "Monthly apartment"),  # see english_name()
        "name_ja": b.get("name") or "",
        "url": "",
        "updated": "",
        "address": ", ".join(x for x in (ward if ward != region else None, region, prefecture) if x),
        "address_ja": address,
        "postcode": f"{zip_code[:3]}-{zip_code[3:]}" if len(zip_code) == 7 else None,
        "prefecture": prefecture,
        "region": region,
        "ward": ward,
        "area_path": area_path,
        "lat": b.get("lat"),
        "lng": b.get("lng"),
        "operator_stations": _stations(b),
        "terminal": None,
        "terms": [],
        "discounts": [],
        "building": {"Building outline": outline} if outline else {},
        "building_tags": [t["tag_id"] for t in b.get("tags") or []],
        "shared_facilities": {},
        "room_types": [],
        "rooms": [],
    }


def process(entries: list[str]) -> list[dict[str, Any]]:
    """build.py's process stage: cached list pages → one property per building, one room per room type."""
    cities = Cities()
    year_now = datetime.now().year
    buildings: dict[str, dict[str, Any]] = {}
    rooms = 0
    for rel in entries:
        for r in _read_json_gz(CACHE_DIR / rel):
            b = r.get("building") or {}
            if not b.get("id"):
                continue
            try:
                prop = buildings.get(b["id"]) or buildings.setdefault(b["id"], _building(b, cities, year_now))
                prop["rooms"].append(_room(r))
                rooms += 1
            except Exception as exc:
                log.error("parse lifull room %s failed: %s", r.get("id"), exc)
    props = list(buildings.values())
    for p in props:
        p["rooms"].sort(key=lambda r: (r["monthly_total"] is None, r["monthly_total"] or 0))
        p["url"] = p["rooms"][0]["url"]
    no_area = sum(1 for p in props if len(p["area_path"]) < 2)
    log.info("lifull: %d rooms → %d buildings (%d without a matched city)", rooms, len(props), no_area)
    return props


def english_name(p: dict[str, Any]) -> str:
    """Listing titles are Japanese marketing copy, so build an English one from facts: building type +
    nearest station (OSM's English name when we found one, else LIFULL's romanised one)."""
    near = (p["stations"][0]["name"] if p.get("stations") else
            p["operator_stations"][0]["station"] if p["operator_stations"] else None)
    if near:
        return f"{p['name']} near {re.sub(r'\s*(Station|Stn)$', '', near)} Stn"
    return f"{p['name']} in {p['ward'] or p['prefecture'] or 'Japan'}"


def fetch(args: argparse.Namespace) -> list[str]:
    """build.py's fetch stage."""
    return fetch_all(delay=args.lifull_delay, limit=args.limit and max(1, args.limit // PER_PAGE + 1))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=2, help="list pages to fetch")
    ap.add_argument("--lifull-delay", type=float, default=1.5)
    args = ap.parse_args()
    setup_logging()
    props = process(fetch_all(delay=args.lifull_delay, limit=args.limit))
    for p in props[:15]:
        p["stations"] = []
        log.info("%d | %s / %s | %s | %s | %s,%s | %d rooms (%s) | %s", p["id"], english_name(p), p["name_ja"],
                 "/".join(p["area_path"]), p["building"].get("Building outline"), p["lat"], p["lng"], len(p["rooms"]),
                 ", ".join(f"{r['number']}:{r['monthly_total']}" for r in p["rooms"][:3]),
                 [s["station"] for s in p["operator_stations"]][:2])


if __name__ == "__main__":
    main()
