// Commute search: estimated train time from every station to a destination, run in the
// browser on the OSM rail network built by scraper/stations.py (rail-graph.json).
// OSM has no timetables, so times come from stop distances; they're estimates.

export interface RailGraph {
  transfer_min: number;
  lines: string[];
  stations: [id: number, name: string, nameJa: string | null, lat: number, lng: number, lines: number[]][];
  /** Stops in order with cumulative tenths of a minute from the first stop. */
  routes: [line: number, stops: number[], cum: number[]][];
  /** Walking links between nearby stations, in tenths of a minute. */
  links: [a: number, b: number, tenths: number][];
}

export interface Commute {
  min: number;
  transfers: number;
}

/** A destination choice: same-named stations of an interchange grouped together. */
export interface Place {
  name: string;
  nameJa: string | null;
  ids: number[];
  lines: string[];
}

/** "Any" number of transfers is capped here. */
export const MAX_TRANSFERS = 3;
export const COMMUTE_CHOICES = [10, 20, 30, 40, 50, 60, 70, 80, 90];
export const DEFAULT_COMMUTE = 30;

interface Prepared {
  index: Map<number, number>;
  routesAt: number[][];
  linksAt: [number, number][][];
}
const prepared = new WeakMap<RailGraph, Prepared>();

function prepare(g: RailGraph): Prepared {
  let p = prepared.get(g);
  if (p) return p;
  const n = g.stations.length;
  p = {
    index: new Map(g.stations.map((s, i) => [s[0], i])),
    routesAt: Array.from({ length: n }, () => []),
    linksAt: Array.from({ length: n }, () => []),
  };
  g.routes.forEach(([, stops], r) => stops.forEach((s) => p!.routesAt[s].push(r)));
  for (const [a, b, t] of g.links) {
    p.linksAt[a].push([b, t]);
    p.linksAt[b].push([a, t]);
  }
  prepared.set(g, p);
  return p;
}

/**
 * Best ride time from every station to any of `destIds`, using at most `maxTransfers` changes.
 * Round-based (RAPTOR-style) without timetables: round k may board k+1 trains, each change
 * costs `transfer_min`, and lines are ridden in either direction.
 */
export function commuteTimes(g: RailGraph, destIds: number[], maxTransfers = MAX_TRANSFERS): Map<number, Commute> {
  const { index, routesAt, linksAt } = prepare(g);
  const n = g.stations.length;
  const penalty = g.transfer_min * 10;

  const start = new Float64Array(n).fill(Infinity);
  let marked = new Set<number>();
  for (const id of destIds) {
    const i = index.get(id);
    if (i == null) continue;
    start[i] = 0;
    marked.add(i);
  }
  for (const s of [...marked]) {
    for (const [o, t] of linksAt[s]) {
      if (t < start[o]) {
        start[o] = t;
        marked.add(o);
      }
    }
  }

  const rounds: Float64Array[] = [];
  for (let k = 0; k <= maxTransfers; k++) {
    const prev = k === 0 ? start : rounds[k - 1];
    const cur = Float64Array.from(prev);
    const lineAt = new Int32Array(n).fill(-1);
    const improved = new Set<number>();

    // Ride one route from labels in `from`; `board(s)` is the time to be at s ready to board.
    const ride = (r: number, board: (s: number) => number) => {
      const [line, stops, cum] = g.routes[r];
      let best = Infinity;
      for (let i = 0; i < stops.length; i++) {
        const s = stops[i];
        best = Math.min(best, board(s) - cum[i]);
        const t = best + cum[i];
        if (t < cur[s]) {
          cur[s] = t;
          lineAt[s] = line;
          improved.add(s);
        }
      }
      best = Infinity;
      for (let i = stops.length - 1; i >= 0; i--) {
        const s = stops[i];
        best = Math.min(best, board(s) + cum[i]);
        const t = best - cum[i];
        if (t < cur[s]) {
          cur[s] = t;
          lineAt[s] = line;
          improved.add(s);
        }
      }
    };

    const scan = new Set<number>();
    for (const s of marked) for (const r of routesAt[s]) scan.add(r);
    const cost = k === 0 ? 0 : penalty;
    for (const r of scan) ride(r, (s) => (marked.has(s) ? prev[s] + cost : Infinity));

    // Switching between services of the same line (local ↔ express) isn't a transfer
    const lines = new Set([...improved].map((s) => lineAt[s]));
    for (const r of new Set([...improved].flatMap((s) => routesAt[s]))) {
      const line = g.routes[r][0];
      if (lines.has(line)) ride(r, (s) => (lineAt[s] === line ? cur[s] : Infinity));
    }

    // Walk to nearby stations before the next train
    for (const s of [...improved]) {
      for (const [o, t] of linksAt[s]) {
        if (cur[s] + t < cur[o]) {
          cur[o] = cur[s] + t;
          improved.add(o);
        }
      }
    }
    rounds.push(cur);
    marked = improved;
    if (!marked.size) break;
  }

  const last = rounds[rounds.length - 1];
  const out = new Map<number, Commute>();
  for (let s = 0; s < n; s++) {
    const t = last[s];
    if (!Number.isFinite(t)) continue;
    const transfers = rounds.findIndex((r) => r[s] <= t + 1e-6);
    out.set(g.stations[s][0], { min: Math.max(1, Math.round(t / 10)), transfers });
  }
  return out;
}

