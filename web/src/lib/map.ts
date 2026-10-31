// The /map/ page's MapLibre map: one clustered point per building that matches the filters.
// Tiles are OpenFreeMap's (no API key); MapLibre is only loaded by the map page.
import { GeolocateControl, LngLatBounds, Map as MapLibre, NavigationControl, Popup, setWorkerUrl, type ExpressionSpecification, type GeoJSONSource, type MapGeoJSONFeature } from 'maplibre-gl';
import type { Feature, FeatureCollection, Point } from 'geojson';
import 'maplibre-gl/dist/maplibre-gl.css';
// MapLibre 6 can't find its worker inside a bundle; Vite emits it as a file and gives us the URL
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url';

setWorkerUrl(workerUrl);
import { CATEGORY_LABELS, esc, OPERATORS, yen, type Group } from './filter';

const STYLES = { light: 'https://tiles.openfreemap.org/styles/liberty', dark: 'https://tiles.openfreemap.org/styles/dark' };
const SOURCE = 'buildings';
// Point colours per operator, matching the site's brand green for Oakhouse
const OP_COLOURS = { light: { o: '#1f7a5c', x: '#c2410c' }, dark: { o: '#5fd4a6', x: '#fb923c' } };
const FONT = ['Noto Sans Regular'];
// Place names: wards/cities in indigo (distinct from the green/orange building dots), neighbourhoods in slate
const LABEL_COLOURS = {
  light: { ward: '#4338ca', area: '#334155', halo: '#ffffff' },
  dark: { ward: '#a5b4fc', area: '#cbd5e1', halo: 'rgba(0,0,0,0.85)' },
};
const WARD_CLASSES = ['city', 'town', 'village'];

type Theme = 'light' | 'dark';
const theme = (): Theme => (document.documentElement.classList.contains('dark') ? 'dark' : 'light');

export function toGeoJSON(groups: Group[], base: string): FeatureCollection<Point> {
  const features: Feature<Point>[] = [];
  for (const g of groups) {
    const p = g.prop;
    if (p.lat == null || p.lng == null) continue;
    const prices = g.units.map((u) => u.t ?? u.r).filter((n): n is number => n != null);
    features.push({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [p.lng, p.lat] },
      properties: {
        id: p.id,
        op: p.o ?? 'o',
        name: p.n,
        kind: `${CATEGORY_LABELS[p.c] ?? p.c} · ${OPERATORS[p.o ?? 'o'].name}`,
        rooms: g.units.length,
        from: prices.length ? Math.min(...prices) : null,
        station: p.st[0] ? `${p.st[0][0]}${p.st[0][1] != null ? ` · ${p.st[0][1]} min walk` : ''}` : '',
        img: p.img ?? '',
        href: `${base}${p.k === 'h' ? 'house' : 'apartment'}/${p.id}/`,
      },
    });
  }
  return { type: 'FeatureCollection', features };
}

