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

/** A room on a building page (buildings.json). Optional fields are omitted when the source doesn't say. */
export interface BuildingRoom {
  id: string;
  number: string;
  status: 'available' | 'soon' | 'full';
  from?: string;
  rent?: number;
  maint?: number;
  total?: number;
  /** One-time contract / registration fee (LIFULL: initial cost beyond the first 30 days, e.g. cleaning). */
  fee?: number;
  size?: number;
  floor?: number;
  type?: string;
  plan?: string;
  occ?: number;
  gender?: 'women' | 'men';
  /** Amenity keys for this room that aren't already listed for the whole building. */
  am?: string[];
  /** The room's own page on the operator's site. */
  url: string;
  /** Oakhouse brokered apartments: [label, value] fee rows. */
  fees?: [string, string][];
  /** LIFULL: [stay length, discount per day, cleaning fee]. */
  plans?: [string, number, number | null][];
  beds?: string[];
  /** LIFULL: paying the whole stay at once is the only option. */
  pu?: 1;
}

/** One building page's data, written by scraper/build.py. Facts only: no operator photos or descriptions. */
export interface Building {
  id: number;
  o: 'o' | 'x' | 'l';
  kind: 'house' | 'apartment';
  category: string;
  name: string;
  name_ja?: string;
  address?: string;
  address_ja?: string;
  postcode?: string;
  prefecture?: string;
  region?: string;
  ward?: string;
  area?: string[];
  lat?: number;
  lng?: number;
  /** Oakhouse only: its building page (Xross and LIFULL rooms each have their own). */
  ext?: string;
  /** [name, walk minutes, line ids]. */
  stations?: [string, number | null, string[]][];
  structure?: 'wood' | 'steel' | 'rc';
  built?: number;
  facts?: [string, string][];
  terms?: string[];
  discounts?: string[];
  /** Building and contract amenity keys shared by every room. */
  amenities?: string[];
  rooms: BuildingRoom[];
}
