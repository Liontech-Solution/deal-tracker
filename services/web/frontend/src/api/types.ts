/**
 * Tipos espejo del contrato de `services/web/src/catalog/catalog.types.ts`.
 * Los precios llegan como string (dinero exacto, sin float): se formatean/parsean en `lib/format`.
 */

/**
 * Veredicto de descuento honesto (espejo de `HonestyVerdict` del backend). Lo calcula el catálogo.
 *
 * `unverified` es el que hay que tratar con cuidado (#332): significa «la tienda enseña un tachado,
 * no es una bajada real, y **no podemos corroborar** que esté inflado». No es una acusación
 * suavizada — es la ausencia de prueba, y se pinta sin badge y sin culpar a nadie.
 */
export type Honesty = 'real' | 'reciente' | 'suspicious' | 'unverified' | 'none';

/**
 * En qué se apoya una acusación (espejo de `HonestyBasis`, #354): en nuestro histórico
 * (`observado`) o en el mínimo de 30 días que la propia tienda publica por la directiva Ómnibus
 * (`declarado`). `null` en todo lo que no sea `suspicious`.
 *
 * Lo consume el texto de la ficha, que no puede ser el mismo en los dos casos: una acusación
 * `declarado` puede caer sobre una prenda que acabamos de descubrir, y ahí «respecto a su
 * historial» sería mentira.
 */
export type HonestyBasis = 'observado' | 'declarado';

export interface ProductListItem {
  id: number;
  retailerId: number;
  retailerSlug: string;
  retailerName: string;
  retailerProductId: string;
  name: string;
  gender: string | null;
  section: string | null;
  category: string | null;
  /**
   * Calzado respetuoso: `si` | `no` | `desconocido`; `null` en ropa, donde no aplica. La API ya
   * filtra por esto (solo `si` salvo `?barefoot=all`), así que en el catálogo normal siempre
   * llega `si` o `null`.
   */
  barefoot: string | null;
  /**
   * Ejes transversales a la categoría (#180). Hoy solo puede traer `deportiva`.
   *
   * Vacío **no** es «no es deportiva»: es «su tienda no lo dice». Solo lo declaran Sfera, Lefties
   * y C&A, así que un chándal de Zara llega sin marca. No pintar nunca una negación con esto.
   */
  tags: string[];
  url: string | null;
  imageUrl: string | null;
  /** Color de la variante cuyo precio muestra la tarjeta: `imageUrl` ya viene resuelta a ese color. */
  colorRepr: string | null;
  priceFrom: string | null;
  listFrom: string | null;
  discountFrom: string | null;
  maxDiscount: string | null;
  /**
   * PVP **creíble** de la variante que enseña la tarjeta, y el descuento que se sostiene contra él
   * (#436). Espejo de los dos campos del backend.
   *
   * `honestListPrice` a `null` significa «no podemos sostener ninguna referencia» (arranque en
   * frío), **no** «usa la de la tienda»: ahí el tachado de `listFrom` no está corroborado por nada,
   * y pintarlo en verde es exactamente lo que este producto denuncia.
   */
  honestListPrice: string | null;
  honestDiscountPct: number;
  honesty: Honesty;
  anyInStock: boolean;
  variantCount: number;
}

export interface ProductListResult {
  items: ProductListItem[];
  limit: number;
  offset: number;
}

