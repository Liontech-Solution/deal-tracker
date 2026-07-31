import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

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
export function useFacets(section?: string) {
  return useQuery({
    queryKey: ['facets', section ?? null],
    queryFn: () => apiGet<Facets>('/catalog/facets', section ? { section } : undefined),
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

/**
 * Estado del vínculo de Telegram. `enabled` para no pedir sin sesión. Mientras hay un enlace
 * en curso (token vivo sin confirmar), sondea suave para detectar cuándo el bot lo confirma.
 */
export function useTelegramSettings(enabled: boolean) {
  return useQuery({
    queryKey: TELEGRAM_KEY,
    enabled,
    queryFn: () => apiGetAuth<TelegramSettingsView>('/settings/telegram'),
    refetchInterval: (query) => (query.state.data?.pendingLink ? 4000 : false),
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
