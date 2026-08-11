import { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { useFacets, useProducts } from '../api/hooks';
import type { ProductQuery, ProductSort } from '../api/types';
import { FilterPanel } from '../components/FilterPanel';
import type { CatalogFilters } from '../components/FilterPanel';
import { CloseIcon, FilterIcon } from '../components/icons';
import { aplicarPatch } from '../lib/filters';
import { ProductCard } from '../components/ProductCard';
import { EmptyState, ErrorState, ProductGridSkeleton } from '../components/States';
import { capitalize } from '../lib/format';

const SORTS: Array<{ value: ProductSort; label: string }> = [
  { value: 'ofertas', label: 'Mejores ofertas reales' },
  { value: 'precio-asc', label: 'Precio: menor a mayor' },
  { value: 'precio-desc', label: 'Precio: mayor a menor' },
  { value: 'descuento', label: 'Mayor % de rebaja' },
];

export function CatalogPage() {
  const [params, setParams] = useSearchParams();
  const [drawer, setDrawer] = useState(false);

  const filters: CatalogFilters = {
    gender: params.get('gender') ?? '',
    section: params.get('section') ?? '',
    category: params.get('category') ?? '',
    // `getAll` y no `get` (#329): estos tres viajan como parámetro repetido, y con `get` solo
    // llegaría el primero — el catálogo filtraría por una talla mientras el panel enseña tres
    // marcadas.
    size: params.getAll('size'),
    color: params.getAll('color'),
    retailer: params.getAll('retailer'),
    inStock: params.get('inStock') === 'true',
    onlyDeals: params.get('onlyDeals') === 'true',
    deportiva: params.get('deportiva') === 'true',
    minPrice: params.get('minPrice') ?? '',
    maxPrice: params.get('maxPrice') ?? '',
  };
  const search = params.get('q') ?? '';
  const sort = (params.get('sort') as ProductSort) ?? 'ofertas';

  // Las facetas describen ESTA vista, así que se les pasa lo que hay filtrado (#292). Antes solo
  // viajaban `section` y el eje deportiva, y el panel ofrecía tallas que dentro de la categoría ya
  // elegida no existían: se pinchaba el chip y el catálogo salía vacío.
  //
  // `inStock` y `onlyDeals` se quedan fuera y **no es un descuido**: el backend los rechaza con 400
  // porque no cruzan (montar el CTE de precios en cada cambio de filtro sale caro). Por eso se
  // enumera lo que va en vez de reenviar `query` entero.
  const facets = useFacets({
    q: search || undefined,
    gender: filters.gender || undefined,
    section: filters.section || undefined,
    category: filters.category || undefined,
    size: filters.size.length ? filters.size : undefined,
    color: filters.color.length ? filters.color : undefined,
    retailer: filters.retailer.length ? filters.retailer : undefined,
    deportiva: filters.deportiva || undefined,
  });

  const setFilters = (patch: Partial<CatalogFilters & { sort: ProductSort; q: string }>) => {
    setParams(aplicarPatch(params, patch), { replace: true });
  };

  const query: Omit<ProductQuery, 'limit' | 'offset'> = {
    q: search || undefined,
    gender: filters.gender || undefined,
    section: filters.section || undefined,
    category: filters.category || undefined,
    size: filters.size.length ? filters.size : undefined,
    color: filters.color.length ? filters.color : undefined,
    retailer: filters.retailer.length ? filters.retailer : undefined,
    inStock: filters.inStock || undefined,
    onlyDeals: filters.onlyDeals || undefined,
    deportiva: filters.deportiva || undefined,
    minPrice: filters.minPrice ? Number(filters.minPrice) : undefined,
    maxPrice: filters.maxPrice ? Number(filters.maxPrice) : undefined,
    sort,
  };

  const q = useProducts(query);
  const items = useMemo(() => q.data?.pages.flatMap((p) => p.items) ?? [], [q.data]);

  /**
   * Lo que hay en pantalla es el resultado de los filtros ANTERIORES (#292).
   *
   * Con `keepPreviousData` la rejilla ya no parpadea a vacío, pero eso solo cambia la mentira de
   * sitio: pasa de decir «0 prendas» a afirmar un recuento viejo como si fuera el nuevo. Mientras
   * dura, ni la cabecera ni el botón dan número — el sitio donde iba el recuento dice qué está
   * pasando, y la rejilla se atenúa para que se vea que lo de debajo aún no es la respuesta.
   *
   * `isPending` entra en la misma cuenta por el primer render, cuando todavía no hay página previa
   * que conservar.
   */
  const contando = q.isPlaceholderData || q.isPending;
  const recuento = `${items.length}${q.hasNextPage ? '+' : ''}`;

  // chips activos
  const retailerName = (slug: string) => facets.data?.retailers.find((r) => r.slug === slug)?.name ?? slug;
  const chips: Array<{ label: string; clear: () => void }> = [];
  // El término de búsqueda también es un filtro puesto: se quita desde aquí, y el campo de la
  // cabecera se vacía solo porque lee la URL.
  if (search) chips.push({ label: `«${search}»`, clear: () => setFilters({ q: '' }) });
  if (filters.onlyDeals) chips.push({ label: 'Solo ofertas reales', clear: () => setFilters({ onlyDeals: false }) });
  if (filters.gender) chips.push({ label: capitalize(filters.gender), clear: () => setFilters({ gender: '' }) });
  if (filters.section) chips.push({ label: capitalize(filters.section), clear: () => setFilters({ section: '' }) });
  if (filters.category) chips.push({ label: capitalize(filters.category), clear: () => setFilters({ category: '' }) });
  // Un chip POR VALOR, cada uno con su aspa (#329). Un solo chip que dijera «Talla 4 años, 104,
  // 36-38» solo se podría quitar entero, y con tres tallas puestas lo normal es querer soltar una.
  for (const s of filters.size) {
    chips.push({ label: `Talla ${s}`, clear: () => setFilters({ size: filters.size.filter((x) => x !== s) }) });
  }
  for (const c of filters.color) {
    chips.push({ label: capitalize(c), clear: () => setFilters({ color: filters.color.filter((x) => x !== c) }) });
  }
  for (const r of filters.retailer) {
    chips.push({ label: retailerName(r), clear: () => setFilters({ retailer: filters.retailer.filter((x) => x !== r) }) });
  }
  if (filters.inStock) chips.push({ label: 'En stock', clear: () => setFilters({ inStock: false }) });
  if (filters.deportiva)
    chips.push({ label: 'Ropa deportiva', clear: () => setFilters({ deportiva: false }) });
  // Un solo chip para el rango: son dos parámetros pero una sola idea, y dos chips que solo se
  // pueden quitar por separado hacen pensar que son filtros distintos.
  if (filters.minPrice || filters.maxPrice) {
    const desde = filters.minPrice ? `${filters.minPrice} €` : '';
    const hasta = filters.maxPrice ? `${filters.maxPrice} €` : '';
    const label = desde && hasta ? `${desde} – ${hasta}` : desde ? `Desde ${desde}` : `Hasta ${hasta}`;
    chips.push({ label, clear: () => setFilters({ minPrice: '', maxPrice: '' }) });
  }

  const activeCount = chips.length;
  const clearAll = () =>
    setFilters({
      q: '',
      gender: '',
      section: '',
      category: '',
      size: [],
      color: [],
      retailer: [],
      inStock: false,
      onlyDeals: false,
      deportiva: false,
      minPrice: '',
      maxPrice: '',
    });

  return (
    <section className="dt-fade" style={{ paddingTop: 24 }}>
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap', marginBottom: 18 }}>
        <div>
          <h1 className="serif" style={{ fontSize: 34, margin: '0 0 2px' }}>
            {search ? `«${search}»` : 'Catálogo'}
          </h1>
          <div style={{ color: 'var(--text-muted)', fontSize: 14.5 }} aria-live="polite">
            {contando
              ? 'Buscando prendas…'
              : `${recuento} ${items.length === 1 && !q.hasNextPage ? 'prenda' : 'prendas'}`}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--r-pill)', padding: '6px 6px 6px 14px' }}>
            <span style={{ fontSize: 13, color: 'var(--text-faint)', fontWeight: 700 }}>Ordenar</span>
            <select className="select" value={sort} onChange={(e) => setFilters({ sort: e.target.value as ProductSort })}>
              {SORTS.map((s) => (
                <option key={s.value} value={s.value}>{s.label}</option>
              ))}
            </select>
          </div>
          <button className="btn btn-primary dt-filterbtn" style={{ padding: '11px 18px', fontSize: 14 }} onClick={() => setDrawer(true)}>
            <FilterIcon size={16} />
            Filtros
            {activeCount > 0 && (
              <span style={{ background: 'var(--on-accent)', color: 'var(--accent)', borderRadius: 99, fontSize: 11, padding: '1px 7px', fontWeight: 800 }}>{activeCount}</span>
            )}
          </button>
        </div>
      </div>

      {chips.length > 0 && (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 16 }}>
          {chips.map((c) => (
            <button
              key={c.label}
              onClick={c.clear}
              style={{ display: 'inline-flex', alignItems: 'center', gap: 7, background: 'var(--surface)', border: '1px solid var(--border-strong)', borderRadius: 'var(--r-pill)', padding: '7px 10px 7px 13px', fontSize: 13, fontWeight: 700, color: 'var(--text)', cursor: 'pointer' }}
            >
              {c.label} <CloseIcon size={13} sw={2.6} />
            </button>
          ))}
          <button onClick={clearAll} style={{ background: 'none', border: 'none', color: 'var(--accent)', fontWeight: 800, fontSize: 13, cursor: 'pointer', padding: '7px 8px' }}>
            Limpiar todo
          </button>
        </div>
      )}

      <div className="dt-catgrid" style={{ display: 'grid', gridTemplateColumns: '250px 1fr', gap: 24 }}>
        {/* 76 = alto de la cabecera, que desde el rediseño es de una sola fila. */}
        <aside className="dt-sidebar" style={{ alignSelf: 'start', position: 'sticky', top: 76 }}>
          <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--r-lg)', padding: '4px 18px 18px' }}>
            <FilterPanel facets={facets.data} value={filters} onChange={setFilters} />
          </div>
        </aside>

        <div>
          {q.isPending ? (
            <ProductGridSkeleton count={8} />
          ) : q.isError ? (
            <ErrorState onRetry={() => q.refetch()} />
          ) : items.length === 0 ? (
            <EmptyState onClear={clearAll} />
          ) : (
            <>
              {/* Atenuada mientras lo de debajo sigue siendo la respuesta a los filtros de antes.
                  `pointerEvents` para que no se pinche una ficha que está a punto de irse. */}
              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fill,minmax(200px,1fr))',
                  gap: 16,
                  opacity: q.isPlaceholderData ? 0.45 : 1,
                  pointerEvents: q.isPlaceholderData ? 'none' : undefined,
                  transition: 'opacity .15s ease',
                }}
                aria-busy={q.isPlaceholderData}
              >
                {items.map((p) => (
                  <ProductCard key={p.id} p={p} />
                ))}
              </div>
              {q.hasNextPage && (
                <div style={{ textAlign: 'center', marginTop: 32 }}>
                  <button className="btn btn-secondary" style={{ padding: '13px 28px' }} disabled={q.isFetchingNextPage} onClick={() => q.fetchNextPage()}>
                    {q.isFetchingNextPage ? 'Cargando…' : 'Cargar más prendas'}
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {drawer && (
        <>
          <div onClick={() => setDrawer(false)} style={{ position: 'fixed', inset: 0, zIndex: 60, background: 'rgba(30,24,16,.42)', backdropFilter: 'blur(2px)' }} />
          <div style={{ position: 'fixed', left: 0, right: 0, bottom: 0, zIndex: 61, background: 'var(--surface)', borderRadius: 'var(--r-xl) var(--r-xl) 0 0', maxHeight: '88vh', display: 'flex', flexDirection: 'column', animation: 'dt-slideup .28s cubic-bezier(.16,1,.3,1)', boxShadow: 'var(--shadow-3)' }}>
            <div style={{ padding: '8px 0 4px', display: 'grid', placeItems: 'center' }}>
              <div style={{ width: 44, height: 5, borderRadius: 99, background: 'var(--border-strong)' }} />
            </div>
            <div style={{ padding: '4px 20px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div className="serif" style={{ fontSize: 22, fontWeight: 600 }}>Filtros</div>
              <button aria-label="Cerrar" onClick={() => setDrawer(false)} className="btn-ghost" style={{ width: 40, height: 40, borderRadius: '50%', display: 'grid', placeItems: 'center' }}>
                <CloseIcon size={18} />
              </button>
            </div>
            <div style={{ overflowY: 'auto', padding: '0 20px' }}>
              <FilterPanel facets={facets.data} value={filters} onChange={setFilters} />
            </div>
            <div style={{ padding: 20, borderTop: '1px solid var(--border)', display: 'flex', gap: 10 }}>
              {activeCount > 0 && (
                <button className="btn btn-secondary" style={{ padding: 16 }} onClick={clearAll}>Limpiar</button>
              )}
              {/* El botón del reporte original: decía «Ver 0 prendas» mientras cargaba. Ahora, si
                  el recuento aún no es el de estos filtros, lleva a los resultados sin cantarlos.
                  No se deshabilita a propósito: cerrar el cajón es una acción que siempre vale, y
                  un botón muerto en un cajón de móvil parece que la app se ha colgado. */}
              <button className="btn btn-primary" style={{ flex: 1, padding: 16, fontSize: 16 }} onClick={() => setDrawer(false)}>
                {contando ? 'Ver resultados' : `Ver ${recuento} prendas`}
              </button>
            </div>
          </div>
        </>
      )}
    </section>
  );
}
