import { defineConfig } from 'vite';

export default defineConfig({
  base: '/',
  server: {
    proxy: { '/api': 'http://127.0.0.1:7860' },
  },
  build: {
    outDir: '../src/echoscript/web_assets',
    emptyOutDir: true,
  },
});
