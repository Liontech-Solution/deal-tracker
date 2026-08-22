import {
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { useRef } from 'react';

import { apiGet, apiGetAuth, apiGetPublic, apiSend, apiSendPublic } from './client';
import type {
  AcceptedInvitation,
  AcceptInvitationInput,
  CreatedInvitation,
  CreateInterestInput,
  Facets,
  FacetQuery,
  FavoriteView,
  InterestView,
  InvitationListView,
  InvitationTokenView,
  PricePoint,
  ProductDetail,
  ProductListResult,
  ProductQuery,
  TelegramLinkResult,
  TelegramSettingsView,
} from './types';

const PAGE_SIZE = 12;

/**
 * Facetas para poblar los filtros (género/sección/categoría/talla/color/tiendas).
 *
 * Desde #292 recibe **todos los filtros activos**, no solo la sección: las facetas se cruzan entre
 * sí en el backend, así que la lista de tallas que devuelve es la de las prendas que quedan tras
 * lo ya filtrado. Antes ofrecía tallas que dentro de la categoría elegida no existían, y pinchar
 * una dejaba el catálogo vacío.
 *
 * Lo que se le manda es `FacetQuery`, que a propósito **no** incluye `inStock` ni `onlyDeals`: el
 * backend los rechaza con 400 (ver el tipo). El objeto entra en la `queryKey`, así que cada
 * combinación de filtros tiene su propia entrada en caché y volver atrás es instantáneo.
 *
 * `staleTime` largo porque el catálogo lo reescribe una pasada del scraper, no el usuario: dentro
 * de una sesión de filtrado las facetas de una misma combinación no cambian.
 */
export function useFacets(query: FacetQuery) {
  return useQuery({
    queryKey: ['facets', query],
    queryFn: () => apiGet<Facets>('/catalog/facets', { ...query }),
    staleTime: 5 * 60 * 1000,
  });
}

/**
 * Catálogo paginado con "cargar más" (offset). `query` no incluye limit/offset.
 *
 * `pageSize` es para quien enseña una tira corta y no pagina (la home y sus ofertas del día): pedir
 * 12 para pintar 4 sería traerse el triple de lo que se ve.
 */
export function useProducts(query: Omit<ProductQuery, 'limit' | 'offset'>, pageSize = PAGE_SIZE) {
  return useInfiniteQuery({
    queryKey: ['products', query, pageSize],
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      apiGet<ProductListResult>('/catalog/products', {
        ...query,
        limit: pageSize,
        offset: pageParam,
      }),
    getNextPageParam: (lastPage) =>
      lastPage.items.length < lastPage.limit ? undefined : lastPage.offset + lastPage.limit,
    /**
     * La mitad de #292: **el contador mentía mientras cargaba**.
     *
     * Los filtros van en la `queryKey`, así que tocar uno estrenaba entrada de caché, `data`
     * volvía a `undefined` e `items` a `[]`. Con eso la cabecera decía «0 prendas» y el botón del
     * cajón «Ver 0 prendas» hasta que llegaba la respuesta — y quien lo reportó lo dijo bien:
     * *«puede que sea un tema de latencias»*. Con los 24 s que tardaba el catálogo antes de #307
     * eso se veía en cada clic.
     *
     * `keepPreviousData` mantiene la página anterior mientras llega la nueva, así que ya no hay
     * ningún instante en el que la SPA afirme un número que no tiene. Lo que queda por hacer en
     * quien lo pinta es NO presentar ese número como definitivo: `isPlaceholderData` dice que lo
     * de pantalla es lo de antes, y `CatalogPage` lo usa para atenuar la rejilla y poner el botón
     * en «ocupado». Un número viejo sin avisar es otra forma de mentir, más silenciosa.
     */
    placeholderData: keepPreviousData,
  });
}

export function useProduct(id: number | undefined) {
  return useQuery({
    queryKey: ['product', id],
    enabled: id !== undefined && Number.isFinite(id),
    queryFn: () => apiGet<ProductDetail>(`/catalog/products/${id}`),
  });
}

export function usePriceHistory(variantId: number | undefined) {
  return useQuery({
    queryKey: ['price-history', variantId],
    enabled: variantId !== undefined && Number.isFinite(variantId),
    queryFn: () => apiGet<PricePoint[]>(`/catalog/variants/${variantId}/price-history`),
  });
}

// --- Intereses (requieren sesión; el token lo adjunta el cliente HTTP) ---

const INTERESTS_KEY = ['interests'];

/** Lista de seguimientos del usuario. `enabled` para no pedir sin sesión. */
export function useInterests(enabled: boolean) {
  return useQuery({
    queryKey: INTERESTS_KEY,
    enabled,
    queryFn: () => apiGetAuth<InterestView[]>('/interests'),
  });
}

export function useCreateInterest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateInterestInput) => apiSend<InterestView>('POST', '/interests', input),
    onSuccess: () => qc.invalidateQueries({ queryKey: INTERESTS_KEY }),
  });
}

