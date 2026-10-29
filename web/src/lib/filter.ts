// Search logic shared by the conditions page (live count) and the results page.
// Runs in the browser against public/data/search-index.json.
import type { Commute } from './commute';

export interface IndexProperty {
  id: number;
  k: 'h' | 'a';
  c: string;
  n: string;
  a: string[];
  w: string | null;
  lat: number | null;
  lng: number | null;
  img: string | null;
  /** Building structure: wood / steel / rc, null when not stated. */
  b: string | null;
  st: [name: string, walk: number | null, lines: string[], ids: number[]][];
}

export interface IndexUnit {
  p: number;
  u: string;
  n: string;
  s: 'a' | 's' | 'f';
  d: string | null;
  r: number | null;
  t: number | null;
  sz: number | null;
  f: number | null;
  rt: string | null;
  am: number[];
}

export interface SearchIndex {
  updated: string;
  amenities: string[];
  properties: IndexProperty[];
  units: IndexUnit[];
}

export interface Query {
  areas: string[];
  stations: number[][];
  lines: string[];
  walk: number | null;
  rmin: number | null;
  rmax: number | null;
  types: string[];
  /** Building structures; 'none' matches listings that don't state one. */
  struct: string[];
  am: string[];
  avail: Status[];
  /** Commute search: destination station ids and max train minutes (times come from commute.ts). */
  to: number[];
  ct: number | null;
  sort: 'price' | 'walk' | 'size' | 'available' | 'commute';
  page: number;
}

const SORTS = ['price', 'walk', 'size', 'available', 'commute'] as const;

/** Sort used when the URL has none: shortest commute for commute searches, else lowest price. */
export const defaultSort = (params: URLSearchParams): Query['sort'] => (params.get('to') ? 'commute' : 'price');

export type Status = IndexUnit['s'];
/** Availability ticked when the URL has no avail param. */
export const DEFAULT_AVAIL: Status[] = ['a', 's'];

export const PAGE_SIZE = 20;

export const TYPE_OF: Record<string, string> = {
  SHAREHOUSE: 'share',
  'SOCIAL RESIDENCE': 'social',
  APARTMENT: 'apartment',
};

const num = (v: string | null) => (v && !Number.isNaN(Number(v)) && v !== '' ? Number(v) : null);

export function parseQuery(params: URLSearchParams): Query {
  const multi = (name: string) =>
    params.getAll(name).flatMap((v) => v.split(',')).map((v) => v.trim()).filter(Boolean);
  const sort = params.get('sort');
  const avail = multi('avail').filter((v): v is Status => v === 'a' || v === 's' || v === 'f');
  return {
    areas: multi('area'),
    stations: multi('st').map((v) => v.split('.').map(Number).filter((n) => !Number.isNaN(n))),
    lines: multi('line'),
    walk: num(params.get('walk')),
    rmin: num(params.get('rmin')),
    rmax: num(params.get('rmax')),
    types: multi('type'),
    struct: multi('struct'),
    am: multi('am'),
    // Older links used status=all ("include full rooms")
    avail: avail.length ? avail : params.get('status') === 'all' ? ['a', 's', 'f'] : [...DEFAULT_AVAIL],
    to: (params.get('to') ?? '').split('.').map(Number).filter((n) => n > 0),
    ct: num(params.get('ct')),
    sort: SORTS.find((s) => s === sort) ?? defaultSort(params),
    page: Math.max(1, num(params.get('page')) ?? 1),
  };
}

/** Walking minutes to the closest station that satisfies the station/line filter, or null if none does. */
export function stationWalk(p: IndexProperty, q: Query): number | null {
  const wanted = new Set(q.stations.flat());
  const anyStation = wanted.size === 0 && q.lines.length === 0;
  let best: number | null = null;
  for (const [, walk, lines, ids] of p.st) {
    if (walk == null) continue;
    const picked = anyStation || ids.some((id) => wanted.has(id)) || lines.some((l) => q.lines.includes(l));
    if (!picked) continue;
    if (q.walk != null && walk > q.walk) continue;
    if (best == null || walk < best) best = walk;
  }
  return best;
}

export interface PropertyCommute extends Commute {
  station: string;
  walk: number;
}

/** Fastest station (train time, then walk) within the walk limit that reaches the destination in time. */
export function stationCommute(p: IndexProperty, q: Query, times: Map<number, Commute>): PropertyCommute | null {
  let best: PropertyCommute | null = null;
  for (const [name, walk, , ids] of p.st) {
    if (walk == null || (q.walk != null && walk > q.walk)) continue;
    for (const id of ids) {
      const c = times.get(id);
      if (!c || (q.ct != null && c.min > q.ct)) continue;
      if (!best || c.min + walk < best.min + best.walk) best = { ...c, station: name, walk };
    }
  }
  return best;
}

