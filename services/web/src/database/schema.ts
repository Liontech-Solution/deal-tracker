/**
 * Definición Drizzle del esquema compartido (contrato en `db/migrations`).
 *
 * IMPORTANTE: Drizzle NO posee las migraciones aquí. Estas tablas son un espejo tipado del
 * SQL neutro de `db/migrations` para consultar con tipos; el esquema lo crean y versionan
 * los ficheros `NNNN_*.sql` (aplicados por el migrador, ver `migrate.ts`). No se usa
 * drizzle-kit para generar migraciones.
 *
 * Reparto: `retailer/product/variant/price_history/scrape_run/vigia_run` los escribe el scraper
 * (aquí solo se leen); `app_user/interest/favorite/notification` son propiedad del servicio web.
 *
 * **El espejo declara el contrato entero, no solo lo que el web consulta.** Que una tabla o una
 * columna no se lea desde aquí no es motivo para omitirla: omitirla es justo lo que hace que el
 * día que se necesite alguien escriba una migración que ya existe (#364). La única ausencia
 * deliberada es `schema_migrations`, que no está en `db/migrations`: la crean los dos aplicadores
 * (`migrate.ts` y `scraper/migrate.py`) para llevar su propia cuenta.
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
    /**
     * Histéresis de la detección de bajas (migración 0008): pasadas consecutivas sin ver la fila.
     * **La escribe solo la ingesta** — la pone a 0 al volver a ver la fila, la incrementa cuando
     * falta, y no marca `delisted_at` hasta `SCRAPER_DELIST_MIN_MISSES`. El web no la lee.
     */
    missingStreak: integer('missing_streak').notNull().default(0),
    /**
     * Cuándo se pidió por última vez la ficha completa (migración 0009). También de la ingesta:
     * el detalle solo se vuelve a pedir cuando cambia la huella del listado, así que esta marca es
     * lo único que hace que una prenda de precio estable se re-observe y tenga serie temporal.
     */
    lastDetailAt: timestamp('last_detail_at', { withTimezone: true }),
    /**
     * Cuándo contestó la tienda por última vez a un sondeo de confirmación de baja (migración
     * 0042, #412). De la ingesta también, y solo con veredicto CONCLUYENTE — si la tienda no
     * contestó, no hay nada que recordar.
     *
     * Existe porque el sondeo tiraba su propio veredicto: `_rescue()` pone la racha a 0 y dos
     * pasadas después se repregunta lo mismo, así que el presupuesto se gastaba en reconfirmar
     * prendas ya conocidas (200 sondeos / 200 vivos / 0 bajas / 504 sin sondear, QA 16/08/2026).
     *
     * **No es un criterio de baja.** A un producto dentro de la ventana no se le repregunta, pero
     * sigue bloqueado frente a la descatalogación exactamente igual que los que no caben en el
     * tope: lo que se ahorra es la petición, no la confirmación. El web no la lee.
     */
    lastProbeAt: timestamp('last_probe_at', { withTimezone: true }),
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
    /** La misma histéresis de bajas que en `product` (migración 0008), variante a variante. */
    missingStreak: integer('missing_streak').notNull().default(0),
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
export const productAgg = pgTable(
  'product_agg',
  {
    productId: bigint('product_id', { mode: 'number' })
      .notNull()
      .references(() => product.id, { onDelete: 'cascade' }),
    /**
     * De qué variantes se ha agregado (migración 0038, #371).
     *
     * `'todas'` son todas las vivas, que es lo que hacía la 0035. `'con_stock'` son solo las que
     * además tienen stock en su última lectura, y **no es el mismo agregado con menos filas**:
     * cambia cuál es la variante representativa y con ella `price_from`, `list_from` y los
     * `*_repr` con los que se decide la honestidad.
     *
     * Un producto sin ninguna variante con stock no tiene fila `'con_stock'`.
     *
     * ⚠️ Toda lectura tiene que fijar el ámbito. Sin el predicado salen las dos filas del mismo
     * producto y el catálogo lo duplica sin decir nada.
     */
    scope: text('scope').notNull(),
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
    /**
     * El mínimo de 30 días que declara la tienda para la variante representativa (migración `0039`,
     * #354). `NULL` en las siete tiendas que no lo publican, que es lo normal: solo lo traen C&A y
     * Springfield.
     */
    retailerMin30dRepr: numeric('retailer_min_30d_repr', { precision: 10, scale: 2 }),
    colorRepr: text('color_repr'),
    refreshedAt: timestamp('refreshed_at', { withTimezone: true }).notNull().defaultNow(),
  },
  (t) => [primaryKey({ columns: [t.productId, t.scope] })],
);

