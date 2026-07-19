import { ValidationPipe } from '@nestjs/common';
import { Test } from '@nestjs/testing';
import type { INestApplication } from '@nestjs/common';
import postgres from 'postgres';

import { runMigrations } from '../src/database/migrate';

/** URL de la Postgres de test. Si no está, los specs de integración se saltan (como el scraper). */
export const TEST_DB = process.env.TEST_DATABASE_URL;

export function makeSql(): postgres.Sql {
  if (!TEST_DB) throw new Error('TEST_DATABASE_URL no definido');
  return postgres(TEST_DB, { max: 1, onnotice: () => {} });
}

/** Aplica migraciones (idempotente) y deja las tablas vacías con identidades reiniciadas. */
export async function resetSchema(sql: postgres.Sql): Promise<void> {
  await runMigrations(sql);
  await sql.unsafe(`
    TRUNCATE notification, interest, app_user,
             price_history, variant, product, scrape_run, retailer
    RESTART IDENTITY CASCADE
  `);
}

export interface SeedIds {
  retailerId: number;
  productId: number;
  variantId: number;
}

/** Siembra un catálogo mínimo: 1 tienda, 1 producto (niña/zapatería/zapatos), 1 variante con precio. */
export async function seedCatalog(sql: postgres.Sql): Promise<SeedIds> {
  const [r] = await sql<{ id: number }[]>`
    INSERT INTO retailer (slug, name, base_url)
    VALUES ('zara', 'Zara', 'https://www.zara.com')
    RETURNING id`;
  const [p] = await sql<{ id: number }[]>`
    INSERT INTO product (retailer_id, retailer_product_id, name, gender, section, category, url)
    VALUES (${r.id}, 'ZARA-1', 'Botas niña', 'niña', 'zapateria', 'zapatos', 'https://x/1')
    RETURNING id`;
  const [v] = await sql<{ id: number }[]>`
    INSERT INTO variant (product_id, retailer_variant_id, size, color, sku)
    VALUES (${p.id}, 'ZARA-1-24-rojo', '24', 'rojo', 'SKU24')
    RETURNING id`;
  // Dos puntos de precio: el último (más reciente) es 19.99.
  await sql`
    INSERT INTO price_history (variant_id, price, list_price, discount_pct, in_stock, scraped_at)
    VALUES (${v.id}, 39.99, 39.99, 0, true, now() - interval '2 days'),
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

/** Levanta la app Nest con el guard de auth opcionalmente sobreescrito para inyectar un usuario. */
export async function makeApp(fakeUser?: {
  id: number;
  keycloakSub: string;
  email: string | null;
  displayName: string | null;
}): Promise<INestApplication> {
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
  const moduleRef = await builder.compile();

  const app = moduleRef.createNestApplication();
  app.setGlobalPrefix('api');
  app.useGlobalPipes(
    new ValidationPipe({ transform: true, whitelist: true, forbidNonWhitelisted: true }),
  );
  await app.init();
  return app;
}
