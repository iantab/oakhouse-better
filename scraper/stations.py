"""Build web/src/data/stations.json + lines.json from OpenStreetMap (Overpass API).

Run by hand a few times a year, after a crawl (it reads property coordinates from
scraper/.cache/coords.json, written by build.py), then commit the two JSON files:

    python stations.py
"""

from __future__ import annotations

import argparse
import hashlib
import math
import time
import re
from collections import defaultdict
from typing import Any

from common import CACHE_DIR, TOOL_UA, WEB_DATA, PoliteClient, log, read_json, setup_logging, write_json

# The main public instance is often overloaded (502/504); fall back to community mirrors.
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
ROUTE_TYPES = "train|subway|light_rail|monorail|tram"
RAW_DIR = CACHE_DIR / "overpass"  # raw responses, so re-processing doesn't re-query
CELL_DEG = 0.25     # group properties into ~25km cells, one query per occupied cell
MARGIN_DEG = 0.025  # ~2.5km around the outermost property in a cell


def _query(bbox: tuple[float, float, float, float]) -> str:
    s, w, n, e = bbox
    b = f"{s:.4f},{w:.4f},{n:.4f},{e:.4f}"
    return f"""
[out:json][timeout:180];
node["railway"~"^(station|halt)$"]["name"]({b})->.s;
.s out body;
rel(bn.s)["public_transport"="stop_area"]->.a;
.a out body;
node(r.a)["public_transport"="stop_position"]->.p;
(rel(bn.p)["type"="route"]["route"~"^({ROUTE_TYPES})$"]; rel(bn.s)["type"="route"]["route"~"^({ROUTE_TYPES})$"];)->.r;
.r out body;
rel(br.r)["type"="route_master"];
out body;
"""


def _overpass(client: PoliteClient, query: str) -> dict[str, Any]:
    for attempt in range(3):
        for url in OVERPASS_URLS:
            try:
                resp = client.get(url, params={"data": query}, retries=2)
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:
                log.warning("overpass %s failed: %s", url, exc)
        log.info("all Overpass endpoints failed; waiting 60s (round %d/3)", attempt + 1)
        time.sleep(60)
    raise RuntimeError("Overpass unavailable; try again later (finished queries are cached)")


def _cells(coords: list[tuple[float, float]]) -> list[tuple[float, float, float, float]]:
    groups: dict[tuple[int, int], list[tuple[float, float]]] = defaultdict(list)
    for lat, lng in coords:
        groups[(math.floor(lat / CELL_DEG), math.floor(lng / CELL_DEG))].append((lat, lng))
    boxes = []
    for pts in groups.values():
        lats, lngs = [p[0] for p in pts], [p[1] for p in pts]
        boxes.append((min(lats) - MARGIN_DEG, min(lngs) - MARGIN_DEG, max(lats) + MARGIN_DEG, max(lngs) + MARGIN_DEG))
    return boxes


_ARROW_RE = re.compile(r"→|=>|->|⇒|⇔|↔")


_LOCAL_RE = re.compile(r"\s*[(（]?\b(Local|Local Train|Each Stop)\b[)）]?\s*$", re.I)
# OSM also maps train *services* (express patterns, named trains); the picker wants physical lines.
_SERVICE_RE = re.compile(
    r"Express|Liner|\bRapid\b(?! Line)|Rapi:t|Direct|inbound|outbound|\(old\)|Shinkansen|Sleeper|Commuter",
    re.I,
)


def _line_name(tags: dict[str, str]) -> str:
    """Route relations exist per direction ("JR Chuo-Sobu Line (Chiba → Mitaka)",
    "Tozai Line Commuter Rapid: Nishi-Funabashi => Nakano"); cut at the first "(" or ":"
    whose remainder contains an arrow, then drop a trailing "Local"."""
    name = tags.get("name:en") or tags.get("name") or tags.get("ref") or "?"
    for i, ch in enumerate(name):
        if ch in "(（:：" and _ARROW_RE.search(name[i:]):
            name = name[:i].strip() or name
            break
    return _LOCAL_RE.sub("", name).strip() or name


def _is_service(tags: dict[str, str], name: str) -> bool:
    """True for express/named-train services rather than a line."""
    if tags.get("service") in ("high_speed", "long_distance", "night"):
        return True
    if _SERVICE_RE.search(name):
        return True
    # A heavy-rail route named without "Line"/線 is almost always a named train ("Kamome", "Hitachi")
    return tags.get("route") == "train" and not re.search(r"Line|線|Railway|Monorail", name, re.I)


def _line_key(tags: dict[str, str], name: str) -> str:
    op = tags.get("operator:en") or tags.get("operator") or tags.get("network") or ""
    return f"{op}|{name}".lower()


