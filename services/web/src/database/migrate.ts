/**
 * Migrador de esquema minimalista (paridad con `services/scraper/src/scraper/migrate.py`).
 *
 * Aplica en orden los ficheros `NNNN_*.sql` de `db/migrations` (SQL neutro, contrato
 * compartido) y registra los aplicados en la MISMA tabla `schema_migrations`, por nombre de
 * fichero. Idempotente y compatible con el migrador Python: cualquiera de los dos servicios
 * puede arrancar el esquema sin pisar al otro.
 *
 * Uso: `node dist/database/migrate.js` (o `pnpm migrate`). Config por entorno:
 *   DATABASE_URL          (requerido)
 *   WEB_MIGRATIONS_DIR    (opcional; por defecto db/migrations relativo al repo)
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

function migrationsDir(): string {
  const override = process.env.WEB_MIGRATIONS_DIR;
  if (override && override.trim() !== '') {
    return resolve(override.trim());
  }
  // dist/database/migrate.js -> 4 niveles arriba está la raíz del repo (services/web/dist/database).
  return resolve(__dirname, '../../../../db/migrations');
}

export async function runMigrations(sql: postgres.Sql): Promise<string[]> {
  const dir = migrationsDir();
  const files = readdirSync(dir)
    .filter((f) => f.endsWith('.sql'))
    .sort();

  await sql.unsafe(TRACKING_TABLE);
  const appliedRows = await sql<{ version: string }[]>`SELECT version FROM schema_migrations`;
  const applied = new Set(appliedRows.map((r) => r.version));

  const justApplied: string[] = [];
  for (const file of files) {
    if (applied.has(file)) continue;
    const text = readFileSync(join(dir, file), 'utf8');
    await sql.begin(async (tx) => {
      await tx.unsafe(text);
      await tx`INSERT INTO schema_migrations (version) VALUES (${file})`;
    });
    justApplied.push(file);
  }
  return justApplied;
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