/**
 * Una pasada de un scraper: qué tienda, cuánto duró, qué vio y cómo terminó (migración 0001, más
 * `message` en la 0013 y los contadores de sondeo en la 0028).
 *
 * La escribe el scraper y el web no la lee —igual que `vigia_run`—, pero está aquí porque el
 * espejo declara el contrato entero, no solo la parte que el web consulta: `price_history` tiene
 * una FK contra esta tabla, y el matching se apoya en sus ids para saber qué pasadas ya evaluó
 * (`matching_scanned_run`, #240). Faltaba desde la 0001 (#364).
 *
 * Los siete `probes_*` son el desglose del sondeo de bajas de una pasada: `probes_sent +
 * probes_over_cap + probes_skipped_fresh` es el pool de candidatas y `probes_dead` el drenaje real
 * (ver la 0028). `probes_unbuyable` (0040, #197) es la tienda contestando que el producto existe
 * pero sin talla comprable: ni rescate ni baja, y NO cuenta como error — al contrario que
 * `probes_unresolved`, que es la tienda negándose a contestar.
 *
 * `probes_skipped_fresh` (0042, #412) es el cuarto que no es un error: candidatos a los que no se
 * ha preguntado porque ya contestaron hace poco. Se separa de `probes_over_cap` porque responden a
 * preguntas distintas —«no cupo» contra «no hacía falta»— y juntas no dejarían ver si la ventana
 * funciona. Lo que comparten es lo que importa: **las dos van bloqueadas frente a la baja**.
 */
export const scrapeRun = pgTable('scrape_run', {
  id: bigint('id', { mode: 'number' }).generatedAlwaysAsIdentity().primaryKey(),
  retailerId: bigint('retailer_id', { mode: 'number' })
    .notNull()
    .references(() => retailer.id),
  startedAt: timestamp('started_at', { withTimezone: true }).notNull().defaultNow(),
  finishedAt: timestamp('finished_at', { withTimezone: true }),
  /** `running` mientras la pasada está abierta; la ingesta es atómica, así que no deja rastro. */
  status: text('status').notNull().default('running'),
  productsSeen: integer('products_seen').notNull().default(0),
  variantsSeen: integer('variants_seen').notNull().default(0),
  errors: integer('errors').notNull().default(0),
  message: text('message'),
  probesSent: integer('probes_sent').notNull().default(0),
  probesAlive: integer('probes_alive').notNull().default(0),
  probesDead: integer('probes_dead').notNull().default(0),
  probesOverCap: integer('probes_over_cap').notNull().default(0),
  probesUnresolved: integer('probes_unresolved').notNull().default(0),
  probesUnbuyable: integer('probes_unbuyable').notNull().default(0),
  probesSkippedFresh: integer('probes_skipped_fresh').notNull().default(0),
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
  // NULL = fila que no viene de ninguna pasada (histórico sembrado a mano); el matching las
  // excluye a propósito. La FK contra `scrape_run` existe en la base desde la 0001.
  scrapeRunId: bigint('scrape_run_id', { mode: 'number' }).references(() => scrapeRun.id),
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

/**
 * Prendas guardadas por el usuario **sin pedir aviso** (migración 0041, #435).
 *
 * Deliberadamente **fuera de `interest`**, y el motivo que manda no es de modelado sino de daño:
 * la única condición de notificabilidad de todo el sistema es el `JOIN interest i ON i.active` de
 * `matching.service.ts`, así que una fila de favorito viviendo en `interest` dispararía avisos de
 * Telegram. Con tabla aparte eso es imposible por construcción.
 *
 * `productId` **sin `references()`**, igual que `interest.productId`: el producto lo posee el
 * scraper y el favorito tiene que sobrevivir a una baja (`delisted_at`) y a su resurrección.
 */
export const favorite = pgTable(
  'favorite',
  {
    id: bigint('id', { mode: 'number' }).generatedAlwaysAsIdentity().primaryKey(),
    userId: bigint('user_id', { mode: 'number' })
      .notNull()
      .references(() => appUser.id, { onDelete: 'cascade' }),
    productId: bigint('product_id', { mode: 'number' }).notNull(),
    createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
  },
  // El favorito es del PRODUCTO entero (un corazón por producto, sin talla), y esta clave es la que
  // hace idempotente el alta: marcar dos veces el mismo corazón no falla ni duplica.
  (t) => [unique('favorite_user_product_uniq').on(t.userId, t.productId)],
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
  scrapeRun,
  priceHistory,
  // `vigiaRun` faltaba aquí desde la 0022 por el mismo motivo por el que faltaba `scrape_run`
  // entera: nadie la consulta desde el web. Declarada pero fuera de este objeto, la tabla es
  // invisible para la API relacional de Drizzle, que es la mitad de por qué existe el espejo.
  vigiaRun,
  appUser,
  interest,
  favorite,
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
export type Favorite = typeof favorite.$inferSelect;
