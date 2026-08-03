import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { usePriceHistory, useProduct } from '../api/hooks';
import type { Honesty, VariantWithPrice } from '../api/types';
import { useAuth } from '../auth/AuthProvider';
import { StoreBadge } from '../components/Badges';
import type { Stock } from '../components/Badges';
import { FollowModal } from '../components/FollowModal';
import { ArrowLeftIcon, BellIcon, ExternalIcon } from '../components/icons';
import { PriceBlock } from '../components/PriceBlock';
import { PriceHistoryChart } from '../components/PriceHistoryChart';
import { ProductImage } from '../components/ProductImage';
import { ProductGridSkeleton, ErrorState } from '../components/States';
import { useToast } from '../components/Toast';
import { colorHex } from '../lib/colors';
import { capitalize } from '../lib/format';

function stockOf(v: VariantWithPrice): Stock {
  if (v.delisted) return 'descatalogado';
  return v.inStock ? 'stock' : 'agotado';
}
function available(v: VariantWithPrice): boolean {
  return !v.delisted;
}

/**
 * Una referencia seleccionable de la ficha: un color **de una ficha concreta de la tienda**.
 *
 * No es lo mismo que un color, y esa es toda la diferencia (#123). En H&M un producto nuestro
 * agrupa varios artículos de la tienda (agrupamos por la raíz del `articleId`), y dos de ellos
 * pueden traer el mismo `colorName` siendo prendas distintas, cada una con su ficha, su precio y
 * sus fotos — 803 casos así en 105 productos, medidos en `dev` el 03/08/2026. Agrupando por el
 * nombre del color, a la segunda no se llegaba por ningún camino y la galería enseñaba las fotos
 * de las dos revueltas.
 *
 * La URL es lo que las separa, y es el mismo criterio que ya usa la API para no colapsarlas
 * (`catalog.service.ts`, la CTE `prenda` de #108). `ordinal` solo se usa para el `title`: cuando
 * dos referencias comparten nombre no hay otro nombre que darles, porque la tienda no publica
 * ninguno, así que se dicen por orden en vez de inventarles un color.
 */
interface ColorRef {
  color: string;
  url: string | null;
  ordinal: number;
  total: number;
}

/**
 * Clave de una referencia. Serializada en vez de concatenada con un separador: cualquier
 * separador que se elija puede aparecer dentro de un nombre de color o de una URL, y entonces
 * dos referencias distintas colisionarían en silencio.
 */
function refKey(color: string, url: string | null): string {
  return JSON.stringify([color, url]);
}

function colorRefs(variants: VariantWithPrice[]): ColorRef[] {
  // Distintas por (color, ficha), en el orden en que las trae la API.
  const distintas = new Map<string, { color: string; url: string | null }>();
  for (const v of variants) {
    if (!v.color) continue;
    const key = refKey(v.color, v.url);
    if (!distintas.has(key)) distintas.set(key, { color: v.color, url: v.url });
  }
  const porColor = new Map<string, number>();
  for (const ref of distintas.values()) porColor.set(ref.color, (porColor.get(ref.color) ?? 0) + 1);

  const vistas = new Map<string, number>();
  return [...distintas.values()].map((ref) => {
    const ordinal = (vistas.get(ref.color) ?? 0) + 1;
    vistas.set(ref.color, ordinal);
    return { ...ref, ordinal, total: porColor.get(ref.color) ?? 1 };
  });
}

