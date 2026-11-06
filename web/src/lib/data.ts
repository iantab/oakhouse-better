// Build-time access to the scraper's output in src/data/.
// Files are read from disk (not imported) so a fresh clone without data still builds;
// run `npm run data:pull` or the scraper to get real listings.
import { readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import type { SearchIndex } from './filter';
import type { AreaNode, Amenity, Line, Meta, Station } from './types';

const DATA_DIR = join(process.cwd(), 'src', 'data');
/** The browser's search index; the line picker reads it at build time too. */
const INDEX_PATH = join(process.cwd(), 'public', 'data', 'search-index.json');

function load<T>(name: string, fallback: T, path = join(DATA_DIR, name)): T {
  try {
    return JSON.parse(readFileSync(path, 'utf-8')) as T;
  } catch {
    console.warn(`[data] ${path} not found; using empty data`);
    return fallback;
  }
}

let cache: {
  index: SearchIndex;
  areas: AreaNode[];
  lines: Line[];
  stations: Station[];
  amenities: { groups: Record<string, string>; items: Amenity[] };
  meta: Meta;
} | null = null;

const DATA_FILES = ['areas.json', 'lines.json', 'stations.json', 'amenities.json', 'meta.json'].map((n) => join(DATA_DIR, n));
let cacheStamp = '';

/** Modification times of the data files: the dev server reloads them when the scraper rewrites one. */
function dataStamp() {
  return [...DATA_FILES, INDEX_PATH]
    .map((path) => {
      try {
        return statSync(path).mtimeMs;
      } catch {
        return 0;
      }
    })
    .join();
}

export function getData() {
  // Builds read the files once; the dev server re-reads them after they change, so regenerated data
  // shows up on refresh without restarting it.
  if (import.meta.env.DEV) {
    const stamp = dataStamp();
    if (stamp !== cacheStamp) {
      cache = null;
      cacheStamp = stamp;
    }
  }
  cache ??= {
    index: load<SearchIndex>('search-index.json', { updated: '', amenities: [], properties: [], units: [] }, INDEX_PATH),
    areas: load<AreaNode[]>('areas.json', []),
    lines: load<Line[]>('lines.json', []),
    stations: load<Station[]>('stations.json', []),
    amenities: load('amenities.json', { groups: {}, items: [] as Amenity[] }),
    meta: load<Meta>('meta.json', { updated: '', properties: 0, units: 0, available: 0 }),
  };
  return cache;
}

export interface LineStation {
  ids: number[];
  name: string;
  properties: number;
  units: number;
}

/** Lines that have at least one property within walking distance, with those stations. */
export function linesWithListings(maxWalk = 20) {
  const { index, lines } = getData();
  const unitCounts = new Map<number, number>();
  for (const u of index.units) unitCounts.set(u.p, (unitCounts.get(u.p) ?? 0) + 1);
  const byLine = new Map<string, Map<string, LineStation>>();
  for (const p of index.properties) {
    // Only OSM-matched stations carry line ids; operator-listed fallbacks have none
    for (const [name, walk, lineIds, osmIds] of p.st) {
      if (walk == null || walk > maxWalk) continue;
      for (const lineId of lineIds) {
        const stations = byLine.get(lineId) ?? new Map<string, LineStation>();
        byLine.set(lineId, stations);
        const entry = stations.get(name) ?? { ids: [], name, properties: 0, units: 0 };
        entry.ids = [...new Set([...entry.ids, ...osmIds])];
        entry.properties += 1;
        entry.units += unitCounts.get(p.id) ?? 0;
        stations.set(name, entry);
      }
    }
  }
  return lines
    .filter((l) => byLine.has(l.id))
    .map((l) => ({
      ...l,
      stations: [...byLine.get(l.id)!.values()].sort((a, b) => a.name.localeCompare(b.name)),
    }))
    .sort((a, b) => (a.operator ?? '').localeCompare(b.operator ?? '') || a.name.localeCompare(b.name));
}
