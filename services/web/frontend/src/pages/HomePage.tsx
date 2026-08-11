import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';

import { useAuth } from '../auth/AuthProvider';
import { HonestyBadge } from '../components/Badges';
import { ArrowRightIcon, CheckIcon, SearchIcon } from '../components/icons';
import { ProductCard } from '../components/ProductCard';
import { ErrorState, ProductGridSkeleton } from '../components/States';
import { useToast } from '../components/Toast';
import { useFacets, useProducts } from '../api/hooks';
import { sectionBg, stripeBg } from '../lib/section';

const HOW = [
  { n: '1', t: 'Elige qué seguir', d: 'Marca una prenda, una variante concreta o crea un aviso por filtros (talla, color, tienda…).' },
  { n: '2', t: 'Vigilamos el precio', d: 'Rastreamos las tiendas y guardamos el historial real de cada prenda, no un “precio original” inventado.' },
  { n: '3', t: 'Te avisamos por Telegram', d: 'Un único mensaje cuando la rebaja es de verdad, comparada con su mínimo reciente.' },
];

/** Atajos del buscador: arrancan una búsqueda de verdad, no son adorno. */
const SUGGESTIONS = ['botas', 'pantalones', 'sudadera'];

/** Cuántas ofertas enseña la portada. Suficiente para dos filas cómodas y ni una tarjeta de relleno. */
const DEALS_ON_HOME = 8;

/** Las dos cifras que no salen del catálogo, y que por tanto ve todo el mundo. */
const STATS_FIJOS = [
  { n: 'Barefoot', l: 'ropa y calzado infantil' },
  { n: 'Cero', l: 'descuentos falsos' },
];

