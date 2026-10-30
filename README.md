# Heya Search

An unofficial, faster way to search the share houses, social residences and furnished apartments of
[Oakhouse](https://www.oakhouse.jp/eng/) and [Xross House](https://x-house.co.jp/en/) in one place, in the style of
SUUMO: choose **area**, **line & station** or **commute time** → set **conditions** → browse **results**.

Live site: https://iantab.github.io/oakhouse-better/

## How it works

```
GitHub Actions (daily 03:00 Toronto time, on push to master, or manual)
  build.py fetch    → "Scrape listings": downloads oakhouse.jp/eng + x-house.co.jp/en pages into scraper/.cache (~1 h)
  build.py process  → "Parse & enrich": parse → geocode → nearest stations → amenities → JSON into web/   (never committed)
     or data-pull   → re-uses the data already on the live site (pushes that don't touch scraper/)
  astro build       → static site, one page per property
  deploy-pages      → GitHub Pages
```

- **When it scrapes:** daily, on pushes that change `scraper/`, and on manual runs (**Actions → Scrape & deploy →
  Run workflow**; untick "scrape" to just rebuild). Other pushes, such as site or README changes, rebuild in a couple
  of minutes using the current live data.
- **No backend or database.** The results page filters `search-index.json` in the browser.
- **Scraped listings are not committed.** Between runs, `actions/cache` keeps the last successful listing counts,
  Xross detail pages and Xross address lookups. If either site's scrape returns more than 10% fewer listings than
  last time, the job fails and the previous deployment stays live.
- **Reference data is committed and refreshed by hand** (see below):
  - `web/src/data/stations.json` + `lines.json`: stations and lines from OpenStreetMap.
  - `web/src/data/rail-graph.json`: the rail network for the commute search (every route's stops in order, with
    estimated minutes), also from OpenStreetMap.
  - `scraper/geocode.json`: chōme + postcode per property, from Nominatim. The daily run looks up only properties
    missing from it (a few seconds) but doesn't save them back to git.

| Path | What |
|---|---|
| `scraper/build.py` | Main pipeline in two stages: `fetch` (sitemaps → pages, records a manifest) and `process` (parse → geocode → nearest stations → amenities → JSON) |
| `scraper/parse.py` | HTML → property/room records (share-house tables and brokered-apartment cards) |
| `scraper/xross.py` | Xross House: sitemap → detail pages + room lists → the same records as `parse.py`; groups apartment units into buildings |
| `scraper/amenities.py` | Maps both sites' free-text facilities to filter keys (`washlet`, `bathtub`, `no_deposit`…) |
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
python build.py                # full run, ~1 h (both sites in parallel; Xross room lists are capped at 30/min)
python build.py fetch          # just download (into scraper/.cache, plus .cache/manifest.json)
python build.py process        # just parse + enrich + write, from what the last fetch downloaded
python build.py --workers 1    # same, one page at a time (3 parallel fetchers by default, 1 s apart each)
python build.py --offline      # same as process (re-parse after changing parse.py or xross.py)
python build.py --no-xross     # Oakhouse only
python xross.py --limit 5      # fetch + parse a few Xross listings and print them (writes no site data)
```

### Refreshing reference data (stations & addresses)

Run every so often, e.g. monthly, or when either site opens in a new region. `build.py` adds addresses for any new
properties to `geocode.json` and `xross-geocode.json` and writes the coordinates `stations.py` needs. Then commit
the files:

```sh
cd scraper
python build.py        # looks up addresses only for properties not yet in geocode.json
python stations.py     # re-uses cached Overpass responses; add --refresh to re-download
git add geocode.json xross-geocode.json ../web/src/data/stations.json ../web/src/data/lines.json ../web/src/data/rail-graph.json
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
- Photos are linked from oakhouse.jp and x-house.co.jp, not copied.
- **Xross House** is scraped within its robots.txt: listing URLs come from its sitemap, and only English detail pages
  and each property's room list are fetched (never the map or search endpoints). The room-list endpoint allows 30
  requests a minute per IP, so refreshing all ~1,600 listings takes ~55 min whatever the parallelism. Detail pages
  are fetched alongside by one fetcher, 2 s apart: at ~2 requests/s overall the site's firewall blocked the CI
  runner after ~550 requests, so keep the total near 1/s. If the site starts blocking anyway, the scrape pauses
  5 min, then fails the job rather than deploying partial data. Detail pages are refetched every 7–14 days, room
  availability daily. Coordinates come from [GSI's address search](https://msearch.gsi.go.jp/) on each listing's
  street address.
- Xross lists every furnished-apartment unit separately; units with the same address and building name are shown as
  one property, and each unit links to its own Xross page. Xross ids are offset by 100000 in this site's URLs.
- Station data © OpenStreetMap contributors (ODbL). Addresses: Nominatim (OSM).
