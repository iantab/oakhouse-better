"""Turn a cached English listing page into a property record with its rooms."""

from __future__ import annotations

import re
from typing import Any

from selectolax.lexbor import LexborHTMLParser, LexborNode

_COORD_RE = re.compile(r"maps\?q=(-?[\d.]+),(-?[\d.]+)")
_DATE_RE = re.compile(r"(\d{4})/(\d{2})/(\d{2})")
_WALK_RE = re.compile(r"(\d+)\s*min")
_NUM_RE = re.compile(r"[\d,]+(?:\.\d+)?")
_ICON_RE = re.compile(r"/svg/icn-([\w-]+)\.svg")


def _text(node: LexborNode | None) -> str:
    return " ".join(node.text(separator=" ").split()) if node else ""


def _yen(text: str) -> int | None:
    m = _NUM_RE.search(text.replace("¥", ""))
    return int(m.group().replace(",", "")) if m else None


def _slug(href: str | None) -> str | None:
    return href.rstrip("/").rsplit("/", 1)[-1] if href else None


def _icon(node: LexborNode) -> str | None:
    img = node.css_first("img")
    m = _ICON_RE.search(img.attrs.get("src") or img.attrs.get("data-src") or "") if img else None
    return m.group(1) if m else None


def _items(ul: LexborNode | None) -> list[dict[str, Any]]:
    """Facility-style list items as {label, icon}."""
    if ul is None:
        return []
    out = []
    for li in ul.css("li"):
        label = _text(li)
        if label:
            out.append({"label": label, "icon": _icon(li)})
    return out


def _categories(scope: LexborNode, cat_sel: str, head_sel: str, list_sel: str) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for cat in scope.css(cat_sel):
        head = cat.css_first(head_sel)
        if head is None:
            continue
        items = _items(cat.css_first(list_sel))
        if items:
            out.setdefault(_text(head), []).extend(items)
    return out


def _labelled_values(scope: LexborNode, title_sel: str) -> dict[str, str]:
    """For <li><strong>Label</strong> value</li> lists."""
    out = {}
    for strong in scope.css(title_sel):
        li = strong.parent
        if li is None:
            continue
        label = _text(strong).rstrip("：:").strip()
        value = _text(li)[len(_text(strong)):].strip(" ：:")
        if label:
            out[label] = value
    return out


def _parse_room(row: LexborNode, base_url: str) -> dict[str, Any]:
    attrs = {k[5:]: v for k, v in row.attrs.items() if k.startswith("data-") and v is not None}
    head = row.css_first(".ext-spheader")
    number = _text(head.css_first("h3")) if head else ""
    status_text = _text(head.css_first("span")) if head else ""
    available_from = None
    m = _DATE_RE.search(status_text)
    if m:
        available_from = "-".join(m.groups())

    prices: dict[str, int | None] = {}
    for h4 in row.css("td h4"):
        label = _text(h4)
        p = h4.next
        while p is not None and p.tag != "p":
            p = p.next
        if label in ("Contract fee", "Rent", "Maintenance fee", "Monthly rent") and p is not None:
            prices[label] = _yen(_text(p))

    details = {}
    for li in row.css("li.has-label"):
        strong = li.css_first("strong")
        if strong is not None:
            details[_text(strong)] = _text(li)[len(_text(strong)):].strip()

    lists = row.css("ul.p-room__caset__facilities")
    requirements = _items(lists[0]) if lists else []
    features = _items(lists[1]) if len(lists) > 1 else []

    photos = []
    for img in row.css("ul.p-room__table__slide img"):
        src = img.attrs.get("data-src") or img.attrs.get("src")
        if src and not src.startswith("data:"):
            photos.append(base_url + src if src.startswith("/") else src)

    size = details.get("Size", "")
    size_m = _NUM_RE.search(size)
    occupancy = _NUM_RE.search(details.get("Maximum occupancy", ""))

    status = attrs.get("status")
    return {
        "id": row.attrs.get("id"),
        "number": number,
        "status": "available" if status == "vacancy" and not available_from else ("soon" if status == "vacancy" else "full"),
        "available_from": available_from,
        "rent": prices.get("Rent"),
        "maintenance": prices.get("Maintenance fee"),
        "monthly_total": prices.get("Monthly rent"),
        "contract_fee": prices.get("Contract fee"),
        "size_m2": float(size_m.group().replace(",", "")) if size_m else None,
        "room_type": details.get("Room type"),
        "floor_plan": details.get("Floor plan"),
        "max_occupancy": int(occupancy.group()) if occupancy else None,
        "requirements": requirements,
        "features": features,
        "photos": photos,
        "attrs": attrs,
    }


