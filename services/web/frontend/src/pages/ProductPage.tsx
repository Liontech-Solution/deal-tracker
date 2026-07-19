import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { usePriceHistory, useProduct } from '../api/hooks';
import type { VariantWithPrice } from '../api/types';
import { StoreBadge } from '../components/Badges';
import type { Stock } from '../components/Badges';
import { ArrowLeftIcon, BellIcon, ExternalIcon } from '../components/icons';
import { PriceBlock } from '../components/PriceBlock';
import { PriceHistoryChart } from '../components/PriceHistoryChart';
import { ProductGridSkeleton, ErrorState } from '../components/States';
import { useToast } from '../components/Toast';
import { colorHex } from '../lib/colors';
import { capitalize } from '../lib/format';
import { honestyFromHistory, honestyFromLatest } from '../lib/honesty';
import type { Honesty } from '../lib/honesty';
import { sectionBg, stripeBg } from '../lib/section';

function stockOf(v: VariantWithPrice): Stock {
  if (v.delisted) return 'descatalogado';
  return v.inStock ? 'stock' : 'agotado';
}
function available(v: VariantWithPrice): boolean {
  return !v.delisted;
}

export function ProductPage() {
  const { id } = useParams();
  const productId = id ? Number(id) : undefined;
  const navigate = useNavigate();
  const toast = useToast();

  const { data: product, isPending, isError, refetch } = useProduct(productId);

  const [size, setSize] = useState<string | null>(null);
  const [color, setColor] = useState<string | null>(null);

  const variants = useMemo(() => product?.variants ?? [], [product]);
  const sizes = useMemo(() => [...new Set(variants.map((v) => v.size).filter((s): s is string => !!s))], [variants]);
  const colors = useMemo(() => [...new Set(variants.map((v) => v.color).filter((c): c is string => !!c))], [variants]);

  // Selección inicial: primera variante disponible (o la primera).
  useEffect(() => {
    if (!product) return;
    const first = variants.find(available) ?? variants[0];
    if (first) {
      setSize(first.size);
      setColor(first.color);
    }
  }, [product]); // eslint-disable-line react-hooks/exhaustive-deps

  const current: VariantWithPrice | undefined = useMemo(() => {
    if (!variants.length) return undefined;
    return (
      variants.find((v) => v.size === size && v.color === color) ??
      variants.find((v) => v.size === size) ??
      variants.find(available) ??
      variants[0]
    );
  }, [variants, size, color]);

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

  const honesty: Honesty = history.data
    ? honestyFromHistory(history.data)
    : current
      ? honestyFromLatest(current.price, current.listPrice, current.discountPct)
      : 'none';

  const colorForSize = (c: string) => variants.some((v) => v.color === c && v.size === size && available(v));
  const sizeAvailable = (s: string) => variants.some((v) => v.size === s && available(v));

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
            <div style={{ aspectRatio: '1', background: stripeBg(sectionBg(product.section), 16), display: 'grid', placeItems: 'center' }}>
              <span style={{ fontSize: 12, color: 'var(--ink-500)', background: 'var(--surface)', padding: '6px 12px', borderRadius: 99, border: '1px solid var(--border)', fontWeight: 700 }}>
                FOTO · {product.name}
              </span>
            </div>
          </div>
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
          {colors.length > 0 && (
            <div style={{ marginBottom: 22 }}>
              <div style={{ fontWeight: 800, fontSize: 14, marginBottom: 9 }}>
                Color · <span style={{ color: 'var(--text-muted)', fontWeight: 600 }}>{color ? capitalize(color) : '—'}</span>
              </div>
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                {colors.map((c) => {
                  const sel = c === color;
                  const hex = colorHex(c);
                  const dis = size !== null && !colorForSize(c);
                  return (
                    <button
                      key={c}
                      aria-label={c}
                      title={capitalize(c)}
                      disabled={dis}
                      onClick={() => setColor(c)}
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
                      {hex ? '' : c.slice(0, 2)}
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 14 }}>
            <button
              onClick={() => toast('Inicia sesión para seguir esta variante · muy pronto')}
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
    </section>
  );
}
