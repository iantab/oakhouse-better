"""Download English listing pages into scraper/.cache/html/{kind}/{id}.html."""

from __future__ import annotations

import time
from pathlib import Path

from common import BASE_URL, HTML_CACHE, PoliteClient, log


def cache_path(kind: str, pid: int) -> Path:
    return HTML_CACHE / kind / f"{pid}.html"


def fetch_listing(client: PoliteClient, kind: str, pid: int, max_age_h: float) -> Path | None:
    """Fetch one listing page unless a cached copy is newer than `max_age_h` hours.

    Returns the cached file path, or None if the listing no longer exists
    (Oakhouse redirects closed listings back to the index page).
    """
    path = cache_path(kind, pid)
    if path.exists() and (time.time() - path.stat().st_mtime) < max_age_h * 3600:
        return path

    url = f"{BASE_URL}/eng/{kind}/{pid}"
    resp = client.get(url)
    final = str(resp.url).rstrip("/")
    if resp.status_code == 404 or not final.endswith(f"/{kind}/{pid}"):
        log.info("%s/%d is gone (HTTP %d -> %s)", kind, pid, resp.status_code, final)
        return None
    resp.raise_for_status()
    if "p-description__name" not in resp.text:
        log.warning("%s/%d: page has no property name block; skipping", kind, pid)
        return None

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(resp.text, encoding="utf-8")
    return path


def fetch_all(
    client: PoliteClient, ids: list[tuple[str, int]], max_age_h: float
) -> list[tuple[str, int, Path]]:
    """Fetch every listing, logging progress. Returns (kind, id, path) for listings that exist."""
    out = []
    start = time.monotonic()
    for i, (kind, pid) in enumerate(ids, 1):
        try:
            path = fetch_listing(client, kind, pid, max_age_h)
        except Exception as exc:  # one bad page shouldn't sink the crawl
            log.error("%s/%d failed: %s", kind, pid, exc)
            path = cache_path(kind, pid) if cache_path(kind, pid).exists() else None
        if path is not None:
            out.append((kind, pid, path))
        if i % 50 == 0 or i == len(ids):
            elapsed = time.monotonic() - start
            log.info("fetched %d/%d (%.0fs elapsed, ~%.0fs left)", i, len(ids), elapsed, elapsed / i * (len(ids) - i))
    return out
