import swc from 'unplugin-swc';
import { defineConfig } from 'vitest/config';

// NestJS usa metadatos de decoradores en runtime; SWC los emite para los tests.
export default defineConfig({
  test: {
    globals: true,
    root: './',
    // `frontend/`: solo los helpers puros de la SPA (strings, formatos). No hay jsdom ni
    // testing-library montados, así que un spec de aquí no puede renderizar componentes.
    include: ['test/**/*.spec.ts', 'src/**/*.spec.ts', 'frontend/src/**/*.spec.ts'],
    environment: 'node',
    // Los specs de integración comparten una única Postgres: ejecutarlos en serie evita
    // choques de DDL (migraciones/TRUNCATE) entre ficheros.
    fileParallelism: false,
  },
  plugins: [
    swc.vite({
      module: { type: 'es6' },
    }),
  ],
});
