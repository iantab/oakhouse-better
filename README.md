# Oakhouse Search

An unofficial, faster way to search [Oakhouse](https://www.oakhouse.jp/eng/) share houses, social residences and
apartments, in the style of SUUMO: choose **area**, **line & station** or **commute time** → set **conditions** →
browse **results**.

Live site: https://iantab.github.io/oakhouse-better/

## How it works

```
GitHub Actions (daily 03:00 JST, on push to master, or manual)
  scraper/build.py  → scrapes oakhouse.jp/eng, writes JSON into web/   (never committed)
     or data-pull   → re-uses the data already on the live site (pushes that don't touch scraper/)
  astro build       → static site, one page per property
  deploy-pages      → GitHub Pages
```

- **When it scrapes:** daily, on pushes that change `scraper/`, and on manual runs (**Actions → Scrape & deploy →
  Run workflow**; untick "scrape" to just rebuild). Other pushes, such as site or README changes, rebuild in a couple
  of minutes using the current live data.
- **No backend or database.** The results page filters `search-index.json` in the browser.
- **Scraped listings are not committed.** Between runs, `actions/cache` keeps only the last successful listing
  counts. If a scrape returns more than 10% fewer listings than last time, the job fails and the previous
  deployment stays live.
- **Reference data is committed and refreshed by hand** (see below):
  - `web/src/data/stations.json` + `lines.json`: stations and lines from OpenStreetMap.
  - `web/src/data/rail-graph.json`: the rail network for the commute search (every route's stops in order, with
    estimated minutes), also from OpenStreetMap.
  - `scraper/geocode.json`: chōme + postcode per property, from Nominatim. The daily run looks up only properties
    missing from it (a few seconds) but doesn't save them back to git.

| Path | What |
|---|---|
| `scraper/build.py` | Main pipeline: sitemap → fetch pages → parse → geocode → nearest stations → amenities → JSON |
| `scraper/parse.py` | HTML → property/room records (share-house tables and brokered-apartment cards) |
| `scraper/amenities.py` | Maps Oakhouse's free-text facilities to filter keys (`washlet`, `bathtub`, `no_deposit`…) |
| `scraper/stations.py` | Overpass (OSM) → `stations.json` + `lines.json` + `rail-graph.json` |
| `scraper/geocode.py` | Nominatim reverse geocoding (chōme + postcode) → `scraper/geocode.json` |
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
python build.py                # full run, about 10 min; pages are cached in scraper/.cache/html
python build.py --workers 1    # same, one page at a time (3 parallel fetchers by default, 1 s apart each)
python build.py --offline      # re-parse cached pages only (after changing parse.py)
```

### Refreshing reference data (stations & addresses)

Run every so often, e.g. monthly, or when Oakhouse opens in a new region. `build.py` adds addresses for any new
properties to `geocode.json` and writes the coordinates `stations.py` needs. Then commit the four files:

```sh
cd scraper
python build.py        # looks up addresses only for properties not yet in geocode.json
python stations.py     # re-uses cached Overpass responses; add --refresh to re-download
git add geocode.json ../web/src/data/stations.json ../web/src/data/lines.json ../web/src/data/rail-graph.json
```

## One-time GitHub setup

Repository **Settings → Pages → Source: GitHub Actions**. The workflow then deploys on every push to `master`
(scraping only if `scraper/` changed) and scrapes and deploys daily.

## Data notes

- Walking times are estimated from straight-line distance to OSM stations (80 m/min, +25% for streets).
  Oakhouse's own station list is shown alongside it, because its walking minutes are sometimes copy-pasted.
- Commute times are estimates: OSM has no timetables, so ride time comes from the distance between stops at a
  per-mode average speed (subway, commuter rail, express), plus a few minutes per transfer. They don't include
  waiting for the first train. The search runs in the browser (`web/src/lib/commute.ts`); speeds and the transfer
  penalty are set at the top of `scraper/stations.py`.
- Photos are linked from oakhouse.jp, not copied.
- Station data © OpenStreetMap contributors (ODbL). Addresses: Nominatim (OSM).
