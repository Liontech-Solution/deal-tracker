/**
 * Definición Drizzle del esquema compartido (contrato en `db/migrations`).
 *
 * IMPORTANTE: Drizzle NO posee las migraciones aquí. Estas tablas son un espejo tipado del
 * SQL neutro de `db/migrations` para consultar con tipos; el esquema lo crean y versionan
 * los ficheros `NNNN_*.sql` (aplicados por el migrador, ver `migrate.ts`). No se usa
 * drizzle-kit para generar migraciones.
 *
 * Reparto: `retailer/product/variant/price_history/scrape_run/vigia_run` los escribe el scraper
 * (aquí solo se leen); `app_user/interest/notification` son propiedad del servicio web.
 */
import { relations } from 'drizzle-orm';
import {
  bigint,
  boolean,
  integer,
  numeric,
  pgTable,
  text,
  timestamp,
  unique,
} from 'drizzle-orm/pg-core';

// --- Tablas del scraper (solo lectura desde el web) ---

export const retailer = pgTable('retailer', {
  id: bigint('id', { mode: 'number' }).generatedAlwaysAsIdentity().primaryKey(),
  slug: text('slug').notNull().unique(),
  name: text('name').notNull(),
  baseUrl: text('base_url').notNull(),
  active: boolean('active').notNull().default(true),
  createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
});

export const product = pgTable(
  'product',
  {
    id: bigint('id', { mode: 'number' }).generatedAlwaysAsIdentity().primaryKey(),
    retailerId: bigint('retailer_id', { mode: 'number' })
      .notNull()
      .references(() => retailer.id),
    retailerProductId: text('retailer_product_id').notNull(),
    name: text('name').notNull(),
    gender: text('gender'),
    section: text('section'),
    category: text('category'),
    /**
     * Calzado respetuoso: `'si' | 'no' | 'desconocido'`, y **NULL cuando no aplica** porque es
     * ropa. Ese NULL no es "sin datos": es la diferencia de la que depende el filtro por defecto
     * del catálogo, que deja pasar toda la ropa y solo el calzado `si` (ver migración 0012).
     */
    barefoot: text('barefoot'),
    url: text('url'),
    imageUrl: text('image_url'),
    listingSignature: text('listing_signature'),
    firstSeenAt: timestamp('first_seen_at', { withTimezone: true }).notNull().defaultNow(),
    lastSeenAt: timestamp('last_seen_at', { withTimezone: true }).notNull().defaultNow(),
    delistedAt: timestamp('delisted_at', { withTimezone: true }),
  },
  (t) => [unique().on(t.retailerId, t.retailerProductId)],
);

/**
 * Galería de fotos, agrupada por color. `color` guarda el MISMO texto que `variant.color`: es la
 * clave con la que la ficha empareja la foto con el precio (el precio cuelga de la variante, y la
 * variante es talla+color, así que en muchas tiendas el color cambia el precio). `position` 0 es
 * la foto que representa a ese color.
 */
export const productImage = pgTable(
  'product_image',
  {
    id: bigint('id', { mode: 'number' }).generatedAlwaysAsIdentity().primaryKey(),
    productId: bigint('product_id', { mode: 'number' })
      .notNull()
      .references(() => product.id, { onDelete: 'cascade' }),
    color: text('color'),
    position: integer('position').notNull(),
    url: text('url').notNull(),
  },
  (t) => [unique().on(t.productId, t.color, t.position)],
);

export const variant = pgTable(
  'variant',
  {
    id: bigint('id', { mode: 'number' }).generatedAlwaysAsIdentity().primaryKey(),
    productId: bigint('product_id', { mode: 'number' })
      .notNull()
      .references(() => product.id),
    retailerVariantId: text('retailer_variant_id').notNull(),
    size: text('size'),
    color: text('color'),
    sku: text('sku'),
    url: text('url'),
    firstSeenAt: timestamp('first_seen_at', { withTimezone: true }).notNull().defaultNow(),
    lastSeenAt: timestamp('last_seen_at', { withTimezone: true }).notNull().defaultNow(),
    delistedAt: timestamp('delisted_at', { withTimezone: true }),
  },
  (t) => [unique().on(t.productId, t.retailerVariantId)],
);

export const priceHistory = pgTable('price_history', {
  id: bigint('id', { mode: 'number' }).generatedAlwaysAsIdentity().primaryKey(),
  variantId: bigint('variant_id', { mode: 'number' })
    .notNull()
    .references(() => variant.id),
  price: numeric('price', { precision: 10, scale: 2 }).notNull(),
  currency: text('currency').notNull().default('EUR'),
  listPrice: numeric('list_price', { precision: 10, scale: 2 }),
  discountPct: numeric('discount_pct', { precision: 5, scale: 2 }),
  // Mínimo de 30 días que declara la propia tienda (Ómnibus). NULL = la tienda no lo declara,
  // que no es lo mismo que "no hubo mínimo". Ver 0018_add_retailer_min_30d.sql.
  retailerMin30d: numeric('retailer_min_30d', { precision: 10, scale: 2 }),
  inStock: boolean('in_stock').notNull().default(true),
  scrapedAt: timestamp('scraped_at', { withTimezone: true }).notNull().defaultNow(),
  scrapeRunId: bigint('scrape_run_id', { mode: 'number' }),
});

