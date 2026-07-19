import { Link, Outlet, useLocation, useNavigate, useSearchParams } from 'react-router-dom';

import { FootIcon, GridIcon, HomeIcon, MoonIcon, SearchIcon, SunIcon, BellIcon } from './icons';
import { useTheme } from './ThemeProvider';
import { useToast } from './Toast';

const SECTIONS: Array<{ label: string; value: string }> = [
  { label: 'Todas', value: '' },
  { label: 'Ropa', value: 'ropa' },
  { label: 'Zapatería', value: 'zapateria' },
];
const GENDERS: Array<{ label: string; value: string }> = [
  { label: 'Todos', value: '' },
  { label: 'Niño', value: 'niño' },
  { label: 'Niña', value: 'niña' },
  { label: 'Unisex', value: 'unisex' },
];

function Logo() {
  return (
    <Link
      to="/"
      aria-label="Ir al inicio"
      style={{ display: 'flex', alignItems: 'center', gap: 9, padding: 4, color: 'var(--text)', textDecoration: 'none' }}
    >
      <span
        style={{
          width: 34,
          height: 34,
          borderRadius: 11,
          background: 'var(--accent)',
          display: 'grid',
          placeItems: 'center',
          color: 'var(--on-accent)',
          flex: 'none',
        }}
      >
        <FootIcon size={19} />
      </span>
      <span className="serif" style={{ fontFamily: 'var(--font-serif)', fontSize: 21, fontWeight: 600 }}>
        deal<span style={{ color: 'var(--accent)' }}>tracker</span>
      </span>
    </Link>
  );
}

