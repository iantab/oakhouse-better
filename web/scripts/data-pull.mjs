// Download the latest generated data from the live site, so local dev works without running the scraper.
//   npm run data:pull                       (from https://iantab.github.io/oakhouse-better/)
//   DATA_URL=http://localhost:4321/oakhouse-better/ npm run data:pull
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

const base = (process.env.DATA_URL ?? 'https://iantab.github.io/oakhouse-better/').replace(/\/?$/, '/');
const root = join(import.meta.dirname, '..');
const files = [
  ['properties.json', 'src/data'],
  ['areas.json', 'src/data'],
  ['codes.json', 'src/data'],
  ['amenities.json', 'src/data'],
  ['meta.json', 'src/data'],
  ['search-index.json', 'public/data'],
  ['properties.json', 'public/data'],
];

for (const [name, dir] of files) {
  const url = `${base}data/${name}`;
  const res = await fetch(url);
  if (!res.ok) {
    console.error(`✗ ${url}: HTTP ${res.status}`);
    process.exitCode = 1;
    continue;
  }
  await mkdir(join(root, dir), { recursive: true });
  const body = Buffer.from(await res.arrayBuffer());
  await writeFile(join(root, dir, name), body);
  console.log(`✓ ${dir}/${name} (${(body.length / 1024).toFixed(0)} KB)`);
}
