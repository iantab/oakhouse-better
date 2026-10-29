// Regroups Oakhouse's Tokyo areas for the area picker: the 23 special wards, split into the
// Central/North/West/South/East groups Tokyo real-estate sites commonly use (都心/城北/城西/城南/城東),
// and everything else (Tama-area cities). Oakhouse's own regions mix the two.
import type { AreaNode } from './types';

// Keyed by Oakhouse's ward slugs (note "chuou", "koutou").
// Borderline wards: Bunkyo (Central vs North), Nerima (West vs North), Arakawa (East vs North).
export const TOKYO_23_GROUPS: { key: string; name: string; wards: string[] }[] = [
  { key: 'central', name: 'Central', wards: ['chiyoda', 'chuou', 'minato', 'shinjuku', 'bunkyo', 'shibuya'] },
  { key: 'north', name: 'North', wards: ['toshima', 'kita', 'itabashi'] },
  { key: 'west', name: 'West', wards: ['nakano', 'suginami', 'nerima'] },
  { key: 'south', name: 'South', wards: ['shinagawa', 'meguro', 'ota', 'setagaya'] },
  { key: 'east', name: 'East', wards: ['taito', 'sumida', 'koutou', 'arakawa', 'adachi', 'katsushika', 'edogawa'] },
];

export interface WardEntry {
  path: string; // full area path as submitted in the URL, e.g. "tokyo/ueno-asakusa-toyosu/koutou"
  slug: string;
  name: string;
  units: number;
}

export interface WardGroup {
  key: string;
  name: string;
  units: number;
  wards: WardEntry[];
}

export function groupTokyo(tokyo: AreaNode) {
  const all: WardEntry[] = tokyo.children.flatMap((region) =>
    region.children.map((w) => ({
      path: `${tokyo.slug}/${region.slug}/${w.slug}`,
      slug: w.slug,
      name: w.name,
      units: w.units,
    })),
  );
  const byUnits = (a: WardEntry, b: WardEntry) => b.units - a.units;
  const sum = (ws: WardEntry[]) => ws.reduce((n, w) => n + w.units, 0);

  const groups: WardGroup[] = TOKYO_23_GROUPS.map((g) => {
    const wards = all.filter((w) => g.wards.includes(w.slug)).sort(byUnits);
    return { key: g.key, name: g.name, units: sum(wards), wards };
  }).filter((g) => g.wards.length > 0);

  const inWards = new Set(TOKYO_23_GROUPS.flatMap((g) => g.wards));
  const rest = all.filter((w) => !inWards.has(w.slug)).sort(byUnits);

  return {
    wards23: { units: groups.reduce((n, g) => n + g.units, 0), groups },
    rest: { units: sum(rest), wards: rest },
  };
}
