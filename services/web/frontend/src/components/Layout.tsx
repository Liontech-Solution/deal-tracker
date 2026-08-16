import { useEffect, useRef, useState } from 'react';
import { Link, Outlet, useLocation, useNavigate, useSearchParams } from 'react-router-dom';

import { useAuth } from '../auth/AuthProvider';
import { patchSeccion } from '../lib/filters';
import { SECCIONES } from '../lib/section';
import { FootIcon, GridIcon, HomeIcon, MoonIcon, SearchIcon, SunIcon, BellIcon } from './icons';
import { useTheme } from './ThemeProvider';
import { useToast } from './Toast';

/**
 * Sección = navegación, no filtro. Antes vivía en una barra de pestañas pegada debajo de otra de
 * género, con dos "Todos/Todas" contiguos que nadie sabía distinguir. Ahora es un eje de la nav
 * principal, y el género bajó al panel de filtros con el resto (talla, color, tienda).
 *
 * La lista es la de `lib/section.ts` desde #434: estaba escrita aquí y en el panel, con los mismos
 * valores, y eran dos controles del mismo eje que se comportaban distinto.
 */

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
  const auth = useAuth();

  const inCatalog = location.pathname === '/catalogo';
  const curSection = params.get('section') ?? '';
  const curQuery = params.get('q') ?? '';

  // "Seguir": lleva a Mis seguimientos si hay sesión; si la auth está activa pero sin sesión,
  // login; en dev local sin realm, placeholder.
  const goFollow = () => {
    // La config llega por red: hasta que no está resuelta, `enabled` no es concluyente y
    // mostrar el placeholder sería mentir.
    if (!auth.ready) return;
    if (!auth.enabled) {
      toast('Inicio de sesión con Keycloak · disponible al desplegar');
    } else if (!auth.authenticated) {
      auth.login();
    } else {
      navigate('/seguimientos');
    }
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
        {/* `flexWrap` para el móvil: ahí el buscador salta a su propia línea (ver `.dt-search` en
            app.css) en vez de estrujar el resto de la cabecera hasta desbordarla. */}
        <div className="dt-headerbar" style={{ maxWidth: 1180, margin: '0 auto', padding: '10px 16px', display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
          <Logo />
          <nav className="dt-desknav" aria-label="Principal" style={{ display: 'flex', gap: 2, marginLeft: 8 }}>
            <Link to="/" style={navBtn(location.pathname === '/')}>
              Inicio
            </Link>
            {/* El destino se PARCHEA sobre lo que ya hay puesto (#434). Con la URL escrita entera,
                React Router sustituye el `search` completo y este enlace se llevaba por delante
                género, categoría, talla, color, tienda, `q`, `sort` y el rango de precio: desde
                `?gender=niña&section=zapateria`, pulsar «Ropa» dejaba `?section=ropa` a secas. Es
                el mismo helper que usan las pestañas del panel, que sí conservaban el resto — que
                dos controles del mismo eje se comportaran distinto era la mitad del fallo.

                El `inCatalog ? params : undefined` es el mismo de `SearchBox` (:189) y por el mismo
                motivo: fuera del catálogo lo que hay en la URL no son filtros, y arrastrarlo hasta
                `/catalogo` colaría parámetros de otra página. */}
            {SECCIONES.map((s) => (
              <Link
                key={s.value}
                to={{
                  pathname: '/catalogo',
                  search: patchSeccion(new URLSearchParams(inCatalog ? params : undefined), s.value).toString(),
                }}
                style={navBtn(inCatalog && curSection === s.value)}
              >
                {s.label}
              </Link>
            ))}
          </nav>

          <div style={{ flex: 1 }} />

          <SearchBox value={curQuery} inCatalog={inCatalog} params={params} />

          <button
            onClick={toggle}
            aria-label="Cambiar tema claro/oscuro"
            className="btn-ghost"
            style={{ width: 44, height: 44, borderRadius: 'var(--r-pill)', display: 'grid', placeItems: 'center', flex: 'none' }}
          >
            {theme === 'dark' ? <SunIcon size={19} /> : <MoonIcon size={19} />}
          </button>

          {auth.authenticated ? (
            <UserMenu name={auth.user?.name ?? auth.user?.email ?? 'Cuenta'} onLogout={auth.logout} />
          ) : (
            <button
              onClick={() =>
                auth.enabled ? auth.login() : toast('Inicio de sesión con Keycloak · disponible al desplegar')
              }
              disabled={!auth.ready}
              className="btn btn-primary dt-login"
              style={{ padding: '11px 18px', fontSize: 14, flex: 'none' }}
            >
              Iniciar sesión
            </button>
          )}
        </div>
      </header>

      <main style={{ maxWidth: 1180, margin: '0 auto', padding: '0 16px 96px' }}>
        <Outlet />
      </main>

      <BottomNav onFollow={goFollow} onToggleTheme={toggle} theme={theme} />
    </div>
  );
}

/**
 * El buscador del producto, y el único que hay.
 *
 * Antes esto era un botón «Buscar prendas…» que solo hacía `navigate('/catalogo')`: no existía
 * ningún campo de texto en toda la aplicación. Vive en la cabecera y no en el catálogo para que
 * buscar sea posible desde cualquier página sin duplicar el control.
 *
 * La URL es la fuente de verdad (`?q=`): así un resultado se comparte tal cual, el botón atrás
 * funciona y el chip «quitar» del catálogo vacía este campo sin que haya que coordinarlos.
 */
function SearchBox({
  value,
  inCatalog,
  params,
}: {
  value: string;
  inCatalog: boolean;
  params: URLSearchParams;
}) {
  const navigate = useNavigate();
  const [text, setText] = useState(value);

  useEffect(() => setText(value), [value]);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    // Desde el catálogo se conservan los filtros ya puestos; desde fuera se empieza limpio.
    const next = new URLSearchParams(inCatalog ? params : undefined);
    const q = text.trim();
    if (q) next.set('q', q);
    else next.delete('q');
    navigate(`/catalogo?${next.toString()}`);
  };

  return (
    <form
      onSubmit={submit}
      role="search"
      className="dt-search"
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        flex: '1 1 180px',
        minWidth: 0,
        maxWidth: 320,
        background: 'var(--surface-2)',
        border: '1px solid var(--border)',
        borderRadius: 'var(--r-pill)',
        padding: '0 6px 0 12px',
        minHeight: 44,
      }}
    >
      <button
        type="submit"
        aria-label="Buscar"
        className="btn-ghost"
        style={{ display: 'grid', placeItems: 'center', padding: 4, borderRadius: '50%', color: 'var(--text-muted)', flex: 'none' }}
      >
        <SearchIcon size={17} />
      </button>
      <input
        type="search"
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="¿Qué estás buscando?"
        aria-label="Buscar prendas"
        maxLength={80}
        style={{
          flex: 1,
          minWidth: 0,
          border: 'none',
          background: 'none',
          outline: 'none',
          fontSize: 14,
          fontFamily: 'inherit',
          color: 'var(--text)',
          padding: '10px 6px 10px 0',
        }}
      />
    </form>
  );
}