function popupHtml(f: MapGeoJSONFeature) {
  const p = f.properties as Record<string, string | number | null>;
  const rooms = Number(p.rooms);
  return `<div class="map-popup">
    ${p.img ? `<img src="${esc(p.img)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : ''}
    <div class="body">
      <p class="text-xs text-muted-foreground">${esc(p.kind)}</p>
      <h3 class="font-semibold leading-snug">${esc(p.name)}</h3>
      <p class="text-sm">${rooms} matching room${rooms === 1 ? '' : 's'}${p.from != null && p.from !== '' ? ` · from <strong>${yen(Number(p.from))}</strong>` : ''}</p>
      ${p.station ? `<p class="text-xs text-muted-foreground">${esc(p.station)}</p>` : ''}
      <a class="btn w-full" data-size="sm" href="${esc(p.href)}">View building →</a>
    </div>
  </div>`;
}

/** Labels in English where OSM has an English name (Japan's tiles default to Japanese). */
function englishLabels(map: MapLibre) {
  for (const layer of map.getStyle().layers ?? []) {
    if (layer.type !== 'symbol' || !map.getLayoutProperty(layer.id, 'text-field')) continue;
    if (layer.id.startsWith('cluster')) continue;
    map.setLayoutProperty(layer.id, 'text-field', ['coalesce', ['get', 'name:en'], ['get', 'name_en'], ['get', 'name']]);
  }
}

/** Coloured, haloed place names so wards stand out (the stock styles use thin black / dim grey). */
function placeLabelStyle(map: MapLibre) {
  const c = LABEL_COLOURS[theme()];
  const isWard: ExpressionSpecification = ['match', ['get', 'class'], WARD_CLASSES, true, false];
  for (const layer of map.getStyle().layers ?? []) {
    if (layer.type !== 'symbol' || layer['source-layer'] !== 'place') continue;
    map.setPaintProperty(layer.id, 'text-color', ['case', isWard, c.ward, c.area]);
    map.setPaintProperty(layer.id, 'text-halo-color', c.halo);
    map.setPaintProperty(layer.id, 'text-halo-width', ['case', isWard, 2, 1.5]);
    map.setPaintProperty(layer.id, 'text-halo-blur', 0);
    map.setLayoutProperty(layer.id, 'text-font', ['case', isWard, ['literal', ['Noto Sans Bold']], ['literal', FONT]]);
    // Prefecture/country labels keep the style's own sizing; wards and neighbourhoods get a readable size
    if (/country|state/.test(layer.id)) continue;
    map.setLayoutProperty(layer.id, 'text-transform', 'none');
    map.setLayoutProperty(layer.id, 'text-size', [
      'interpolate', ['linear'], ['zoom'],
      8, ['case', isWard, 12, 10],
      12, ['case', isWard, 15, 12],
      15, ['case', isWard, 17, 13],
    ]);
  }
}

function addLayers(map: MapLibre, data: FeatureCollection) {
  const t = theme();
  const colours = OP_COLOURS[t];
  map.addSource(SOURCE, { type: 'geojson', data, cluster: true, clusterMaxZoom: 13, clusterRadius: 45 });
  map.addLayer({
    id: 'clusters',
    type: 'circle',
    source: SOURCE,
    filter: ['has', 'point_count'],
    paint: {
      'circle-color': colours.o,
      'circle-opacity': 0.85,
      'circle-radius': ['step', ['get', 'point_count'], 15, 10, 19, 50, 24, 200, 30],
      'circle-stroke-width': 2,
      'circle-stroke-color': t === 'dark' ? '#0a0a0a' : '#ffffff',
    },
  });
  map.addLayer({
    id: 'cluster-count',
    type: 'symbol',
    source: SOURCE,
    filter: ['has', 'point_count'],
    layout: { 'text-field': '{point_count_abbreviated}', 'text-font': FONT, 'text-size': 12, 'text-allow-overlap': true },
    paint: { 'text-color': t === 'dark' ? '#0a0a0a' : '#ffffff' },
  });
  map.addLayer({
    id: 'buildings',
    type: 'circle',
    source: SOURCE,
    filter: ['!', ['has', 'point_count']],
    paint: {
      'circle-color': ['match', ['get', 'op'], 'x', colours.x, colours.o],
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 10, 5, 16, 9],
      'circle-stroke-width': 2,
      'circle-stroke-color': t === 'dark' ? '#0a0a0a' : '#ffffff',
    },
  });
}

export interface BuildingMap {
  setGroups(groups: Group[]): void;
}

/** Create the map in `container`; the first setGroups() call also fits the view to the points. */
export function createMap(container: HTMLElement, base: string): BuildingMap {
  let data: FeatureCollection<Point> = { type: 'FeatureCollection', features: [] };
  let fitted = false;
  let ready = false;
  let popup: Popup | null = null;
  const map = new MapLibre({
    container,
    style: STYLES[theme()],
    center: [137.5, 36.2], // all of Japan until the data arrives
    zoom: 4.3,
    attributionControl: { compact: true },
    fadeDuration: 0,
  });
  map.addControl(new NavigationControl({ showCompass: false }), 'top-right');
  map.addControl(new GeolocateControl({}), 'top-right');

  const fit = () => {
    if (fitted || !ready || !data.features.length) return;
    fitted = true;
    const b = new LngLatBounds();
    for (const f of data.features) b.extend(f.geometry.coordinates as [number, number]);
    map.fitBounds(b, { padding: 40, maxZoom: 14, duration: 0 });
  };

  // Re-adds our source/layers after a style change (theme toggle) as well as on first load
  map.on('style.load', () => {
    englishLabels(map);
    placeLabelStyle(map);
    addLayers(map, data);
    ready = true;
    fit();
  });

  map.on('click', 'clusters', async (e) => {
    const f = e.features?.[0];
    if (!f) return;
    const zoom = await map.getSource<GeoJSONSource>(SOURCE)!.getClusterExpansionZoom(f.properties.cluster_id);
    map.easeTo({ center: (f.geometry as Point).coordinates as [number, number], zoom });
  });
  map.on('click', 'buildings', (e) => {
    const f = e.features?.[0];
    if (!f) return;
    popup?.remove();
    popup = new Popup({ maxWidth: '260px', offset: 10, className: 'heya-popup' })
      .setLngLat((f.geometry as Point).coordinates as [number, number])
      .setHTML(popupHtml(f))
      .addTo(map);
  });
  for (const layer of ['clusters', 'buildings']) {
    map.on('mouseenter', layer, () => (map.getCanvas().style.cursor = 'pointer'));
    map.on('mouseleave', layer, () => (map.getCanvas().style.cursor = ''));
  }

  // Follow the site's light/dark toggle (Base.astro flips html.dark)
  let current = theme();
  new MutationObserver(() => {
    if (theme() === current) return;
    current = theme();
    ready = false;
    map.setStyle(STYLES[current]);
  }).observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });

  return {
    setGroups(groups) {
      popup?.remove(); // its building may no longer match
      data = toGeoJSON(groups, base);
      if (ready) map.getSource<GeoJSONSource>(SOURCE)?.setData(data);
      fit();
    },
  };
}