export function Layout() {
  const { theme, toggle } = useTheme();
  const toast = useToast();
  const navigate = useNavigate();
  const location = useLocation();
  const [params] = useSearchParams();

  const curSection = params.get('section') ?? '';
  const curGender = params.get('gender') ?? '';

  const goCatalogWith = (key: 'section' | 'gender', value: string) => {
    const next = new URLSearchParams(location.pathname === '/catalogo' ? params : undefined);
    if (value) next.set(key, value);
    else next.delete(key);
    navigate(`/catalogo?${next.toString()}`);
  };

  const navBtn = (active: boolean): React.CSSProperties => ({
    background: active ? 'var(--surface-2)' : 'none',
    border: 'none',
    padding: '9px 14px',
    borderRadius: 'var(--r-pill)',
    fontSize: 14,
    fontWeight: 700,
    color: active ? 'var(--text)' : 'var(--text-muted)',
    cursor: 'pointer',
    textDecoration: 'none',
  });

  return (
    <div style={{ minHeight: '100vh', background: 'var(--bg-2)' }}>
      <header
        style={{
          position: 'sticky',
          top: 0,
          zIndex: 40,
          background: 'color-mix(in srgb, var(--bg) 88%, transparent)',
          backdropFilter: 'blur(12px)',
          borderBottom: '1px solid var(--border)',
        }}
      >
        <div style={{ maxWidth: 1180, margin: '0 auto', padding: '10px 16px', display: 'flex', alignItems: 'center', gap: 14 }}>
          <Logo />
          <nav className="dt-desknav" aria-label="Principal" style={{ display: 'flex', gap: 2, marginLeft: 8 }}>
            <Link to="/" style={navBtn(location.pathname === '/')}>
              Inicio
            </Link>
            <Link to="/catalogo" style={navBtn(location.pathname === '/catalogo')}>
              Catálogo
            </Link>
          </nav>

          <div style={{ flex: 1 }} />

          <button
            onClick={() => navigate('/catalogo')}
            aria-label="Buscar"
            className="btn-ghost"
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              borderRadius: 'var(--r-pill)',
              padding: '8px 14px',
              color: 'var(--text-muted)',
              cursor: 'pointer',
              minHeight: 44,
            }}
          >
            <SearchIcon size={17} />
            <span className="dt-searchtxt" style={{ fontSize: 14 }}>
              Buscar prendas…
            </span>
          </button>

          <button
            onClick={toggle}
            aria-label="Cambiar tema claro/oscuro"
            className="btn-ghost"
            style={{ width: 44, height: 44, borderRadius: 'var(--r-pill)', display: 'grid', placeItems: 'center', flex: 'none' }}
          >
            {theme === 'dark' ? <SunIcon size={19} /> : <MoonIcon size={19} />}
          </button>

          <button
            onClick={() => toast('Inicio de sesión con Keycloak · muy pronto')}
            className="btn btn-primary"
            style={{ padding: '11px 18px', fontSize: 14, flex: 'none' }}
          >
            Iniciar sesión
          </button>
        </div>

        {/* conmutador sección + género */}
        <div style={{ borderTop: '1px solid var(--border)', background: 'color-mix(in srgb,var(--bg) 60%,transparent)' }}>
          <div style={{ maxWidth: 1180, margin: '0 auto', padding: '9px 16px', display: 'flex', gap: 14, alignItems: 'center', overflowX: 'auto' }}>
            <div
              role="tablist"
              aria-label="Sección"
              style={{ display: 'flex', gap: 4, background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 'var(--r-pill)', padding: 4, flex: 'none' }}
            >
              {SECTIONS.map((s) => {
                const sel = curSection === s.value;
                return (
                  <button
                    key={s.label}
                    role="tab"
                    aria-selected={sel}
                    onClick={() => goCatalogWith('section', s.value)}
                    style={{
                      border: 'none',
                      cursor: 'pointer',
                      borderRadius: 'var(--r-pill)',
                      padding: '7px 15px',
                      fontSize: 13.5,
                      fontWeight: 800,
                      background: sel ? 'var(--surface)' : 'transparent',
                      color: sel ? 'var(--text)' : 'var(--text-muted)',
                      boxShadow: sel ? 'var(--shadow-1)' : 'none',
                    }}
                  >
                    {s.label}
                  </button>
                );
              })}
            </div>
            <div style={{ width: 1, height: 26, background: 'var(--border)', flex: 'none' }} />
            <div role="tablist" aria-label="Género" style={{ display: 'flex', gap: 6, flex: 'none' }}>
              {GENDERS.map((g) => {
                const sel = curGender === g.value;
                return (
                  <button
                    key={g.label}
                    role="tab"
                    aria-selected={sel}
                    onClick={() => goCatalogWith('gender', g.value)}
                    style={{
                      border: '1px solid ' + (sel ? 'transparent' : 'var(--border)'),
                      cursor: 'pointer',
                      borderRadius: 'var(--r-pill)',
                      padding: '7px 14px',
                      fontSize: 13.5,
                      fontWeight: 700,
                      background: sel ? 'var(--accent-soft)' : 'var(--surface)',
                      color: sel ? 'var(--accent)' : 'var(--text-muted)',
                    }}
                  >
                    {g.label}
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      </header>

      <main style={{ maxWidth: 1180, margin: '0 auto', padding: '0 16px 96px' }}>
        <Outlet />
      </main>

      <BottomNav onFollow={() => toast('Inicia sesión para seguir prendas · muy pronto')} onToggleTheme={toggle} theme={theme} />
    </div>
  );
}

function BottomNav({ onFollow, onToggleTheme, theme }: { onFollow: () => void; onToggleTheme: () => void; theme: string }) {
  const location = useLocation();
  const item = (active: boolean): React.CSSProperties => ({
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: 3,
    background: 'none',
    border: 'none',
    cursor: 'pointer',
    padding: '6px 12px',
    color: active ? 'var(--accent)' : 'var(--text-muted)',
    textDecoration: 'none',
  });
  return (
    <nav
      className="dt-bottomnav"
      aria-label="Navegación móvil"
      style={{
        position: 'fixed',
        bottom: 0,
        left: 0,
        right: 0,
        zIndex: 35,
        background: 'color-mix(in srgb,var(--bg) 92%,transparent)',
        backdropFilter: 'blur(12px)',
        borderTop: '1px solid var(--border)',
        padding: '6px 8px 8px',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-around', maxWidth: 520, margin: '0 auto' }}>
        <Link to="/" style={item(location.pathname === '/')}>
          <HomeIcon size={22} />
          <span style={{ fontSize: 10.5, fontWeight: 700 }}>Inicio</span>
        </Link>
        <Link to="/catalogo" style={item(location.pathname === '/catalogo')}>
          <GridIcon size={22} />
          <span style={{ fontSize: 10.5, fontWeight: 700 }}>Catálogo</span>
        </Link>
        <button onClick={onFollow} style={item(false)}>
          <BellIcon size={22} />
          <span style={{ fontSize: 10.5, fontWeight: 700 }}>Seguir</span>
        </button>
        <button onClick={onToggleTheme} style={item(false)}>
          {theme === 'dark' ? <SunIcon size={22} /> : <MoonIcon size={22} />}
          <span style={{ fontSize: 10.5, fontWeight: 700 }}>Tema</span>
        </button>
      </div>
    </nav>
  );
}
