// @ts-check
import { defineConfig } from 'astro/config';
import tailwindcss from '@tailwindcss/vite';

// Deployed to GitHub Pages at https://iantab.github.io/oakhouse-better/
export default defineConfig({
  site: 'https://iantab.github.io',
  base: '/oakhouse-better',
  output: 'static',
  trailingSlash: 'always',
  vite: {
    plugins: [tailwindcss()],
  },
});
