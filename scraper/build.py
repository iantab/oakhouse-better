"""Scrape Oakhouse and Xross House and write the site's data files.

    python build.py                 # full run: fetch, then process
    python build.py fetch           # download only: both sites' pages into scraper/.cache + a manifest
    python build.py process         # parse the manifest's cached pages → geocode → stations → amenities → write
    python build.py --limit 20      # quick local run on the first 20 listings of each operator
    python build.py --offline       # same as `process` (re-parse after changing parse.py / xross.py)
    python build.py --no-xross      # Oakhouse only

CI runs the two stages as separate workflow steps ("Scrape listings", "Parse & enrich").

Outputs (all gitignored, regenerated every run):
    web/src/data/properties.json        full detail, read by Astro at build time
    web/src/data/areas.json, codes.json, amenities.json, meta.json
    web/public/data/search-index.json   compact rows the results page filters in the browser
    web/public/data/*.json              copies of the above for `npm run data:pull`

The process stage exits with status 2 (writing nothing) if either operator's listing count drops
more than --max-drop below the previous successful run, so a broken scrape never deploys.
"""

from __future__ import annotations

import argparse
import math
import re
import shutil
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

import amenities as am
import xross
from common import CACHE_DIR, WEB_DATA, WEB_PUBLIC_DATA, PoliteClient, log, read_json, setup_logging, write_json
from fetch import cache_path, fetch_all
from geocode import Geocoder
from parse import parse_listing
from sitemap import listing_ids

LAST_GOOD = CACHE_DIR / "last-good.json"
MANIFEST = CACHE_DIR / "manifest.json"  # what the fetch stage downloaded; the process stage reads only these
WALK_M_PER_MIN = 80      # Japanese real-estate standard (不動産の表示に関する公正競争規約)
DETOUR = 1.25            # straight line → street distance
MAX_WALK_MIN = 20
DIRECTION_FIXES = {"Notrh": "North"}


def _distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest_stations(prop: dict[str, Any], stations: list[dict[str, Any]], grid: dict) -> list[dict[str, Any]]:
    if prop["lat"] is None:
        return []
    cell = (int(prop["lat"] * 50), int(prop["lng"] * 50))  # ~2km cells
    out = []
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            for s in grid.get((cell[0] + dy, cell[1] + dx), []):
                d = _distance_m(prop["lat"], prop["lng"], s["lat"], s["lng"])
                walk = math.ceil(d * DETOUR / WALK_M_PER_MIN)
                if walk <= MAX_WALK_MIN:
                    out.append({"id": s["id"], "name": s["name"], "lines": s["lines"], "distance_m": round(d), "walk_min": walk})
    # The same station often has one node per operator (JR + Metro); merge by name.
    merged: dict[str, dict[str, Any]] = {}
    for s in sorted(out, key=lambda s: s["distance_m"]):
        if s["name"].lower() in merged:
            m = merged[s["name"].lower()]
            m["lines"] = sorted(set(m["lines"]) | set(s["lines"]))
            m["ids"].append(s["id"])
        else:
            merged[s["name"].lower()] = {**{k: v for k, v in s.items() if k != "id"}, "ids": [s["id"]]}
    return list(merged.values())[:8]


def _dedupe_stations(stations: list[dict[str, Any]]) -> list[tuple[str, int | None]]:
    """Oakhouse lists a station once per line; keep each station once, with its shortest walk."""
    best: dict[str, int | None] = {}
    for s in stations:
        name, walk = s["station"], s["walk_min"]
        if name not in best or (walk is not None and (best[name] is None or walk < best[name])):
            best[name] = walk
    return list(best.items())


def _station_grid(stations: list[dict[str, Any]]) -> dict:
    grid: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for s in stations:
        grid[(int(s["lat"] * 50), int(s["lng"] * 50))].append(s)
    return grid


def _areas(props: list[dict[str, Any]], unit_counts: dict[int, int]) -> list[dict[str, Any]]:
    """Prefecture → region → ward tree with listing/unit counts."""
    tree: dict[str, Any] = {}
    for p in props:
        names = [p["prefecture"], p["region"], p["ward"]]
        node = tree
        for depth, slug in enumerate(p["area_path"]):
            entry = node.setdefault(slug, {"slug": slug, "name": names[depth] or slug, "properties": 0, "units": 0, "children": {}})
            entry["properties"] += 1
            entry["units"] += unit_counts.get(p["id"], 0)
            node = entry["children"]

    def to_list(d: dict[str, Any]) -> list[dict[str, Any]]:
        return sorted(
            ({**v, "children": to_list(v["children"])} for v in d.values()),
            key=lambda x: -x["units"],
        )

    return to_list(tree)


