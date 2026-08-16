import { AlertIcon, CheckIcon, ClockIcon } from './icons';
import { storeColor } from '../lib/stores';

/**
 * Los tres veredictos que afirman algo. El de en medio, `reciente`, nace con #436.
 *
 * **El color es la afirmación**, no el rótulo: el verde dice «esto es una ganga comprobada» y solo
 * se lo gana quien tiene cobertura para sostenerlo (`REAL_EVIDENCE_DAYS`). `reciente` va en neutro
 * a propósito aunque sea una buena noticia — ha bajado — porque lo que no sabemos es si el precio
 * del que ha bajado significaba algo. Pintarlo de verde sería el elogio sin pruebas otra vez, con
 * otro nombre.
 */
export function HonestyBadge({
  kind,
  big = false,
}: {
  kind: 'real' | 'reciente' | 'suspicious';
  big?: boolean;
}) {
  const real = kind === 'real';
  const reciente = kind === 'reciente';
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        background: reciente ? 'var(--surface-2)' : real ? 'var(--good-soft)' : 'var(--warn-soft)',
        color: reciente ? 'var(--text-muted)' : real ? 'var(--good-text)' : 'var(--warn-text)',
        border: reciente ? '1px solid var(--border)' : undefined,
        borderRadius: 999,
        padding: big ? '6px 12px' : '4px 9px',
        fontSize: big ? 13 : 11.5,
        fontWeight: 800,
        whiteSpace: 'nowrap',
      }}
    >
      {reciente ? (
        <ClockIcon size={big ? 15 : 12} />
      ) : real ? (
        <CheckIcon size={big ? 15 : 12} sw={3} />
      ) : (
        <AlertIcon size={big ? 15 : 12} />
      )}
      {reciente ? 'Bajada reciente' : real ? 'Oferta real' : 'Precio inflado'}
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
