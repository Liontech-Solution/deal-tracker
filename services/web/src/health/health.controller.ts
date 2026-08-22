import { Controller, Get, Inject, ServiceUnavailableException } from '@nestjs/common';
import type postgres from 'postgres';

import { PG_CLIENT } from '../database/database.module';

/**
 * Salud del servicio, en **dos** endpoints que contestan a dos preguntas distintas (#540).
 *
 * Tenerlas juntas era la última pieza de una cadena medida sobre un contenedor muerto de QA el
 * 20/08/2026: una consulta del catálogo cruza el `statement_timeout` de 30 s → `CONNECTION_ENDED`
 * → se cae el pool → falla todo lo que toque la base, incluido el `insert into app_user` del
 * camino de autenticación (116 veces, y no es una pantalla: es el peaje de cada petición con
 * sesión) → este controlador da 503 → tres fallos de liveness → kubelet mata la única réplica.
 *
 * Reiniciar el pod no arregla una Postgres compartida que va lenta: la liveness pregunta «¿sigo
 * vivo?» y estaba respondiendo «¿está la base?». La readiness sí quiere saberlo, y por eso se
 * queda como estaba.
 */
@Controller('health')
export class HealthController {
  constructor(@Inject(PG_CLIENT) private readonly sql: postgres.Sql) {}

  /**
   * **Liveness**: que el proceso siga en pie y sirviendo HTTP. No toca la base a propósito.
   *
   * Que esta ruta sea nueva tiene una consecuencia de despliegue que no se ve desde aquí: las
   * probes viven en el repo de manifiestos, en `base/`, y `base` lo heredan los tres entornos.
   * Apuntar la liveness aquí antes de que la imagen con este endpoint esté en QA **y en prod** es
   * un 404 en cada sondeo, o sea CrashLoopBackOff en producción. Por eso entra por el overlay de
   * `dev` y sube a `base` cuando la versión que la trae ya está en prod.
   */
  @Get('live')
  live() {
    return { status: 'ok' };
  }

  /** **Readiness**: además de vivo, en condiciones de contestar — o sea, con la base al otro lado. */
  @Get()
  async check() {
    try {
      await this.sql`SELECT 1`;
    } catch {
      throw new ServiceUnavailableException({ status: 'error', db: 'down' });
    }
    return { status: 'ok', db: 'up' };
  }
}