/** Menú de usuario autenticado: iniciales + desplegable con "Mis seguimientos" y "Cerrar sesión". */
function UserMenu({ name, onLogout }: { name: string; onLogout: () => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const initials = name
    .split(/\s+/)
    .slice(0, 2)
    .map((s) => s[0]?.toUpperCase() ?? '')
    .join('');

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);

  return (
    <div ref={ref} style={{ position: 'relative', flex: 'none' }}>
      <button
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Menú de usuario"
        style={{
          width: 40,
          height: 40,
          borderRadius: '50%',
          border: '1px solid var(--border)',
          background: 'var(--accent-soft)',
          color: 'var(--accent)',
          fontWeight: 800,
          fontSize: 14,
          cursor: 'pointer',
        }}
      >
        {initials || '·'}
      </button>
      {open && (
        <div
          role="menu"
          style={{
            position: 'absolute',
            right: 0,
            top: 'calc(100% + 8px)',
            minWidth: 200,
            background: 'var(--surface)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--r-md)',
            boxShadow: 'var(--shadow-3)',
            padding: 6,
            zIndex: 50,
          }}
        >
          <div style={{ padding: '8px 12px', fontSize: 12.5, color: 'var(--text-faint)', fontWeight: 700, borderBottom: '1px solid var(--border)', marginBottom: 4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {name}
          </div>
          <MenuLink to="/seguimientos" onClick={() => setOpen(false)}>
            Mis seguimientos
          </MenuLink>
          <MenuLink to="/ajustes" onClick={() => setOpen(false)}>
            Ajustes
          </MenuLink>
          <button
            role="menuitem"
            onClick={() => {
              setOpen(false);
              onLogout();
            }}
            style={{ width: '100%', textAlign: 'left', background: 'none', border: 'none', cursor: 'pointer', padding: '10px 12px', borderRadius: 'var(--r-sm)', fontSize: 14, fontWeight: 700, color: 'var(--text)' }}
          >
            Cerrar sesión
          </button>
        </div>
      )}
    </div>
  );
}

function MenuLink({ to, onClick, children }: { to: string; onClick: () => void; children: React.ReactNode }) {
  return (
    <Link
      to={to}
      role="menuitem"
      onClick={onClick}
      style={{ display: 'block', padding: '10px 12px', borderRadius: 'var(--r-sm)', fontSize: 14, fontWeight: 700, color: 'var(--text)', textDecoration: 'none' }}
    >
      {children}
    </Link>
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
