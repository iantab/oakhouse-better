"""Shared paths, HTTP client and small helpers for the scraper."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
SCRAPER_DIR = ROOT / "scraper"
CACHE_DIR = SCRAPER_DIR / ".cache"
HTML_CACHE = CACHE_DIR / "html"
WEB_DATA = ROOT / "web" / "src" / "data"
WEB_PUBLIC_DATA = ROOT / "web" / "public" / "data"

BASE_URL = "https://www.oakhouse.jp"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
# Nominatim/Overpass ask for an identifying User-Agent instead of a browser one.
TOOL_UA = "oakhouse-better/0.1 (+https://github.com/iantab/oakhouse-better)"

log = logging.getLogger("scraper")


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


class PoliteClient:
    """httpx client that waits `delay` seconds between requests and retries with backoff."""

    def __init__(self, delay: float = 1.0, user_agent: str = BROWSER_UA, timeout: float = 60.0):
        self.delay = delay
        self._last = 0.0
        self.client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept-Language": "en"},
            timeout=timeout,
            follow_redirects=True,
        )

    def get(self, url: str, retries: int = 4, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, retries, **kwargs)

    def post(self, url: str, retries: int = 4, **kwargs: Any) -> httpx.Response:
        return self.request("POST", url, retries, **kwargs)

    def request(self, method: str, url: str, retries: int = 4, **kwargs: Any) -> httpx.Response:
        for attempt in range(retries):
            wait = self.delay - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                resp = self.client.request(method, url, **kwargs)
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise httpx.HTTPStatusError(f"HTTP {resp.status_code}", request=resp.request, response=resp)
                return resp
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                backoff = 5 * 2**attempt
                # Rate limiters (x-house.co.jp's Laravel throttle) say how long to wait
                retry_after = exc.response.headers.get("Retry-After") if isinstance(exc, httpx.HTTPStatusError) else None
                if retry_after and retry_after.isdigit():
                    backoff = int(retry_after) + 1
                log.warning("%s %s failed (%s); retry %d/%d in %ds", method, url, exc, attempt + 1, retries, backoff)
                time.sleep(backoff)
        raise RuntimeError(f"giving up on {url}")

    def close(self) -> None:
        self.client.close()


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def write_json(path: Path, data: Any, pretty: bool = False, sort_keys: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2 if pretty else None,
                      separators=None if pretty else (",", ":"), sort_keys=sort_keys)
    if pretty:
        text += "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
