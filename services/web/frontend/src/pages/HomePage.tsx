import { useNavigate } from 'react-router-dom';

import { HonestyBadge } from '../components/Badges';
import { ArrowRightIcon, CheckIcon, ZapIcon } from '../components/icons';
import { PriceBlock } from '../components/PriceBlock';
import { useToast } from '../components/Toast';
import { useFacets } from '../api/hooks';
import { sectionBg, stripeBg } from '../lib/section';

const HOW = [
  { n: '1', t: 'Elige qué seguir', d: 'Marca una prenda, una variante concreta o crea un aviso por filtros (talla, color, tienda…).' },
  { n: '2', t: 'Vigilamos el precio', d: 'Rastreamos las tiendas y guardamos el historial real de cada prenda, no un “precio original” inventado.' },
  { n: '3', t: 'Te avisamos por Telegram', d: 'Un único mensaje cuando la rebaja es de verdad, comparada con su mínimo reciente.' },
];

export function HomePage() {
  const navigate = useNavigate();
  const toast = useToast();
  const facets = useFacets();
  const stores = facets.data?.retailers.length ?? 8;

  const stats = [
    { n: `${stores}`, l: 'tiendas rastreadas' },
    { n: 'Barefoot', l: 'ropa y calzado infantil' },
    { n: 'Cero', l: 'descuentos falsos' },
  ];

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
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            <button className="btn btn-primary" style={{ padding: '15px 26px', fontSize: 16, boxShadow: 'var(--shadow-2)' }} onClick={() => navigate('/catalogo')}>
              Explorar el catálogo
            </button>
            <button className="btn btn-secondary" style={{ padding: '15px 26px', fontSize: 16 }} onClick={() => toast('Inicia sesión para guardar tus seguimientos · muy pronto')}>
              Empieza a seguir prendas
            </button>
          </div>
          <div style={{ display: 'flex', gap: 22, marginTop: 30, flexWrap: 'wrap' }}>
            {stats.map((st) => (
              <div key={st.l}>
                <div className="serif" style={{ fontSize: 26, fontWeight: 600, color: 'var(--text)' }}>{st.n}</div>
                <div style={{ fontSize: 13, color: 'var(--text-faint)' }}>{st.l}</div>
              </div>
            ))}
          </div>
        </div>
        <div style={{ position: 'relative' }}>
          <div style={{ borderRadius: 'var(--r-xl)', overflow: 'hidden', boxShadow: 'var(--shadow-3)', background: 'var(--surface)', border: '1px solid var(--border)' }}>
            <div style={{ aspectRatio: '4/5', background: stripeBg('var(--sand-200)', 14), display: 'grid', placeItems: 'center' }}>
              <span style={{ fontSize: 12, letterSpacing: '.08em', color: 'var(--ink-500)', background: 'var(--surface)', padding: '6px 12px', borderRadius: 99, border: '1px solid var(--border)', fontWeight: 700 }}>
                FOTO · niños barefoot en el campo
              </span>
            </div>
          </div>
          <div style={{ position: 'absolute', bottom: -18, left: -18, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--r-lg)', boxShadow: 'var(--shadow-3)', padding: '14px 16px', display: 'flex', gap: 12, alignItems: 'center' }}>
            <span style={{ width: 40, height: 40, borderRadius: 12, background: 'var(--good-soft)', color: 'var(--good-text)', display: 'grid', placeItems: 'center', flex: 'none' }}>
              <ZapIcon size={20} />
            </span>
            <div>
              <div style={{ fontWeight: 800, fontSize: 14 }}>¡Oferta real encontrada!</div>
              <div style={{ fontSize: 12.5, color: 'var(--text-muted)' }}>Botas Zapa −38% vs mínimo</div>
            </div>
          </div>
        </div>
      </div>

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
        <div style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 'var(--r-lg)', padding: 20 }}>
          <PriceBlock price="15.90" listPrice="25.90" discountPct="39" stock="stock" honesty="real" />
        </div>
      </div>
    </section>
  );
}