/** Stations grouped into destination choices: same name within ~600 m is one place. */
export function places(g: RailGraph): Place[] {
  const groups = new Map<string, { lat: number; lng: number; place: Place; lineIdx: Set<number> }[]>();
  for (const [id, name, nameJa, lat, lng, lines] of g.stations) {
    const key = (nameJa || name).toLowerCase();
    const list = groups.get(key) ?? [];
    groups.set(key, list);
    const near = list.find((c) => Math.abs(c.lat - lat) < 0.006 && Math.abs(c.lng - lng) < 0.007);
    if (near) {
      near.place.ids.push(id);
      lines.forEach((l) => near.lineIdx.add(l));
    } else {
      list.push({ lat, lng, place: { name, nameJa, ids: [id], lines: [] }, lineIdx: new Set(lines) });
    }
  }
  const out: Place[] = [];
  for (const list of groups.values()) {
    for (const c of list) {
      c.place.lines = [...new Set([...c.lineIdx].map((l) => g.lines[l]))].sort();
      out.push(c.place);
    }
  }
  return out.sort((a, b) => b.lines.length - a.lines.length || a.name.localeCompare(b.name));
}

/** The place whose station ids match a `to` param, for labels. */
export function findPlace(all: Place[], ids: number[]): Place | undefined {
  const want = new Set(ids);
  return all.find((p) => p.ids.some((id) => want.has(id)));
}

export function transfersLabel(n: number) {
  return n === 0 ? 'direct' : `${n} transfer${n > 1 ? 's' : ''}`;
}

let graphPromise: Promise<RailGraph> | null = null;
export function loadGraph(base: string): Promise<RailGraph> {
  graphPromise ??= fetch(`${base}data/rail-graph.json`).then((r) => {
    if (!r.ok) throw new Error(`rail graph: HTTP ${r.status}`);
    return r.json();
  });
  return graphPromise;
}

export interface CommuteContext {
  times: Map<number, Commute>;
  dest: string;
}

/** Commute times for a URL's `to`/`xf`, or null when it isn't a commute search. */
export async function commuteFor(base: string, params: URLSearchParams): Promise<CommuteContext | null> {
  const to = (params.get('to') ?? '').split('.').map(Number).filter((n) => n > 0);
  if (!to.length) return null;
  const xf = params.get('xf');
  const g = await loadGraph(base);
  const times = commuteTimes(g, to, xf === '0' || xf === '1' || xf === '2' ? Number(xf) : MAX_TRANSFERS);
  return { times, dest: findPlace(places(g), to)?.name ?? 'your destination' };
}