export function HomePage() {
  const navigate = useNavigate();
  const toast = useToast();
  const auth = useAuth();
  const [text, setText] = useState('');

  /**
   * Si quien mira puede ver catálogo (#309). Con Keycloak configurado hace falta sesión; sin él
   * —así corre `dev`— la home es la de siempre. Mientras `ready` es falso se asume que no: pintar
   * la tira de ofertas antes de saberlo dispararía `useProducts` sin token y devolvería un 401.
   */
  const conCatalogo = auth.ready && (!auth.enabled || auth.authenticated);

  const goSearch = (q: string) => {
    const term = q.trim();
    navigate(term ? `/catalogo?q=${encodeURIComponent(term)}` : '/catalogo');
  };

  return (
    <section className="dt-fade">
      <div className="dt-hero" style={{ display: 'grid', gap: 34, gridTemplateColumns: '1.1fr .9fr', alignItems: 'center', padding: '44px 0 24px' }}>
        <div>
          <div style={{ display: 'inline-flex', alignItems: 'center', gap: 8, background: 'var(--good-soft)', color: 'var(--good-text)', borderRadius: 'var(--r-pill)', padding: '7px 14px', fontSize: 13, fontWeight: 700, marginBottom: 20 }}>
            <CheckIcon size={15} sw={2.6} />
            Descuentos de verdad, sin trampas
          </div>
          <h1 className="serif" style={{ fontSize: 'clamp(38px,6vw,62px)', lineHeight: 1.02, letterSpacing: '-.02em', margin: '0 0 18px' }}>
            Ropa y calzado <em style={{ fontStyle: 'italic', color: 'var(--accent)' }}>barefoot</em> para peques, al precio justo.
          </h1>
          <p style={{ fontSize: 18, lineHeight: 1.55, color: 'var(--text-muted)', maxWidth: 520, margin: '0 0 28px' }}>
            Seguimos las tiendas por ti y te avisamos por Telegram cuando una prenda que sigues baja de precio{' '}
            <strong style={{ color: 'var(--text)' }}>de verdad</strong>. Detectamos los descuentos inflados para que no piques.
          </p>
          {/* El CTA es el buscador. Antes era un «Explorar el catálogo» que llevaba al mismo sitio
              que las tarjetas de sección de más abajo y que la barra de la cabecera: tres caminos
              al mismo `/catalogo` en una sola pantalla. */}
          <form
            role="search"
            onSubmit={(e) => {
              e.preventDefault();
              goSearch(text);
            }}
            style={{ display: 'flex', alignItems: 'center', gap: 8, background: 'var(--surface)', border: '1px solid var(--border-strong)', borderRadius: 'var(--r-pill)', padding: '6px 6px 6px 18px', maxWidth: 520, boxShadow: 'var(--shadow-2)' }}
          >
            <SearchIcon size={20} />
            <input
              type="search"
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="¿Qué estás buscando?"
              aria-label="Buscar prendas"
              maxLength={80}
              style={{ flex: 1, minWidth: 0, border: 'none', background: 'none', outline: 'none', fontSize: 16, fontFamily: 'inherit', color: 'var(--text)', padding: '13px 4px' }}
            />
            <button className="btn btn-primary" type="submit" style={{ padding: '13px 22px', fontSize: 15, flex: 'none' }}>
              Buscar
            </button>
          </form>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginTop: 12 }}>
            <span style={{ fontSize: 13, color: 'var(--text-faint)' }}>Prueba con</span>
            {SUGGESTIONS.map((s) => (
              <button
                key={s}
                onClick={() => goSearch(s)}
                style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 'var(--r-pill)', padding: '5px 12px', fontSize: 13, fontWeight: 700, color: 'var(--text-muted)', cursor: 'pointer' }}
              >
                {s}
              </button>
            ))}
          </div>
          <div style={{ marginTop: 16 }}>
            <button className="btn btn-secondary" style={{ padding: '13px 22px', fontSize: 15 }} onClick={() => toast('Inicia sesión para guardar tus seguimientos · muy pronto')}>
              Empieza a seguir prendas
            </button>
          </div>
          {conCatalogo ? <StatsConTiendas /> : <StatsRow items={STATS_FIJOS} />}
        </div>
        <div style={{ position: 'relative' }}>
          <div style={{ borderRadius: 'var(--r-xl)', overflow: 'hidden', boxShadow: 'var(--shadow-3)', background: 'var(--surface)', border: '1px solid var(--border)' }}>
            <div style={{ aspectRatio: '4/5', background: stripeBg('var(--sand-200)', 14), display: 'grid', placeItems: 'center' }}>
              <span style={{ fontSize: 12, letterSpacing: '.08em', color: 'var(--ink-500)', background: 'var(--surface)', padding: '6px 12px', borderRadius: 99, border: '1px solid var(--border)', fontWeight: 700 }}>
                FOTO · niños barefoot en el campo
              </span>
            </div>
          </div>
        </div>
      </div>

      {conCatalogo ? <TodaysDeals /> : <DealsTrasLaPuerta />}

      {/* dos secciones */}
      <div className="dt-two" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginTop: 36 }}>
        {[
          { title: 'Ropa', desc: 'Pantalones, camisetas, sudaderas, vestidos y ropa interior.', section: 'ropa' },
          { title: 'Zapatería', desc: 'Calzado barefoot: botas, zapatillas y sandalias respetuosas.', section: 'zapateria' },
        ].map((hs) => (
          <button
            key={hs.section}
            onClick={() => navigate(`/catalogo?section=${hs.section}`)}
            className="card-hover"
            style={{ textAlign: 'left', border: '1px solid var(--border)', background: 'var(--surface)', borderRadius: 'var(--r-lg)', padding: 0, overflow: 'hidden', cursor: 'pointer', display: 'flex', alignItems: 'stretch', minHeight: 150 }}
          >
            <div style={{ padding: 24, flex: 1 }}>
              <div className="serif" style={{ fontSize: 27, fontWeight: 600, marginBottom: 6 }}>{hs.title}</div>
              <div style={{ color: 'var(--text-muted)', fontSize: 14.5, marginBottom: 16 }}>{hs.desc}</div>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color: 'var(--accent)', fontWeight: 800, fontSize: 14 }}>
                Ver {hs.title} <ArrowRightIcon size={16} />
              </span>
            </div>
            <div style={{ width: 130, background: stripeBg(sectionBg(hs.section)), flex: 'none' }} />
          </button>
        ))}
      </div>

      {/* cómo funciona */}
      <div style={{ marginTop: 56 }}>
        <h2 className="serif" style={{ fontSize: 32, textAlign: 'center', margin: '0 0 8px' }}>
          Cómo funciona el <em style={{ color: 'var(--accent)' }}>descuento honesto</em>
        </h2>
        <p style={{ textAlign: 'center', color: 'var(--text-muted)', maxWidth: 560, margin: '0 auto 32px' }}>
          Comparamos el precio actual con el historial real de cada prenda, no con un “precio original” inventado.
        </p>
        <div className="dt-three" style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 16 }}>
          {HOW.map((w) => (
            <div key={w.n} style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--r-lg)', padding: 24 }}>
              <div className="serif" style={{ width: 44, height: 44, borderRadius: 13, background: 'var(--accent-soft)', color: 'var(--accent)', display: 'grid', placeItems: 'center', fontSize: 20, fontWeight: 600, marginBottom: 14 }}>{w.n}</div>
              <div style={{ fontWeight: 800, fontSize: 17, marginBottom: 6 }}>{w.t}</div>
              <div style={{ color: 'var(--text-muted)', fontSize: 14.5, lineHeight: 1.55 }}>{w.d}</div>
            </div>
          ))}
        </div>
      </div>

      {/* explicador honestidad */}
      <div className="dt-two" style={{ marginTop: 40, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--r-xl)', padding: 30, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 28, alignItems: 'center' }}>
        <div>
          <div className="serif" style={{ fontSize: 26, fontWeight: 600, marginBottom: 10 }}>Dos etiquetas, cero confusión</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
              <span style={{ flex: 'none', marginTop: 2 }}><HonestyBadge kind="real" /></span>
              <div style={{ fontSize: 14.5, color: 'var(--text-muted)' }}>
                <strong style={{ color: 'var(--text)' }}>Oferta real:</strong> el precio ha bajado de verdad frente a su mínimo reciente. Buen momento para comprar.
              </div>
            </div>
            <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
              <span style={{ flex: 'none', marginTop: 2 }}><HonestyBadge kind="suspicious" /></span>
              <div style={{ fontSize: 14.5, color: 'var(--text-muted)' }}>
                <strong style={{ color: 'var(--text)' }}>Precio inflado:</strong> el precio tachado está hinchado respecto al histórico. El “descuento” no es real.
              </div>
            </div>
          </div>
        </div>
        <div style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 'var(--r-lg)', padding: 24, display: 'grid', placeItems: 'center', textAlign: 'center', gap: 10 }}>
          <div className="serif" style={{ fontSize: 20 }}>Sin letra pequeña</div>
          <p style={{ color: 'var(--text-muted)', fontSize: 14.5, lineHeight: 1.55, margin: 0 }}>
            Cada prenda del catálogo lleva su etiqueta y su gráfica de precios. Si no podemos
            corroborar la rebaja con el histórico, no la llamamos oferta.
          </p>
          <Link to="/catalogo?onlyDeals=true" style={{ color: 'var(--accent)', fontWeight: 800, fontSize: 14, textDecoration: 'none' }}>
            Ver las ofertas de hoy
          </Link>
        </div>
      </div>
    </section>
  );
}

