"""Read Oakhouse's sitemap and return every house/apartment page id."""

from __future__ import annotations

import re

from common import BASE_URL, PoliteClient

SITEMAP_URL = f"{BASE_URL}/sitemap-pages.xml"
_PAGE_RE = re.compile(r"<loc>https://www\.oakhouse\.jp/(house|apartment)/(\d+)</loc>")


def listing_ids(client: PoliteClient) -> list[tuple[str, int]]:
    """Return sorted (kind, id) pairs, kind being "house" or "apartment".

    "house" pages cover share houses and social residences.
    """
    xml = client.get(SITEMAP_URL).text
    found = {(kind, int(pid)) for kind, pid in _PAGE_RE.findall(xml)}
    if not found:
        raise RuntimeError("sitemap contained no house/apartment URLs; layout changed?")
    return sorted(found)
