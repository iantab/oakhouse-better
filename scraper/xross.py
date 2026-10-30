"""Scrape Xross House (x-house.co.jp) into the same property/room records parse.py builds for Oakhouse.

Follows x-house.co.jp's robots.txt: listing URLs come from the sitemap, and only the English detail
pages (/en/sharehouse/…) and each property's room list (/ajax/load_more_rooms) are fetched. The map
and search endpoints that robots.txt closes to crawlers are never used. Coordinates come from GSI's
address search (国土地理院) on the full street address in the page's map embed.

Detail pages change rarely, so they're cached gzipped in .cache/xross/detail and refetched after
7–14 days (spread by id so the refreshes don't all land on one day); CI keeps that folder in
actions/cache. Room lists carry availability and are fetched on every run; their endpoint allows
30 requests/minute per IP, which puts a ~55 min floor under a full run (1,634 listings).

    python xross.py --limit 5     # fetch + parse a few listings and print a summary (no files written)
"""

from __future__ import annotations

import argparse
import gzip
import queue
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from selectolax.lexbor import LexborHTMLParser, LexborNode

from common import CACHE_DIR, SCRAPER_DIR, TOOL_UA, PoliteClient, log, read_json, setup_logging, write_json

XROSS_URL = "https://x-house.co.jp"
ID_OFFSET = 100_000  # Xross ids share the site's /{kind}/{id}/ routes with Oakhouse ids
DETAIL_CACHE = CACHE_DIR / "xross" / "detail"
ROOMS_CACHE = CACHE_DIR / "xross" / "rooms"
GEO_FILE = SCRAPER_DIR / "xross-geocode.json"
GSI_URL = "https://msearch.gsi.go.jp/address-search/AddressSearch"
ROOMS_INTERVAL_S = 2.1  # /ajax/load_more_rooms allows 30 requests/minute per IP; stay just under it

_PATH_RE = re.compile(r"<loc>https://x-house\.co\.jp/(sharehouse/([a-z-]+)/city/([a-z-]+)/xross-(\d+))/</loc>")
_TOKEN_RE = re.compile(r'<meta name="csrf-token" content="([^"]+)"')
_EMBED_RE = re.compile(r"maps\?output=embed&(?:amp;)?q=([^&\"]+)")
_DATE_RE = re.compile(r"(\d{4})[/-](\d{2})[/-](\d{2})")
_NUM_RE = re.compile(r"[\d,]+(?:\.\d+)?")
_AGE_RE = re.compile(r"(\d+)\s*to\s*(\d+)\s*years")
_AP_RE = re.compile(r"^AP\d+\s+")

# Oakhouse's area tree (prefecture → region → ward for Tokyo, prefecture → city elsewhere), so both
# operators share one area picker. Tokyo cities Oakhouse has no region for go to the Tama region.
TOKYO_REGIONS = {
    "ueno-asakusa-toyosu": ("Asakusa - Ueno - Toyosu", "edogawa koutou taito katsushika adachi sumida"),
    "ikebukuro-korakuen-akabane-nerima": ("Ikebukuro - Akabane - Nerima - Korakuen", "itabashi nerima toshima kita arakawa bunkyo"),
    "akihabara-aoyama-shinagawa-kamata": ("Kamata - Shinagawa - Akihabara - Aoyama", "ota minato shinagawa chuou chiyoda"),
    "shinjuku-takadanobaba-nakano-koenji": ("Shinjuku - Nakano - Koenji - Takadanobaba", "suginami shinjuku nakano"),
    "shibuya-meguro-setagaya": ("Shibuya - Meguro - Setagaya", "setagaya shibuya meguro"),
}
TAMA_REGION = ("kichijoji-tachikawa-koganei-machida", "Kichijoji - Tachikawa - Koganei - Machida")
# Xross slug → Oakhouse slug where they differ
SLUG_ALIASES = {"chuo": "chuou", "koto": "koutou", "higashikurume": "higasikurume", "niiza": "niza"}
# The English pages' machine translation of バス (bath) and リビング (living room)
CATEGORY_FIXES = {"Bus": "Bath", "Living": "Living room"}


def _text(node: LexborNode | None) -> str:
    return " ".join(node.text(separator=" ").split()) if node else ""