function StatsRow({ items }: { items: { n: string; l: string }[] }) {
  return (
    <div style={{ display: 'flex', gap: 22, marginTop: 30, flexWrap: 'wrap' }}>
      {items.map((st) => (
        <div key={st.l}>
          <div className="serif" style={{ fontSize: 26, fontWeight: 600, color: 'var(--text)' }}>{st.n}</div>
          <div style={{ fontSize: 13, color: 'var(--text-faint)' }}>{st.l}</div>
        </div>
      ))}
    </div>
  );
}

/**
 * El contador de tiendas sale de `useFacets()`, o sea del catálogo, que desde #309 pide sesión.
 * Vive en su propio componente para que el hook **no llegue a montarse** sin ella: es la misma
 * razón por la que `TodaysDeals` siempre fue un componente aparte.
 */
function StatsConTiendas() {
  const facets = useFacets();
  const tiendas = {
    // Sin número inventado mientras carga: un guion dice la verdad, un "8" por defecto no.
    n: facets.data ? `${facets.data.retailers.length}` : '—',
    l: 'tiendas rastreadas',
  };
  return <StatsRow items={[tiendas, ...STATS_FIJOS]} />;
}

/**
 * Lo que ocupa el sitio de la tira de ofertas cuando quien mira no tiene sesión (#309). No enseña
 * ni una prenda ni una tienda —que es justo lo que la issue pide esconder—, y evita que la home
 * anónima se quede con un hueco sin explicar entre el hero y las dos secciones.
 */