export interface VariantWithPrice {
  id: number;
  retailerVariantId: string;
  size: string | null;
  /**
   * La talla plegada por `size_canon`, que la calcula la base. Es la que hay que pasarle a
   * `etiquetaVariante()` (#297); `size` sigue siendo lo que rotula el selector de la ficha.
   */
  sizeCanon: string | null;
  /**
   * Lo que ROTULA el chip del selector (#331): la canónica, con la medida en cm detrás solo cuando
   * este producto publica dos tallas físicas bajo la misma etiqueta ('0-1 meses · 44 cm'). No es la
   * clave —esa sigue siendo `size`— ni lo que se guarda al seguir la prenda —esa es `sizeCanon`—.
   */
  sizeLabel: string | null;
  color: string | null;
  sku: string | null;
  url: string | null;
  delisted: boolean;
  price: string | null;
  listPrice: string | null;
  discountPct: string | null;
  inStock: boolean | null;
  scrapedAt: string | null;
  /**
   * Cómo se nombra esta variante donde el usuario la reconoce como «la prenda que sigo»: la calcula
   * el backend con la misma función que rotula `/seguimientos` y el aviso de Telegram (#223, #248),
   * así que **no se reconstruye aquí**. Lleva la talla CANÓNICA, mientras que `size` sigue siendo la
   * de la tienda — que es lo que pinta el selector de tallas.
   */
  variantLabel: string | null;
  /** Días enteros que llevamos observando esta variante. Solo lo trae la ficha (#332). */
  trackedDays: number;
  /**
   * El tramo que una afirmación de MÍNIMO puede citar sin mentir (#517): el backend ya le ha
   * aplicado el techo de la ventana de honestidad, que aquí no se conoce ni debe conocerse.
   *
   * Es el que va en el texto cuando la frase dice «lo más barato que la hemos visto en N días»;
   * `trackedDays` sigue siendo el que va cuando la frase habla de **cobertura** («llevamos N días
   * siguiéndola»). Los dos coinciden mientras la serie sea más corta que la ventana.
   */
  claimDays: number;
  /** Mínimo de 30 días declarado por la tienda (#354); `null` salvo en C&A y Springfield. */
  retailerMin30d: string | null;
  /** PVP creíble de ESTA variante y su descuento sostenible (#436). Mismo criterio que el listado. */
  honestListPrice: string | null;
  honestDiscountPct: number;
  honesty: Honesty;
  honestyBasis: HonestyBasis | null;
}

/** Una foto de la galería, atribuida al color que retrata (`null` = sin color atribuible). */
export interface ProductImageRef {
  color: string | null;
  url: string;
  /**
   * Ficha de la tienda de la que sale la foto (= `VariantWithPrice.url`). Solo la rellena H&M,
   * donde dos artículos distintos pueden compartir nombre de color (#123); `null` en las demás
   * tiendas y en lo ingerido antes de la 0023.
   */
  variantUrl: string | null;
}

export interface ProductDetail {
  id: number;
  retailerId: number;
  retailerSlug: string;
  retailerName: string;
  retailerProductId: string;
  name: string;
  gender: string | null;
  section: string | null;
  category: string | null;
  /** Igual que en la tarjeta, pero aquí SÍ puede llegar `no`/`desconocido`: la ficha directa no
   * se filtra, solo se filtra lo que el catálogo ofrece. */
  barefoot: string | null;
  /** Igual que en la tarjeta: vacío es «su tienda no lo dice», no «no lo es». */
  tags: string[];
  url: string | null;
  imageUrl: string | null;
  variants: VariantWithPrice[];
  /** Galería ordenada por color y posición. La ficha la filtra por el color seleccionado. */
  images: ProductImageRef[];
}

export interface PricePoint {
  price: string;
  listPrice: string | null;
  discountPct: string | null;
  inStock: boolean;
  scrapedAt: string;
}

export interface RetailerFacet {
  slug: string;
  name: string;
}

export interface Facets {
  genders: string[];
  sections: string[];
  categories: string[];
  sizes: string[];
  /**
   * Las tallas concretas de la banda ya elegida (#367). **Vacía si no hay banda**, y vacía siempre
   * en `zapateria`, donde el primer piso ya es la canónica. El panel pinta el segundo nivel solo
   * cuando esta lista trae algo, así que no necesita saber ninguna de las dos reglas.
   */
  sizeValues: string[];
  colors: string[];
  retailers: RetailerFacet[];
}

/**
 * Los ejes que `GET /catalog/facets` acepta (#292): desde que las facetas se cruzan, hay que
 * mandarle lo mismo que al listado para que describa la vista que el listado va a devolver.
 *
 * **Los que necesitan precio no están, y no es por omisión:** el backend va con
 * `forbidNonWhitelisted`, así que mandarle `inStock` u `onlyDeals` no se ignora, responde **400**.
 * Por eso este tipo se deriva de `ProductQuery` en vez de reenviar el objeto de filtros entero —
 * si algún día se añade un filtro barato, se suma aquí y en el DTO del backend a la vez.
 */
export type FacetQuery = Pick<
  ProductQuery,
  'q' | 'gender' | 'section' | 'category' | 'size' | 'sizeExact' | 'color' | 'retailer' | 'deportiva'
>;

/** Base de comparación de la regla de aviso (espejo de `interest.compare_base`). */
export type CompareBase = 'list_price' | 'recent_min';

/** Alta de un interés: espejo de `CreateInterestDto` del backend. */
export interface CreateInterestInput {
  retailerId?: number;
  productId?: number;
  variantId?: number;
  gender?: string;
  section?: string;
  category?: string;
  size?: string;
  color?: string;
  minDiscountPct?: number;
  compareBase?: CompareBase;
  windowDays?: number;
}

