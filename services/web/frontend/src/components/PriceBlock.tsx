import { HonestyBadge, StockBadge } from './Badges';
import type { Stock } from './Badges';
import { AlertIcon, CheckIcon } from './icons';
import type { Honesty } from '../lib/honesty';
import { discountInt, eurStr } from '../lib/format';

interface Props {
  price: string | null;
  listPrice: string | null;
  discountPct: string | null;
  stock: Stock;
  honesty: Honesty;
}

export function PriceBlock({ price, listPrice, discountPct, stock, honesty }: Props) {
  const suspicious = honesty === 'suspicious';
  const priceStr = eurStr(price);
  const listStr = eurStr(listPrice);
  const disc = discountInt(discountPct);
  const hasMarkdown = disc !== null && disc > 0 && listStr !== null;

  return (
    <div>
      {honesty !== 'none' && (
        <div style={{ marginBottom: 12 }}>
          <HonestyBadge kind={honesty} big />
        </div>
      )}
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
        <span
          className="serif"
          style={{ fontSize: 44, fontWeight: 600, lineHeight: 1, color: suspicious ? 'var(--text)' : 'var(--accent)' }}
        >
          {priceStr ?? '—'}
        </span>
        {hasMarkdown && (
          <>
            <span style={{ fontSize: 19, color: 'var(--text-faint)', textDecoration: 'line-through' }}>{listStr}</span>
            <span
              style={{
                background: suspicious ? 'var(--warn-soft)' : 'var(--good-soft)',
                color: suspicious ? 'var(--warn-text)' : 'var(--good-text)',
                borderRadius: 999,
                padding: '4px 11px',
                fontSize: 14,
                fontWeight: 800,
              }}
            >
              -{disc}%
            </span>
          </>
        )}
      </div>

      <div style={{ marginTop: 12, display: 'flex', gap: 14, alignItems: 'center', flexWrap: 'wrap' }}>
        <StockBadge state={stock} />
        {listStr && <span style={{ fontSize: 13, color: 'var(--text-faint)' }}>PVP declarado: {listStr}</span>}
      </div>

      {honesty !== 'none' && (
        <div
          style={{
            marginTop: 12,
            display: 'flex',
            gap: 10,
            background: suspicious ? 'var(--warn-soft)' : 'var(--good-soft)',
            border: '1px solid ' + (suspicious ? 'color-mix(in srgb,var(--warn) 30%,transparent)' : 'color-mix(in srgb,var(--good) 30%,transparent)'),
            borderRadius: 12,
            padding: '11px 13px',
          }}
        >
          <span style={{ color: suspicious ? 'var(--warn-text)' : 'var(--good-text)', flex: 'none', marginTop: 1 }}>
            {suspicious ? <AlertIcon size={17} /> : <CheckIcon size={17} sw={2.6} />}
          </span>
          <span style={{ fontSize: 13, lineHeight: 1.5, color: suspicious ? 'var(--warn-text)' : 'var(--good-text)', fontWeight: 600 }}>
            {suspicious
              ? 'Descuento no real: el precio tachado está inflado respecto a su historial. No ha bajado de verdad.'
              : 'Rebaja honesta: es el precio más bajo de los últimos meses. Buen momento para comprar.'}
          </span>
        </div>
      )}
    </div>
  );
}