function DealsTrasLaPuerta() {
  return (
    <div style={{ marginTop: 48, background: 'var(--surface-2)', border: '1px dashed var(--border-strong)', borderRadius: 'var(--r-lg)', padding: '40px 24px', textAlign: 'center' }}>
      <h2 className="serif" style={{ fontSize: 28, margin: '0 0 8px' }}>
        Las ofertas <em style={{ color: 'var(--accent)' }}>reales</em> están dentro
      </h2>
      <p style={{ color: 'var(--text-muted)', maxWidth: 460, margin: '0 auto 20px', lineHeight: 1.55, fontSize: 14.5 }}>
        El catálogo y las prendas por debajo de su mínimo reciente se ven con la sesión iniciada.
        Hace falta cuenta, y el registro está cerrado por ahora.
      </p>
      <Link className="btn btn-primary" to="/acceso" style={{ padding: '12px 22px', textDecoration: 'none' }}>
        Iniciar sesión
      </Link>
    </div>
  );
}

/**
 * Las ofertas reales del momento, salidas de la API.
 *
 * Aquí antes había una tarjeta que decía «¡Oferta real encontrada! · Botas Zapa −38 % vs mínimo»
 * escrita a mano. Enseñar un descuento inventado en la portada de un producto que se vende como
 * detector de descuentos inventados era el peor sitio posible para hacerlo.
 *
 * `onlyDeals` filtra en el servidor por oferta **real**: nunca aparece aquí un «precio inflado»
 * como gancho. Y si hoy no hay ninguna, se dice; no se rellena con productos sin rebaja.
 */
function TodaysDeals() {
  const q = useProducts({ sort: 'ofertas', onlyDeals: true, inStock: true }, DEALS_ON_HOME);
  const items = q.data?.pages[0]?.items ?? [];

  return (
    <div style={{ marginTop: 48 }}>
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap', marginBottom: 18 }}>
        <div>
          <h2 className="serif" style={{ fontSize: 30, margin: '0 0 4px' }}>
            Ofertas <em style={{ color: 'var(--accent)' }}>reales</em> de hoy
          </h2>
          <p style={{ color: 'var(--text-muted)', fontSize: 14.5, margin: 0 }}>
            Prendas por debajo de su mínimo de los últimos meses. Comprobado contra su historial.
          </p>
        </div>
        <Link to="/catalogo?onlyDeals=true" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color: 'var(--accent)', fontWeight: 800, fontSize: 14.5, textDecoration: 'none' }}>
          Ver todas las ofertas <ArrowRightIcon size={16} />
        </Link>
      </div>

      {q.isPending ? (
        <ProductGridSkeleton count={4} />
      ) : q.isError ? (
        <ErrorState onRetry={() => q.refetch()} />
      ) : items.length === 0 ? (
        <div style={{ background: 'var(--surface)', border: '1px dashed var(--border-strong)', borderRadius: 'var(--r-lg)', padding: '40px 24px', textAlign: 'center' }}>
          <div className="serif" style={{ fontSize: 22, marginBottom: 8 }}>Hoy no hay ninguna oferta real</div>
          <p style={{ color: 'var(--text-muted)', maxWidth: 460, margin: '0 auto 18px', lineHeight: 1.55 }}>
            Ninguna prenda ha bajado de su mínimo reciente. Preferimos decírtelo a llenar esto de
            rebajas que no lo son. Volvemos a mirar las tiendas cada día.
          </p>
          <Link className="btn btn-secondary" to="/catalogo" style={{ padding: '12px 22px', textDecoration: 'none' }}>
            Ver el catálogo completo
          </Link>
        </div>
      ) : (
        // Tarjetas algo más anchas que en el catálogo (4 por fila en vez de 5): así las 8 de la
        // portada caen en dos filas completas en lugar de dejar una huérfana.
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(250px,1fr))', gap: 16 }}>
          {items.map((p) => (
            <ProductCard key={p.id} p={p} />
          ))}
        </div>
      )}
    </div>
  );
}
