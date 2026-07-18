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

  it('aplica 0001–0005 y crea las tablas del contrato', async () => {
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
      ]),
    );

    const tables = await sql<{ table_name: string }[]>`
      SELECT table_name FROM information_schema.tables
      WHERE table_schema = 'public' AND table_name IN ('app_user', 'interest', 'notification')`;
    expect(tables.map((t) => t.table_name).sort()).toEqual(['app_user', 'interest', 'notification']);
  });

  it('es idempotente (re-ejecutar no aplica nada nuevo)', async () => {
    const applied = await runMigrations(sql);
    expect(applied).toEqual([]);
  });
});
