import { Test } from '@nestjs/testing';
import type { TestingModule } from '@nestjs/testing';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

import { MatchingService } from '../src/matching/matching.service';
import { TEST_DB } from './helpers';

/**
 * Que el contenedor de Nest sepa construir los servicios de los jobs de `src/jobs/`.
 *
 * Los demás specs de matching construyen el servicio a mano (`new MatchingService(db, telegram)`),
 * así que **el contenedor nunca lo resolvía en ningún test** (#239): un parámetro de constructor
 * que Nest no supiera resolver —un primitivo, un provider de otro módulo, un token sin registrar—
 * pasaba `lint`, `typecheck`, `test` y el CI entero en verde, y reventaba al arrancar el CronJob
 * en el cluster, con el Job en `Error` sin haber evaluado nada. Este spec es lo que convierte ese
 * fallo en rojo aquí.
 *
 * **No necesita Postgres, y por eso NO se salta** como el resto de specs de integración:
 * `postgres(url, { max: 10 })` es perezoso —no abre conexión hasta la primera consulta— y montar
 * el contenedor no ejecuta ninguna. Se usa `TEST_DATABASE_URL` si está por higiene, pero una URL
 * cualquiera bien formada vale igual. Que corra **siempre**, también en un `pnpm test` a pelo sin
 * base, es justo la garantía que se busca: el agujero que tapa es de CI, no de datos.
 *
 * El día que haya un segundo job en `src/jobs/`, su módulo se añade aquí.
 */
describe('jobs · el contenedor de Nest resuelve sus servicios', () => {
  let ctx: TestingModule;

  beforeAll(async () => {
    process.env.NODE_ENV = 'test';
    // Sin esto el import de abajo falla: `ConfigModule.forRoot()` valida el entorno al evaluarse
    // el decorador del módulo, o sea al cargar el fichero. Mismo motivo que el import diferido de
    // `AppModule` en `helpers.ts`.
    process.env.DATABASE_URL = TEST_DB ?? 'postgresql://x:x@127.0.0.1:5432/x';
    // Deliberadamente NO se pone `TELEGRAM_POLLING_ENABLED`: así `TelegramPollingService` sale por
    // su return temprano y no deja un bucle de long-polling vivo detrás del spec.

    const { MatchingJobModule } = await import('../src/jobs/matching-job.module');
    ctx = await Test.createTestingModule({ imports: [MatchingJobModule] }).compile();
    await ctx.init();
  });

  afterAll(async () => {
    // Cierra el pool (`DatabaseModule.onModuleDestroy`); sin esto el event loop se queda vivo.
    await ctx?.close();
  });

  it('construye MatchingService con todas sus dependencias', () => {
    const service = ctx.get(MatchingService);
    expect(service).toBeInstanceOf(MatchingService);
    expect(typeof service.run).toBe('function');
  });
});
