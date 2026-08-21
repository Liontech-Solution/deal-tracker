import { ValidationPipe } from '@nestjs/common';
import { Test } from '@nestjs/testing';
import type { INestApplication } from '@nestjs/common';
import postgres from 'postgres';
import { describe, expect, it } from 'vitest';

import { runMigrations } from '../src/database/migrate';

/** URL de la Postgres de test. Si no está, los specs de integración se saltan (como el scraper). */
export const TEST_DB = process.env.TEST_DATABASE_URL;

/**
 * URL de una segunda Postgres **con ctype `C`**, que es el de la base del cluster
 * (`deal_tracker` y `deal_tracker_qa` son `UTF8 | C | C`, verificado el 02/08/2026).
 *
 * No es un lujo ni un duplicado: bajo ctype `C`, `lower()` **no baja las letras acentuadas**
 * (`lower('ÍNDIGO') = 'Índigo'`), y de ahí sale #105 — 748 variantes con la canónica a medias y dos
 * chips partidos en la faceta. El defecto llevaba desde la 0014 sin verse porque el único sitio
 * donde se comprobaba —CI, con el locale por defecto de `postgres:16-alpine`— lo tapa. Un test que
 * solo corra con el locale bueno no prueba lo que el cluster hace.
 *
 * Se crea con `TEMPLATE template0`, que es obligatorio para cambiar el locale de una base:
 *
 *     CREATE DATABASE deal_tracker_ctype_c
 *       TEMPLATE template0 ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C';
 */
export const TEST_DB_CTYPE_C = process.env.TEST_DATABASE_URL_CTYPE_C;

export interface BaseDeTest {
  /** Etiqueta que sale en el nombre del suite, para saber cuál de las dos falló. */
  nombre: string;
  url: string;
}

/**
 * Las bases contra las que se ejercitan las funciones del esquema (`size_canon`, `color_canon` y el
 * plegado de la búsqueda). Se salta sola la que no esté configurada, igual que hace `TEST_DB` con
 * el resto de specs de integración.
 */
export const BASES_CANON: BaseDeTest[] = [
  { nombre: 'locale por defecto (como CI)', url: TEST_DB },
  { nombre: 'ctype C (como el cluster)', url: TEST_DB_CTYPE_C },
].filter((b): b is BaseDeTest => Boolean(b.url));

/**
 * Los specs que usan `describe.each(BASES_CANON)` no declaran NINGÚN suite cuando no hay base
 * configurada, y eso vitest no lo salta: lo da por error («No test suite found in file»). Esto
 * declara uno saltado en ese caso, para que el comportamiento sea el mismo que el `skipIf(!TEST_DB)`
 * del resto de specs de integración: sin Postgres, se saltan; nunca fallan.
 */
export function saltarSiNoHayBase(nombre: string): void {
  if (BASES_CANON.length === 0) {
    describe.skip(nombre, () => {
      it('necesita TEST_DATABASE_URL (y TEST_DATABASE_URL_CTYPE_C para el caso del cluster)', () => {
        expect(true).toBe(true);
      });
    });
  }
}

export function makeSql(): postgres.Sql {
  if (!TEST_DB) throw new Error('TEST_DATABASE_URL no definido');
  return makeSqlAt(TEST_DB);
}

export function makeSqlAt(url: string): postgres.Sql {
  return postgres(url, { max: 1, onnotice: () => {} });
}

/** Aplica migraciones (idempotente) y deja las tablas vacías con identidades reiniciadas. */
export async function resetSchema(sql: postgres.Sql): Promise<void> {
  await runMigrations(sql);
  // `job_state` incluida: si la marca de agua sobrevive a un reset, los ids reiniciados quedan
  // por debajo de ella y el job no vería ningún lote. `matching_scanned_run` por lo mismo y peor:
  // no hay ningún suelo que lo delate, así que una pasada "ya procesada" de otro spec dejaría el
  // lote vacío sin que nada lo explicase.
  await sql.unsafe(`
    TRUNCATE notification, interest, favorite, invitation, app_user, job_state, matching_scanned_run,
             price_history, product_image, variant, product_agg, product, scrape_run, retailer
    RESTART IDENTITY CASCADE
  `);
}

/**
 * Repuebla `product_agg`, el agregado por producto que lee el catálogo (migración 0035, #314).
 *
 * **Hay que llamarlo en todo spec que siembre catálogo a mano y luego lea el listado.** En
 * producción lo hace `ingest.py` al final de cada pasada, que es el único sitio donde se escribe
 * `price_history`; un test que inserta filas por su cuenta se salta ese paso, y el catálogo le
 * devolverá menos productos de los que sembró —o ninguno— sin ningún error de por medio.
 *
 * Se nota enseguida (el spec se pone en rojo con listas vacías), pero cuesta un rato entender por
 * qué si no se sabe que esto existe. Va después de TODA la siembra, no en medio.
 */
export async function refrescarAgregado(sql: postgres.Sql): Promise<void> {
  await sql`SELECT refresh_product_agg()`;
}

export interface SeedIds {
  retailerId: number;
  productId: number;
  variantId: number;
}