def _parse_card(card: LexborNode, base_url: str) -> dict[str, Any]:
    """Room card used by brokered (third-party) apartment pages: <article class="p-room__caset">."""
    attrs = {k[5:]: v for k, v in card.attrs.items() if k.startswith("data-") and v is not None}
    head = card.css_first(".p-room__caset__number")
    em = head.css_first("em") if head else None
    number = _text(head).split("|")[0].strip() if head else ""
    status_text = _text(em)
    m = _DATE_RE.search(status_text)
    available_from = "-".join(m.groups()) if m else None

    table: dict[str, str] = {}
    for th in card.css("table.p-room__caset__table th"):
        td = th.next
        while td is not None and td.tag != "td":
            td = td.next
        label = _text(th)
        if td is not None and label and label not in table:
            table[label] = _text(td)

    inline = {}
    for strong in card.css(".p-room__inlinelist span > strong"):
        label = _text(strong).rstrip("：:").strip()
        value = _text(strong.parent)[len(_text(strong)):].strip(" ：:")
        if label and value:
            inline[label] = value

    facilities: dict[str, list[dict[str, Any]]] = {}
    for cat in card.css(".p-room__caset__category"):
        head3 = _text(cat.css_first("h3"))
        items = _items(cat.css_first("ul.p-room__caset__facilities"))
        if head3 and items:
            facilities.setdefault(head3, []).extend(items)
    features = []
    for h3 in card.css("h3.p-room__headline"):
        ul = h3.next
        while ul is not None and ul.tag != "ul":
            ul = ul.next
        features.extend(_items(ul))

    photos = []
    for img in card.css(".p-room__caset__slide img"):
        src = img.attrs.get("data-src") or img.attrs.get("src") or ""
        if src.startswith("/uploads") or src.startswith("http"):
            photos.append(base_url + src if src.startswith("/") else src)

    size_m = _NUM_RE.search(inline.get("Size", ""))
    status = attrs.get("status")
    if status == "vacancy" or "Vacancy" in status_text:
        st = "soon" if available_from or "soon" in status_text.lower() else "available"
    else:
        st = "full"
    rent = _yen(table.get("Rent", ""))
    maint = _yen(table.get("Maintenance fee・Management fee", "")) or 0
    return {
        "id": number,
        "number": number,
        "status": st,
        "available_from": available_from,
        "rent": rent,
        "maintenance": maint,
        "monthly_total": rent + maint if rent is not None else None,
        "contract_fee": None,
        "size_m2": float(size_m.group().replace(",", "")) if size_m else None,
        "room_type": inline.get("Room type"),
        "floor_plan": inline.get("Floor plan"),
        "floor_label": inline.get("Floor"),
        "max_occupancy": None,
        "requirements": [{"label": x, "icon": None} for x in [inline.get("Move in conditions")] if x],
        "features": features,
        "facilities": facilities,
        "fees": {k: v for k, v in table.items() if k not in ("Monthly fee", "Rent", "Maintenance fee・Management fee", "Remarks")},
        "remarks": table.get("Remarks"),
        "move_in": inline.get("handover and move-in"),
        "photos": photos,
        "attrs": attrs,
    }


def _filter_codes(tree: LexborHTMLParser) -> dict[str, dict[str, str]]:
    """Value → label maps from the page's room filter drawer (e.g. direction 3 → South)."""
    codes: dict[str, dict[str, str]] = {}
    for inp in tree.css("label.c-checkbox input[name], label.c-radio input[name]"):
        name, value = inp.attrs.get("name"), inp.attrs.get("value")
        label = _text(inp.parent)
        if name and value is not None and label:
            codes.setdefault(name, {})[value] = label
    return codes


