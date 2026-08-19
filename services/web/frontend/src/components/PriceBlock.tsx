import { HonestyBadge, StockBadge } from './Badges';
import type { Stock } from './Badges';
import { AlertIcon, CheckIcon, ClockIcon } from './icons';
import type { Honesty, HonestyBasis } from '../api/types';
import { eurStr } from '../lib/format';
import type { TonoDescuento } from '../lib/honesty';
import {
  cifrasDeRebaja,
  llevaBadge,
  textoDeLaCaja,
  tonoDeLaCaja,
  tonoDelDescuento,
  tonoDelPrecio,
} from '../lib/honesty';

/**
 * Cómo se traduce a CSS el tono de la caja explicativa. Aquí no se DECIDE nada: qué tono le toca a
 * cada veredicto lo dice `tonoDeLaCaja()` y esta tabla solo lo pinta (#489). Si en este fichero
 * vuelve a aparecer un booleano derivado de `honesty`, es la divergencia volviendo.
 */
const CAJA: Record<
  TonoDescuento,
  { fondo: string; borde: string; icono: string; texto: string; Icono: typeof ClockIcon }
> = {
  neutro: {
    fondo: 'var(--surface-2)',
    borde: 'var(--border)',
    icono: 'var(--text-faint)',
    texto: 'var(--text-muted)',
    Icono: ClockIcon,
  },
  warn: {
    fondo: 'var(--warn-soft)',
    borde: 'color-mix(in srgb,var(--warn) 30%,transparent)',
    icono: 'var(--warn-text)',
    texto: 'var(--warn-text)',
    Icono: AlertIcon,
  },
  good: {
    fondo: 'var(--good-soft)',
    borde: 'color-mix(in srgb,var(--good) 30%,transparent)',
    icono: 'var(--good-text)',
    texto: 'var(--good-text)',
    Icono: CheckIcon,
  },
};

interface Props {
  price: string | null;
  listPrice: string | null;
  discountPct: string | null;
  stock: Stock;
  honesty: Honesty;
  /** Días que llevamos observando la variante. Lo enseña el texto de `unverified` (#332). */
  trackedDays: number;
  /**
   * El tramo que una afirmación de MÍNIMO puede citar sin mentir (#517): `trackedDays` con el
   * techo de la ventana de honestidad ya aplicado por el backend. Es el que va en los textos de
   * `reciente` y `real`, mientras que `trackedDays` es el que va cuando la frase habla de
   * cobertura. Hoy coinciden en todas partes, y esa coincidencia caduca sola.
   */
  claimDays: number;
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
  claimDays,
  honestyBasis,
  retailerMin30d,
  honestListPrice,
  honestDiscountPct,
}: Props) {
  // De qué color va la caja NO se decide aquí: la condición vive en `tonoDeLaCaja()` por lo mismo
  // que la del porcentaje vive en `tonoDelDescuento()` (#489). `null` es «la caja no se pinta», que
  // es el único caso que la función no cubre porque lo tiene fuera del tipo.
  const caja = honesty === 'none' ? null : CAJA[tonoDeLaCaja(honesty)];
  const priceStr = eurStr(price);
  // El tachado y el porcentaje que se PINTAN salen de la regla, no de la tienda (#436). El tachado
  // declarado no se esconde: sigue abajo, rotulado «PVP declarado», que es donde no lo avalamos.
  const cifras = cifrasDeRebaja({ listPrice, discountPct, honestListPrice, honestDiscountPct });
  // Misma condición que la tarjeta, en el mismo sitio, porque no lo era: la ficha neutralizaba
  // `unverified` y la tarjeta no, y a un `suspicious` sin PVP creíble le quitaba el ámbar (#473).
  const tono = tonoDelDescuento(honesty, cifras.sostenido);
  const listStr = eurStr(cifras.tachado);
  const declaradoStr = eurStr(listPrice);
  const disc = cifras.descuento;
  // La cifra que cita una acusación `declarado` (#354). Si faltara —no debería: la vía declarada no
  // puede dispararse sin ella— el texto cae al de siempre en vez de enseñar un hueco.
  const min30Str = eurStr(retailerMin30d);
  // Y el TEXTO tampoco se decide aquí, por lo mismo que el color y con un motivo aún más caro
  // (#517): vivía en un ternario anidado dentro del JSX, sin un solo test que lo mirara, y le
  // decía de un `reciente` que no sabíamos si el precio era «su precio de siempre» mientras la
  // gráfica de arriba dibujaba la observación más cara que es justo lo que lo hace `reciente`.
  // Se calcula junto a `caja` y no en el JSX porque es aquí donde `honesty` se estrecha de verdad:
  // el `caja !== null` de abajo no le dice nada al compilador sobre el veredicto.
  const textoCaja =
    honesty === 'none'
      ? null
      : textoDeLaCaja(honesty, { trackedDays, claimDays, honestyBasis, min30: min30Str });
  const hasMarkdown = disc !== null && disc > 0 && listStr !== null;

  return (
    <div>
      {llevaBadge(honesty) && (
        <div style={{ marginBottom: 12 }}>
          <HonestyBadge kind={honesty} big />
        </div>
      )}
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
        {/* Quién pierde el acento lo decide `tonoDelPrecio()`, no este fichero: esta ficha se lo
            quitaba a `unverified` y la tarjeta no (#473). El porqué, en su docstring. */}
        <span
          className="serif"
          style={{
            fontSize: 44,
            fontWeight: 600,
            lineHeight: 1,
            color: tonoDelPrecio(honesty) === 'plano' ? 'var(--text)' : 'var(--accent)',
          }}
        >
          {priceStr ?? '—'}
        </span>
        {hasMarkdown && (
          <>
            <span style={{ fontSize: 19, color: 'var(--text-faint)', textDecoration: 'line-through' }}>{listStr}</span>
            {/* El acento verde afirma «esto es una ganga», y solo `real` se lo gana (#473). Lo que
                decide el tono está en `tonoDelDescuento()`; aquí solo se traduce a fondo + texto. */}
            <span
              style={{
                background: tono === 'warn' ? 'var(--warn-soft)' : tono === 'good' ? 'var(--good-soft)' : 'var(--surface-2)',
                color: tono === 'warn' ? 'var(--warn-text)' : tono === 'good' ? 'var(--good-text)' : 'var(--text-muted)',
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
      {caja !== null && (
        <div
          style={{
            marginTop: 12,
            display: 'flex',
            gap: 10,
            background: caja.fondo,
            border: '1px solid ' + caja.borde,
            borderRadius: 12,
            padding: '11px 13px',
          }}
        >
          <span style={{ color: caja.icono, flex: 'none', marginTop: 1 }}>
            {/* Sin `sw`: cada icono trae ya por defecto el grosor que este bloque le pasaba a
                mano (el visto, 2,6), así que repetirlo aquí solo crea un número que puede
                separarse del de `icons.tsx` sin que nada lo note. */}
            <caja.Icono size={17} />
          </span>
          <span style={{ fontSize: 13, lineHeight: 1.5, color: caja.texto, fontWeight: 600 }}>
            {textoCaja}
          </span>
        </div>
      )}
    </div>
  );
}
