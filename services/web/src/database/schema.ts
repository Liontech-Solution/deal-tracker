/**
 * Definición Drizzle del esquema compartido (contrato en `db/migrations`).
 *
 * IMPORTANTE: Drizzle NO posee las migraciones aquí. Estas tablas son un espejo tipado del
 * SQL neutro de `db/migrations` para consultar con tipos; el esquema lo crean y versionan
 * los ficheros `NNNN_*.sql` (aplicados por el migrador, ver `migrate.ts`). No se usa
 * drizzle-kit para generar migraciones.
 *
 * Reparto: `retailer/product/variant/price_history/scrape_run` los escribe el scraper (aquí
 * solo se leen); `app_user/interest/notification` son propiedad del servicio web.
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
    url: text('url'),
    listingSignature: text('listing_signature'),
    firstSeenAt: timestamp('first_seen_at', { withTimezone: true }).notNull().defaultNow(),
    lastSeenAt: timestamp('last_seen_at', { withTimezone: true }).notNull().defaultNow(),
    delistedAt: timestamp('delisted_at', { withTimezone: true }),
  },
  (t) => [unique().on(t.retailerId, t.retailerProductId)],
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
  inStock: boolean('in_stock').notNull().default(true),
  scrapedAt: timestamp('scraped_at', { withTimezone: true }).notNull().defaultNow(),
  scrapeRunId: bigint('scrape_run_id', { mode: 'number' }),
});

// --- Tablas del web ---

export const appUser = pgTable('app_user', {
  id: bigint('id', { mode: 'number' }).generatedAlwaysAsIdentity().primaryKey(),
  keycloakSub: text('keycloak_sub').notNull().unique(),
  email: text('email'),
  displayName: text('display_name'),
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

export const productRelations = relations(product, ({ one, many }) => ({
  retailer: one(retailer, { fields: [product.retailerId], references: [retailer.id] }),
  variants: many(variant),
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
  variant,
  priceHistory,
  appUser,
  interest,
  notification,
  productRelations,
  variantRelations,
  priceHistoryRelations,
};

export type AppUser = typeof appUser.$inferSelect;
export type Interest = typeof interest.$inferSelect;
export type NewInterest = typeof interest.$inferInsert;
