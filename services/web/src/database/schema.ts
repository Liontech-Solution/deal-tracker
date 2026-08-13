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
import { relations, sql } from 'drizzle-orm';
import {
  bigint,
  boolean,
  integer,
  numeric,
  pgTable,
  primaryKey,
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
    // Ficha de la tienda a la que pertenece la foto (= variant.url), 0023. NULL en las seis
    // tiendas que no publican dos artículos bajo un mismo nombre de color. NO entra en el UNIQUE
    // a propósito: la tarjeta del catálogo hace join por position = 0 y espera una fila por
    // (producto, color). Ver la cabecera de la migración.
    variantUrl: text('variant_url'),
  },
  (t) => [unique().on(t.productId, t.color, t.position)],
);

/**
 * Ejes transversales a la categoría (0026), escritos por el scraper desde la hoja de origen de
 * cada tienda. Hoy solo `deportiva` (#180).
 *
 * Es una tabla y no una columna como `barefoot` porque ya hay un segundo eje con la misma forma
 * esperando (#189, el uniforme escolar de H&M): así el siguiente es una fila con otro `tag` en vez
 * de otra migración con su recorrido entero hasta la SPA.
 *
 * Ojo con la **cobertura** al leerla: solo cinco de las nueve tiendas publican un cajón de deporte
 * identificable, así que filtrar por `deportiva` excluye enteras a Zara, Hipercor, Springfield y
 * Cacles. No es un hueco de datos que se vaya a rellenar solo: esas tiendas no lo dicen.
 */
export const productTag = pgTable(
  'product_tag',
  {
    productId: bigint('product_id', { mode: 'number' })
      .notNull()
      .references(() => product.id, { onDelete: 'cascade' }),
    tag: text('tag').notNull(),
  },
  (t) => [primaryKey({ columns: [t.productId, t.tag] })],
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
    /**
     * Familia de color ya calculada (0031, #327). **La escribe Postgres**, no el scraper ni el
     * web: es `GENERATED ALWAYS AS (color_family(color)) STORED`, así que aquí es de solo lectura
     * y ningún `INSERT`/`UPDATE` puede darle valor.
     *
     * Existe porque enumerar las familias para la faceta obligaba a evaluar `color_family` sobre
     * 3.312 formas crudas (1,67 s; 140 ms leyendo la columna).
     */
    colorFamilyCache: text('color_family_cache').generatedAlwaysAs(
      sql`color_family(color)`,
    ),
    sku: text('sku'),
    url: text('url'),
    firstSeenAt: timestamp('first_seen_at', { withTimezone: true }).notNull().defaultNow(),
    lastSeenAt: timestamp('last_seen_at', { withTimezone: true }).notNull().defaultNow(),
    delistedAt: timestamp('delisted_at', { withTimezone: true }),
  },
  (t) => [unique().on(t.productId, t.retailerVariantId)],
);

/**
 * Agregado por producto que lee el catálogo (migración 0035, #314).
 *
 * **La escribe Postgres, no el scraper ni el web**: la puebla `refresh_product_agg(retailer_id)`,
 * que `ingest.py` invoca al final de cada pasada dentro de su propia transacción. Aquí es de solo
 * lectura — ningún `INSERT`/`UPDATE` del web debe tocarla.
 *
 * Existe porque ordenar el catálogo por `price_from` / `is_real_deal` / `honest_discount` obliga a
 * agregar TODAS las variantes vivas antes del `LIMIT`: 1,9 s por petición en prod, 69 ms leyendo
 * esta tabla.
 *
 * No guarda el veredicto de honestidad, solo los estadísticos con los que se calcula: meterlo aquí
 * sería un tercer espejo de la regla de `deal-rule.ts` (ver #228).
 */
