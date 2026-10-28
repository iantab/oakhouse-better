# Oakhouse Search

An unofficial, faster way to search [Oakhouse](https://www.oakhouse.jp/eng/) share houses, social residences and
apartments, in the style of SUUMO: choose **area** or **line & station** → set **conditions** → browse **results**.

Live site: https://iantab.github.io/oakhouse-better/

## How it works

```
GitHub Actions (daily 03:00 JST, on push to master, or manual)
  scraper/build.py  → scrapes oakhouse.jp/eng, writes JSON into web/   (never committed)
  astro build       → static site, one page per property
  deploy-pages      → GitHub Pages
```

- **No backend or database.** The results page filters `search-index.json` in the browser.
- **Scraped data is not committed.** Between runs, `actions/cache` keeps the Nominatim geocode cache and the last
  successful listing counts. If a scrape returns more than 10% fewer listings than last time, the job fails and the
  previous deployment stays live.
- **Station data is committed:** `web/src/data/stations.json` and `lines.json`. It comes from OpenStreetMap and is
  refreshed by hand (see below).

| Path | What |
|---|---|
| `scraper/build.py` | Main pipeline: sitemap → fetch pages → parse → geocode → nearest stations → amenities → JSON |
| `scraper/parse.py` | HTML → property/room records (share-house tables and brokered-apartment cards) |
| `scraper/amenities.py` | Maps Oakhouse's free-text facilities to filter keys (`washlet`, `bathtub`, `no_deposit`…) |
| `scraper/stations.py` | Overpass (OSM) → `stations.json` + `lines.json` |
| `scraper/geocode.py` | Nominatim reverse geocoding (chōme + postcode), cached |
| `web/` | Astro 7 static site |
| `.github/workflows/site.yml` | Scrape → build → deploy |

## Local development

```sh
cd web
npm install
npm run data:pull     # download the latest data from the live site
npm run dev           # http://localhost:4321/oakhouse-better/
```

To scrape yourself instead (Python 3.12+):

```sh
cd scraper
python -m venv .venv && .venv/Scripts/activate   # or: source .venv/bin/activate
pip install -r requirements.txt
python build.py --limit 20     # quick run on 20 listings
python build.py                # full run, about 25 min; pages are cached in scraper/.cache/html
python build.py --offline      # re-parse cached pages only (after changing parse.py)
```

### Refreshing station data

Run a few times a year, or when Oakhouse opens in a new region. It needs a prior `build.py` run for property
coordinates. Then commit the two files:

```sh
cd scraper && python stations.py
git add ../web/src/data/stations.json ../web/src/data/lines.json
```

## One-time GitHub setup

Repository **Settings → Pages → Source: GitHub Actions**. The workflow then deploys on every push to `master` and
daily.

## Data notes

- Walking times are estimated from straight-line distance to OSM stations (80 m/min, +25% for streets).
  Oakhouse's own station list is shown alongside it, because its walking minutes are sometimes copy-pasted.
- Photos are linked from oakhouse.jp, not copied.
- Station data © OpenStreetMap contributors (ODbL). Addresses: Nominatim (OSM).
