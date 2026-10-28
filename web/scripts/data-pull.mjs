// Download the latest generated data from the live site, so local dev works without running the scraper.
// CI also uses it on pushes that don't touch scraper/, to rebuild the site without re-scraping.
//   npm run data:pull                       (from https://iantab.github.io/oakhouse-better/)
//   DATA_URL=http://localhost:4321/oakhouse-better/ npm run data:pull
// Exits non-zero if any file fails to download.
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

const base = (process.env.DATA_URL ?? 'https://iantab.github.io/oakhouse-better/').replace(/\/?$/, '/');
const root = join(import.meta.dirname, '..');
// Same layout scraper/build.py writes: src/data is read at build time, public/data is published
// (so the next pull — locally or in CI — finds every file on the live site again).
const files = {
  'properties.json': ['src/data', 'public/data'],
  'areas.json': ['src/data', 'public/data'],
  'codes.json': ['src/data', 'public/data'],
  'amenities.json': ['src/data', 'public/data'],
  'meta.json': ['src/data', 'public/data'],
  'search-index.json': ['public/data'],
};

for (const [name, dirs] of Object.entries(files)) {
  const url = `${base}data/${name}`;
  const res = await fetch(url);
  if (!res.ok) {
    console.error(`✗ ${url}: HTTP ${res.status}`);
    process.exitCode = 1;
    continue;
  }
  const body = Buffer.from(await res.arrayBuffer());
  for (const dir of dirs) {
    await mkdir(join(root, dir), { recursive: true });
    await writeFile(join(root, dir, name), body);
    console.log(`✓ ${dir}/${name} (${(body.length / 1024).toFixed(0)} KB)`);
  }
}
