import { useEffect, useState } from 'react';

import type { Facets } from '../api/types';
import { colorHex } from '../lib/colors';
import { alternar } from '../lib/filters';
import { capitalize } from '../lib/format';

export interface CatalogFilters {
  gender: string;
  section: string;
  category: string;
  /**
   * Los tres ejes que admiten varios valores (#329). Lista vacía = sin filtrar por ese eje.
   *
   * Son estos tres porque combinarlos es lo natural y porque en la talla la selección única era
   * además **incorrecta**: el vocabulario lo fija la tienda, así que pedir `4 años` dejaba fuera a
   * C&A, que mide en centímetros y llama `104` a esa misma talla.
   */
  size: string[];
  color: string[];
  retailer: string[];
  inStock: boolean;
  onlyDeals: boolean;
  deportiva: boolean;
  /** Extremos del rango de precio (#290). Cadena vacía = sin tope por ese lado. */
  minPrice: string;
  maxPrice: string;
}

/**
 * Tope superior de la barra de precio, en euros.
 *
 * Medido sobre la copia de dev el 11/08/2026 (último precio de cada variante viva): el catálogo va
 * de **1,99 €** a **120,00 €**, con el percentil 99 en 55,90 €. O sea que la barra cubre el
 * catálogo entero y le sobran dos tercios de recorrido para el 1 % de arriba.
 *
 * Es una constante y no un dato de la faceta a propósito: calcularlo exige el CTE `latest` sobre
 * `price_history` (298 ms medidos) y las facetas se piden ahora en cada cambio de filtro. Si algún
 * día entra una prenda más cara, **no se queda inalcanzable**: con el tope arriba del todo no se
 * manda `maxPrice`, así que el rango queda abierto, y el campo de texto admite teclear lo que sea.
 */
const PRECIO_MAX = 120;

interface Props {
  facets: Facets | undefined;
  value: CatalogFilters;
  onChange: (patch: Partial<CatalogFilters>) => void;
}

function Group({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ padding: '14px 0', borderTop: '1px solid var(--border)' }}>
      <div style={{ fontWeight: 800, fontSize: 13, marginBottom: 10, color: 'var(--text)' }}>{label}</div>
      {children}
    </div>
  );
}

function Chip({ label, selected, onClick, dot }: { label: string; selected: boolean; onClick: () => void; dot?: string | null }) {
  return (
    <button
      onClick={onClick}
      aria-pressed={selected}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 7,
        border: '1px solid ' + (selected ? 'transparent' : 'var(--border)'),
        background: selected ? 'var(--accent-soft)' : 'var(--surface)',
        color: selected ? 'var(--accent)' : 'var(--text-muted)',
        borderRadius: 'var(--r-pill)',
        padding: dot ? '6px 12px 6px 8px' : '6px 12px',
        fontSize: 13,
        fontWeight: 700,
        cursor: 'pointer',
      }}
    >
      {dot && <span style={{ width: 12, height: 12, borderRadius: '50%', background: dot, border: '1px solid var(--border-strong)', flex: 'none' }} />}
      {label}
    </button>
  );
}

/** Secciones del catálogo, en el orden en que las ofrece la cabecera. */
const SECCIONES: Array<{ value: string; label: string }> = [
  { value: 'ropa', label: 'Ropa' },
  { value: 'zapateria', label: 'Zapatería' },
];

/**
 * Rango de precio: barra de dos topes + los dos campos, sobre el mismo estado (#290).
 *
 * Los dos controles no son redundancia: la barra sirve para explorar («enséñame lo barato») y los
 * campos para pedir algo exacto, que en la barra costaría puntería. Se sincronizan porque son la
 * misma pareja de valores.
 *
 * El movimiento **no escribe la URL en cada paso**: el tope que se está moviendo vive aquí y sube al
 * padre tras una pausa. Sin eso, cada píxel de arrastre —y cada pulsación de flecha, que es donde se
 * vio: doce flechas eran doce URLs y doce peticiones— estrenaría entrada en la `queryKey`.
 *
 * El retardo va sobre el borrador y no en `onPointerUp`, que es lo primero que se intenta: soltar el
 * ratón cubre el arrastre pero deja el teclado fuera, y el teclado es la única forma de usar esto
 * para quien no puede apuntar con precisión.
 */
