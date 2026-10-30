// Shapes of the JSON written by scraper/build.py.

export interface Facility {
  label: string;
  icon: string | null;
}

export interface Room {
  id: string;
  number: string;
  status: 'available' | 'soon' | 'full';
  available_from: string | null;
  rent: number | null;
  maintenance: number | null;
  monthly_total: number | null;
  contract_fee: number | null;
  size_m2: number | null;
  room_type: string | null;
  floor_plan: string | null;
  floor: number | null;
  max_occupancy: number | null;
  requirements: Facility[];
  features: Facility[];
  facilities?: Record<string, Facility[]>;
  specific_facilities: Facility[];
  fees?: Record<string, string>;
  remarks?: string | null;
  move_in?: string | null;
  photos: string[];
  type_key: string;
  amenities: string[];
  gender: 'any' | 'women' | 'men';
  /** Xross apartment units keep their own listing page after being grouped into a building. */
  url?: string;
}

export interface NearStation {
  ids: number[];
  name: string;
  lines: string[];
  distance_m: number;
  walk_min: number;
}

export interface Property {
  /** Xross House ids are offset by 100000 so they never collide with Oakhouse's. */
  id: number;
  operator: 'oakhouse' | 'xross';
  kind: 'house' | 'apartment';
  category: string;
  name: string;
  url: string;
  updated: string;
  description: string;
  address: string;
  /** Xross only (Oakhouse's comes from reverse geocoding, in `geo`). */
  postcode?: string | null;
  prefecture: string | null;
  region: string | null;
  ward: string | null;
  area_path: string[];
  lat: number | null;
  lng: number | null;
  geo: {
    postcode: string | null;
    chome: string | null;
    town: string | null;
    ward: string | null;
    city: string | null;
    prefecture: string | null;
  } | null;
  /** The operator's own station list (we compute `stations` ourselves from OSM). */
  operator_stations: {
    line: string | null;
    line_slug: string | null;
    station: string;
    station_slug: string | null;
    walk_min: number | null;
  }[];
  stations: NearStation[];
  terminal: string | null;
  terms: Facility[];
  discounts: string[];
  building: Record<string, string>;
  /** Parsed from building["Building outline"]; null when the page doesn't say. */
  structure: 'wood' | 'steel' | 'rc' | null;
  shared_facilities: Record<string, Facility[]>;
  room_types: { key: string; name: string; facilities: Record<string, Facility[]> }[];
  rooms: Room[];
  photos: { src: string; caption: string }[];
  amenities: string[];
}

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
  operators?: Record<'oakhouse' | 'xross', { properties: number; units: number }>;
}