export function useDeleteInterest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => apiSend<void>('DELETE', `/interests/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: INTERESTS_KEY }),
  });
}

// --- Favoritos (requieren sesión; el token lo adjunta el cliente HTTP) ---

const FAVORITES_KEY = ['favorites'];

/**
 * Lista de favoritos del usuario. `enabled` para no pedir sin sesión.
 *
 * **Incluye los de baja**: el backend no filtra por `delisted_at` a propósito, para que la página
 * pueda pintarlos apagados en vez de hacerlos desaparecer sin explicación.
 */
export function useFavorites(enabled: boolean) {
  return useQuery({
    queryKey: FAVORITES_KEY,
    enabled,
    queryFn: () => apiGetAuth<FavoriteView[]>('/favorites'),
  });
}

/**
 * Los ids favoritos del usuario, para que cada corazón sepa si va relleno.
 *
 * **Es la MISMA query que `useFavorites`**, no otra: comparte `queryKey`, así que react-query la
 * pide una sola vez aunque la consulten las N tarjetas de una rejilla, y `select` solo transforma
 * lo ya cacheado. Esa es la razón de que no exista un `GET /favorites/ids`: haría falta un endpoint
 * más para no ahorrar ninguna petición.
 */
export function useFavoriteIds(enabled: boolean) {
  return useQuery({
    queryKey: FAVORITES_KEY,
    enabled,
    queryFn: () => apiGetAuth<FavoriteView[]>('/favorites'),
    select: (favs) => new Set(favs.map((f) => f.productId)),
  });
}

export function useAddFavorite() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (productId: number) =>
      apiSend<FavoriteView>('POST', '/favorites', { productId }),
    onSuccess: () => qc.invalidateQueries({ queryKey: FAVORITES_KEY }),
  });
}

/** Quitar el corazón va por `productId`: quien lo pulsa sabe qué prenda mira, no qué fila es. */
export function useRemoveFavorite() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (productId: number) => apiSend<void>('DELETE', `/favorites/${productId}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: FAVORITES_KEY }),
  });
}

// --- Ajustes: vínculo de Telegram (requieren sesión) ---

const TELEGRAM_KEY = ['settings', 'telegram'];

/** Durante este rato tras iniciar el enlace se sondea rápido; después se afloja. */
const TELEGRAM_POLL_FAST_MS = 4000;
const TELEGRAM_POLL_SLOW_MS = 15000;
const TELEGRAM_POLL_FAST_WINDOW_MS = 2 * 60 * 1000;

/**
 * Estado del vínculo de Telegram. `enabled` para no pedir sin sesión. Mientras hay un enlace
 * en curso (token vivo sin confirmar), sondea para detectar cuándo el bot lo confirma.
 *
 * El sondeo se escalona a propósito. `pendingLink` es cierto durante toda la vida del token, que
 * con #266 pasó de 15 a 60 min; a 4 s fijos eso serían ~900 peticiones, y cada una pasa por
 * `getFreshToken()` → `kc.updateToken()`, que es justo el terreno de #262. Así que se sondea
 * rápido los primeros minutos —cuando el usuario está escaneando el QR y espera respuesta— y
 * despacio después, que es cuando ha dejado la pestaña abierta y ya volverá.
 */
