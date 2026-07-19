import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// SPA servida en producción por NestJS desde `dist/`. En dev, proxy de `/api` al backend
// (NestJS en :3000) para poder desarrollar con recarga en caliente contra la API real.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.API_PROXY_TARGET ?? 'http://localhost:3000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
});
