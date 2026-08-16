import { useNavigate } from 'react-router-dom';

import { HonestyBadge, StockBadge, StoreBadge } from './Badges';
import { FollowModal } from './FollowModal';
import { BellIcon } from './icons';
import { ProductImage } from './ProductImage';
import type { ProductListItem } from '../api/types';
import { useSeguirPrenda } from '../auth/useSeguirPrenda';
import { eurStr } from '../lib/format';
import { cifrasDeRebaja, llevaBadge } from '../lib/honesty';

export function ProductCard({ p }: { p: ProductListItem }) {
  const navigate = useNavigate();
  const seguir = useSeguirPrenda();

  const honesty = p.honesty;
  const suspicious = honesty === 'suspicious';
  const price = eurStr(p.priceFrom);
  // Lo que se pinta NO es sin más lo que declara la tienda (#436): cuando la regla ha descartado su
  // tachado, la tarjeta enseña el PVP creíble y el descuento que ese PVP sostiene. Misma decisión
  // que la ficha, tomada en el mismo sitio.
  const cifras = cifrasDeRebaja({
    listPrice: p.listFrom,
    discountPct: p.discountFrom,
    honestListPrice: p.honestListPrice,
    honestDiscountPct: p.honestDiscountPct,
  });
  const list = eurStr(cifras.tachado);
  const disc = cifras.descuento;

  return (
    <>
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
        <ProductImage src={p.imageUrl} alt={p.name} section={p.section} width={563} />
        {llevaBadge(honesty) && (
          <div style={{ position: 'absolute', top: 10, left: 10 }}>
            <HonestyBadge kind={honesty} />
          </div>
        )}
        {/* Abre el mismo modal que la ficha, con alcance de producto entero: desde la rejilla no
            hay talla ni color elegidos, así que el aviso cubre «todas las tallas y colores», que es
            un caso que `FollowModal` ya contempla. El `stopPropagation` sigue siendo necesario:
            toda la tarjeta navega a la ficha. */}
        <button
          aria-label="Seguir prenda"
          onClick={(e) => {
            e.stopPropagation();
            seguir.abrir();
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
                // El verde afirma «esto es una ganga». Sin PVP creíble no lo sabemos, así que el
                // porcentaje sale en neutro: ni lo celebramos ni lo denunciamos.
                color: suspicious
                  ? 'var(--warn-text)'
                  : cifras.sostenido
                    ? 'var(--good-text)'
                    : 'var(--text-muted)',
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

    {/* Hermano de la tarjeta y no hijo suyo: el modal es una capa fija, así que da igual dónde
        cuelgue, pero dentro de la tarjeta cualquier clic suyo burbujearía hasta el `onClick` que
        navega a la ficha y el modal se cerraría llevándote a otra página. */}
    <FollowModal
      open={seguir.abierto}
      onClose={seguir.cerrar}
      target={{ productId: p.id, productName: p.name }}
    />
    </>
  );
}