def _yen(text: str) -> int | None:
    m = _NUM_RE.search(text)
    return int(m.group().replace(",", "")) if m else None


def _br_split(node: LexborNode) -> list[str]:
    """Text of each <br>-separated line of a node (inline tags like <span> stay on their line)."""
    inner = re.sub(r"^<[^>]+>|</[^>]+>$", "", node.html or "")
    parts = re.split(r"<br\s*/?>", inner)
    return [" ".join(LexborHTMLParser(f"<p>{p}</p>").css_first("p").text().split()) for p in parts]


def _lines(node: LexborNode | None) -> list[str]:
    """Cell contents split into items: <li>s if there are any, else <br>-separated lines. Each <li>
    holds "Label<br>longer note", so only its first line is kept."""
    if node is None:
        return []
    lis = node.css("li")
    lines = [next((t for t in _br_split(li) if t), "") for li in lis] if lis else _br_split(node)
    return [t for t in lines if t]


def _facility_items(node: LexborNode | None) -> list[dict[str, Any]]:
    """Facility names from a cell, minus the explanatory sentences that sometimes follow them."""
    return [{"label": t, "icon": None} for t in _lines(node) if len(t) <= 60 and not re.search(r"\.( |$)", t)]


# ---------------------------------------------------------------------------------------- fetching

def listing_paths(client: PoliteClient) -> dict[int, str]:
    """{xross id: "sharehouse/{pref}/city/{city}/xross-{id}"} from the sitemap."""
    xml = client.get(f"{XROSS_URL}/sitemap.xml").text
    found = {int(xid): path for path, _, _, xid in _PATH_RE.findall(xml)}
    if not found:
        raise RuntimeError("x-house.co.jp sitemap contained no property URLs; layout changed?")
    return found


def _detail_path(xid: int) -> Path:
    return DETAIL_CACHE / f"{xid}.html.gz"


def _rooms_path(xid: int) -> Path:
    return ROOMS_CACHE / f"{xid}.html.gz"


def _read_gz(path: Path) -> str:
    return gzip.decompress(path.read_bytes()).decode("utf-8")


def _write_gz(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(text.encode("utf-8")))


class Fetcher:
    def __init__(self, delay: float, detail_max_age_d: float):
        self.client = PoliteClient(delay=delay)
        self.detail_max_age_d = detail_max_age_d
        self.token: str | None = None

    def _token(self, html: str | None = None) -> str:
        """Laravel CSRF token for the room-list POST; any page's <meta> works with the session cookie."""
        if html and (m := _TOKEN_RE.search(html)):
            self.token = m.group(1)
        if self.token is None:
            m = _TOKEN_RE.search(self.client.get(f"{XROSS_URL}/en/").text)
            if not m:
                raise RuntimeError("x-house.co.jp: no csrf-token meta tag")
            self.token = m.group(1)
        return self.token

    def detail(self, xid: int, path: str) -> str | None:
        cached = _detail_path(xid)
        # Stagger refreshes over max_age..2*max_age days so they don't all expire together
        max_age_s = self.detail_max_age_d * 86400 * (1 + (xid % 7) / 7)
        if cached.exists() and time.time() - cached.stat().st_mtime < max_age_s:
            return _read_gz(cached)
        resp = self.client.get(f"{XROSS_URL}/en/{path}/")
        if resp.status_code == 404:
            log.info("xross %d is gone (404)", xid)
            return None
        resp.raise_for_status()
        if 'class="c-ttl03"' not in resp.text:
            log.warning("xross %d: page has no property title; skipping", xid)
            return None
        self._token(resp.text)
        _write_gz(cached, resp.text)
        return resp.text

    def rooms(self, xid: int) -> str:
        data = {"language_for_db_code": "en", "language": "en", "property_id": str(xid),
                "vac_check": "all", "kamata_load": "all"}
        for attempt in range(2):
            resp = self.client.post(f"{XROSS_URL}/ajax/load_more_rooms", data=data, headers={
                "X-Requested-With": "XMLHttpRequest", "X-CSRF-TOKEN": self._token()})
            if resp.status_code == 419 and attempt == 0:  # session/token expired
                self.token = None
                continue
            resp.raise_for_status()
            html = resp.json().get("html") or ""
            _write_gz(_rooms_path(xid), html)
            return html
        raise RuntimeError(f"xross {xid}: room list kept failing")

    def close(self) -> None:
        self.client.close()


