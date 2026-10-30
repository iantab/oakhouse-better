"""Normalise Oakhouse's free-text facilities into a fixed set of filterable amenity keys.

Matching uses the facility icon name first (stable: /svg/icn-<name>.svg) and falls
back to label text. Each unit gets a set of keys drawn from three levels:
room (its own facilities + its room type's + data-* attributes), building
(shared facilities) and contract (terms + move-in requirements).
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Iterable

# key, label shown in the UI, group, icons, label regex
AMENITIES: list[tuple[str, str, str, set[str], str | None]] = [
    # Room
    ("private_toilet", "Private toilet", "room", {"toilet"}, r"^Toilet$"),
    ("private_bath", "Private bath/shower", "room", {"bathroom", "shower"}, r"^(Bathroom|Shower)$"),
    ("bath_toilet_separate", "Separate bath & toilet", "room", {"bath_toilet_separated"}, r"Separated bath and toilet"),
    ("washlet", "Bidet toilet (Washlet) in room", "room", {"washlet"}, r"Washlet"),
    ("private_kitchen", "Private kitchen", "room", {"kitchen"}, r"^Kitchen$"),
    ("private_sink", "Sink / basin in room", "room", {"sink", "separate_sink", "wetarea"}, r"basin"),
    ("washer_in_room", "Washing machine in room", "room", {"laundry"}, r"^Washing machine"),
    ("aircon", "Air conditioner", "room", {"air_conditioner"}, r"Air conditioner"),
    ("fridge", "Refrigerator", "room", {"refrigerator"}, r"Refrigerator"),
    ("microwave", "Microwave", "room", {"microwave_oven"}, r"Microwave"),
    ("desk", "Desk & chair", "room", {"desk_chair"}, r"Desk and chair"),
    ("furnished", "Furnished", "room", {"furnished"}, r"[Ff]urnished"),
    ("balcony", "Balcony / veranda", "room", {"balcony", "veranda"}, r"Balcony|Veranda"),
    ("internet", "Internet in room", "room", {"wifi", "internet_cable", "internet_compatible"}, r"Wireless|Wired|Internet"),
    ("corner", "Corner room", "room", {"pos_corner"}, r"Corner room"),
    ("near_toilet", "Close to a toilet", "room", {"pos_toilet"}, r"Close to a toilet"),
    ("tatami", "Japanese-style (tatami)", "room", {"style_japanese"}, r"Interior: Japanese"),
    ("two_people", "2 people OK", "room", set(), None),
    # Building (shared)
    ("shared_bidet", "Shared bidet toilet", "building", set(), r"bidet"),
    ("bathtub", "Bathtub (shared)", "building", {"bathroom", "bigbath"}, r"^(Bathroom|Large bath)$"),
    ("shared_washer", "Shared washing machine", "building", {"washing_machine"}, r"Washing machine"),
    ("dryer", "Clothes dryer", "building", {"dryer_machine"}, r"^Dryer$"),
    ("lounge", "Shared lounge", "building", {"shared_lounge", "large_lounge"}, r"lounge"),
    ("women_toilet", "Women-only toilet", "building", {"only_womens_restroom"}, r"Toilet Female only"),
    ("autolock", "Entrance auto-lock", "building", {"automatic_lock"}, r"auto-lock"),
    ("elevator", "Elevator", "building", {"elevator"}, r"Elevator"),
    ("delivery_box", "Delivery box", "building", {"deliverylocker"}, r"Delivery box"),
    ("bike_parking", "Bicycle parking", "building", {"parkingcycle"}, r"Bicycle parking"),
    ("gym", "Gym", "building", {"gym"}, r"gym"),
    ("coworking", "Coworking space", "building", {"coworking_space"}, r"Coworking"),
    ("free_internet", "Free internet", "building", {"internet_free_wifi"}, r"Free Internet|usage fee: free"),
    # Contract
    ("foreigners_ok", "Foreigners welcome", "contract", {"worldwide"}, r"Foreign Residents Allowed"),
    ("short_term", "Short-term OK", "contract", {"monthly"}, r"Short-Term Rental|Monthly available"),
    ("no_deposit", "No security deposit", "contract", set(), r"^No security deposit$"),
    ("no_key_money", "No key money", "contract", set(), r"^No Key Money$"),
    ("no_guarantor", "No guarantor needed", "contract", set(), r"^No guarantor$"),
    ("no_brokerage", "No brokerage fee", "contract", set(), r"^No brokerage Fee$"),
    ("guests_ok", "Overnight guests OK", "contract", {"overnight_guest_ok"}, r"Accommodation for friends"),
    ("non_smoking", "Non-smoking building", "contract", {"nosmoking"}, r"Non-Smoking"),
    ("age_40_plus", "Ages 40+ accepted", "contract", set(), None),
    ("women_only", "Women only", "contract", set(), None),
    ("men_only", "Men only", "contract", set(), None),
    ("campaign", "Campaign / discount", "contract", set(), None),
    ("floor2", "2nd floor or higher", "room", set(), None),
]
GROUPS = {"room": "Room", "building": "Building", "contract": "Contract"}
_COMPILED = [(k, g, icons, re.compile(rx) if rx else None) for k, _, g, icons, rx in AMENITIES]

unmatched: Counter[tuple[str, str, str | None]] = Counter()


# Facilities that exist almost everywhere or aren't worth filtering on; don't report them as unmapped.
IGNORED_ICONS = {
    "light", "tv_antenna", "curtain", "individual_mailbox", "mailboxes", "style_western", "sunlight",
    "daylight_window", "window", "storage", "outdoor_clothes_airer", "indoor_closeths_airer", "flooring",
    "table", "sofa", "rice_cooker", "toaster", "oven", "frying_pan", "plates", "pot", "utencils",
    "pos_stairs", "pos_lounge", "pos_smoking", "cable_tv", "single", "semi_double", "double", "queen_bed",
}
IGNORED_LABELS = re.compile(r"^(Man / Woman|Woman Only|Man Only|Close to |Kitchen stove|Long-term lease|"
                            r".*Required$|Invitation of friends|Overnight stays)")


def _match(items: Iterable[dict[str, Any]], *groups: str) -> set[str]:
    keys = set()
    for item in items:
        icon, label = item.get("icon"), item.get("label", "")
        hit = False
        for key, g, icons, rx in _COMPILED:
            if g not in groups:
                continue
            if (icon and icon in icons) or (rx and rx.search(label)):
                keys.add(key)
                hit = True
        if not hit and icon not in IGNORED_ICONS and not IGNORED_LABELS.match(label):
            unmatched[("/".join(groups), label[:60], icon)] += 1
    return keys


def _flatten(cats: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    return [i for items in cats.values() for i in items]


def property_amenities(prop: dict[str, Any]) -> tuple[set[str], set[str]]:
    """Building-level and property-wide contract amenities."""
    building = _match(_flatten(prop["shared_facilities"]), "building")
    contract = _match(prop["terms"], "contract", "room")
    # Oakhouse listings never state an age limit (Xross ones always do; see xross_property_amenities)
    if not any(re.search(r"\bage\b|years old", t["label"], re.I) for t in prop["terms"]):
        contract.add("age_40_plus")
    return building, contract


def _is_none_fee(value: str | None) -> bool:
    return value is not None and bool(re.fullmatch(r"\s*(None|なし|-|0|0 ?months?|¥0)\s*", value, re.I))


def unit_amenities(prop: dict[str, Any], room: dict[str, Any], building: set[str], contract: set[str]) -> set[str]:
    room_type = next((t for t in prop["room_types"] if t["key"] == room.get("type_key")), None)
    room_items = (
        _flatten(room_type["facilities"]) if room_type else []
    ) + _flatten(room.get("facilities", {})) + room.get("specific_facilities", []) + room["features"]
    keys = _match(room_items, "room") | building | _match(room["requirements"], "contract", "room")

    # Brokered apartments state their own deposit/key money per unit; trust that over the
    # property-wide terms box.
    fees = room.get("fees", {})
    unit_contract = set(contract)
    if "Security deposit" in fees:
        unit_contract.discard("no_deposit")
        if _is_none_fee(fees["Security deposit"]):
            unit_contract.add("no_deposit")
    if "Key money" in fees:
        unit_contract.discard("no_key_money")
        if _is_none_fee(fees["Key money"]):
            unit_contract.add("no_key_money")
    keys |= unit_contract

    a = room["attrs"]
    yes = lambda name: a.get(name) == "0"  # Oakhouse uses ○/× → "0" yes, "X" no
    if yes("bathroom"):
        keys.add("private_bath")
    if yes("laundry"):
        keys.add("washer_in_room")
    if yes("veranda") or yes("balcony"):
        keys.add("balcony")
    if yes("deskchair"):
        keys.add("desk")
    if yes("pos-corner"):
        keys.add("corner")
    if yes("pos-toilet"):
        keys.add("near_toilet")
    if yes("wetarea"):
        keys.add("private_sink")
    if a.get("gender") == "W":
        keys.add("women_only")
    elif a.get("gender") == "M":
        keys.add("men_only")
    if a.get("campaign") == "1":
        keys.add("campaign")
    if a.get("style") == "japanese":
        keys.add("tatami")
    if (room.get("max_occupancy") or 0) >= 2:
        keys.add("two_people")
    floor = floor_number(room)
    if floor is not None and floor >= 2:
        keys.add("floor2")
    # In share houses "Bathroom"/"Shower" listed for the room type is the shared one;
    # only apartments (cards / apartment room types) really have a private bath.
    if prop["kind"] == "house" and room.get("type_key") != "card" and (room.get("room_type") or "") != "Apartments":
        if not yes("bathroom"):
            keys.discard("private_bath")
            keys.discard("private_toilet")
    return keys


# Xross House: matched against "Category: item" lines from the room-equipment and shared-facilities
# tables ("Wash basin: 3-piece unit Bathroom", "Entrance key type: Auto lock (PIN code)") and the
# room-feature icon labels. Same keys as above, different vocabulary.
XROSS_ROOM: list[tuple[str, str]] = [
    ("private_bath", r"unit Bath|^(Bath|Bathroom|Shower)\b"),
    ("private_toilet", r"unit Bath|^Toilet\b"),
    ("bath_toilet_separate", r"(?i)separate.*(bath|toilet)|(bath|toilet).*separate"),
    ("washlet", r"(?i)washlet|bidet"),
    ("private_kitchen", r"^Kitchen\b"),
    ("private_sink", r"^Wash basin\b"),
    ("washer_in_room", r"^Washing machine\b"),
    ("aircon", r"Air conditioner"),
    ("fridge", r"^Refrigerator\b"),
    ("microwave", r"^Microwave\b"),
    ("desk", r"^Desk: Desk"),
    ("balcony", r"(?i)balcony|veranda"),
    ("internet", r"^Internet\b|^WiFi"),
    ("tatami", r"(?i)japanese-style|tatami"),
    ("two_people", r"^2 people move in"),
]
XROSS_BUILDING: list[tuple[str, str]] = [
    ("shared_bidet", r"(?i)^Toilet: .*(washlet|bidet)"),
    ("women_toilet", r"(?i)^Toilet: .*(women|female)"),
    ("bathtub", r"(?i)^Bath: .*(tub|unit bath|large bath|bathroom)"),
    ("shared_washer", r"^Washing machine\b"),
    ("dryer", r"(?i)(?<!hair)dryer|drying machine"),
    ("lounge", r"(?i)^Living room|lounge"),
    ("autolock", r"(?i)auto ?lock"),
    ("elevator", r"(?i)elevator"),
    ("delivery_box", r"(?i)delivery (box|locker)"),
    ("bike_parking", r"^Bicycle parking"),
    ("gym", r"(?i)\bgym\b"),
    ("coworking", r"(?i)co-?working|work(ing)? space"),
    ("free_internet", r"(?i)wifi \(free\)|free wi-?fi"),
]
# Shared-facility categories that never map to a filter; not reported as unmapped
XROSS_IGNORED = re.compile(r"^(Post|Mailbox|Air conditioner|Kitchen|Wash basin|Other/special equipment|"
                           r"Entrance key type|Key type|Bed|Chair|Storage|Tv set|Futon|Mattress|Clothes drying|"
                           r"Other equipment|Toilet: Toilet|Bath: (Shower|Toiletries)|Living room: (Living|Tv)|"
                           r"(Desk|Chair|Storage space|TV)$|Desk: (Low table|Folding|Simple table)|Safe box)")
_XROSS_ROOM = [(k, re.compile(rx)) for k, rx in XROSS_ROOM]
_XROSS_BUILDING = [(k, re.compile(rx)) for k, rx in XROSS_BUILDING]


def _xross_lines(cats: dict[str, list[dict[str, Any]]]) -> list[str]:
    return [f"{cat}: {i['label']}" for cat, items in cats.items() for i in items]


def _xross_match(lines: Iterable[str], table: list[tuple[str, re.Pattern[str]]], group: str) -> set[str]:
    keys = set()
    for line in lines:
        hits = {k for k, rx in table if rx.search(line)}
        keys |= hits
        if not hits and not XROSS_IGNORED.match(line):
            unmatched[(f"xross/{group}", line[:60], None)] += 1
    return keys


def xross_property_amenities(prop: dict[str, Any]) -> tuple[set[str], set[str]]:
    """Building-level and contract amenities for an Xross House listing."""
    building = _xross_match(_xross_lines(prop["shared_facilities"]), _XROSS_BUILDING, "building")
    b = prop["building"]
    contract = set()
    promos = " | ".join(t["label"] for t in prop["terms"])
    if re.search(r"No security deposit", promos, re.I):
        contract.add("no_deposit")
    if re.search(r"agency fee|brokerage", promos, re.I):
        contract.add("no_brokerage")
    if re.search(r"key money", promos, re.I):
        contract.add("no_key_money")
    if prop["discounts"]:
        contract.add("campaign")
    if re.search(r"as short as one month", b.get("Contract Period", "")):
        contract.add("short_term")
    if re.match(r"No smoking", b.get("About smoking", ""), re.I):
        contract.add("non_smoking")
    if "English" in b.get("Available languages", ""):
        contract.add("foreigners_ok")
    age = re.search(r"to\s*(\d+)\s*years", b.get("Age limit", ""))
    if not b.get("Age limit") or (age and int(age.group(1)) >= 40):
        contract.add("age_40_plus")
    # A guarantee company is always required, so never "no_guarantor"
    return building, contract


def xross_unit_amenities(room: dict[str, Any], building: set[str], contract: set[str]) -> set[str]:
    # Icon labels are bare ("Kitchen", "Desk"); the equipment table says what each one is ("Desk: Low table")
    lines = _xross_lines(room.get("facilities", {})) + [f["label"] for f in room["features"]]
    keys = _xross_match(lines, _XROSS_ROOM, "room") | building | contract | {"furnished"}
    # Apartments list their (free) WiFi per room rather than as a shared facility
    if any(re.search(r"(?i)^Internet: WiFi \(free\)", line) for line in lines):
        keys.add("free_internet")
    if room["attrs"].get("gender") == "W":
        keys.add("women_only")
    floor = floor_number(room)
    if floor is not None and floor >= 2:
        keys.add("floor2")
    return keys


def floor_number(room: dict[str, Any]) -> int | None:
    raw = room["attrs"].get("floor") or ""
    if raw.lstrip("-").isdigit():
        return int(raw)
    m = re.match(r"(\d+)", room.get("floor_label") or "")
    return int(m.group(1)) if m else None


def is_oakhouse_lease(prop: dict[str, Any]) -> bool:
    """Oakhouse's own month-to-month contract with a flat contract fee, flagged on the listing as
    "Short-Term Rental"; other apartments are typical Japanese leases arranged via an agent."""
    return any(t["label"] == "Short-Term Rental" for t in prop["terms"])


def structure(prop: dict[str, Any]) -> str | None:
    """Building structure from "Building outline" (e.g. "RC (Reinforced Concrete) 8-story building"):
    rc (RC/RCC/SRC), wood, steel (incl. light-gauge and ALC), or None when the page doesn't say."""
    # Oakhouse: "Building outline"; Xross: "Structure" ("Steel frame construction, 4 stories")
    outline = prop["building"].get("Building outline") or prop["building"].get("Structure") or ""
    # Concrete first: "Steel-framed Reinforced Concrete" (SRC) also mentions steel
    if re.search(r"\bS?RCC?\b|Reinforced Concrete", outline, re.I):
        return "rc"
    if re.search(r"\bWood", outline, re.I):
        return "wood"
    if re.search(r"Steel|\bALC\b", outline, re.I):
        return "steel"
    return None


def catalog() -> list[dict[str, str]]:
    return [{"key": k, "label": label, "group": g} for k, label, g, _, _ in AMENITIES]
