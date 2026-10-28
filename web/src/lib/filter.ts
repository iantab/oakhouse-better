// Search logic shared by the conditions page (live count) and the results page.
// Runs in the browser against public/data/search-index.json.

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
  am: string[];
  status: 'open' | 'all';
  sort: 'price' | 'walk' | 'size' | 'available';
  page: number;
}

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
  return {
    areas: multi('area'),
    stations: multi('st').map((v) => v.split('.').map(Number).filter((n) => !Number.isNaN(n))),
    lines: multi('line'),
    walk: num(params.get('walk')),
    rmin: num(params.get('rmin')),
    rmax: num(params.get('rmax')),
    types: multi('type'),
    am: multi('am'),
    status: params.get('status') === 'all' ? 'all' : 'open',
    sort: sort === 'walk' || sort === 'size' || sort === 'available' ? sort : 'price',
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

export function propertyMatches(p: IndexProperty, q: Query): { ok: boolean; walk: number | null } {
  if (q.areas.length) {
    const path = p.a.join('/');
    if (!q.areas.some((a) => path === a || path.startsWith(a + '/'))) return { ok: false, walk: null };
  }
  if (q.types.length && !q.types.includes(TYPE_OF[p.c] ?? 'other')) return { ok: false, walk: null };
  const walk = stationWalk(p, q);
  const stationFiltered = q.stations.length > 0 || q.lines.length > 0 || q.walk != null;
  if (stationFiltered && walk == null) return { ok: false, walk: null };
  return { ok: true, walk };
}

export function unitMatches(u: IndexUnit, q: Query, amIdx: number[]): boolean {
  if (q.status === 'open' && u.s === 'f') return false;
  if (q.rmin != null && (u.t ?? u.r ?? 0) < q.rmin) return false;
  if (q.rmax != null && (u.t ?? u.r ?? Infinity) > q.rmax) return false;
  for (const i of amIdx) if (!u.am.includes(i)) return false;
  return true;
}

export interface Group {
  prop: IndexProperty;
  units: IndexUnit[];
  walk: number | null;
}

export function search(index: SearchIndex, q: Query): { groups: Group[]; unitCount: number } {
  const amIdx = q.am.map((k) => index.amenities.indexOf(k)).filter((i) => i >= 0);
  const props = new Map<number, { prop: IndexProperty; walk: number | null }>();
  for (const p of index.properties) {
    const m = propertyMatches(p, q);
    if (m.ok) props.set(p.id, { prop: p, walk: m.walk ?? p.st[0]?.[1] ?? null });
  }
  const groups = new Map<number, Group>();
  let unitCount = 0;
  for (const u of index.units) {
    const hit = props.get(u.p);
    if (!hit || !unitMatches(u, q, amIdx)) continue;
    unitCount++;
    const g = groups.get(u.p) ?? { prop: hit.prop, units: [], walk: hit.walk };
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
  const firstDate = (g: Group) =>
    Math.min(...g.units.map((u) => (u.s === 'a' ? 0 : u.d ? Date.parse(u.d) : Infinity)));
  const sorters: Record<Query['sort'], (a: Group, b: Group) => number> = {
    price: (a, b) => minPrice(a) - minPrice(b),
    walk: (a, b) => (a.walk ?? 99) - (b.walk ?? 99) || minPrice(a) - minPrice(b),
    size: (a, b) => maxSize(b) - maxSize(a),
    available: (a, b) => firstDate(a) - firstDate(b) || minPrice(a) - minPrice(b),
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