const NO_MATCH = { ok: false, walk: null, commute: null } as const;

export function propertyMatches(
  p: IndexProperty,
  q: Query,
  times?: Map<number, Commute> | null,
): { ok: boolean; walk: number | null; commute: PropertyCommute | null } {
  if (q.areas.length) {
    const path = p.a.join('/');
    if (!q.areas.some((a) => path === a || path.startsWith(a + '/'))) return NO_MATCH;
  }
  if (q.types.length && !q.types.includes(TYPE_OF[p.c] ?? 'other')) return NO_MATCH;
  if (q.struct.length && !q.struct.includes(p.b ?? 'none')) return NO_MATCH;
  const walk = stationWalk(p, q);
  const stationFiltered = q.stations.length > 0 || q.lines.length > 0 || q.walk != null;
  if (stationFiltered && walk == null) return NO_MATCH;
  let commute: PropertyCommute | null = null;
  if (q.to.length) {
    commute = times ? stationCommute(p, q, times) : null;
    if (!commute) return NO_MATCH;
  }
  return { ok: true, walk: commute?.walk ?? walk, commute };
}

export function unitMatches(u: IndexUnit, q: Query, amIdx: number[]): boolean {
  if (!q.avail.includes(u.s)) return false;
  if (q.rmin != null && (u.t ?? u.r ?? 0) < q.rmin) return false;
  if (q.rmax != null && (u.t ?? u.r ?? Infinity) > q.rmax) return false;
  for (const i of amIdx) if (!u.am.includes(i)) return false;
  return true;
}

export interface Group {
  prop: IndexProperty;
  units: IndexUnit[];
  walk: number | null;
  commute: PropertyCommute | null;
}

/** `times` (from commute.ts) is required for commute searches (q.to); without it they match nothing. */
export function search(
  index: SearchIndex,
  q: Query,
  times?: Map<number, Commute> | null,
): { groups: Group[]; unitCount: number } {
  const amIdx = q.am.map((k) => index.amenities.indexOf(k)).filter((i) => i >= 0);
  const props = new Map<number, Omit<Group, 'units'>>();
  for (const p of index.properties) {
    const m = propertyMatches(p, q, times);
    if (m.ok) props.set(p.id, { prop: p, walk: m.walk ?? p.st[0]?.[1] ?? null, commute: m.commute });
  }
  const groups = new Map<number, Group>();
  let unitCount = 0;
  for (const u of index.units) {
    const hit = props.get(u.p);
    if (!hit || !unitMatches(u, q, amIdx)) continue;
    unitCount++;
    const g = groups.get(u.p) ?? { ...hit, units: [] };
    g.units.push(u);
    groups.set(u.p, g);
  }
  const price = (u: IndexUnit) => u.t ?? u.r ?? Infinity;
  const statusRank = { a: 0, s: 1, f: 2 } as const;
  for (const g of groups.values()) {
    g.units.sort((a, b) => statusRank[a.s] - statusRank[b.s] || price(a) - price(b));
  }
  const list = [...groups.values()];
  const minPrice = (g: Group) => Math.min(...g.units.map(price));
  const maxSize = (g: Group) => Math.max(...g.units.map((u) => u.sz ?? 0));
  const doorToDoor = (g: Group) => (g.commute ? g.commute.min + g.commute.walk : Infinity);
  const firstDate = (g: Group) =>
    Math.min(...g.units.map((u) => (u.s === 'a' ? 0 : u.d ? Date.parse(u.d) : Infinity)));
  const sorters: Record<Query['sort'], (a: Group, b: Group) => number> = {
    price: (a, b) => minPrice(a) - minPrice(b),
    walk: (a, b) => (a.walk ?? 99) - (b.walk ?? 99) || minPrice(a) - minPrice(b),
    size: (a, b) => maxSize(b) - maxSize(a),
    available: (a, b) => firstDate(a) - firstDate(b) || minPrice(a) - minPrice(b),
    commute: (a, b) => doorToDoor(a) - doorToDoor(b) || minPrice(a) - minPrice(b),
  };
  list.sort(sorters[q.sort]);
  return { groups: list, unitCount };
}

let indexPromise: Promise<SearchIndex> | null = null;
export function loadIndex(base: string): Promise<SearchIndex> {
  indexPromise ??= fetch(`${base}data/search-index.json`).then((r) => {
    if (!r.ok) throw new Error(`search index: HTTP ${r.status}`);
    return r.json();
  });
  return indexPromise;
}

export const esc = (s: unknown) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!);

export const yen = (n: number | null | undefined) => (n == null ? '—' : `¥${n.toLocaleString('en-US')}`);
