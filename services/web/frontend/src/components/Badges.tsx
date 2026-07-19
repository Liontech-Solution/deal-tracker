import { AlertIcon, CheckIcon } from './icons';
import { storeColor } from '../lib/stores';

/** Etiqueta de honestidad del descuento: oferta real vs precio inflado. */
export function HonestyBadge({ kind, big = false }: { kind: 'real' | 'suspicious'; big?: boolean }) {
  const real = kind === 'real';
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        background: real ? 'var(--good-soft)' : 'var(--warn-soft)',
        color: real ? 'var(--good-text)' : 'var(--warn-text)',
        borderRadius: 999,
        padding: big ? '6px 12px' : '4px 9px',
        fontSize: big ? 13 : 11.5,
        fontWeight: 800,
        whiteSpace: 'nowrap',
      }}
    >
      {real ? <CheckIcon size={big ? 15 : 12} sw={3} /> : <AlertIcon size={big ? 15 : 12} />}
      {real ? 'Oferta real' : 'Precio inflado'}
    </span>
  );
}

/** Chip de la tienda de origen con su punto de marca. */
export function StoreBadge({ name }: { name: string }) {
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        background: 'var(--surface-2)',
        border: '1px solid var(--border)',
        borderRadius: 999,
        padding: '4px 11px 4px 8px',
        fontSize: 12,
        fontWeight: 800,
        color: 'var(--text)',
        whiteSpace: 'nowrap',
      }}
    >
      <span
        style={{ width: 8, height: 8, borderRadius: '50%', background: storeColor(name), flex: 'none' }}
      />
      {name}
    </span>
  );
}

export type Stock = 'stock' | 'agotado' | 'descatalogado';

export function StockBadge({ state }: { state: Stock }) {
  const map: Record<Stock, [string, string]> = {
    stock: ['var(--good)', 'En stock'],
    agotado: ['var(--text-faint)', 'Agotado'],
    descatalogado: ['var(--warn)', 'Descatalogado'],
  };
  const [dot, label] = map[state];
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        fontSize: 12.5,
        fontWeight: 700,
        color: state === 'stock' ? 'var(--good-text)' : 'var(--text-muted)',
      }}
    >
      <span style={{ width: 8, height: 8, borderRadius: '50%', background: dot, flex: 'none' }} />
      {label}
    </span>
  );
}