def _counts(props: list[dict[str, Any]]) -> dict[str, int]:
    return {"properties": len(props), "units": sum(len(p["rooms"]) for p in props)}


def _sanity_check(operator: str, props: list[dict[str, Any]], max_drop: float) -> None:
    last = read_json(LAST_GOOD)
    if not last:
        return
    # Runs before Xross was added stored Oakhouse's counts at the top level
    before = last.get("operators", {}).get(operator) or (last if operator == "oakhouse" else None)
    if not before:
        return
    for label, now in _counts(props).items():
        if before[label] and now < before[label] * (1 - max_drop):
            log.error("sanity check failed: %s %s dropped %d → %d (more than %.0f%%); not writing data",
                      operator, label, before[label], now, max_drop * 100)
            sys.exit(2)


def room_class(room_type: str | None) -> str | None:
    """Share-house room class across both operators' wording: private / semi / dorm (None for apartments)."""
    t = (room_type or "").lower()
    if re.search(r"dormitory|share room", t):
        return "dorm"
    if re.search(r"semi|compartment", t):
        return "semi"
    if re.search(r"single room|private room", t):
        return "private"
    return None


def _dur(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}m{s:02d}s" if m else f"{s}s"


@contextmanager
def step(name: str) -> Iterator[None]:
    """Log a step's start and duration (the CI log's landmarks)."""
    log.info("== %s", name)
    start = time.monotonic()
    yield
    log.info("== %s: done in %s", name, _dur(time.monotonic() - start))


def fetch_stage(args: argparse.Namespace) -> None:
    """Network only: download both sites into the cache and record what was fetched in the manifest."""
    with step("fetch"):
        # Xross runs alongside Oakhouse (different hosts, so neither slows the other); its room lists are
        # capped at 30/min, so it takes about an hour, longer than Oakhouse's whole crawl.
        pool = ThreadPoolExecutor(max_workers=1)
        xross_job = None if args.no_xross else pool.submit(xross.fetch, args)

        client = PoliteClient(delay=args.delay)
        ids = listing_ids(client)
        client.close()
        log.info("oakhouse sitemap: %d listings", len(ids))
        if args.limit:
            ids = ids[: args.limit]
        oakhouse = [[kind, pid] for kind, pid, _ in fetch_all(ids, args.max_age_h, workers=args.workers, delay=args.delay)]
        log.info("oakhouse: fetched %d listings", len(oakhouse))

        xross_entries = xross_job.result() if xross_job is not None else None  # re-raises if Xross failed
        pool.shutdown()
        write_json(MANIFEST, {
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "limited": bool(args.limit),
            "oakhouse": oakhouse,
            "xross": [list(e) for e in xross_entries] if xross_entries is not None else None,
        }, pretty=True)
        log.info("manifest: oakhouse %d, xross %s", len(oakhouse),
                 len(xross_entries) if xross_entries is not None else "skipped")


def _summary(operator: str, props: list[dict[str, Any]]) -> None:
    """Counts of missing fields; a big jump here means a site changed its markup."""
    rooms = [r for p in props for r in p["rooms"]]
    log.info("%s: %d properties, %d units | no coords %d, no stations %d, no structure %d | units without price %d, "
             "without status date (soon) %d", operator, len(props), len(rooms),
             sum(p["lat"] is None for p in props), sum(not p["stations"] for p in props),
             sum(p["structure"] is None for p in props),
             sum(r["monthly_total"] is None and r["rent"] is None for r in rooms),
             sum(r["status"] == "soon" and not r["available_from"] for r in rooms))


