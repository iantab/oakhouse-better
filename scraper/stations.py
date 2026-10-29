"""Build web/src/data/stations.json, lines.json and rail-graph.json from OpenStreetMap (Overpass API).

Run by hand a few times a year, after a crawl (it reads property coordinates from
scraper/.cache/coords.json, written by build.py), then commit the three JSON files:

    python stations.py

rail-graph.json is the network the commute search runs on in the browser: every route's
stops in order with estimated cumulative minutes, plus walking links between nearby
stations. OSM has no timetables, so ride times come from stop distance and a per-mode speed.
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

# Rail graph. Each hop between stops costs STOP_MIN (dwell + braking/acceleration) plus the
# straight-line distance at a cruising speed, so lines with fewer stops come out faster.
# Calibrated against real journey times (see the calibration notes in README).
GRAPH_CHUNK = 100   # route relations per stop-fetching query
CRUISE_KMH = {"subway": 60.0, "train": 60.0, "monorail": 50.0, "light_rail": 30.0, "tram": 20.0}
EXPRESS_KMH = 70.0  # express/rapid services that skip stops
STOP_MIN = {"subway": 1.0, "train": 1.3}  # per hop; other modes use the subway value
TRANSFER_MIN = 4.0  # changing trains: platform walk + average wait
WALK_M_PER_MIN = 64.0       # 80 m/min with +25% for streets, as in build.py
SAME_NAME_RADIUS_M = 500    # same-named stations this close are one interchange (JR/Metro Shinjuku)
NEAR_RADIUS_M = 250         # any stations this close are linked (Hibiya–Yurakucho)
# Services that need a supplementary ticket or aren't commuter trains; never routed on.
_COMMUTER_NAME_RE = re.compile(r"Line|線|Railway|Monorail|Rapid|Express|Local|快速|急行|普通", re.I)
_NOT_COMMUTER_RE = re.compile(r"Shinkansen|Limited Express|Liner|Sleeper|Romancecar|Skyliner|N'EX|Narita Express", re.I)


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


def _graph_query(route_ids: list[int]) -> str:
    """Every stop of the given routes (wherever it is) plus the stations those stops belong to."""
    return f"""
[out:json][timeout:300];
rel(id:{",".join(map(str, route_ids))})->.r;
node(r.r)->.p;
.p out body;
rel(bn.p)["public_transport"="stop_area"]->.a;
.a out body;
node(r.a)["railway"~"^(station|halt)$"];
out body;
"""


def _cached(client: PoliteClient, query: str, refresh: bool, label: str) -> dict[str, Any]:
    raw = RAW_DIR / (hashlib.sha1(query.encode()).hexdigest()[:16] + ".json")
    if raw.exists() and not refresh:
        return read_json(raw)
    log.info("overpass %s", label)
    data = _overpass(client, query)
    write_json(raw, data)
    return data


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
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[int, dict[str, Any]]]:
    """Returns (stations, lines, route relations). Routes carry their route_master's tags."""
    nodes: dict[int, dict[str, Any]] = {}
    areas: dict[int, dict[str, Any]] = {}
    routes: dict[int, dict[str, Any]] = {}
    masters: dict[int, dict[str, Any]] = {}
    boxes = _cells(coords)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for i, box in enumerate(boxes, 1):
        data = _cached(client, _query(box), refresh, f"query {i}/{len(boxes)} bbox={tuple(round(x, 3) for x in box)}")
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
    return stations, sorted(lines.values(), key=lambda l: l["name"]), routes