def fetch_all(delay: float = 2.0, detail_max_age_d: float = 7, limit: int | None = None,
              offline: bool = False, workers: int = 3) -> list[tuple[int, str, str, str]]:
    """(xross id, path, detail html, rooms html) for every live listing."""
    if offline:
        out = []
        for f in sorted(DETAIL_CACHE.glob("*.html.gz")):
            xid = int(f.name.split(".")[0])
            html = _read_gz(f)
            m = re.search(r'/en/(sharehouse/[a-z-]+/city/[a-z-]+/xross-\d+)/', html)
            if m and _rooms_path(xid).exists():
                out.append((xid, m.group(1), html, _read_gz(_rooms_path(xid))))
        return out[:limit] if limit else out

    # Room lists are throttled to 30 requests/minute per IP (x-ratelimit-limit: 30), so one thread
    # fetches them at just under that rate; more workers can't make that part faster. Detail pages
    # aren't throttled, so a small pool fetches them ahead of it and hands them over through a queue.
    rooms_f = Fetcher(ROOMS_INTERVAL_S, detail_max_age_d)
    paths = listing_paths(rooms_f.client)
    log.info("xross sitemap: %d listings", len(paths))
    ids = sorted(paths)[:limit] if limit else sorted(paths)
    ready: queue.Queue[tuple[int, str] | None] = queue.Queue()
    local = threading.local()
    fetchers: list[Fetcher] = [rooms_f]
    lock = threading.Lock()

    def get_detail(xid: int) -> None:
        f = getattr(local, "f", None)
        if f is None:
            f = local.f = Fetcher(delay, detail_max_age_d)
            with lock:
                fetchers.append(f)
        try:
            detail = f.detail(xid, paths[xid])
            if detail is not None:
                ready.put((xid, detail))
        except Exception as exc:  # one bad listing shouldn't sink the crawl
            log.error("xross %d detail failed: %s", xid, exc)

    def details_then_stop() -> None:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            list(pool.map(get_detail, ids))
        ready.put(None)

    out, done, start = [], 0, time.monotonic()
    producer = threading.Thread(target=details_then_stop, daemon=True)
    producer.start()
    try:
        while (item := ready.get()) is not None:
            xid, detail = item
            try:
                out.append((xid, paths[xid], detail, rooms_f.rooms(xid)))
            except Exception as exc:
                log.error("xross %d rooms failed: %s", xid, exc)
            done += 1
            if done % 100 == 0:
                elapsed = time.monotonic() - start
                log.info("xross fetched %d/%d (%.0fs elapsed, ~%.0fs left)", done, len(ids), elapsed,
                         (len(ids) - done) * ROOMS_INTERVAL_S)
        producer.join()
        log.info("xross fetched %d listings in %.0fs", len(out), time.monotonic() - start)
        return sorted(out)
    finally:
        for f in fetchers:
            f.close()


class AddressGeocoder:
    """Japanese street address → (lat, lng) via GSI, cached in the committed scraper/xross-geocode.json."""

    def __init__(self) -> None:
        self.cache: dict[str, list[float] | None] = read_json(GEO_FILE, {})
        self.client: PoliteClient | None = None
        self.new = 0

    def lookup(self, address: str) -> tuple[float, float] | None:
        if address not in self.cache:
            self.client = self.client or PoliteClient(delay=0.5, user_agent=TOOL_UA)
            try:
                resp = self.client.get(GSI_URL, params={"q": address})
                resp.raise_for_status()
                hits = resp.json()
            except Exception as exc:
                log.warning("GSI geocode %s failed: %s", address, exc)
                return None
            lng, lat = hits[0]["geometry"]["coordinates"] if hits else (None, None)
            self.cache[address] = [round(lat, 6), round(lng, 6)] if lat is not None else None
            self.new += 1
        hit = self.cache[address]
        return (hit[0], hit[1]) if hit else None

    def save(self) -> None:
        if self.new:
            write_json(GEO_FILE, self.cache, pretty=True, sort_keys=True)
            log.info("GSI geocoded %d new addresses", self.new)


# ----------------------------------------------------------------------------------------- parsing