/** Interés enriquecido tal y como lo devuelve `GET /interests` (espejo de `InterestView`). */
export interface InterestView {
  id: number;
  userId: number;
  retailerId: number | null;
  productId: number | null;
  variantId: number | null;
  gender: string | null;
  section: string | null;
  category: string | null;
  size: string | null;
  color: string | null;
  minDiscountPct: string;
  compareBase: CompareBase;
  windowDays: number;
  active: boolean;
  createdAt: string;
  retailerName: string | null;
  productName: string | null;
  /**
   * La etiqueta que compone el backend. **Es la del aviso de Telegram**, con el color crudo; la SPA
   * no la pinta desde #297, sino que arma la suya con `etiquetaVariante(variantSize, variantColor)`
   * para capitalizar el color sin cambiar lo que se envía.
   */
  variantLabel: string | null;
  /** Talla CANÓNICA y color CRUDO: las piezas con las que la SPA rotula la variante (#297). */
  variantSize: string | null;
  variantColor: string | null;
  /**
   * Con qué enseñar la prenda seguida (#302). Los tres vienen `null` en un interés por filtros,
   * que no apunta a ninguna prenda, así que la tarjeta tiene que seguir funcionando sin ellos.
   * `targetProductId` no es `productId`: ese es el alcance declarado del interés, y un interés de
   * variante lo trae `null` aunque tenga ficha que enseñar.
   */
  targetProductId: number | null;
  imageUrl: string | null;
  productSection: string | null;
  /**
   * La prenda seguida lleva N pasadas sin aparecer (#435). No es «ya no existe»: la baja se deshace
   * sola si el producto vuelve, así que la fila se pinta apagada pero el seguimiento no se cancela.
   */
  delisted: boolean;
}

/** Favorito enriquecido tal y como lo devuelve `GET /favorites` (espejo de `FavoriteView`). */
export interface FavoriteView {
  id: number;
  userId: number;
  productId: number;
  createdAt: string;
  productName: string | null;
  retailerName: string | null;
  imageUrl: string | null;
  productSection: string | null;
  /** El «desde» del producto, con el mismo ámbito que enseña el catálogo (`scope = 'todas'`). */
  priceFrom: string | null;
  /** La prenda lleva N pasadas sin aparecer: la fila va apagada, pero NO se borra sola. */
  delisted: boolean;
  /** Ya hay un seguimiento activo del mismo producto: no se ofrece crear otro igual. */
  seguido: boolean;
}

/** Estado del vínculo de Telegram (espejo de `TelegramSettingsView` del backend). */
export interface TelegramSettingsView {
  linked: boolean;
  telegramUsername: string | null;
  linkedAt: string | null;
  pendingLink: boolean;
}

/** Resultado de iniciar un vínculo de Telegram (espejo de `TelegramLinkResult`). */
export interface TelegramLinkResult {
  deepLink: string;
  token: string;
  botUsername: string;
  expiresAt: string;
}

export type ProductSort = 'ofertas' | 'precio-asc' | 'precio-desc' | 'descuento';

export interface ProductQuery {
  /** Búsqueda libre sobre nombre, categoría y género. */
  q?: string;
  gender?: string;
  section?: string;
  category?: string;
  /**
   * Los tres ejes multiseleccionables (#329): viajan como parámetro repetido y el backend los
   * resuelve con `= ANY(...)`, o sea unión. Una lista vacía es «sin filtrar por este eje».
   *
   * `gender`, `section` y `category` se quedan de un solo valor a propósito — ver el DTO.
   */
  size?: string[];
  /**
   * La talla **concreta** dentro de la banda (#367). Se cruza con `size`, no la sustituye: la banda
   * es dónde estás y esta lo que pides dentro. Solo tiene sentido en `ropa`, que es donde `size` se
   * pliega a bandas.
   */
  sizeExact?: string[];
  color?: string[];
  retailer?: string[];
  inStock?: boolean;
  /** Solo ofertas reales (mínimo nuevo con rebaja honesta), no cualquier rebaja declarada. */
  onlyDeals?: boolean;
  /** Solo lo que la tienda publica como ropa de deporte (#180). Deja fuera a seis de las nueve. */
  deportiva?: boolean;
  /** Rango de precio sobre el precio desde el que arranca el producto (#290). Ambos incluyen. */
  minPrice?: number;
  maxPrice?: number;
  sort?: ProductSort;
  limit?: number;
  offset?: number;
}

