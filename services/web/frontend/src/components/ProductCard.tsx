import { useNavigate } from 'react-router-dom';

import { HonestyBadge, StockBadge, StoreBadge } from './Badges';
import { HeartIcon } from './icons';
import { ProductImage } from './ProductImage';
import type { ProductListItem } from '../api/types';
import { useFavorito } from '../auth/useFavorito';
import { eurStr } from '../lib/format';
import { cifrasDeRebaja, llevaBadge } from '../lib/honesty';

export function ProductCard({ p }: { p: ProductListItem }) {
  const navigate = useNavigate();
  const favorito = useFavorito(p.id);

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
        {/* Guardar la prenda, sin pedir aviso (#435). Aquí va el CORAZÓN y no la campana, y la
            razón está medida: a los 200 px a los que baja la rejilla (`CatalogPage`), dos botones
            de 38 px dejan 96 px para el badge de honestidad, y los tres rótulos lo pasan —«Oferta
            real» 96,2, «Precio inflado» 111,4 y «Bajada reciente» 120,2—. O sea que los dos no
            caben, y la salida que #435 declaraba preferida es dejar el corazón: la campana sigue
            entera en la ficha y en `/favoritos`, donde además se puede elegir talla.

            El `stopPropagation` es imprescindible: toda la tarjeta navega a la ficha. */}
        <button
          aria-label={favorito.esFavorito ? 'Quitar de favoritos' : 'Guardar en favoritos'}
          title={favorito.esFavorito ? 'Quitar de favoritos' : 'Guardar en favoritos'}
          aria-pressed={favorito.esFavorito}
          disabled={favorito.ocupado}
          onClick={(e) => {
            e.stopPropagation();
            favorito.alternar();
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
          <HeartIcon size={18} filled={favorito.esFavorito} />
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
            // `reciente` mantiene el acento: la prenda SÍ ha bajado, y eso no está en duda (#436).
            // Lo que se le retira es el verde del porcentaje y la palabra «real» del badge.
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
  );
}
