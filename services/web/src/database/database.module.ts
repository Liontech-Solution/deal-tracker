import { Global, Inject, Module } from '@nestjs/common';
import type { OnModuleDestroy } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { drizzle } from 'drizzle-orm/postgres-js';
import postgres from 'postgres';

import type { EnvConfig } from '../config/configuration';
import { schema } from './schema';

/** Token DI del cliente Drizzle. Se inyecta con `@Inject(DRIZZLE)`. */
export const DRIZZLE = Symbol('DRIZZLE');

/** Cliente postgres.js subyacente, expuesto para health-checks y cierre limpio. */
export const PG_CLIENT = Symbol('PG_CLIENT');

export type Database = ReturnType<typeof drizzle<typeof schema>>;

/**
 * Provee un único cliente Drizzle (sobre postgres.js) para toda la app. Global para no tener
 * que importar el módulo en cada feature. El pool se cierra al apagar el módulo.
 */
@Global()
@Module({
  providers: [
    {
      provide: PG_CLIENT,
      inject: [ConfigService],
      useFactory: (config: ConfigService<EnvConfig, true>) => {
        const url = config.get('DATABASE_URL', { infer: true });
        // max moderado: el cluster son Raspberry Pi y la Postgres es compartida.
        //
        // `statement_timeout` es cinturón, no el arreglo de nada (#515). La base viene con
        // `statement_timeout = 0`, o sea sin tope: cuando el planificador eligió mal, la consulta
        // del catálogo corrió **239 s** mientras la pasarela cerraba el HTTP con un 524 a los 125 s
        // — y seguía corriendo en Postgres después, quemando CPU de una base que comparten los tres
        // entornos por una respuesta que ya no iba a leer nadie.
        //
        // 30 s es holgado a propósito: la consulta más lenta que este servicio hace legítimamente
        // ronda los 3 s (el suelo del camino vivo, color+tienda), así que esto no puede cortar nada
        // sano — solo convierte un cuelgue indefinido en un error rápido. No sustituye a arreglar
        // el plan, y por eso va acompañado y no en lugar del MATERIALIZED de `listProducts()`.
        return postgres(url, { max: 10, connection: { statement_timeout: 30_000 } });
      },
    },
    {
      provide: DRIZZLE,
      inject: [PG_CLIENT],
      useFactory: (client: postgres.Sql) => drizzle(client, { schema }),
    },
  ],
  exports: [DRIZZLE, PG_CLIENT],
})
export class DatabaseModule implements OnModuleDestroy {
  constructor(@Inject(PG_CLIENT) private readonly client: postgres.Sql) {}

  /**
   * Cierra el pool al apagar. Imprescindible para los CLI (`src/jobs/*`): sin esto las conexiones
   * mantienen vivo el event loop y el proceso **nunca termina** — un CronJob se quedaría colgado
   * indefinidamente en vez de completar.
   */
  async onModuleDestroy(): Promise<void> {
    await this.client.end();
  }
}
