// Shapes of the JSON written by scraper/build.py (the search index's are in filter.ts).

export interface AreaNode {
  slug: string;
  name: string;
  properties: number;
  units: number;
  children: AreaNode[];
}

export interface Line {
  id: string;
  name: string;
  name_ja: string | null;
  ref: string | null;
  operator: string | null;
  colour: string | null;
  type: string | null;
}

export interface Station {
  id: number;
  name: string;
  name_ja: string | null;
  slug: string;
  lat: number;
  lng: number;
  operator: string | null;
  lines: string[];
}

export interface Amenity {
  key: string;
  label: string;
  group: 'room' | 'building' | 'contract';
}

export interface Meta {
  updated: string;
  properties: number;
  units: number;
  available: number;
  operators?: Partial<Record<'oakhouse' | 'xross' | 'lifull', { properties: number; units: number }>>;
}