def _slugify(text: str) -> str:
    text = text.lower().replace("ō", "o").replace("ū", "u").replace("ā", "a").replace("ī", "i").replace("ē", "e")
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def build(
    client: PoliteClient, coords: list[tuple[float, float]], refresh: bool = False
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    nodes: dict[int, dict[str, Any]] = {}
    areas: dict[int, dict[str, Any]] = {}
    routes: dict[int, dict[str, Any]] = {}
    masters: dict[int, dict[str, Any]] = {}
    boxes = _cells(coords)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for i, box in enumerate(boxes, 1):
        query = _query(box)
        raw = RAW_DIR / (hashlib.sha1(query.encode()).hexdigest()[:16] + ".json")
        if raw.exists() and not refresh:
            data = read_json(raw)
        else:
            log.info("overpass query %d/%d bbox=%s", i, len(boxes), tuple(round(x, 3) for x in box))
            data = _overpass(client, query)
            write_json(raw, data)
        for el in data["elements"]:
            if el["type"] == "node":
                nodes[el["id"]] = el
            elif el.get("tags", {}).get("public_transport") == "stop_area":
                areas[el["id"]] = el
            elif el.get("tags", {}).get("type") == "route_master":
                masters[el["id"]] = el
            else:
                routes[el["id"]] = el

    # A route_master groups both directions of one line under a clean name; let it
    # override each member route's naming tags (refs/operators usually match).
    for m in masters.values():
        mtags = m.get("tags", {})
        for mem in m.get("members", []):
            r = routes.get(mem["ref"]) if mem["type"] == "relation" else None
            if r is not None:
                r["tags"] = {**r.get("tags", {}), **{k: v for k, v in mtags.items() if k != "type"}, "_master": str(m["id"])}

    # Which routes serve which node (stop_position or station node itself)
    routes_by_member: dict[int, set[int]] = defaultdict(set)
    for rid, r in routes.items():
        for m in r.get("members", []):
            if m["type"] == "node":
                routes_by_member[m["ref"]].add(rid)
    areas_by_station: dict[int, list[int]] = defaultdict(list)
    for aid, a in areas.items():
        for m in a.get("members", []):
            if m["type"] == "node":
                areas_by_station[m["ref"]].append(aid)

    lines: dict[str, dict[str, Any]] = {}
    used_ids: set[str] = set()
    stations = []
    for nid, n in nodes.items():
        tags = n.get("tags", {})
        if tags.get("railway") not in ("station", "halt") or tags.get("disused") == "yes":
            continue
        route_ids = set(routes_by_member.get(nid, set()))
        for aid in areas_by_station.get(nid, []):
            for m in areas[aid].get("members", []):
                if m["type"] == "node":
                    route_ids |= routes_by_member.get(m["ref"], set())
        line_ids = set()
        for rid in route_ids:
            rtags = routes[rid].get("tags", {})
            name = _line_name(rtags)
            if _is_service(rtags, name):
                continue
            key = _line_key(rtags, name)
            if key not in lines:
                # Ids must be unique even if two operators share a line name ("Main Line")
                line_id = _slugify(name)[:60] or str(rid)
                if line_id in used_ids:
                    line_id = _slugify(f"{name} {rtags.get('operator:en') or rtags.get('operator') or rid}")[:80]
                while line_id in used_ids:
                    line_id += "-x"
                used_ids.add(line_id)
                lines[key] = {
                    "id": line_id,
                    "name": name,
                    "name_ja": _line_name({"name": rtags.get("name", "")}) if rtags.get("name") else None,
                    "ref": rtags.get("ref"),
                    "operator": rtags.get("operator:en") or rtags.get("operator"),
                    "colour": rtags.get("colour"),
                    "type": rtags.get("route"),
                }
            line_ids.add(lines[key]["id"])
        name_en = re.sub(r"\s+[Ss]tation$", "", tags.get("name:en") or tags.get("name:ja-Latn") or tags.get("name") or "")
        stations.append({
            "id": nid,
            "name": name_en,
            "name_ja": tags.get("name"),
            "slug": _slugify(name_en or str(nid)),
            "lat": round(n["lat"], 6),
            "lng": round(n["lon"], 6),
            "operator": tags.get("operator:en") or tags.get("operator"),
            "lines": sorted(line_ids),
        })

    stations.sort(key=lambda s: (s["name"] or "", s["id"]))
    return stations, sorted(lines.values(), key=lambda l: l["name"])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--coords", default=str(CACHE_DIR / "coords.json"), help="JSON list of [lat, lng]")
    ap.add_argument("--refresh", action="store_true", help="ignore cached Overpass responses")
    args = ap.parse_args()
    setup_logging()
    coords = [tuple(c) for c in read_json(__import__("pathlib").Path(args.coords), [])]
    if not coords:
        raise SystemExit(f"no coordinates in {args.coords}; run build.py first")
    client = PoliteClient(delay=5.0, user_agent=TOOL_UA, timeout=240)
    stations, lines = build(client, coords, refresh=args.refresh)
    write_json(WEB_DATA / "stations.json", stations)
    write_json(WEB_DATA / "lines.json", lines, pretty=True)
    no_line = sum(1 for s in stations if not s["lines"])
    log.info("wrote %d stations (%d without a line) and %d lines", len(stations), no_line, len(lines))


if __name__ == "__main__":
    main()
