import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

export default defineConfig({
  base: '/',
  plugins: [svelte()],
  server: {
    host: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8765',
        changeOrigin: true,
        // Keep the SSE connection open / streaming.
        ws: false
      }
    }
  },
  build: {
    outDir: 'dist',
    sourcemap: true
  }
});