def _tables(tree: LexborHTMLParser) -> dict[str, LexborNode]:
    """First <td> for each <th> label in the page's .c-table01 tables (rows can hold two th/td pairs)."""
    out: dict[str, LexborNode] = {}
    for th in tree.css("div.c-table01 > table th"):
        table = th.parent
        while table is not None and table.tag != "table":
            table = table.parent
        if table is not None and "inner-table" in (table.attributes.get("class") or ""):
            continue  # per-room-type fee breakdowns; read by _fees_by_type
        td = th.next
        while td is not None and td.tag != "td":
            td = td.next
        label = _text(th)
        if td is not None and label and label not in out:
            out[label] = td
    return out


def _section(tree: LexborHTMLParser, heading: str) -> LexborNode | None:
    for h2 in tree.css("h2"):
        if _text(h2).startswith(heading):
            return h2.parent
    return None


def _fees_by_type(tree: LexborHTMLParser) -> dict[str, dict[str, int | None]]:
    """{"Semi-private": {"Rent": 40000, "Common Fee": 15000, "Support Service Fee": 1500, ...}}."""
    out = {}
    for tr in tree.css(".overview-table-wrap tr.has-inner"):
        name = re.sub(r"\s*\(Monthly cost\)\s*$", "", _text(tr.css_first("th")))
        out[name] = {_text(row.css_first("th")): _yen(_text(row.css_first("td")))
                     for row in tr.css("table.inner-table tr") if row.css_first("th")}
    return out


def _stations(td: LexborNode | None) -> list[dict[str, Any]]:
    """"JR Yamanote Line Ueno Station 5 minutes walk" lines → Oakhouse-style station records."""
    out = []
    for line in _lines(td):
        walk = re.search(r"(\d+)\s*minutes? walk", line)
        name = re.sub(r"\s*\d+\s*minutes? walk.*$", "", line).strip()
        m = re.match(r"^(?P<line>.*?(?:Line|line|Liner|Monorail|Railway|線)(?:\s*\([^)]*\))?)\s+(?P<st>.+?)$", name)
        line_name, station = (m.group("line"), m.group("st")) if m else (None, name)
        out.append({"line": line_name, "line_slug": None, "station": re.sub(r"\s*Station$", "", station),
                    "station_slug": None, "walk_min": int(walk.group(1)) if walk else None})
    return out


def _area(pref: str, city: str) -> tuple[list[str], str, str, str]:
    """area_path and (prefecture, region, ward) names in Oakhouse's tree shape."""
    city = SLUG_ALIASES.get(city, city)
    city = re.sub(r"^(kyoto|kobe)-.+$", r"\1", city)
    city = re.sub(r"-shi$", "", city)
    city_name = city.replace("-", " ").title()
    pref_name = pref.title()
    if pref != "tokyo":
        return [pref, city], pref_name, city_name, city_name
    region = next((slug for slug, (_, wards) in TOKYO_REGIONS.items() if city in wards.split()), None)
    region_name = TOKYO_REGIONS[region][0] if region else TAMA_REGION[1]
    return ["tokyo", region or TAMA_REGION[0], city], "Tokyo", region_name, city_name


def _room_status(info: str) -> tuple[str, str | None]:
    m = _DATE_RE.search(info)
    if m:
        when = "-".join(m.groups())
        return ("available" if when <= date.today().isoformat() else "soon"), when
    if "No vacancy" in info or not info:
        return "full", None
    return "available", None


