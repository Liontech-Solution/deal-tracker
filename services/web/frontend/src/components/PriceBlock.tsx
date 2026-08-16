import { HonestyBadge, StockBadge } from './Badges';
import type { Stock } from './Badges';
import { AlertIcon, CheckIcon, ClockIcon } from './icons';
import type { Honesty, HonestyBasis } from '../api/types';
import { eurStr } from '../lib/format';
import { cifrasDeRebaja, llevaBadge } from '../lib/honesty';

interface Props {
  price: string | null;
  listPrice: string | null;
  discountPct: string | null;
  stock: Stock;
  honesty: Honesty;
  /** Días que llevamos observando la variante. Lo enseña el texto de `unverified` (#332). */
  trackedDays: number;
  /** En qué se apoya la acusación, cuando la hay (#354). Cambia la frase, no el badge. */
  honestyBasis: HonestyBasis | null;
  /** Mínimo de 30 días declarado por la tienda. Lo CITA el texto de una acusación `declarado`. */
  retailerMin30d: string | null;
  /** PVP creíble y descuento sostenible (#436). Es lo que se pinta cuando difiere de lo declarado. */
  honestListPrice: string | null;
  honestDiscountPct: number;
}

export function PriceBlock({
  price,
  listPrice,
  discountPct,
  stock,
  honesty,
  trackedDays,
  honestyBasis,
  retailerMin30d,
  honestListPrice,
  honestDiscountPct,
}: Props) {
  const suspicious = honesty === 'suspicious';
  const unverified = honesty === 'unverified';
  const priceStr = eurStr(price);
  // El tachado y el porcentaje que se PINTAN salen de la regla, no de la tienda (#436). El tachado
  // declarado no se esconde: sigue abajo, rotulado «PVP declarado», que es donde no lo avalamos.
  const cifras = cifrasDeRebaja({ listPrice, discountPct, honestListPrice, honestDiscountPct });
  const listStr = eurStr(cifras.tachado);
  const declaradoStr = eurStr(listPrice);
  const disc = cifras.descuento;
  // La cifra que cita una acusación `declarado` (#354). Si faltara —no debería: la vía declarada no
  // puede dispararse sin ella— el texto cae al de siempre en vez de enseñar un hueco.
  const min30Str = eurStr(retailerMin30d);
  const hasMarkdown = disc !== null && disc > 0 && listStr !== null;

  return (
    <div>
      {llevaBadge(honesty) && (
        <div style={{ marginBottom: 12 }}>
          <HonestyBadge kind={honesty} big />
        </div>
      )}
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
        <span
          className="serif"
          style={{ fontSize: 44, fontWeight: 600, lineHeight: 1, color: suspicious || unverified ? 'var(--text)' : 'var(--accent)' }}
        >
          {priceStr ?? '—'}
        </span>
        {hasMarkdown && (
          <>
            <span style={{ fontSize: 19, color: 'var(--text-faint)', textDecoration: 'line-through' }}>{listStr}</span>
            {/* El acento verde afirma «esto es una ganga». Con `unverified` no lo sabemos, así que
                el porcentaje se pinta en neutro: ni lo celebramos ni lo denunciamos. */}
            <span
              style={{
                background: unverified || !cifras.sostenido ? 'var(--surface-2)' : suspicious ? 'var(--warn-soft)' : 'var(--good-soft)',
                color: unverified || !cifras.sostenido ? 'var(--text-muted)' : suspicious ? 'var(--warn-text)' : 'var(--good-text)',
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
        {declaradoStr && (
          <span style={{ fontSize: 13, color: 'var(--text-faint)' }}>PVP declarado: {declaradoStr}</span>
        )}
      </div>

      {/* El texto solo afirma lo observado (#332). `unverified` es el caso que antes se colaba en
          «Descuento no real»: la tienda enseña un tachado que no hemos podido desmentir, y decir
          que está inflado sería acusarla de un fraude que no hemos comprobado. Se pinta en tono
          neutro —ni alerta ni visto bueno— y contando lo único que sabemos: cuánto llevamos
          mirando. */}
      {honesty !== 'none' && (
        <div
          style={{
            marginTop: 12,
            display: 'flex',
            gap: 10,
            background: unverified ? 'var(--surface-2)' : suspicious ? 'var(--warn-soft)' : 'var(--good-soft)',
            border: '1px solid ' + (unverified ? 'var(--border)' : suspicious ? 'color-mix(in srgb,var(--warn) 30%,transparent)' : 'color-mix(in srgb,var(--good) 30%,transparent)'),
            borderRadius: 12,
            padding: '11px 13px',
          }}
        >
          <span style={{ color: unverified ? 'var(--text-faint)' : suspicious ? 'var(--warn-text)' : 'var(--good-text)', flex: 'none', marginTop: 1 }}>
            {unverified ? <ClockIcon size={17} /> : suspicious ? <AlertIcon size={17} /> : <CheckIcon size={17} sw={2.6} />}
          </span>
          <span style={{ fontSize: 13, lineHeight: 1.5, color: unverified ? 'var(--text-muted)' : suspicious ? 'var(--warn-text)' : 'var(--good-text)', fontWeight: 600 }}>
            {unverified
              ? `Descuento sin confirmar: ${trackedDays === 0 ? 'acabamos de empezar a seguir esta prenda' : `llevamos ${trackedDays} ${trackedDays === 1 ? 'día' : 'días'} siguiéndola`} y su historial todavía no da para saber si el precio tachado es el que costaba de verdad.`
              : suspicious
                ? honestyBasis === 'declarado' && min30Str !== null
                  ? `Descuento no real: la propia tienda declara haber vendido esta prenda a ${min30Str} en los últimos 30 días, por debajo de lo que pides ahora. No ha bajado de verdad.`
                  : 'Descuento no real: el precio tachado está inflado respecto a su historial. No ha bajado de verdad.'
                : 'Rebaja honesta: es el precio más bajo de los últimos meses. Buen momento para comprar.'}
          </span>
        </div>
      )}
    </div>
  );
}