// --- El alta por invitación: la mitad pública (#549/#550) ---
//
// Los tipos de quien INVITA (la lista, el alta y la revocación de `/ajustes`) son de #551 y están en
// su propio bloque, debajo de éste: así los dos PR no escribieron en el mismo sitio del fichero.

/**
 * Estado de un token de invitación (espejo de `InvitationTokenView['status']`).
 *
 * **No es** `InvitationStatus`, el de la lista de quien invita: allí el vivo se llama `'viva'` y
 * existe `'revocada'`. Aquí el revocado se colapsa en `'desconocida'` a propósito — quien invitó se
 * la quitó, y no hay nada útil que contarle a quien recibió el correo. No se unifican.
 */
export type InvitationTokenStatus = 'valida' | 'caducada' | 'canjeada' | 'desconocida';

/**
 * Lo que contesta `GET /invitations/token/:token` (espejo de `InvitationTokenView`).
 *
 * Responde **200 siempre**: el estado va en el cuerpo y no en el código, para que la pantalla tenga
 * un contrato que parsear en vez del cuerpo de un error, y para que el status no diga si un token
 * existe. Los tres opcionales llegan **solo** con `valida`, que es lo que evita filtrar a qué correo
 * se invitó.
 */
export interface InvitationTokenView {
  status: InvitationTokenStatus;
  email?: string;
  /** Nunca falta cuando el token vale, pero **puede ser `''`**: cae a `inviterEmail` y de ahí a vacío. */
  inviterName?: string;
  expiresAt?: string;
}

/**
 * Cuerpo del alta (espejo de `AcceptInvitationDto`).
 *
 * **No hay campo `email`, y su ausencia es la regla**: el correo lo fija la invitación. Mandarlo no
 * se ignora en silencio — el `ValidationPipe` global corre con `forbidNonWhitelisted` y es un 400.
 */
export interface AcceptInvitationInput {
  password: string;
  firstName?: string;
}

/** Un alta consumada (espejo de `AcceptedInvitation`). El correo, para llevarlo a `/acceso`. */
export interface AcceptedInvitation {
  email: string;
}

/**
 * Lo que publica `GET /config` (espejo de `PublicAuthConfig` del backend).
 *
 * Vivía duplicado y a medias dentro de `auth/keycloak.ts`, que se había quedado sin el cuarto
 * campo. `invitesEnabled` es `boolean` estricto, nunca nulo: dice si **este entorno** puede dar de
 * alta a alguien, y en `dev` es `false` por construcción.
 */
export interface PublicConfig {
  url: string | null;
  realm: string | null;
  clientId: string | null;
  invitesEnabled: boolean;
}

// --- El alta por invitación: la mitad de QUIEN INVITA (#551) ---
//
// Éste es el bloque que el de arriba anunciaba. Los tres endpoints llevan sesión, al revés que los
// dos públicos de #550.

/**
 * Estado de una invitación en la lista de quien la mandó (espejo de `InvitationStatus`).
 *
 * **No se unifica con `InvitationTokenStatus`** y las dos uniones conviven a propósito: aquí el vivo
 * se llama `'viva'` y allí `'valida'`, y `'revocada'` no existe en la pública porque el backend la
 * colapsa en `'desconocida'` — quien invitó se la quitó, y eso no es asunto de quien recibió el
 * correo. Fundirlas obligaría a mentir en una de las dos puntas.
 */
export type InvitationStatus = 'viva' | 'caducada' | 'canjeada' | 'revocada';

/** Una invitación de la lista (espejo de `InvitationView`). El correo viaja **entero**, sin enmascarar. */
export interface InvitationView {
  id: number;
  email: string;
  status: InvitationStatus;
  createdAt: string;
  expiresAt: string;
}

/**
 * Lo que contesta `GET /invitations` (espejo de `InvitationListView`).
 *
 * **No es un array**: el cupo viaja con la lista para que la pantalla no pueda enseñar un número que
 * no cuadre con las filas que tiene debajo. Antes de #551 lo era, y `invitesRemaining` no salía por
 * HTTP más que al gastarlo.
 */
export interface InvitationListView {
  invitesRemaining: number;
  invitations: InvitationView[];
}

/**
 * Lo que devuelve invitar (espejo de `CreatedInvitation`).
 *
 * `invitesRemaining` es el cupo **ya descontado**. Aun así la pantalla invalida la lista en vez de
 * fiarse de este número: la fila nueva hay que traerla igualmente.
 */
export interface CreatedInvitation {
  id: number;
  email: string;
  expiresAt: string;
  invitesRemaining: number;
}
