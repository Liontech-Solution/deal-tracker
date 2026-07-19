import { useInfiniteQuery, useQuery } from '@tanstack/react-query';

import { apiGet } from './client';
import type { Facets, PricePoint, ProductDetail, ProductListResult, ProductQuery } from './types';

const PAGE_SIZE = 12;

/** Facetas para poblar los filtros (género/sección/categoría/talla/color/tiendas). */
export function useFacets() {
  return useQuery({
    queryKey: ['facets'],
    queryFn: () => apiGet<Facets>('/catalog/facets'),
    staleTime: 5 * 60 * 1000,
  });
}

/** Catálogo paginado con "cargar más" (offset). `query` no incluye limit/offset. */
export function useProducts(query: Omit<ProductQuery, 'limit' | 'offset'>) {
  return useInfiniteQuery({
    queryKey: ['products', query],
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      apiGet<ProductListResult>('/catalog/products', {
        ...query,
        limit: PAGE_SIZE,
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
