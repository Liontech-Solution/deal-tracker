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

  it('0041 crea `favorite` sin FK al producto, con su clave de idempotencia y su índice', async () => {
    await runMigrations(sql);

    const cols = await sql<{ column_name: string }[]>`
      SELECT column_name FROM information_schema.columns
      WHERE table_schema = 'public' AND table_name = 'favorite'`;
    expect(cols.map((c) => c.column_name).sort()).toEqual([
      'created_at',
      'id',
      'product_id',
      'user_id',
    ]);

    // Las dos claves foráneas NO son simétricas y esa asimetría es el diseño (#435): el favorito
    // se va con el usuario, pero NO cuelga del producto — tiene que sobrevivir a una baja y a su
    // resurrección, igual que `interest.product_id`. Una FK a `product` aquí haría que cualquier
    // limpieza futura del catálogo se llevara por delante lo que el usuario guardó.
    const fks = await sql<{ column_name: string; foreign_table: string }[]>`
      SELECT kcu.column_name, ccu.table_name AS foreign_table
      FROM information_schema.table_constraints tc
      JOIN information_schema.key_column_usage kcu
        ON kcu.constraint_name = tc.constraint_name
      JOIN information_schema.constraint_column_usage ccu
        ON ccu.constraint_name = tc.constraint_name
      WHERE tc.table_name = 'favorite' AND tc.constraint_type = 'FOREIGN KEY'`;
    expect(fks).toEqual([{ column_name: 'user_id', foreign_table: 'app_user' }]);

    // La que hace idempotente el alta: marcar dos veces el mismo corazón no puede duplicar.
    const uniq = await sql<{ constraint_name: string }[]>`
      SELECT constraint_name FROM information_schema.table_constraints
      WHERE table_name = 'favorite' AND constraint_type = 'UNIQUE'`;
    expect(uniq.map((u) => u.constraint_name)).toEqual(['favorite_user_product_uniq']);

    const idx = await sql<{ indexname: string }[]>`
      SELECT indexname FROM pg_indexes WHERE tablename = 'favorite' AND indexname = 'ix_favorite_user'`;
    expect(idx).toHaveLength(1);
  });

  it('0044 crea `invitation` con la unicidad PARCIAL del correo vivo y el cupo a cero', async () => {
    await runMigrations(sql);

    const cols = await sql<{ column_name: string }[]>`
      SELECT column_name FROM information_schema.columns
      WHERE table_schema = 'public' AND table_name = 'invitation'`;
    expect(cols.map((c) => c.column_name).sort()).toEqual([
      'accepted_at',
      'accepted_user_id',
      'created_at',
      'email',
      'expires_at',
      'id',
      'inviter_user_id',
      'revoked_at',
      'token_hash',
    ]);

    // Las dos FK apuntan a la misma tabla y NO se borran igual, que es el diseño (#546): la
    // invitación se va con quien invitó —si esa cuenta muere, su invitación pendiente no debe
    // poder canjearse— pero sobrevive a la cuenta que nació de ella, porque gastar cupo y mandar
    // un correo son hechos ocurridos. Un `cascade` en la segunda borraría el rastro del alta.
    const fks = await sql<{ column_name: string; foreign_table: string; delete_rule: string }[]>`
      SELECT kcu.column_name, ccu.table_name AS foreign_table, rc.delete_rule
      FROM information_schema.table_constraints tc
      JOIN information_schema.key_column_usage kcu
        ON kcu.constraint_name = tc.constraint_name
      JOIN information_schema.constraint_column_usage ccu
        ON ccu.constraint_name = tc.constraint_name
      JOIN information_schema.referential_constraints rc
        ON rc.constraint_name = tc.constraint_name
      WHERE tc.table_name = 'invitation' AND tc.constraint_type = 'FOREIGN KEY'
      ORDER BY kcu.column_name`;
    expect(fks).toEqual([
      { column_name: 'accepted_user_id', foreign_table: 'app_user', delete_rule: 'SET NULL' },
      { column_name: 'inviter_user_id', foreign_table: 'app_user', delete_rule: 'CASCADE' },
    ]);

    // Dos invitaciones no pueden compartir secreto, y el canje busca por aquí.
    const uniq = await sql<{ constraint_name: string }[]>`
      SELECT constraint_name FROM information_schema.table_constraints
      WHERE table_name = 'invitation' AND constraint_type = 'UNIQUE'`;
    expect(uniq.map((u) => u.constraint_name)).toEqual(['invitation_token_hash_uniq']);

    // Y la que de verdad importa: que el índice del correo sea PARCIAL. Uno total pasaría todos
    // los asserts de arriba y cambiaría la regla en silencio — prohibiría reinvitar a quien
    // declinó y a quien caducó, que es justo lo contrario de lo que la 0044 decide.
    const [idx] = await sql<{ indexdef: string }[]>`
      SELECT indexdef FROM pg_indexes
      WHERE tablename = 'invitation' AND indexname = 'ux_invitation_email_viva'`;
    expect(idx).toBeDefined();
    expect(idx.indexdef).toContain('UNIQUE');
    expect(idx.indexdef).toContain('WHERE ((accepted_at IS NULL) AND (revoked_at IS NULL))');

    // El cupo: `0` es la política, no un relleno. Nadie invita hasta que se le da cupo a mano.
    const [cupo] = await sql<{ is_nullable: string; column_default: string }[]>`
      SELECT is_nullable, column_default FROM information_schema.columns
      WHERE table_schema = 'public' AND table_name = 'app_user'
        AND column_name = 'invites_remaining'`;
    expect(cupo).toBeDefined();
    expect(cupo.is_nullable).toBe('NO');
    expect(cupo.column_default).toBe('0');

    // El CHECK no protege del camino normal —`UPDATE ... WHERE invites_remaining > 0` ya resuelve
    // la carrera—, sino del reparto de cupo A MANO por SQL en prod, que es el que existe de veras.
    const chk = await sql<{ constraint_name: string }[]>`
      SELECT constraint_name FROM information_schema.table_constraints
      WHERE table_name = 'app_user' AND constraint_type = 'CHECK'
        AND constraint_name = 'app_user_invites_remaining_chk'`;
    expect(chk).toHaveLength(1);
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
