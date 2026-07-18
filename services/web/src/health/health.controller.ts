import { Controller, Get, Inject, ServiceUnavailableException } from '@nestjs/common';
import type postgres from 'postgres';

import { PG_CLIENT } from '../database/database.module';

/** Liveness/readiness. Incluye un ping a la Postgres compartida. */
@Controller('health')
export class HealthController {
  constructor(@Inject(PG_CLIENT) private readonly sql: postgres.Sql) {}

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