const PRECIO_RETARDO_MS = 350;

function RangoPrecio({
  min,
  max,
  onChange,
}: {
  min: string;
  max: string;
  onChange: (patch: Partial<CatalogFilters>) => void;
}) {
  // `null` = "lo que diga la URL". Solo hay valor local mientras se está arrastrando o escribiendo.
  const [borrador, setBorrador] = useState<[number, number] | null>(null);

  const desdeUrl: [number, number] = [
    min === '' ? 0 : Math.max(0, Number(min)),
    max === '' ? PRECIO_MAX : Math.min(PRECIO_MAX, Number(max)),
  ];
  const [lo, hi] = borrador ?? desdeUrl;

  /**
   * Sube al padre cuando el borrador lleva un rato quieto. El extremo pegado al borde significa
   * "sin tope" y por eso viaja vacío: así el rango queda abierto y una prenda por encima de
   * `PRECIO_MAX` sigue siendo alcanzable.
   *
   * El borrador NO se limpia aquí: hacerlo devolvería el control al valor de la URL, que en ese
   * instante aún es el viejo, y el tope pegaría un salto atrás antes de volver. Se limpia cuando la
   * URL ya coincide con lo que hay en pantalla.
   */
  useEffect(() => {
    if (borrador === null) return;
    const [a, b] = borrador;
    const t = setTimeout(() => {
      onChange({ minPrice: a <= 0 ? '' : String(a), maxPrice: b >= PRECIO_MAX ? '' : String(b) });
    }, PRECIO_RETARDO_MS);
    return () => clearTimeout(t);
    // `onChange` se recrea en cada render del padre; meterlo en las dependencias reiniciaría el
    // temporizador sin parar y el filtro no llegaría a aplicarse nunca.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [borrador]);

  useEffect(() => {
    if (borrador && borrador[0] === desdeUrl[0] && borrador[1] === desdeUrl[1]) setBorrador(null);
  }, [borrador, desdeUrl]);

  return (
    <Group label="Precio">
      <div className="dt-range">
        <div className="dt-range-pista" />
        <div
          className="dt-range-relleno"
          style={{ left: `${(lo / PRECIO_MAX) * 100}%`, right: `${100 - (hi / PRECIO_MAX) * 100}%` }}
        />
        {/* Los topes no se cruzan: cada uno topa con el otro. Cruzarlos daría un rango invertido,
            que en SQL no es un error sino cero resultados sin explicación. */}
        <input
          type="range"
          aria-label="Precio mínimo"
          min={0}
          max={PRECIO_MAX}
          value={lo}
          onChange={(e) => setBorrador([Math.min(Number(e.target.value), hi), hi])}
        />
        <input
          type="range"
          aria-label="Precio máximo"
          min={0}
          max={PRECIO_MAX}
          value={hi}
          onChange={(e) => setBorrador([lo, Math.max(Number(e.target.value), lo)])}
        />
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 4 }}>
        <input
          className="dt-precio-input"
          type="number"
          inputMode="decimal"
          min={0}
          aria-label="Desde, en euros"
          placeholder="Desde"
          value={min}
          onChange={(e) => onChange({ minPrice: e.target.value })}
        />
        <span style={{ color: 'var(--text-faint)', fontSize: 13, fontWeight: 700 }}>—</span>
        <input
          className="dt-precio-input"
          type="number"
          inputMode="decimal"
          min={0}
          aria-label="Hasta, en euros"
          placeholder="Hasta"
          value={max}
          onChange={(e) => onChange({ maxPrice: e.target.value })}
        />
        <span style={{ color: 'var(--text-faint)', fontSize: 13, fontWeight: 700 }}>€</span>
      </div>
    </Group>
  );
}