def process_stage(args: argparse.Namespace) -> None:
    """No page downloads: parse what the fetch stage cached, enrich, check and write the site's data."""
    manifest = read_json(MANIFEST)
    if not manifest:
        log.error("%s not found; run `python build.py fetch` first", MANIFEST)
        sys.exit(1)
    limited = bool(args.limit or manifest.get("limited"))
    with step(f"process (listings fetched {manifest['fetched_at']})"):
        # 1. Parse
        with step("parse oakhouse"):
            props: list[dict[str, Any]] = []
            codes: dict[str, dict[str, str]] = defaultdict(dict)
            entries = manifest["oakhouse"][: args.limit] if args.limit else manifest["oakhouse"]
            for kind, pid in entries:
                path = cache_path(kind, pid)
                if not path.exists():
                    log.warning("%s/%d: not in the cache; skipping", kind, pid)
                    continue
                try:
                    prop = parse_listing(path.read_text(encoding="utf-8"), kind, pid)
                except Exception as exc:
                    log.error("parse %s/%d failed: %s", kind, pid, exc)
                    continue
                for name, values in prop.pop("_codes").items():
                    for value, label in values.items():
                        codes[name][value] = DIRECTION_FIXES.get(label, label)
                if prop["name_missing"] and not prop["rooms"]:
                    log.warning("%s/%d: no name and no rooms, skipping", kind, pid)
                    continue
                props.append(prop)
            log.info("oakhouse: parsed %d properties, %d units", len(props), sum(len(p["rooms"]) for p in props))

        xprops: list[dict[str, Any]] = []
        if manifest.get("xross") is not None and not args.no_xross:
            with step("parse xross + GSI geocode + group apartments"):
                xentries = [(xid, path) for xid, path in manifest["xross"]]
                xprops = xross.process(xentries[: args.limit] if args.limit else xentries)

        if not limited:
            _sanity_check("oakhouse", props, args.max_drop)
            if manifest.get("xross") is not None:
                _sanity_check("xross", xprops, args.max_drop)
        props += xprops
        n_units = sum(len(p["rooms"]) for p in props)
        write_json(CACHE_DIR / "coords.json", [[p["lat"], p["lng"]] for p in props if p["lat"] is not None])

        # 2. Enrich: address (Nominatim) + nearest stations (OSM, from committed stations.json)
        with step("geocode oakhouse (Nominatim) + nearest stations"):
            geocoder = Geocoder(max_new=args.max_geocode)
            stations = read_json(WEB_DATA / "stations.json", [])
            if not stations:
                log.warning("web/src/data/stations.json missing; run stations.py. Falling back to the operators' station lists")
            grid = _station_grid(stations)
            for p in props:
                # Xross pages already give an English street address and postcode; only Oakhouse needs Nominatim
                p["geo"] = geocoder.lookup(p["lat"], p["lng"]) if p["lat"] is not None and p["operator"] == "oakhouse" else None
                if p["geo"] and not p["ward"]:
                    p["ward"] = p["geo"].get("ward")
                p["stations"] = nearest_stations(p, stations, grid) if stations else []
            geocoder.save()

        # 3. Amenities + compact search index
        with step("amenities + search index"):
            amenity_keys = [a["key"] for a in am.catalog()]
            amenity_index = {k: i for i, k in enumerate(amenity_keys)}
            index_props, index_units = [], []
            unit_counts: dict[int, int] = {}
            for p in props:
                is_xross = p["operator"] == "xross"
                building, contract = am.xross_property_amenities(p) if is_xross else am.property_amenities(p)
                unit_counts[p["id"]] = len(p["rooms"])
                p["amenities"] = sorted(building | contract)
                p["structure"] = am.structure(p)
                if not is_xross and p["kind"] == "apartment" and am.is_oakhouse_lease(p):
                    p["category"] = "OAKHOUSE APARTMENT"  # month-to-month, flat contract fee
                cover = next((ph["src"] for ph in p["photos"]), None) or next(
                    (r["photos"][0] for r in p["rooms"] if r["photos"]), None)
                index_props.append({
                    "id": p["id"], "o": p["operator"][0], "k": p["kind"][0], "c": p["category"], "n": p["name"],
                    "a": p["area_path"], "w": p["ward"], "lat": p["lat"], "lng": p["lng"],
                    "img": cover, "b": p["structure"],
                    # nearest stations: [station name, walk minutes, [line ids], [osm node ids]]
                    "st": [[s["name"], s["walk_min"], s["lines"], s["ids"]] for s in p["stations"]]
                          or [[name, walk, [], []] for name, walk in _dedupe_stations(p["operator_stations"])],
                })
                for r in p["rooms"]:
                    keys = (am.xross_unit_amenities(r, building, contract) if is_xross
                            else am.unit_amenities(p, r, building, contract))
                    r["amenities"] = sorted(keys)
                    r["floor"] = am.floor_number(r)
                    index_units.append({
                        "p": p["id"], "u": r["id"], "n": r["number"], "s": r["status"][0],
                        "d": r["available_from"], "r": r["rent"], "t": r["monthly_total"],
                        "sz": r["size_m2"], "f": r["floor"], "rt": r["room_type"], "rc": room_class(r["room_type"]),
                        "am": sorted(amenity_index[k] for k in keys),
                    })
            for operator in ("oakhouse", "xross"):
                _summary(operator, [p for p in props if p["operator"] == operator])
                misses = [(key, n) for key, n in am.unmatched.most_common()
                          if key[0].startswith("xross/") == (operator == "xross")]
                if misses:
                    log.info("%s: %d facility labels not mapped to an amenity (top 15):", operator, len(misses))
                    for (group, label, icon), n in misses[:15]:
                        log.info("  %5d  %-14s %-44s icon=%s", n, group, label, icon)

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        meta = {"updated": now, "properties": len(props), "units": n_units,
                "available": sum(1 for u in index_units if u["s"] != "f"),
                "operators": {op: _counts([p for p in props if p["operator"] == op]) for op in ("oakhouse", "xross")}}

        # 4. Write. Raw data-* attributes are only needed to derive amenities; keep the few the UI shows.
        with step("write data files"):
            for p in props:
                for r in p["rooms"]:
                    a = r.pop("attrs")
                    r["gender"] = {"W": "women", "M": "men"}.get(a.get("gender", ""), "any")
            write_json(WEB_DATA / "properties.json", props)
            write_json(WEB_DATA / "areas.json", _areas(props, unit_counts))
            write_json(WEB_DATA / "codes.json", codes, pretty=True)
            write_json(WEB_DATA / "amenities.json", {"groups": am.GROUPS, "items": am.catalog()}, pretty=True)
            write_json(WEB_DATA / "meta.json", meta, pretty=True)
            write_json(WEB_PUBLIC_DATA / "search-index.json",
                       {"updated": now, "amenities": amenity_keys, "properties": index_props, "units": index_units})
            # Publish the generated files too, so `npm run data:pull` can fetch them from the live site
            for name in ("properties.json", "areas.json", "codes.json", "amenities.json", "meta.json"):
                shutil.copyfile(WEB_DATA / name, WEB_PUBLIC_DATA / name)
            if not limited:
                write_json(LAST_GOOD, meta, pretty=True)
        log.info("done: %s", meta)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", nargs="?", choices=["fetch", "process", "all"], default="all",
                    help="fetch = download only; process = parse + enrich + write from the cache; all = both")
    ap.add_argument("--limit", type=int, help="only the first N listings of each operator (local testing)")
    ap.add_argument("--offline", action="store_true", help="same as the `process` stage")
    ap.add_argument("--max-age-h", type=float, default=20, help="re-fetch cached Oakhouse pages older than this")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between Oakhouse page requests, per worker")
    ap.add_argument("--workers", type=int, default=3, help="parallel Oakhouse page fetchers (1 = sequential)")
    ap.add_argument("--max-geocode", type=int, help="cap new Nominatim lookups this run")
    ap.add_argument("--max-drop", type=float, default=0.10, help="sanity gate: allowed fractional drop")
    ap.add_argument("--no-xross", action="store_true", help="skip Xross House")
    ap.add_argument("--xross-delay", type=float, default=2.0, help="seconds between x-house.co.jp detail requests, per worker")
    ap.add_argument("--xross-workers", type=int, default=1, help="Xross detail-page fetchers; keep the total near 1 req/s or the site blocks us (room lists are capped at 30/min regardless)")
    ap.add_argument("--xross-max-age-d", type=float, default=7, help="refetch Xross detail pages after this many days")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    setup_logging(args.verbose)
    stage = "process" if args.offline else args.stage
    if stage in ("fetch", "all"):
        fetch_stage(args)
    if stage in ("process", "all"):
        process_stage(args)


if __name__ == "__main__":
    main()
