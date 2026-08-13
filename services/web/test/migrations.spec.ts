import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import type postgres from 'postgres';

import { LOCK_MIGRACIONES, runMigrations } from '../src/database/migrate';
import { makeSql, makeSqlAt, TEST_DB } from './helpers';

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

  it('suelta el lock al terminar, para no arrastrarlo a lo que venga después', async () => {
    await runMigrations(sql);
    const [{ n }] = await sql<{ n: number }[]>`
      SELECT count(*)::int AS n FROM pg_locks
       WHERE locktype = 'advisory' AND objid = ${LOCK_MIGRACIONES}`;
    expect(n).toBe(0);
  });

  // La carrera del cuerpo de #298: los dos migradores leen el conjunto de aplicadas ANTES del
  // bucle, así que sin lock los dos ven la misma pendiente y los dos la aplican. Comprobado que
  // sin él esto NO es teórico: el perdedor muere con un `UniqueViolation` sobre el `CREATE TABLE`.
  it('dos migradores concurrentes aplican la migración una sola vez', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'dt-298-'));
    const nombre = '9999_test_concurrencia_298.sql';
    const tabla = 't_298_concurrencia_web';
    // El `pg_sleep` ensancha la ventana: la carrera existe igual sin él, pero que el test la
    // pillara dependería del azar del planificador.
    writeFileSync(join(dir, nombre), `CREATE TABLE ${tabla} (id int); SELECT pg_sleep(0.5);`);

    const previo = process.env.WEB_MIGRATIONS_DIR;
    process.env.WEB_MIGRATIONS_DIR = dir;
    // Dos pools independientes: un advisory lock de sesión vive en su backend, así que compartir
    // pool no ejercería nada.
    const a = makeSqlAt(TEST_DB as string);
    const b = makeSqlAt(TEST_DB as string);
    try {
      const [aplicadasA, aplicadasB] = await Promise.all([runMigrations(a), runMigrations(b)]);

      // Ninguno falla, y la aplicó exactamente uno: listas disjuntas y su unión es la pendiente.
      expect([...aplicadasA, ...aplicadasB].sort()).toEqual([nombre]);

      const [{ n }] = await sql<{ n: number }[]>`
        SELECT count(*)::int AS n FROM schema_migrations WHERE version = ${nombre}`;
      expect(n).toBe(1);
    } finally {
      if (previo === undefined) delete process.env.WEB_MIGRATIONS_DIR;
      else process.env.WEB_MIGRATIONS_DIR = previo;
      await a.end();
      await b.end();
      await sql.unsafe(`DROP TABLE IF EXISTS ${tabla}`);
      await sql`DELETE FROM schema_migrations WHERE version = ${nombre}`;
    }
  });
});