export const productAgg = pgTable('product_agg', {
  productId: bigint('product_id', { mode: 'number' })
    .primaryKey()
    .references(() => product.id, { onDelete: 'cascade' }),
  retailerId: bigint('retailer_id', { mode: 'number' })
    .notNull()
    .references(() => retailer.id),
  priceFrom: numeric('price_from', { precision: 10, scale: 2 }),
  listFrom: numeric('list_from', { precision: 10, scale: 2 }),
  discountFrom: numeric('discount_from', { precision: 5, scale: 2 }),
  maxDiscount: numeric('max_discount', { precision: 5, scale: 2 }),
  anyInStock: boolean('any_in_stock'),
  priceRepr: numeric('price_repr', { precision: 10, scale: 2 }),
  recentMinRepr: numeric('recent_min_repr', { precision: 10, scale: 2 }),
  maxObservedRepr: numeric('max_observed_repr', { precision: 10, scale: 2 }),
  priorPointsRepr: bigint('prior_points_repr', { mode: 'number' }),
  trackedDaysRepr: numeric('tracked_days_repr'),
  colorRepr: text('color_repr'),
  refreshedAt: timestamp('refreshed_at', { withTimezone: true }).notNull().defaultNow(),
});

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

export const interest = pgTable(
  'interest',
  {
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
  },
  // Migración 0025: el alcance identifica al interés, y por eso el alta puede reactivar la fila que
  // ya existía en vez de crear otra (#149). `nullsNotDistinct` porque aquí un NULL es «cualquiera»,
  // no «desconocido»: sin él, dos intereses de «cualquier talla» no colisionarían nunca.
  (t) => [
    unique('interest_alcance_uniq')
      .on(
        t.userId,
        t.retailerId,
        t.productId,
        t.variantId,
        t.gender,
        t.section,
        t.category,
        t.size,
        t.color,
      )
      .nullsNotDistinct(),
  ],
);

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
 * Suelo de los jobs (migración 0007). Para el matching, todo `scrape_run_id` por debajo está
 * resuelto; lo que hay por encima lo decide `matchingScannedRun`.
 */
export const jobState = pgTable('job_state', {
  job: text('job').primaryKey(),
  lastScrapeRunId: bigint('last_scrape_run_id', { mode: 'number' }).notNull().default(0),
  updatedAt: timestamp('updated_at', { withTimezone: true }).notNull().defaultNow(),
});

/**
 * Pasadas ya evaluadas por el matching, por encima del suelo (migración 0027). Existe porque los
 * `scrape_run` no se completan en orden de id: una pasada larga puede commitear después de otra
 * posterior, y con un simple "mayor id visto" sus filas quedaban bajo la marca y no se avisaban
 * nunca (#240). Sin FK a `scrape_run`, como `notification.variant_id`: tabla del web, tabla ajena.
 */
export const matchingScannedRun = pgTable('matching_scanned_run', {
  scrapeRunId: bigint('scrape_run_id', { mode: 'number' }).primaryKey(),
  processedAt: timestamp('processed_at', { withTimezone: true }).notNull().defaultNow(),
});

export const productRelations = relations(product, ({ one, many }) => ({
  retailer: one(retailer, { fields: [product.retailerId], references: [retailer.id] }),
  variants: many(variant),
  images: many(productImage),
  tags: many(productTag),
}));

export const productImageRelations = relations(productImage, ({ one }) => ({
  product: one(product, { fields: [productImage.productId], references: [product.id] }),
}));

export const productTagRelations = relations(productTag, ({ one }) => ({
  product: one(product, { fields: [productTag.productId], references: [product.id] }),
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
  productTag,
  variant,
  productAgg,
  priceHistory,
  appUser,
  interest,
  notification,
  jobState,
  matchingScannedRun,
  productRelations,
  productImageRelations,
  productTagRelations,
  variantRelations,
  priceHistoryRelations,
};

export type AppUser = typeof appUser.$inferSelect;
export type Interest = typeof interest.$inferSelect;
export type NewInterest = typeof interest.$inferInsert;
