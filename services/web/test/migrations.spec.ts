import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import type postgres from 'postgres';

import { runMigrations } from '../src/database/migrate';
import { makeSql, TEST_DB } from './helpers';

// Sin BD de test no se puede verificar la ingesta; se salta (igual que el scraper).
describe.skipIf(!TEST_DB)('migraciones', () => {
  let sql: postgres.Sql;

  beforeAll(() => {
    sql = makeSql();
  });

  afterAll(async () => {
    await sql.end();
  });

  it('aplica 0001–0007 y crea las tablas del contrato', async () => {
    await runMigrations(sql);
    const versions = await sql<{ version: string }[]>`SELECT version FROM schema_migrations ORDER BY version`;
    const names = versions.map((v) => v.version);
    expect(names).toEqual(
      expect.arrayContaining([
        '0001_init.sql',
        '0002_add_listing_signature.sql',
        '0003_app_user.sql',
        '0004_interest.sql',
        '0005_notification.sql',
        '0006_telegram_link.sql',
        '0007_job_state.sql',
      ]),
    );

    const tables = await sql<{ table_name: string }[]>`
      SELECT table_name FROM information_schema.tables
      WHERE table_schema = 'public'
        AND table_name IN ('app_user', 'interest', 'notification', 'job_state')`;
    expect(tables.map((t) => t.table_name).sort()).toEqual([
      'app_user',
      'interest',
      'job_state',
      'notification',
    ]);

    // 0006 amplía app_user con las columnas del vínculo de Telegram.
    const cols = await sql<{ column_name: string }[]>`
      SELECT column_name FROM information_schema.columns
      WHERE table_schema = 'public' AND table_name = 'app_user'
        AND column_name LIKE 'telegram%'`;
    expect(cols.map((c) => c.column_name).sort()).toEqual([
      'telegram_chat_id',
      'telegram_link_token',
      'telegram_link_token_expires_at',
      'telegram_linked_at',
      'telegram_username',
    ]);
  });

  it('es idempotente (re-ejecutar no aplica nada nuevo)', async () => {
    const applied = await runMigrations(sql);
    expect(applied).toEqual([]);
  });
});