export function useTelegramSettings(enabled: boolean) {
  // Cuándo se vio por primera vez el enlace en curso. En un ref y no en estado: cambiarlo no debe
  // repintar, solo decidir la cadencia del siguiente sondeo.
  const pendingSince = useRef<number | null>(null);
  return useQuery({
    queryKey: TELEGRAM_KEY,
    enabled,
    queryFn: () => apiGetAuth<TelegramSettingsView>('/settings/telegram'),
    refetchInterval: (query) => {
      if (!query.state.data?.pendingLink) {
        pendingSince.current = null;
        return false;
      }
      pendingSince.current ??= Date.now();
      return Date.now() - pendingSince.current < TELEGRAM_POLL_FAST_WINDOW_MS
        ? TELEGRAM_POLL_FAST_MS
        : TELEGRAM_POLL_SLOW_MS;
    },
  });
}

/** Inicia un vínculo: devuelve el deep-link a abrir en Telegram. */
export function useLinkTelegram() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiSend<TelegramLinkResult>('POST', '/settings/telegram/link'),
    onSuccess: () => qc.invalidateQueries({ queryKey: TELEGRAM_KEY }),
  });
}

export function useUnlinkTelegram() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiSend<void>('DELETE', '/settings/telegram'),
    onSuccess: () => qc.invalidateQueries({ queryKey: TELEGRAM_KEY }),
  });
}

// --- Alta por invitación (#550). Los dos endpoints son PÚBLICOS: quien los usa todavía no tiene
// cuenta, así que van por el camino anónimo del cliente y no por el de «token si lo hay». ---

/**
 * Consulta el token del enlace del correo. Contesta **200 siempre** y el estado va en el cuerpo, o
 * sea que un error aquí es un error de verdad (red o 503), no un token malo.
 *
 * `retry: false` a propósito, contra el `retry: 1` que trae el `QueryClient` global: en esta
 * pantalla todo lo que puede fallar es terminal, y reintentar solo retrasa lo que hay que enseñar.
 */
export function useInvitationToken(token: string | null) {
  return useQuery({
    queryKey: ['invitation-token', token],
    enabled: token !== null,
    retry: false,
    queryFn: () => apiGetPublic<InvitationTokenView>(`/invitations/token/${encodeURIComponent(token as string)}`),
  });
}

/**
 * Consuma el alta. Sin `onSuccess`: esta página no tiene ninguna caché que refrescar, y lo que pasa
 * después —llevar a `/acceso` con el correo— lo decide quien llama.
 *
 * El cuerpo **no lleva `email`**: lo fija la invitación. Mandarlo sería un 400.
 */
export function useAcceptInvitation(token: string | null) {
  return useMutation({
    mutationFn: (input: AcceptInvitationInput) =>
      apiSendPublic<AcceptedInvitation>(
        'POST',
        `/invitations/token/${encodeURIComponent(token as string)}/accept`,
        input,
      ),
  });
}

// --- Las invitaciones de quien INVITA (#551). Los tres llevan sesión: van por `apiGetAuth`/`apiSend`
// de siempre, no por el camino público de arriba, que existe para páginas que se abren sin cuenta. ---

const INVITATIONS_KEY = ['invitations'];

/**
 * La lista **y el cupo**, que vienen en la misma respuesta (ver `InvitationListView`).
 *
 * `enabled` lo decide quien llama y tiene que incluir `invitesEnabled`: sin registro configurado los
 * cinco endpoints contestan 503, y pedirlo igual solo sirve para pintar un error donde lo correcto
 * es explicar que este servidor no da altas.
 */
export function useInvitations(enabled: boolean) {
  return useQuery({
    queryKey: INVITATIONS_KEY,
    enabled,
    queryFn: () => apiGetAuth<InvitationListView>('/invitations'),
  });
}

/**
 * Invita a un correo. El error lo interpreta quien llama con `mensajeDelErrorAlInvitar()`: los
 * cuatro códigos que devuelve este endpoint dicen cosas muy distintas y ninguna es «ha fallado».
 */
export function useCreateInvitation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (email: string) => apiSend<CreatedInvitation>('POST', '/invitations', { email }),
    onSuccess: () => qc.invalidateQueries({ queryKey: INVITATIONS_KEY }),
  });
}

/**
 * Revoca una invitación. Contesta `204` sin cuerpo, así que el cupo nuevo llega por el refetch que
 * dispara la invalidación — que es exactamente el motivo de que el cupo viaje dentro de la lista.
 */
export function useRevokeInvitation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => apiSend<void>('DELETE', `/invitations/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: INVITATIONS_KEY }),
  });
}
