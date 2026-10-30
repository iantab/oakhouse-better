"""Download English listing pages into scraper/.cache/html/{kind}/{id}.html."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
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
    ids: list[tuple[str, int]], max_age_h: float, workers: int = 3, delay: float = 1.0
) -> list[tuple[str, int, Path]]:
    """Fetch every listing with `workers` threads, logging progress.

    Each worker has its own PoliteClient, so each keeps its own `delay` between requests
    (at most `workers` requests per `delay` seconds overall). Returns (kind, id, path),
    sorted by (kind, id), for listings that exist.
    """
    local = threading.local()
    clients: list[PoliteClient] = []
    lock = threading.Lock()
    done = 0
    start = time.monotonic()

    def worker(kind: str, pid: int) -> tuple[str, int, Path] | None:
        nonlocal done
        client = getattr(local, "client", None)
        if client is None:
            client = local.client = PoliteClient(delay=delay)
            with lock:
                clients.append(client)
        try:
            path = fetch_listing(client, kind, pid, max_age_h)
        except Exception as exc:  # one bad page shouldn't sink the crawl
            log.error("%s/%d failed: %s", kind, pid, exc)
            path = cache_path(kind, pid) if cache_path(kind, pid).exists() else None
        with lock:
            done += 1
            if done % 50 == 0 or done == len(ids):
                elapsed = time.monotonic() - start
                log.info("oakhouse fetched %d/%d (%.0fs elapsed, ~%.0fs left)", done, len(ids), elapsed,
                         elapsed / done * (len(ids) - done))
        return (kind, pid, path) if path is not None else None

    try:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            results = list(pool.map(lambda item: worker(*item), ids))
    finally:
        for client in clients:
            client.close()
    return sorted((r for r in results if r is not None), key=lambda r: (r[0], r[1]))
