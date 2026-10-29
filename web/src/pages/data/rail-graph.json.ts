// Serves src/data/rail-graph.json (committed, built by scraper/stations.py) to the commute search.
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const EMPTY = { transfer_min: 4, lines: [], stations: [], routes: [], links: [] };

export function GET() {
  let body: string;
  try {
    body = readFileSync(join(process.cwd(), 'src', 'data', 'rail-graph.json'), 'utf-8');
  } catch {
    console.warn('[data] rail-graph.json not found in src/data; commute search will find nothing');
    body = JSON.stringify(EMPTY);
  }
  return new Response(body, { headers: { 'Content-Type': 'application/json' } });
}
