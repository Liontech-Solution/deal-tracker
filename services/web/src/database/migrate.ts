/**
 * Migrador de esquema minimalista (paridad con `services/scraper/src/scraper/migrate.py`).
 *
 * Aplica en orden los ficheros `NNNN_*.sql` de `db/migrations` (SQL neutro, contrato
 * compartido) y registra los aplicados en la MISMA tabla `schema_migrations`, por nombre de
 * fichero. Idempotente y compatible con el migrador Python: cualquiera de los dos servicios
 * puede arrancar el esquema sin pisar al otro.
 *
 * Serializado con un advisory lock que comparte con el migrador Python (#298): los dos leen el
 * conjunto de aplicadas ANTES del bucle, así que sin él dos procesos que arranquen a la vez ven la
 * misma lista de pendientes e intentan aplicar el mismo fichero.
 *
 * Uso: `node dist/database/migrate.js` (o `pnpm migrate`). Config por entorno:
 *   DATABASE_URL             (requerido)
 *   WEB_MIGRATIONS_DIR       (opcional; por defecto db/migrations relativo al repo)
 *   WEB_MIGRATION_LOCK_WAIT  (opcional; segundos de espera del lock, por defecto 300)
 */
import { readdirSync, readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';

import postgres from 'postgres';

const TRACKING_TABLE = `
  CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
  )
`;

/**
 * Identificador del advisory lock que serializa las migraciones. Tiene que ser el MISMO número que
 * `LOCK_MIGRACIONES` en `services/scraper/src/scraper/migrate.py` o los dos migradores no se ven;
 * lo ata el test de paridad de `services/scraper/tests/test_migrate.py`. El valor es `0x64746d67`,
 * que en ASCII es `dtmg` (deal-tracker migrations): arbitrario, pero reconocible en un `pg_locks`.
 *
 * Los advisory locks son POR BASE DE DATOS, no por cluster (verificado el 13/08/2026), así que
 * `deal_tracker`, `deal_tracker_qa` y `deal_tracker_prod` no se estorban entre sí pese a compartir
 * instancia CNPG con otros cuatro proyectos.
 */
export const LOCK_MIGRACIONES = 1685351783;

/**
 * Segundos que se espera el lock. Generoso a propósito: quien lo retiene está aplicando
 * migraciones, y algunas obligan a un `REINDEX` (0014, 0029) que sobre el catálogo entero puede
 * tardar. Lo que esto acota no es contención, es que el otro migrador haya muerto dejando el lock
 * colgado — y ahí 5 minutos ya es un tope. `0` espera lo que haga falta.
 */
const DEFAULT_LOCK_WAIT_SEC = 300;

function migrationsDir(): string {
  const override = process.env.WEB_MIGRATIONS_DIR;
  if (override && override.trim() !== '') {
    return resolve(override.trim());
  }
  // dist/database/migrate.js -> 4 niveles arriba está la raíz del repo (services/web/dist/database).
  return resolve(__dirname, '../../../../db/migrations');
}

function lockWaitSeconds(): number {
  const raw = process.env.WEB_MIGRATION_LOCK_WAIT;
  if (!raw || raw.trim() === '') return DEFAULT_LOCK_WAIT_SEC;
  const parsed = Number(raw);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : DEFAULT_LOCK_WAIT_SEC;
}

/**
 * Toma el lock de migraciones sobre una conexión FIJA, esperando como mucho `waitSec` segundos.
 *
 * El `lock_timeout` va por `set_config(..., is_local => true)` —o sea `SET LOCAL`— dentro de una
 * transacción que se cierra aquí mismo, así que revierte solo y no queda pisado para el resto de
 * la sesión. El lock es de SESIÓN y sobrevive a ese `commit` (comprobado).
 *
 * Ojo con la transacción a mano: `sql.reserve()` devuelve un objeto que en tiempo de EJECUCIÓN no
 * tiene `.begin()` —solo lo lleva el pool—, aunque los tipos de `postgres` digan que
 * `ReservedSql extends Sql`. Verificado con postgres 3.4.9: `typeof reserved.begin === 'undefined'`.
 */
async function tomarLock(reserved: postgres.ReservedSql, waitSec: number): Promise<void> {
  await reserved.unsafe('begin');
  try {
    if (waitSec > 0) {
      await reserved`SELECT set_config('lock_timeout', ${`${Math.round(waitSec * 1000)}ms`}, true)`;
    }
    await reserved`SELECT pg_advisory_lock(${LOCK_MIGRACIONES})`;
    await reserved.unsafe('commit');
  } catch (err) {
    await reserved.unsafe('rollback');
    throw err;
  }
}

export async function runMigrations(sql: postgres.Sql): Promise<string[]> {
  const dir = migrationsDir();
  const files = readdirSync(dir)
    .filter((f) => f.endsWith('.sql'))
    .sort();

  // Una conexión fija para todo: un advisory lock de sesión vive en SU backend, así que si el
  // cuerpo saliera por otra conexión del pool el lock no cubriría nada. Hoy `max: 1` lo hacía
  // cierto por accidente; esto lo hace cierto por construcción.
  const reserved = await sql.reserve();
  try {
    await tomarLock(reserved, lockWaitSeconds());
    try {
      await reserved.unsafe(TRACKING_TABLE);
      const appliedRows = await reserved<{ version: string }[]>`SELECT version FROM schema_migrations`;
      const applied = new Set(appliedRows.map((r) => r.version));

      const justApplied: string[] = [];
      for (const file of files) {
        if (applied.has(file)) continue;
        const text = readFileSync(join(dir, file), 'utf8');
        await reserved.unsafe('begin');
        try {
          await reserved.unsafe(text);
          await reserved`INSERT INTO schema_migrations (version) VALUES (${file})`;
          await reserved.unsafe('commit');
        } catch (err) {
          await reserved.unsafe('rollback');
          throw err;
        }
        justApplied.push(file);
      }
      return justApplied;
    } finally {
      await reserved`SELECT pg_advisory_unlock(${LOCK_MIGRACIONES})`;
    }
  } finally {
    reserved.release();
  }
}

async function main(): Promise<void> {
  const url = process.env.DATABASE_URL;
  if (!url) {
    throw new Error('Falta DATABASE_URL');
  }
  // onnotice compacto: los NOTICE de Postgres (p.ej. "IF NOT EXISTS ... skipping") no deben
  // volcar el objeto entero por consola.
  const sql = postgres(url, {
    max: 1,
    onnotice: (n) => {
      if (n.severity !== 'NOTICE') console.warn(`[pg ${n.severity}] ${n.message}`);
    },
  });
  try {
    const applied = await runMigrations(sql);
    if (applied.length === 0) {
      console.log('Sin migraciones pendientes.');
    } else {
      console.log(`Aplicadas ${applied.length} migración(es): ${applied.join(', ')}`);
    }
  } finally {
    await sql.end();
  }
}

// Solo ejecuta si se invoca directamente (no al importarlo desde un test).
if (require.main === module) {
  main().catch((err) => {
    console.error(err);
    process.exit(1);
  });
}