def _parse_rooms(html: str, fees: dict[str, dict[str, int | None]], kind: str, women_only: bool,
                 registration_fee: int | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    tree = LexborHTMLParser(html)
    type_names = [_text(li) for li in tree.css(".tab-btn-list li")]
    room_types, rooms = [], []
    for i, cont in enumerate(tree.css(".tab-wrap > .tab-cont")):
        type_name = type_names[i] if i < len(type_names) else ""
        key = f"x{i}"
        room_types.append({"key": key, "name": type_name, "facilities": {}})
        fee = fees.get(type_name, {})
        extra = [v for k, v in fee.items() if k in ("Common Fee", "Support Service Fee") and v]
        for card in cont.css("article.c-vacancy-card"):
            number = _text(card.css_first(".room-number .num"))
            status, available_from = _room_status(_text(card.css_first(".room-number .info")))
            rent = _yen(_text(card.css_first(".rent .num")))
            features = [{"label": _text(li.css_first(".txt")), "icon": (li.css_first(".c-ico").attributes.get("class") or "").split()[-1]}
                        for li in card.css("ul.c-ico-list li") if li.css_first(".c-ico")]
            facilities: dict[str, list[dict[str, Any]]] = {}
            size = None
            for tr in card.css(".c-table01 tr"):
                label, td = _text(tr.css_first("th")), tr.css_first("td")
                if label == "Floor area":
                    m = _NUM_RE.search(_text(td))
                    size = float(m.group().replace(",", "")) if m else None
                elif label:
                    facilities[CATEGORY_FIXES.get(label, label)] = _facility_items(td)
            photos = [img.attributes["src"] for img in card.css(".c-vacancy-sub-img img") if img.attributes.get("src")]
            maint = sum(extra) if extra else None
            # Apartment units are numbered by floor ("302" → 3F); share-house rooms aren't ("03")
            floor = f"{number[:-2]}F" if kind == "apartment" and re.fullmatch(r"\d{3,4}", number) else None
            rooms.append({
                "id": f"{key}-{number}",
                "number": number,
                "status": status,
                "available_from": available_from,
                "rent": rent,
                "maintenance": maint,
                "monthly_total": rent + (maint or 0) if rent is not None else None,
                "contract_fee": registration_fee,
                "size_m2": size,
                "room_type": type_name,
                "floor_plan": None,
                "floor_label": floor,
                "max_occupancy": 2 if "2 people move in" in facilities else None,
                "requirements": [],
                "features": features,
                "facilities": facilities,
                "photos": photos,
                "attrs": {"gender": "W"} if women_only else {},
                "type_key": key,
                "specific_facilities": [],
            })
    return room_types, rooms


def parse_xross(detail_html: str, rooms_html: str, xid: int, path: str) -> dict[str, Any]:
    tree = LexborHTMLParser(detail_html)
    t = _tables(tree)
    val = lambda label: _text(t.get(label))  # noqa: E731

    prop_type = val("Property Type")  # "Share house (Some are for women only)", "Furnished apartment"
    kind = "apartment" if "apartment" in prop_type.lower() else "house"
    women_only = bool(re.search(r"women only|female only", prop_type, re.I)) and not re.search(r"\bSome\b", prop_type, re.I)

    promotions = [_text(p) for p in t["Promotions"].css(".campaign-title")] if "Promotions" in t else []
    terms = [{"label": p, "icon": None} for p in promotions]
    discounts = [p for p in promotions if not re.search(r"security deposit|agency fee|brokerage", p, re.I)]

    building = {k: val(k) for k in ("Building age", "Structure", "Capacity", "About smoking", "Age limit",
                                    "Contract Period", "Guarantee company", "Registration fee",
                                    "Cancellation fee (moving out fee)", "Contract renewal fee",
                                    "Available languages") if val(k)}
    # The free-text notes ("*Please note…") make these long; keep the first sentence
    for k in ("Contract Period", "Guarantee company"):
        if k in building:
            building[k] = building[k].split("*")[0].strip()

    shared: dict[str, list[dict[str, Any]]] = {}
    common = _section(tree, "Common facilities")
    if common is not None:
        for th in common.css("table th"):
            td = th.next
            while td is not None and td.tag != "td":
                td = td.next
            items = _facility_items(td)
            if items:
                shared[CATEGORY_FIXES.get(_text(th), _text(th))] = items

    comments = _section(tree, "Property comments")
    description = _text(comments)[len("Property comments"):].strip() if comments else ""

    photos, seen = [], set()
    for img in tree.css(".main-slider .swiper-slide img"):
        src = img.attributes.get("src")
        if src and src not in seen:
            seen.add(src)
            photos.append({"src": src, "caption": ""})

    address_td = t.get("Address")
    address_lines = _lines(address_td)
    postcode = address_lines[0].lstrip("〒") if address_lines and address_lines[0].startswith("〒") else None
    address = " ".join(a for a in address_lines if not a.startswith("〒"))
    embed = _EMBED_RE.search(detail_html)

    _, pref, _, city, _ = path.split("/")  # sharehouse/{pref}/city/{city}/xross-{id}
    area_path, prefecture, region, ward = _area(pref, city)

    fees = _fees_by_type(tree)
    room_types, rooms = _parse_rooms(rooms_html, fees, kind, women_only, _yen(val("Registration fee").split("→")[-1]))

    return {
        "id": ID_OFFSET + xid,
        "operator": "xross",
        "kind": kind,
        "name_missing": False,
        "category": "FURNISHED APARTMENT" if kind == "apartment" else "SHAREHOUSE",
        "name": _text(tree.css_first("h1.c-ttl03")),
        "url": f"{XROSS_URL}/en/{path}/",
        "updated": "",
        "description": description,
        "address": address,
        "postcode": postcode,
        "address_ja": unquote(embed.group(1)) if embed else None,
        "prefecture": prefecture,
        "region": region,
        "ward": ward,
        "area_path": area_path,
        "lat": None,
        "lng": None,
        "operator_stations": _stations(t.get("Transportation")),
        "terminal": None,
        "terms": terms,
        "discounts": discounts,
        "building": building,
        "shared_facilities": shared,
        "room_types": room_types,
        "rooms": rooms,
        "photos": photos,
    }


# ---------------------------------------------------------------------------------------- grouping

def _building_name(name: str, numbers: list[str]) -> str:
    """"AP22 Sunrise Nakadai 101 (Kami-Itabashi)" → "Sunrise Nakadai (Kami-Itabashi)"."""
    name = _AP_RE.sub("", name)
    for n in numbers:
        name = re.sub(rf"\s+{re.escape(n)}(?=\s|$)", "", name)
    return " ".join(name.split())


def group_apartments(props: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Xross lists each furnished-apartment unit as its own property; merge units of one building
    (same street address and building name) into a single property whose rooms are the units."""
    out, buildings = [], {}
    for p in sorted(props, key=lambda p: p["id"]):
        if p["kind"] != "apartment":
            out.append(p)
            continue
        name = _building_name(p["name"], [r["number"] for r in p["rooms"]])
        for r in p["rooms"]:
            r["id"] = f"{p['id']}-{r['number']}"
            r["url"] = p["url"]
        key = (p["address_ja"] or p["address"], name)
        if key not in buildings:
            buildings[key] = {**p, "name": name}
            out.append(buildings[key])
            continue
        b = buildings[key]
        b["rooms"] = b["rooms"] + p["rooms"]
        seen = {ph["src"] for ph in b["photos"]}
        b["photos"] = b["photos"] + [ph for ph in p["photos"] if ph["src"] not in seen]
        b["description"] = b["description"] or p["description"]
        b["discounts"] = sorted(set(b["discounts"]) | set(p["discounts"]))
    return out


def scrape(args: argparse.Namespace) -> list[dict[str, Any]]:
    """Fetch, parse, geocode and group; returns property records ready for build.py's enrichment."""
    listings = fetch_all(delay=args.xross_delay, detail_max_age_d=args.xross_max_age_d,
                         limit=args.limit, offline=args.offline, workers=args.xross_workers)
    props = []
    for xid, path, detail, rooms in listings:
        try:
            props.append(parse_xross(detail, rooms, xid, path))
        except Exception as exc:
            log.error("parse xross %d failed: %s", xid, exc)
    geo = AddressGeocoder()
    for p in props:
        hit = geo.lookup(p["address_ja"]) if p["address_ja"] else None
        if hit:
            p["lat"], p["lng"] = hit
    geo.save()
    grouped = group_apartments(props)
    log.info("xross: %d listings → %d properties, %d units", len(props), len(grouped),
             sum(len(p["rooms"]) for p in grouped))
    return grouped


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--xross-delay", type=float, default=2.0)
    ap.add_argument("--xross-max-age-d", type=float, default=7)
    ap.add_argument("--xross-workers", type=int, default=3)
    args = ap.parse_args()
    setup_logging()
    for p in scrape(args):
        rooms = p["rooms"]
        log.info("%d %s | %s | %s | %s | %s,%s | %d rooms (%s) | stations %s", p["id"], p["category"], p["name"],
                 "/".join(p["area_path"]), p["address"], p["lat"], p["lng"], len(rooms),
                 ", ".join(f"{r['number']}:{r['status']}:{r['monthly_total']}" for r in rooms[:4]),
                 [s["station"] for s in p["operator_stations"]][:3])


if __name__ == "__main__":
    main()