export function ProductPage() {
  const { id } = useParams();
  const productId = id ? Number(id) : undefined;
  const navigate = useNavigate();
  const toast = useToast();
  const auth = useAuth();

  const { data: product, isPending, isError, refetch } = useProduct(productId);

  const [size, setSize] = useState<string | null>(null);
  const [color, setColor] = useState<string | null>(null);
  // La ficha de la tienda de la referencia elegida: junto al color, es lo que la identifica.
  const [refUrl, setRefUrl] = useState<string | null>(null);
  const [followOpen, setFollowOpen] = useState(false);

  const variants = useMemo(() => product?.variants ?? [], [product]);
  const sizes = useMemo(() => [...new Set(variants.map((v) => v.size).filter((s): s is string => !!s))], [variants]);
  const refs = useMemo(() => colorRefs(variants), [variants]);

  // Selección inicial: primera variante disponible (o la primera).
  useEffect(() => {
    if (!product) return;
    const first = variants.find(available) ?? variants[0];
    if (first) {
      setSize(first.size);
      setColor(first.color);
      setRefUrl(first.url);
    }
  }, [product]); // eslint-disable-line react-hooks/exhaustive-deps

  const current: VariantWithPrice | undefined = useMemo(() => {
    if (!variants.length) return undefined;
    return (
      variants.find((v) => v.size === size && v.color === color && v.url === refUrl) ??
      variants.find((v) => v.size === size && v.color === color) ??
      variants.find((v) => v.size === size) ??
      variants.find(available) ??
      variants[0]
    );
  }, [variants, size, color, refUrl]);

  /**
   * Fotos de la referencia seleccionada. Esto es lo que mantiene coherentes foto y precio: el
   * precio cuelga de la variante (talla+color), así que al cambiar de referencia cambia `current`
   * —y con él el precio— y la galería tiene que moverse con él.
   *
   * La cadena de respaldo tiene cuatro escalones y **el segundo es el que sostiene todo lo demás**:
   *
   *   1. fotos de este color atribuidas a ESTA ficha — el caso que arregla la #123;
   *   2. fotos de este color sin ficha atribuida (`variantUrl === null`) — las otras seis tiendas,
   *      que no distinguen dos artículos bajo un mismo nombre de color, y también H&M mientras la
   *      galería siga siendo la de antes de la 0023: la columna se puebla según el detalle
   *      condicional y el refresco forzado vuelvan a pedir cada producto, no de golpe;
   *   3. fotos sin color atribuible;
   *   4. la foto suelta del producto.
   *
   * Los escalones 2-4 son literalmente el comportamiento anterior, así que ninguna tienda cambia
   * de aspecto por este arreglo: solo aparece donde hay dato nuevo.
   */
  const gallery = useMemo(() => {
    const images = product?.images ?? [];
    const ofRef = images.filter((i) => i.color === color && i.variantUrl === refUrl);
    const ofColor = ofRef.length ? ofRef : images.filter((i) => i.color === color && i.variantUrl === null);
    const usable = ofColor.length ? ofColor : images.filter((i) => i.color === null);
    if (usable.length) return usable.map((i) => i.url);
    return product?.imageUrl ? [product.imageUrl] : [];
  }, [product, color, refUrl]);

  // Al cambiar de referencia la miniatura activa vuelve a la primera: el índice de la anterior no
  // significa nada en la nueva (y puede no existir).
  const [shot, setShot] = useState(0);
  useEffect(() => setShot(0), [color, refUrl]);
  const heroSrc = gallery[shot] ?? gallery[0] ?? null;

  const history = usePriceHistory(current?.id);

  if (isPending) {
    return (
      <section style={{ paddingTop: 24 }}>
        <ProductGridSkeleton count={4} />
      </section>
    );
  }
  if (isError || !product) {
    return (
      <section style={{ paddingTop: 24 }}>
        <ErrorState onRetry={() => refetch()} />
      </section>
    );
  }

  const honesty: Honesty = current?.honesty ?? 'none';

  const refForSize = (r: ColorRef) =>
    variants.some((v) => v.color === r.color && v.url === r.url && v.size === size && available(v));
  const sizeAvailable = (s: string) => variants.some((v) => v.size === s && available(v));

  const onFollow = () => {
    // Hasta que `/api/config` no resuelve, `enabled` no es concluyente (ver AuthProvider).
    if (!auth.ready) return;
    if (!auth.enabled) {
      toast('Inicio de sesión con Keycloak · disponible al desplegar');
      return;
    }
    if (!auth.authenticated) {
      auth.login();
      return;
    }
    setFollowOpen(true);
  };

  return (
    <section className="dt-fade" style={{ paddingTop: 18 }}>
      <button
        onClick={() => navigate('/catalogo')}
        style={{ display: 'inline-flex', alignItems: 'center', gap: 7, background: 'none', border: 'none', color: 'var(--text-muted)', fontWeight: 700, fontSize: 14, cursor: 'pointer', padding: '8px 0', marginBottom: 8 }}
      >
        <ArrowLeftIcon size={16} /> Volver al catálogo
      </button>

      <div className="dt-detail" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 36 }}>
        {/* galería */}
        <div>
          <div style={{ borderRadius: 'var(--r-lg)', overflow: 'hidden', border: '1px solid var(--border)', background: 'var(--surface)' }}>
            <ProductImage src={heroSrc} alt={product.name} section={product.section} width={1024} />
          </div>
          {gallery.length > 1 && (
            <div style={{ display: 'flex', gap: 8, marginTop: 10, overflowX: 'auto', paddingBottom: 4 }}>
              {gallery.map((url, i) => (
                <button
                  key={url}
                  onClick={() => setShot(i)}
                  aria-label={`Foto ${i + 1} de ${gallery.length}`}
                  aria-current={i === shot}
                  style={{
                    flex: '0 0 auto', width: 68, padding: 0, cursor: 'pointer', borderRadius: 'var(--r-sm)',
                    overflow: 'hidden', background: 'none',
                    border: `2px solid ${i === shot ? 'var(--accent)' : 'var(--border)'}`,
                  }}
                >
                  <ProductImage src={url} alt="" section={product.section} width={160} />
                </button>
              ))}
            </div>
          )}
        </div>

        {/* info */}
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
            <StoreBadge name={product.retailerName} />
            {product.category && (
              <>
                <span style={{ color: 'var(--text-faint)', fontSize: 13 }}>·</span>
                <span style={{ color: 'var(--text-faint)', fontSize: 13 }}>{capitalize(product.category)}</span>
              </>
            )}
          </div>
          <h1 className="serif" style={{ fontSize: 33, lineHeight: 1.08, margin: '0 0 18px' }}>{product.name}</h1>

          {/* precio */}
          <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--r-lg)', padding: 20, marginBottom: 20 }}>
            {current ? (
              <PriceBlock price={current.price} listPrice={current.listPrice} discountPct={current.discountPct} stock={stockOf(current)} honesty={honesty} />
            ) : (
              <div style={{ color: 'var(--text-muted)' }}>Sin precio disponible.</div>
            )}
          </div>

          {/* talla */}
          {sizes.length > 0 && (
            <div style={{ marginBottom: 18 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 9 }}>
                <span style={{ fontWeight: 800, fontSize: 14 }}>Talla</span>
                <span style={{ fontSize: 13, color: 'var(--text-faint)' }}>{sizes.length} disponibles</span>
              </div>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                {sizes.map((s) => {
                  const sel = s === size;
                  const dis = !sizeAvailable(s);
                  return (
                    <button
                      key={s}
                      disabled={dis}
                      onClick={() => setSize(s)}
                      style={{
                        minWidth: 46,
                        padding: '10px 14px',
                        borderRadius: 'var(--r-sm)',
                        border: '1.5px solid ' + (sel ? 'var(--accent)' : 'var(--border)'),
                        background: sel ? 'var(--accent-soft)' : 'var(--surface)',
                        color: dis ? 'var(--text-faint)' : sel ? 'var(--accent)' : 'var(--text)',
                        fontWeight: 800,
                        fontSize: 14,
                        cursor: dis ? 'not-allowed' : 'pointer',
                        textDecoration: dis ? 'line-through' : 'none',
                        opacity: dis ? 0.6 : 1,
                      }}
                    >
                      {s}
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          {/* color */}
          {refs.length > 0 && (
            <div style={{ marginBottom: 22 }}>
              <div style={{ fontWeight: 800, fontSize: 14, marginBottom: 9 }}>
                Color · <span style={{ color: 'var(--text-muted)', fontWeight: 600 }}>{color ? capitalize(color) : '—'}</span>
              </div>
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                {refs.map((r) => {
                  const sel = r.color === color && r.url === refUrl;
                  const hex = colorHex(r.color);
                  const dis = size !== null && !refForSize(r);
                  // Dos referencias con el mismo nombre son dos prendas distintas de la tienda, y
                  // la tienda no les da otro nombre: se dicen por orden en vez de inventarles uno.
                  const etiqueta =
                    r.total > 1 ? `${capitalize(r.color)} · ${r.ordinal}ª referencia` : capitalize(r.color);
                  return (
                    <button
                      key={refKey(r.color, r.url)}
                      aria-label={etiqueta}
                      title={etiqueta}
                      disabled={dis}
                      onClick={() => {
                        setColor(r.color);
                        setRefUrl(r.url);
                      }}
                      style={{
                        width: 38,
                        height: 38,
                        borderRadius: '50%',
                        cursor: dis ? 'not-allowed' : 'pointer',
                        background: hex ?? 'var(--surface-2)',
                        border: '2.5px solid ' + (sel ? 'var(--accent)' : 'var(--border-strong)'),
                        boxShadow: sel ? '0 0 0 3px var(--accent-soft)' : 'none',
                        opacity: dis ? 0.35 : 1,
                        display: 'grid',
                        placeItems: 'center',
                        fontSize: 10,
                        fontWeight: 800,
                        color: 'var(--ink-500)',
                      }}
                    >
                      {hex ? '' : r.color.slice(0, 2)}
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 14 }}>
            <button
              onClick={onFollow}
              className="btn btn-primary"
              style={{ flex: 1, minWidth: 180, padding: 15, fontSize: 15.5, boxShadow: 'var(--shadow-2)' }}
            >
              <BellIcon size={18} />
              Seguir esta variante
            </button>
            <a
              href={current?.url ?? product.url ?? '#'}
              target="_blank"
              rel="noreferrer noopener"
              className="btn btn-secondary"
              style={{ flex: 'none', padding: '15px 22px', fontSize: 15, textDecoration: 'none' }}
            >
              Ver en {product.retailerName} <ExternalIcon size={15} />
            </a>
          </div>

          {/* historial */}
          <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--r-lg)', padding: 20, marginTop: 6 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 4 }}>
              <div>
                <div style={{ fontWeight: 800, fontSize: 15 }}>Historial de precio</div>
                <div style={{ fontSize: 13, color: 'var(--text-faint)' }}>Variante seleccionada</div>
              </div>
            </div>
            {history.isPending ? (
              <div className="dt-skel" style={{ height: 160, marginTop: 12 }} />
            ) : history.isError ? (
              <div style={{ color: 'var(--text-muted)', fontSize: 13.5, padding: '20px 0' }}>No se pudo cargar el historial.</div>
            ) : (
              <PriceHistoryChart history={history.data ?? []} />
            )}
            <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', marginTop: 14, fontSize: 12.5, color: 'var(--text-muted)' }}>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}>
                <span style={{ width: 16, height: 3, borderRadius: 2, background: 'var(--accent)', display: 'inline-block' }} />
                Precio real
              </span>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}>
                <span style={{ width: 16, height: 0, borderTop: '2px dashed var(--text-faint)', display: 'inline-block' }} />
                PVP (precio de lista)
              </span>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}>
                <span style={{ width: 10, height: 10, borderRadius: '50%', background: 'var(--good)', display: 'inline-block' }} />
                Mínimo histórico
              </span>
            </div>
          </div>
        </div>
      </div>

      <FollowModal
        open={followOpen}
        onClose={() => setFollowOpen(false)}
        target={{
          productId: product.id,
          productName: product.name,
          variantId: current?.id,
          variantLabel:
            current && (current.size || current.color)
              ? [current.size ? `Talla ${current.size}` : null, current.color].filter(Boolean).join(' · ')
              : null,
        }}
      />
    </section>
  );
}