def parse_listing(html: str, kind: str, pid: int, base_url: str = "https://www.oakhouse.jp") -> dict[str, Any]:
    tree = LexborHTMLParser(html)

    name = _text(tree.css_first("h1.p-description__name"))
    category = _text(tree.css_first(".p-titile__box .p-description__category"))
    updated = _text(tree.css_first(".p-description__date")).replace("Last updated", "").strip()
    description = _text(tree.css_first(".p-description__text"))

    # Breadcrumbs: Home > Share Houses for Rent > Tokyo > Region > Ward > Station > Name
    # The page repeats the breadcrumb at the bottom, so keep the first list only.
    crumbs, seen_hrefs = [], set()
    for li in tree.css('[itemtype="http://schema.org/ListItem"]'):
        a = li.css_first("a")
        href = a.attrs.get("href") if a else None
        if href in seen_hrefs:
            continue
        seen_hrefs.add(href)
        crumbs.append({"name": _text(li.css_first('[itemprop="name"]')), "href": href})
    area_crumbs = [c for c in crumbs if c["href"] and "/eng/area/" in c["href"]]

    # Access box: address + stations + terminal station
    address, stations, terminal = "", [], None
    access = None
    for box in tree.css("section.c-box"):
        if _text(box.css_first(".c-box__title")) == "Access":
            access = box
            break
    if access is not None:
        for li in access.css("ul.c-list > li"):
            strong = li.css_first("strong")
            label = _text(strong)
            if label == "Address":
                address = re.sub(r"\s+-(to|ku|shi|ken|fu|cho|machi|gun|mura)\b", r"-\1", _text(li)[len("Address"):].strip())
            elif label.startswith("Station"):
                for span in li.css("span"):
                    st_label = _text(span.css_first("strong"))
                    if not st_label.startswith("Station"):
                        continue
                    links = span.css("a")
                    line = next((a for a in links if "/line/" in (a.attrs.get("href") or "")), None)
                    station = next((a for a in links if "/station/" in (a.attrs.get("href") or "")), None)
                    walk = _WALK_RE.search(_text(span))
                    if station is not None:
                        stations.append({
                            "line": _text(line) or None,
                            "line_slug": _slug(line.attrs.get("href")) if line else None,
                            "station": re.sub(r"\s*Station$", "", _text(station)),
                            "station_slug": _slug(station.attrs.get("href")),
                            "walk_min": int(walk.group(1)) if walk else None,
                        })
            elif "Terminal station" in label:
                terminal = _text(li)[len(label):].strip()

    coords = _COORD_RE.search(html)

    # Other summary boxes keyed by title
    boxes: dict[str, LexborNode] = {}
    for box in tree.css("section.c-box"):
        title = _text(box.css_first(".c-box__title"))
        if title and title not in boxes:
            boxes[title] = box

    terms = []
    if "Initial payment and terms" in boxes:
        for el in boxes["Initial payment and terms"].css(".p-description__label > a, .p-description__label > span"):
            label = _text(el)
            if label:
                terms.append({"label": label, "icon": _icon(el)})
    discounts = []
    if "Available discounts and special offers" in boxes:
        for el in boxes["Available discounts and special offers"].css(".p-description__label > a, .p-description__label > span"):
            label = _text(el)
            if label:
                discounts.append(label)
    building = _labelled_values(boxes["Building and management information"], "ul.c-list > li > strong") \
        if "Building and management information" in boxes else {}
    shared = _categories(boxes["Shared facilities"], ".p-summary__category", "h3", ".p-facilities__list") \
        if "Shared facilities" in boxes else {}
    # Shared facilities are <a> tags rather than <li>
    if "Shared facilities" in boxes:
        shared = {}
        for cat in boxes["Shared facilities"].css(".p-summary__category"):
            head = _text(cat.css_first("h3"))
            items = [{"label": _text(a), "icon": _icon(a)} for a in cat.css(".p-facilities__list a") if _text(a)]
            if head and items:
                shared[head] = items

    # Room types (tabs), each with common facilities and its rooms
    room_types, rooms = [], []
    for tab in tree.css("div.c-tab__content"):
        rows = tab.css("tr.p-room__caset__row")
        common = tab.css_first(".p-room__common")
        if not rows and common is None:
            continue
        type_name = _text(common.css_first(".p-room__common__title")) if common else ""
        type_facilities: dict[str, list[dict[str, Any]]] = {}
        specific: dict[str, list[dict[str, Any]]] = {}
        if common is not None:
            for cat in common.css(".p-room__caset__category"):
                head = _text(cat.css_first("h4"))
                items = _items(cat.css_first("ul.p-room__caset__facilities"))
                if not head or not items:
                    continue
                target = specific if "ext-unique" in (cat.attrs.get("class") or "") else type_facilities
                target.setdefault(head, []).extend(items)
        room_types.append({"key": tab.attrs.get("id"), "name": type_name, "facilities": type_facilities})
        for row in rows:
            room = _parse_room(row, base_url)
            room["type_key"] = tab.attrs.get("id")
            room["specific_facilities"] = specific.get(room["number"], [])
            rooms.append(room)

    # Brokered apartments: one <article> card per unit, no room-type tabs
    for card in tree.css("article.p-room__caset"):
        room = _parse_card(card, base_url)
        room["type_key"] = "card"
        room["specific_facilities"] = []
        rooms.append(room)

    # Property photos: key visual + full photo list modal
    photos, seen = [], set()
    for el in tree.css(".p-photo__list__item, [data-photo-src]"):
        style = el.attrs.get("style") or ""
        m = re.search(r"url\(([^)\"']+)", style)
        if m:
            src = m.group(1)
            title_el = el.css_first("a")
            if src not in seen:
                seen.add(src)
                photos.append({
                    "src": base_url + src if src.startswith("/") else src,
                    "caption": (title_el.attrs.get("data-title") or "").strip() if title_el else "",
                })

    prefecture = area_crumbs[0]["name"] if area_crumbs else None
    region = area_crumbs[1]["name"] if len(area_crumbs) > 1 else None
    ward = area_crumbs[2]["name"] if len(area_crumbs) > 2 else None
    # Area hrefs look like /eng/area/tokyo/<region>/<ward>/apartment/furnished; the first
    # len(area_crumbs) segments after /area/ are the hierarchy, the rest is a category suffix.
    area_path: list[str] = []
    if area_crumbs:
        segments = area_crumbs[-1]["href"].split("/eng/area/", 1)[1].strip("/").split("/")
        area_path = segments[: len(area_crumbs)]

    name_missing = not name
    if name_missing:
        # Some brokered listings have an empty name on oakhouse.jp itself
        near = stations[0]["station"] if stations else None
        noun = (category or kind).title().replace("Sharehouse", "Share house")
        name = f"{noun} near {re.sub(r'[(（].*$', '', near).strip()} Station" if near else f"{noun} in {ward or prefecture or 'Japan'}"
        if near and ward:
            name += f" ({ward})"

    return {
        "id": pid,
        "kind": kind,
        "name_missing": name_missing,
        "category": category,  # SHAREHOUSE / APARTMENT / SOCIAL RESIDENCE ...
        "name": name,
        "url": f"{base_url}/eng/{kind}/{pid}",
        "updated": updated,
        "description": description,
        "address": address,
        "prefecture": prefecture,
        "region": region,
        "ward": ward,
        "area_path": area_path,
        "lat": float(coords.group(1)) if coords else None,
        "lng": float(coords.group(2)) if coords else None,
        "oakhouse_stations": stations,
        "terminal": terminal,
        "terms": terms,
        "discounts": discounts,
        "building": building,
        "shared_facilities": shared,
        "room_types": room_types,
        "rooms": rooms,
        "photos": photos,
        "_codes": _filter_codes(tree),
    }