/**
 * Serie temporal de lo que tarda el vigía por tienda y capa (migración 0022, #111).
 *
 * La escribe el vigía del scraper y el web no la lee: está aquí para que el espejo no se bifurque
 * del contrato. `retailerSlug` es texto y no una FK a `retailer` a propósito — el vigía sondea
 * tiendas que aún no han ingerido nunca y por tanto no tienen fila allí (ver la migración).
 */
export const vigiaRun = pgTable('vigia_run', {
  id: bigint('id', { mode: 'number' }).generatedAlwaysAsIdentity().primaryKey(),
  retailerSlug: text('retailer_slug').notNull(),
  capa: text('capa').notNull(),
  ranAt: timestamp('ran_at', { withTimezone: true }).notNull().defaultNow(),
  segundos: numeric('segundos', { precision: 10, scale: 3 }).notNull(),
  unidades: integer('unidades').notNull(),
});

// --- Tablas del web ---

export const appUser = pgTable('app_user', {
  id: bigint('id', { mode: 'number' }).generatedAlwaysAsIdentity().primaryKey(),
  keycloakSub: text('keycloak_sub').notNull().unique(),
  email: text('email'),
  displayName: text('display_name'),
  // Vínculo Telegram (migración 0006). Nulos = sin vincular / sin enlace en curso.
  telegramChatId: bigint('telegram_chat_id', { mode: 'number' }).unique(),
  telegramUsername: text('telegram_username'),
  telegramLinkedAt: timestamp('telegram_linked_at', { withTimezone: true }),
  telegramLinkToken: text('telegram_link_token').unique(),
  telegramLinkTokenExpiresAt: timestamp('telegram_link_token_expires_at', {
    withTimezone: true,
  }),
  createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
  updatedAt: timestamp('updated_at', { withTimezone: true }).notNull().defaultNow(),
});

export const interest = pgTable('interest', {
  id: bigint('id', { mode: 'number' }).generatedAlwaysAsIdentity().primaryKey(),
  userId: bigint('user_id', { mode: 'number' })
    .notNull()
    .references(() => appUser.id, { onDelete: 'cascade' }),
  retailerId: bigint('retailer_id', { mode: 'number' }).references(() => retailer.id),
  productId: bigint('product_id', { mode: 'number' }),
  variantId: bigint('variant_id', { mode: 'number' }),
  gender: text('gender'),
  section: text('section'),
  category: text('category'),
  size: text('size'),
  color: text('color'),
  minDiscountPct: numeric('min_discount_pct', { precision: 5, scale: 2 }).notNull().default('20'),
  compareBase: text('compare_base').notNull().default('recent_min'),
  windowDays: integer('window_days').notNull().default(30),
  active: boolean('active').notNull().default(true),
  createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
});

export const notification = pgTable(
  'notification',
  {
    id: bigint('id', { mode: 'number' }).generatedAlwaysAsIdentity().primaryKey(),
    userId: bigint('user_id', { mode: 'number' })
      .notNull()
      .references(() => appUser.id, { onDelete: 'cascade' }),
    interestId: bigint('interest_id', { mode: 'number' })
      .notNull()
      .references(() => interest.id, { onDelete: 'cascade' }),
    variantId: bigint('variant_id', { mode: 'number' }).notNull(),
    price: numeric('price', { precision: 10, scale: 2 }).notNull(),
    listPrice: numeric('list_price', { precision: 10, scale: 2 }),
    discountPct: numeric('discount_pct', { precision: 5, scale: 2 }),
    priceEventKey: text('price_event_key').notNull(),
    sentAt: timestamp('sent_at', { withTimezone: true }).notNull().defaultNow(),
  },
  (t) => [unique().on(t.interestId, t.variantId, t.priceEventKey)],
);

/**
 * Marca de agua de los jobs (migración 0007). El job de matching solo evalúa los precios de
 * `scrape_run_id` mayores que el último procesado.
 */
export const jobState = pgTable('job_state', {
  job: text('job').primaryKey(),
  lastScrapeRunId: bigint('last_scrape_run_id', { mode: 'number' }).notNull().default(0),
  updatedAt: timestamp('updated_at', { withTimezone: true }).notNull().defaultNow(),
});

export const productRelations = relations(product, ({ one, many }) => ({
  retailer: one(retailer, { fields: [product.retailerId], references: [retailer.id] }),
  variants: many(variant),
  images: many(productImage),
}));

export const productImageRelations = relations(productImage, ({ one }) => ({
  product: one(product, { fields: [productImage.productId], references: [product.id] }),
}));

export const variantRelations = relations(variant, ({ one, many }) => ({
  product: one(product, { fields: [variant.productId], references: [product.id] }),
  prices: many(priceHistory),
}));

export const priceHistoryRelations = relations(priceHistory, ({ one }) => ({
  variant: one(variant, { fields: [priceHistory.variantId], references: [variant.id] }),
}));

export const schema = {
  retailer,
  product,
  productImage,
  variant,
  priceHistory,
  appUser,
  interest,
  notification,
  jobState,
  productRelations,
  productImageRelations,
  variantRelations,
  priceHistoryRelations,
};

export type AppUser = typeof appUser.$inferSelect;
export type Interest = typeof interest.$inferSelect;
export type NewInterest = typeof interest.$inferInsert;
