import { Module } from '@nestjs/common';
import { ConfigModule } from '@nestjs/config';

import { validateJobEnv } from '../config/configuration';
import { DatabaseModule } from '../database/database.module';
import { MatchingModule } from '../matching/matching.module';

/**
 * Grafo de DI del job de matching. Contexto mínimo: reutiliza la DI (cliente de Telegram, Drizzle)
 * sin levantar HTTP, auth ni estáticos. `TelegramPollingService` viaja en `TelegramModule` pero
 * queda inerte: sin `TELEGRAM_POLLING_ENABLED` no arranca ningún bucle.
 *
 * Vive en su propio fichero, separado del entrypoint `matching.job.ts`, para que los tests puedan
 * montar **este** módulo —el de verdad, no una copia de sus `imports` que se desincronizaría sola—
 * sin importar el CLI, cuyo arranque depende del guard `require.main === module` y del build
 * CommonJS que corre en el cluster. Ver #239: hasta entonces nada montaba este grafo en ningún
 * test, así que una dependencia irresoluble pasaba el CI entero y reventaba en el CronJob.
 *
 * ⚠️ `ConfigModule.forRoot()` valida el entorno al **evaluarse el decorador**, o sea al importar
 * este fichero: quien lo importe sin `DATABASE_URL` puesta se lleva el fallo en el import.
 */
@Module({
  imports: [
    ConfigModule.forRoot({ isGlobal: true, cache: true, validate: validateJobEnv }),
    DatabaseModule,
    MatchingModule,
  ],
})
export class MatchingJobModule {}