function Switch({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, cursor: 'pointer' }}>
      <span style={{ fontSize: 14, fontWeight: 700 }}>{label}</span>
      <button
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        style={{
          width: 46,
          height: 28,
          borderRadius: 999,
          border: 'none',
          cursor: 'pointer',
          background: checked ? 'var(--accent)' : 'var(--border-strong)',
          position: 'relative',
          transition: 'background .15s ease',
          flex: 'none',
        }}
      >
        <span
          style={{
            position: 'absolute',
            top: 3,
            left: checked ? 21 : 3,
            width: 22,
            height: 22,
            borderRadius: '50%',
            background: '#fff',
            transition: 'left .15s ease',
            boxShadow: 'var(--shadow-1)',
          }}
        />
      </button>
    </label>
  );
}

export function FilterPanel({ facets, value, onChange }: Props) {
  /** Ejes de un solo valor: volver a pulsar el chip elegido lo quita. */
  const toggle = (key: 'gender' | 'category', v: string) =>
    onChange({ [key]: value[key] === v ? '' : v });
  /** Ejes multiseleccionables (#329): el chip suma o resta de la lista. */
  const toggleMulti = (key: 'size' | 'color' | 'retailer', v: string) =>
    onChange({ [key]: alternar(value[key], v) });

  return (
    <div>
      {/* El género vivía en una barra de pestañas de la cabecera, pegada a la de sección y
          confundiéndose con ella. Es un filtro como talla o color, así que va donde están todos. */}
      {facets?.genders.length ? (
        <Group label="Para">
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {facets.genders.map((g) => (
              <Chip key={g} label={capitalize(g)} selected={value.gender === g} onClick={() => toggle('gender', g)} />
            ))}
          </div>
        </Group>
      ) : null}

      {facets?.categories.length ? (
        <Group label="Categoría">
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {facets.categories.map((c) => (
              <Chip key={c} label={capitalize(c)} selected={value.category === c} onClick={() => toggle('category', c)} />
            ))}
          </div>
        </Group>
      ) : null}

      {/* La tienda va JUSTO ENCIMA de la talla, y es deliberado: el vocabulario de talla lo fija
          la tienda, no la prenda (medido: todas las categorías publican 4-6 vocabularios distintos,
          mientras que Sfera solo usa años y C&A solo alturas en cm). Elegir tienda es la vía más
          rápida para que la lista de tallas sea una sola forma de medir, y ponerla debajo escondía
          esa relación. No es obligatorio a propósito: el catálogo existe para no ir tienda por
          tienda, así que forzar a elegir una para poder filtrar por talla sería quitarle el sentido. */}
      {facets?.retailers.length ? (
        <Group label="Tienda">
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {facets.retailers.map((r) => (
              <Chip key={r.slug} label={r.name} selected={value.retailer.includes(r.slug)} onClick={() => toggleMulti('retailer', r.slug)} />
            ))}
          </div>
        </Group>
      ) : null}

      {/*
        La talla abre con dos pestañas, y no es una floritura: ropa y zapatería NO comparten
        vocabulario, y lo grave es que **se solapan**. Sin sección elegida el panel ofrecía 205
        chips de los cuales **36 son ambiguos** — `36-38` es un calcetín en ropa y un número de pie
        en zapatería, y pinchar uno filtraba las dos cosas a la vez. Así que sin sección no se
        ofrecen tallas: se ofrece la elección.
      */}
      <Group label="Talla">
        <div style={{ display: 'flex', gap: 8, marginBottom: value.section ? 12 : 0 }}>
          {SECCIONES.map((s) => (
            <button
              key={s.value}
              role="tab"
              aria-selected={value.section === s.value}
              onClick={() => {
                if (value.section === s.value) return;
                // Cambiar de sección LIMPIA la talla y la categoría. Sin esto, un '36-38' elegido
                // como calcetín se quedaría puesto al saltar a zapatería y pasaría a significar un
                // número de pie sin que nadie lo haya pedido — que es justo la ambigüedad que estas
                // pestañas existen para cortar. La categoría por lo mismo: `pantalones` no existe
                // en zapatería y dejaría el catálogo vacío.
                onChange({ section: s.value, size: [], category: '' });
              }}
              style={{
                flex: 1,
                padding: '9px 12px',
                borderRadius: 'var(--r-pill)',
                border: '1px solid ' + (value.section === s.value ? 'transparent' : 'var(--border)'),
                background: value.section === s.value ? 'var(--accent-soft)' : 'var(--surface)',
                color: value.section === s.value ? 'var(--accent)' : 'var(--text-muted)',
                fontSize: 13,
                fontWeight: 700,
                cursor: 'pointer',
              }}
            >
              {s.label}
            </button>
          ))}
        </div>

        {!value.section ? (
          <div style={{ fontSize: 12.5, color: 'var(--text-faint)', lineHeight: 1.45 }}>
            Elige <strong>Ropa</strong> o <strong>Zapatería</strong>: las tallas de una y otra no
            son la misma cosa, y algunas se escriben igual significando cosas distintas.
          </div>
        ) : facets?.sizes.length ? (
          <>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {facets.sizes.map((s) => (
                <Chip key={s} label={s} selected={value.size.includes(s)} onClick={() => toggleMulti('size', s)} />
              ))}
            </div>
          </>
        ) : (
          <div style={{ fontSize: 12.5, color: 'var(--text-faint)', lineHeight: 1.45 }}>
            Con estos filtros no queda ninguna talla.
          </div>
        )}
      </Group>

      {facets?.colors.length ? (
        <Group label="Color">
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {facets.colors.map((c) => (
              <Chip key={c} label={capitalize(c)} selected={value.color.includes(c)} onClick={() => toggleMulti('color', c)} dot={colorHex(c)} />
            ))}
          </div>
        </Group>
      ) : null}

      <RangoPrecio min={value.minPrice} max={value.maxPrice} onChange={onChange} />

      <Group label="Ofertas">
        <Switch
          label="Solo ofertas reales"
          checked={value.onlyDeals}
          onChange={(v) => onChange({ onlyDeals: v })}
        />
        <div style={{ fontSize: 12.5, color: 'var(--text-faint)', marginTop: 8, lineHeight: 1.45 }}>
          Deja solo lo que ha bajado de verdad respecto a su mínimo reciente. Apagado, el catálogo
          se ve entero con las ofertas primero.
        </div>
      </Group>

      <Group label="Disponibilidad">
        <Switch
          label="Solo en stock"
          checked={value.inStock}
          onChange={(v) => onChange({ inStock: v })}
        />
      </Group>

      {/* El eje transversal (#180). La ropa deportiva vive repartida entre pantalones, camisetas y
          sudaderas —no es una categoría—, así que es un interruptor y no un chip más de categoría.
          El texto de abajo NO es decorativo: solo tres tiendas publican un cajón de deporte, y sin
          decirlo el filtro parece que se ha comido medio catálogo. */}
      <Group label="Educación física">
        <Switch
          label="Solo ropa deportiva"
          checked={value.deportiva}
          onChange={(v) => onChange({ deportiva: v })}
        />
        <div
          style={{
            fontSize: 12.5,
            color: 'var(--text-faint)',
            marginTop: 8,
            lineHeight: 1.45,
          }}
        >
          Lo que la tienda publica como ropa de deporte, esté donde esté su categoría. El dato solo
          lo dan <strong>Sfera, Lefties y C&amp;A</strong>: con el filtro puesto, las demás tiendas
          no aparecen. Para calzado deportivo, mira la categoría <em>zapatillas</em>.
        </div>
      </Group>
    </div>
  );
}
