import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef } from 'react';

import { apiGet, apiGetAuth, apiSend } from './client';
import type {
  CreateInterestInput,
  Facets,
  InterestView,
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
 * `section` viaja al backend porque las tallas y las categorías de ropa y calzado no comparten
 * vocabulario: sin acotar, la lista de tallas es la unión de números de pie y rangos de edad.
 */
export function useFacets(section?: string, deportiva?: boolean) {
  return useQuery({
    queryKey: ['facets', section ?? null, deportiva ?? false],
    queryFn: () =>
      apiGet<Facets>('/catalog/facets', {
        ...(section ? { section } : {}),
        ...(deportiva ? { deportiva: true } : {}),
      }),
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