def _dist_m(a: dict[str, Any], b: dict[str, Any]) -> float:
    lat1, lng1, lat2, lng2 = map(math.radians, (a["lat"], a["lon"], b["lat"], b["lon"]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def _station_name(tags: dict[str, str]) -> str:
    return re.sub(r"\s+[Ss]tation$", "", tags.get("name:en") or tags.get("name:ja-Latn") or tags.get("name") or "")


def _name_key(tags: dict[str, str]) -> str:
    """Comparable station name: Japanese name if present (most consistent across operators)."""
    return re.sub(r"駅$", "", tags.get("name") or "") or _station_name(tags).lower()


class _Grid:
    """Bucket nodes by ~1km cells for radius lookups."""

    STEP = 0.01

    def __init__(self) -> None:
        self.cells: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)

    def add(self, n: dict[str, Any]) -> None:
        self.cells[(int(n["lat"] // self.STEP), int(n["lon"] // self.STEP))].append(n)

    def near(self, n: dict[str, Any], radius_m: float) -> list[tuple[float, dict[str, Any]]]:
        cy, cx = int(n["lat"] // self.STEP), int(n["lon"] // self.STEP)
        out = []
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                for m in self.cells.get((cy + dy, cx + dx), []):
                    d = _dist_m(n, m)
                    if d <= radius_m:
                        out.append((d, m))
        return sorted(out, key=lambda t: t[0])


def build_graph(client: PoliteClient, routes: dict[int, dict[str, Any]], line_ids: dict[str, str],
                refresh: bool = False) -> dict[str, Any]:
    """Rail network for the commute search. `line_ids` maps _line_key → lines.json id."""
    def commuter(r: dict[str, Any]) -> bool:
        tags = r.get("tags", {})
        name = tags.get("name:en") or tags.get("name") or ""
        if tags.get("service") in ("high_speed", "long_distance", "night") or _NOT_COMMUTER_RE.search(name):
            return False
        # A train named without Line/Rapid/Express/Local is a named limited express ("Azusa", "Hitachi")
        return tags.get("route") != "train" or bool(_COMMUTER_NAME_RE.search(_line_name(tags)))

    usable = {rid: r for rid, r in routes.items() if commuter(r)}
    nodes: dict[int, dict[str, Any]] = {}
    areas: dict[int, dict[str, Any]] = {}
    ids = sorted(usable)
    chunks = [ids[i:i + GRAPH_CHUNK] for i in range(0, len(ids), GRAPH_CHUNK)]
    for i, chunk in enumerate(chunks, 1):
        data = _cached(client, _graph_query(chunk), refresh, f"route stops {i}/{len(chunks)}")
        for el in data["elements"]:
            if el["type"] == "node":
                nodes[el["id"]] = el
            elif el.get("tags", {}).get("public_transport") == "stop_area":
                areas[el["id"]] = el

    def is_station(n: dict[str, Any]) -> bool:
        t = n.get("tags", {})
        return t.get("railway") in ("station", "halt") and t.get("disused") != "yes"

    stations = {nid: n for nid, n in nodes.items() if is_station(n)}
    grid = _Grid()
    for n in stations.values():
        grid.add(n)
    area_station: dict[int, int] = {}
    for a in areas.values():
        members = [m["ref"] for m in a.get("members", []) if m["type"] == "node"]
        st = next((ref for ref in members if ref in stations), None)
        if st is not None:
            for ref in members:
                area_station.setdefault(ref, st)

    def station_of(ref: int) -> int | None:
        n = nodes.get(ref)
        if n is None:
            return None
        if ref in stations:
            return ref
        if ref in area_station:
            return area_station[ref]
        key = _name_key(n.get("tags", {}))
        near = grid.near(n, 500)
        same = [m for _, m in near if key and _name_key(m.get("tags", {})) == key]
        if same:
            return same[0]["id"]
        if near and near[0][0] <= 200:
            return near[0][1]["id"]
        if n.get("tags", {}).get("name"):  # a stop with no station node: let it stand in for one
            stations[ref] = n
            grid.add(n)
            return ref
        return None

    # Ordered station sequences per route
    sequences: dict[int, list[int]] = {}
    for rid, r in usable.items():
        seq: list[int] = []
        for m in r.get("members", []):
            if m["type"] != "node" or not (m["role"].startswith("stop") or m["role"] == ""):
                continue
            sid = station_of(m["ref"])
            if sid is not None and (not seq or seq[-1] != sid):
                seq.append(sid)
        if len(seq) >= 2:
            sequences[rid] = seq

    # Line identity, so switching between a line's local and express isn't a transfer:
    # a service borrows the line of a same-operator local route covering most of its stops.
    def op(r: dict[str, Any]) -> str:
        t = r.get("tags", {})
        return t.get("operator:en") or t.get("operator") or t.get("network") or ""

    route_line: dict[int, str] = {}
    line_names: dict[str, str] = {}
    locals_: list[int] = []
    for rid in sequences:
        tags = usable[rid].get("tags", {})
        name = _line_name(tags)
        if not _is_service(tags, name):
            route_line[rid] = line_ids.get(_line_key(tags, name)) or _line_key(tags, name)
            line_names.setdefault(route_line[rid], name)
            locals_.append(rid)
    for rid in sequences:
        if rid in route_line:
            continue
        own = set(sequences[rid])
        best, score = None, 0.0
        for lid in locals_:
            if op(usable[lid]) != op(usable[rid]):
                continue
            s = len(own & set(sequences[lid])) / len(own)
            if s > score:
                best, score = lid, s
        tags = usable[rid].get("tags", {})
        route_line[rid] = route_line[best] if best is not None and score >= 0.8 else _line_key(tags, _line_name(tags))
        line_names.setdefault(route_line[rid], _line_name(tags))

    # Emit compact arrays
    used = sorted({sid for seq in sequences.values() for sid in seq})
    idx = {sid: i for i, sid in enumerate(used)}
    line_list = sorted(set(route_line.values()))
    line_idx = {l: i for i, l in enumerate(line_list)}
    out_routes = []
    for rid, seq in sequences.items():
        tags = usable[rid].get("tags", {})
        express = _is_service(tags, _line_name(tags))
        mode = tags.get("route", "train")
        speed = EXPRESS_KMH if express else CRUISE_KMH.get(mode, CRUISE_KMH["train"])
        stop = STOP_MIN.get(mode, STOP_MIN["subway"])
        cum, t = [0], 0.0
        for a, b in zip(seq, seq[1:]):
            t += stop + _dist_m(stations[a], stations[b]) / 1000 / speed * 60
            cum.append(round(t * 10))  # tenths of a minute
        out_routes.append([line_idx[route_line[rid]], [idx[s] for s in seq], cum])

    links = []
    used_set = set(used)
    for sid in used:
        n = stations[sid]
        key = _name_key(n.get("tags", {}))
        for d, m in grid.near(n, SAME_NAME_RADIUS_M):
            mid = m["id"]
            if mid <= sid or mid not in used_set:
                continue
            if d <= NEAR_RADIUS_M or (key and _name_key(m.get("tags", {})) == key):
                links.append([idx[sid], idx[mid], round(d / WALK_M_PER_MIN * 10)])

    # Lines serving each station, for the destination picker's hint
    serving: dict[int, set[int]] = defaultdict(set)
    for line, seq_idx, _ in out_routes:
        for s in seq_idx:
            serving[s].add(line)
    out_stations = []
    for sid in used:
        tags = stations[sid].get("tags", {})
        out_stations.append([
            sid, _station_name(tags) or tags.get("name", ""), tags.get("name"),
            round(stations[sid]["lat"], 5), round(stations[sid]["lon"], 5), sorted(serving[idx[sid]]),
        ])
    log.info("rail graph: %d stations, %d routes, %d lines, %d walking links",
             len(out_stations), len(out_routes), len(line_list), len(links))
    return {
        "transfer_min": TRANSFER_MIN,
        "lines": [line_names[l] for l in line_list],
        "stations": out_stations,
        "routes": out_routes,
        "links": links,
    }


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
    stations, lines, routes = build(client, coords, refresh=args.refresh)
    write_json(WEB_DATA / "stations.json", stations)
    write_json(WEB_DATA / "lines.json", lines, pretty=True)
    no_line = sum(1 for s in stations if not s["lines"])
    log.info("wrote %d stations (%d without a line) and %d lines", len(stations), no_line, len(lines))
    line_ids = {_line_key({"operator": l["operator"] or ""}, l["name"]): l["id"] for l in lines}
    write_json(WEB_DATA / "rail-graph.json", build_graph(client, routes, line_ids, refresh=args.refresh))


if __name__ == "__main__":
    main()
