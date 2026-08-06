/**
 * Job de matching de ofertas (CronJob de k3s, tras el scraper).
 *
 * Evalúa los precios recién scrapeados contra los intereses de los usuarios y manda un resumen
 * por Telegram a quien tenga una bajada real. Idempotente: el lote son las pasadas que aún no se
 * han evaluado (`job_state` + `matching_scanned_run`) y el UNIQUE de `notification` impide avisar
 * dos veces del mismo evento de precio.
 *
 * Uso: `node dist/jobs/matching.job.js [--dry-run]` (o `pnpm job:matching`). Config por entorno:
 *   DATABASE_URL         (requerido)
 *   TELEGRAM_BOT_TOKEN   (opcional; sin él se fuerza dry-run — no hay forma de avisar)
 *
 * `--dry-run` registra qué avisos habría mandado y **no cambia nada**: ni `notification` ni marca
 * de agua. Es lo que corre en `dev`, donde el bot está apagado.
 */
import { Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { NestFactory } from '@nestjs/core';

import type { EnvConfig } from '../config/configuration';
import { MatchingService } from '../matching/matching.service';
import { MatchingJobModule } from './matching-job.module';

async function main(): Promise<void> {
  const logger = new Logger('MatchingJob');
  const app = await NestFactory.createApplicationContext(MatchingJobModule, {
    logger: ['log', 'warn', 'error'],
  });

  try {
    const config = app.get(ConfigService<EnvConfig, true>);
    const asked = process.argv.includes('--dry-run');
    const noBot = config.get('TELEGRAM_BOT_TOKEN', { infer: true }) === '';
    if (noBot && !asked) {
      logger.warn('Sin TELEGRAM_BOT_TOKEN: no hay forma de avisar, se fuerza --dry-run');
    }

    const summary = await app.get(MatchingService).run(asked || noBot);
    // Un aviso que no se pudo entregar debe verse en el estado del Job de k8s.
    if (summary.failedSends > 0) {
      throw new Error(`${summary.failedSends} aviso(s) no se pudieron entregar`);
    }
  } finally {
    await app.close();
  }
}

// Solo ejecuta si se invoca directamente (no al importarlo desde un test).
if (require.main === module) {
  main().catch((err) => {
    console.error(err);
    process.exit(1);
  });
}