/** Foto del producto sembrado (hotlink al CDN de la tienda, como la que guarda el scraper). */
export const SEED_IMAGE_URL = 'https://static.example/p/ZARA-1.jpg?ts=1';

/**
 * Siembra un catálogo mínimo: 1 tienda, 1 producto (niña/zapatería/zapatos), 1 variante con precio.
 *
 * El producto va marcado como barefoot `si` a propósito: desde #30 el catálogo esconde por defecto
 * el calzado no respetuoso, así que un seed sin marcar sería invisible para casi todos los specs y
 * los haría fallar por una razón que no es la que están comprobando.
 */
export async function seedCatalog(sql: postgres.Sql): Promise<SeedIds> {
  const [r] = await sql<{ id: number }[]>`
    INSERT INTO retailer (slug, name, base_url)
    VALUES ('zara', 'Zara', 'https://www.zara.com')
    RETURNING id`;
  const [p] = await sql<{ id: number }[]>`
    INSERT INTO product (retailer_id, retailer_product_id, name, gender, section, category,
                         barefoot, url, image_url)
    VALUES (${r.id}, 'ZARA-1', 'Botas niña', 'niña', 'zapateria', 'zapatos', 'si', 'https://x/1',
            ${SEED_IMAGE_URL})
    RETURNING id`;
  const [v] = await sql<{ id: number }[]>`
    INSERT INTO variant (product_id, retailer_variant_id, size, color, sku)
    VALUES (${p.id}, 'ZARA-1-24-rojo', '24', 'rojo', 'SKU24')
    RETURNING id`;
  // Dos puntos de precio: el último (más reciente) es 19.99.
  //
  // La DISTANCIA entre los dos está encajada entre dos límites, y moverla rompe uno u otro:
  //
  //  - por abajo, `REAL_EVIDENCE_DAYS` (14, #436): con menos cobertura la bajada se etiqueta
  //    `reciente` en vez de `real`, porque el precio del que baja lo habríamos visto una sola vez.
  //    Con los 2 días que tenía este seed, los specs del catálogo veían `reciente`.
  //  - por arriba, la ventana del interés que siembra `matching.e2e.spec.ts` (`window_days: 30`):
  //    a 30 días exactos el punto anterior se sale de la ventana, `recent_min` deja de existir y
  //    el job no detecta la bajada. Con 30 caía el spec de la pasada rezagada de #240.
  //
  // 20 despeja los dos. Para ejercer el lado `reciente` hay un producto sembrado a propósito en
  // `catalog.e2e.spec.ts`, que no depende de este seed.
  await sql`
    INSERT INTO price_history (variant_id, price, list_price, discount_pct, in_stock, scraped_at)
    VALUES (${v.id}, 39.99, 39.99, 0, true, now() - interval '20 days'),
           (${v.id}, 19.99, 39.99, 50, true, now())`;
  return { retailerId: r.id, productId: p.id, variantId: v.id };
}

/** Inserta un usuario y devuelve el principal que un guard de test pondrá en `req.user`. */
export async function seedUser(sql: postgres.Sql, sub = 'kc-sub-test') {
  const [u] = await sql<{ id: number }[]>`
    INSERT INTO app_user (keycloak_sub, email, display_name)
    VALUES (${sub}, 'test@example.com', 'Test User')
    RETURNING id`;
  return { id: u.id, keycloakSub: sub, email: 'test@example.com', displayName: 'Test User' };
}

/**
 * Un proveedor doblado para `makeApp`. Existe para los clientes que hablan con otro sistema —la
 * Admin API de Keycloak (#548) y Resend (#547)—, que no se pueden ejercer en un test: uno crearía
 * usuarios de verdad en el realm y el otro gastaría envíos reales. Doblarlos es lo que permite
 * probar el alta entera sin Keycloak vivo (#549).
 */
export interface ProviderDoblado {
  provide: unknown;
  useValue: unknown;
}

/** Levanta la app Nest con el guard de auth opcionalmente sobreescrito para inyectar un usuario. */
export async function makeApp(
  fakeUser?: {
    id: number;
    keycloakSub: string;
    email: string | null;
    displayName: string | null;
  },
  doblados: ProviderDoblado[] = [],
): Promise<INestApplication> {
  process.env.NODE_ENV = 'test';
  process.env.DATABASE_URL = TEST_DB!;

  // Import diferido: AppModule lee el entorno al cargarse.
  const { AppModule } = await import('../src/app.module');
  const { JwtAuthGuard } = await import('../src/auth/jwt-auth.guard');

  const builder = Test.createTestingModule({ imports: [AppModule] });
  if (fakeUser) {
    builder.overrideGuard(JwtAuthGuard).useValue({
      canActivate: (ctx: {
        switchToHttp: () => { getRequest: () => { user?: unknown } };
      }) => {
        ctx.switchToHttp().getRequest().user = fakeUser;
        return true;
      },
    });
  }
  for (const { provide, useValue } of doblados) {
    builder.overrideProvider(provide).useValue(useValue);
  }
  const moduleRef = await builder.compile();

  const app = moduleRef.createNestApplication();
  app.setGlobalPrefix('api');
  app.useGlobalPipes(
    new ValidationPipe({ transform: true, whitelist: true, forbidNonWhitelisted: true }),
  );
  await app.init();
  return app;
}
