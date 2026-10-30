// Build-time access to the scraper's output in src/data/.
// Files are read from disk (not imported) so a fresh clone without data still builds;
// run `npm run data:pull` or the scraper to get real listings.
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { CATEGORY_LABELS, OPERATORS } from './filter';
import type { AreaNode, Amenity, Line, Meta, Property, Station } from './types';

const DATA_DIR = join(process.cwd(), 'src', 'data');

function load<T>(name: string, fallback: T): T {
  try {
    return JSON.parse(readFileSync(join(DATA_DIR, name), 'utf-8')) as T;
  } catch {
    console.warn(`[data] ${name} not found in src/data; using empty data`);
    return fallback;
  }
}

let cache: {
  properties: Property[];
  areas: AreaNode[];
  lines: Line[];
  stations: Station[];
  amenities: { groups: Record<string, string>; items: Amenity[] };
  meta: Meta;
} | null = null;

/** Data scraped before Xross House was added has no operator fields; fill them in. */
function withOperator(p: Property & { oakhouse_stations?: Property['operator_stations'] }): Property {
  return { ...p, operator: p.operator ?? 'oakhouse', operator_stations: p.operator_stations ?? p.oakhouse_stations ?? [] };
}

export function getData() {
  cache ??= {
    properties: load<Property[]>('properties.json', []).map(withOperator),
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
  const { properties, lines } = getData();
  const byLine = new Map<string, Map<string, LineStation>>();
  for (const p of properties) {
    for (const s of p.stations) {
      if (s.walk_min > maxWalk) continue;
      for (const lineId of s.lines) {
        const stations = byLine.get(lineId) ?? new Map<string, LineStation>();
        byLine.set(lineId, stations);
        const entry = stations.get(s.name) ?? { ids: [], name: s.name, properties: 0, units: 0 };
        entry.ids = [...new Set([...entry.ids, ...s.ids])];
        entry.properties += 1;
        entry.units += p.rooms.length;
        stations.set(s.name, entry);
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

export const yen = (n: number | null | undefined) =>
  n == null ? '—' : `¥${n.toLocaleString('en-US')}`;

export function categoryLabel(category: string) {
  return CATEGORY_LABELS[category] ?? category.charAt(0) + category.slice(1).toLowerCase();
}

/** Operator display info for a property (name, site, home URL). */
export const operatorOf = (p: Property) => OPERATORS[p.operator === 'xross' ? 'x' : 'o'];
