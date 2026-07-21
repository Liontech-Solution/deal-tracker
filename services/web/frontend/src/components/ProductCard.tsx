import { useNavigate } from 'react-router-dom';

import { HonestyBadge, StockBadge, StoreBadge } from './Badges';
import { BellIcon } from './icons';
import { useToast } from './Toast';
import type { ProductListItem } from '../api/types';
import { discountInt, eurStr } from '../lib/format';
import { sectionBg, stripeBg } from '../lib/section';

export function ProductCard({ p }: { p: ProductListItem }) {
  const navigate = useNavigate();
  const toast = useToast();

  const honesty = p.honesty;
  const suspicious = honesty === 'suspicious';
  const price = eurStr(p.priceFrom);
  const list = eurStr(p.listFrom);
  const disc = discountInt(p.discountFrom);

  return (
    <div
      className="card-hover"
      onClick={() => navigate(`/producto/${p.id}`)}
      style={{
        background: 'var(--surface)',
        border: '1px solid var(--border)',
        borderRadius: 'var(--r-lg)',
        overflow: 'hidden',
        cursor: 'pointer',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      <div style={{ position: 'relative' }}>
        <div
          style={{
            aspectRatio: '1',
            background: stripeBg(sectionBg(p.section)),
            display: 'grid',
            placeItems: 'center',
          }}
        >
          <span
            style={{
              fontSize: 10.5,
              color: 'var(--ink-500)',
              background: 'var(--surface)',
              padding: '4px 9px',
              borderRadius: 99,
              border: '1px solid var(--border)',
              fontWeight: 700,
            }}
          >
            FOTO
          </span>
        </div>
        {honesty !== 'none' && (
          <div style={{ position: 'absolute', top: 10, left: 10 }}>
            <HonestyBadge kind={honesty} />
          </div>
        )}
        <button
          aria-label="Seguir prenda"
          onClick={(e) => {
            e.stopPropagation();
            toast('Inicia sesión para seguir prendas · muy pronto');
          }}
          style={{
            position: 'absolute',
            top: 10,
            right: 10,
            width: 38,
            height: 38,
            borderRadius: '50%',
            background: 'var(--surface)',
            border: '1px solid var(--border)',
            cursor: 'pointer',
            display: 'grid',
            placeItems: 'center',
            color: 'var(--accent)',
            boxShadow: 'var(--shadow-1)',
          }}
        >
          <BellIcon size={18} />
        </button>
      </div>

      <div style={{ padding: '13px 14px 15px', display: 'flex', flexDirection: 'column', gap: 7, flex: 1 }}>
        <StoreBadge name={p.retailerName} />
        <div style={{ fontWeight: 800, fontSize: 14.5, lineHeight: 1.25 }}>{p.name}</div>
        <div
          style={{
            marginTop: 'auto',
            display: 'flex',
            alignItems: 'baseline',
            gap: 8,
            flexWrap: 'wrap',
          }}
        >
          <span style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>desde</span>
          <span
            className="serif"
            style={{ fontSize: 22, fontWeight: 600, color: suspicious ? 'var(--text)' : 'var(--accent)' }}
          >
            {price ?? '—'}
          </span>
          {list && disc !== null && disc > 0 && (
            <span style={{ fontSize: 12.5, color: 'var(--text-faint)', textDecoration: 'line-through' }}>
              {list}
            </span>
          )}
          {disc !== null && disc > 0 && (
            <span
              style={{
                fontSize: 12,
                fontWeight: 800,
                color: suspicious ? 'var(--warn-text)' : 'var(--good-text)',
              }}
            >
              -{disc}%
            </span>
          )}
        </div>
        <div style={{ marginTop: 2 }}>
          <StockBadge state={p.anyInStock ? 'stock' : 'agotado'} />
        </div>
      </div>
    </div>
  );
}
