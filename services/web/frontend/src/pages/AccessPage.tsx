import { Link, useLocation } from 'react-router-dom';

import { useAuth } from '../auth/AuthProvider';
import { CheckIcon } from '../components/icons';

/**
 * Página de acceso (#309). Aquí aterriza quien intenta ver catálogo sin sesión.
 *
 * Dice tres cosas y las tres tienen que ser verdad: hace falta cuenta, el registro está cerrado
 * por ahora, y quien ya la tenga puede entrar. Lo segundo se sostiene en que el realm tenga
 * `registrationAllowed=false` — si lo publicara, esta página mentiría y sería falsable en dos
 * clics.
 */
const DESTINO_POR_DEFECTO = '/catalogo';

export function AccessPage() {
  const auth = useAuth();
  const location = useLocation();

  // `RequireSession` deja aquí el destino real. Se puede llegar sin él (el CTA de la home enlaza
  // a /acceso a pelo), y entonces se entra al catálogo, que es lo que venía buscando.
  const state = location.state as { from?: string } | null;
  const destino = state?.from ?? DESTINO_POR_DEFECTO;

  return (
    <section className="dt-fade" style={{ padding: '60px 0', display: 'grid', placeItems: 'center' }}>
      <div
        style={{
          background: 'var(--surface)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--r-lg)',
          padding: '40px 32px',
          maxWidth: 520,
          textAlign: 'center',
        }}
      >
        <h1 className="serif" style={{ fontSize: 30, margin: '0 0 10px' }}>
          Hace falta <em style={{ color: 'var(--accent)' }}>cuenta</em>
        </h1>
        <p style={{ color: 'var(--text-muted)', fontSize: 15, lineHeight: 1.6, margin: '0 0 8px' }}>
          El catálogo y las fichas de producto solo se ven con la sesión iniciada.
        </p>
        <p style={{ color: 'var(--text-muted)', fontSize: 15, lineHeight: 1.6, margin: '0 0 26px' }}>
          <strong>El registro está cerrado por ahora.</strong> Las cuentas se dan de alta a mano.
        </p>

        <button
          onClick={() => auth.login(window.location.origin + destino)}
          disabled={!auth.ready || !auth.enabled}
          className="btn btn-primary"
          style={{ padding: '13px 24px', fontSize: 15 }}
        >
          Iniciar sesión
        </button>

        {auth.ready && !auth.enabled && (
          // Solo se ve en un entorno sin Keycloak, donde además `RequireSession` deja pasar y
          // nadie debería acabar aquí. Mejor decirlo que ofrecer un botón muerto sin explicación.
          <p style={{ color: 'var(--text-muted)', fontSize: 13.5, marginTop: 14 }}>
            Este entorno no tiene inicio de sesión configurado.
          </p>
        )}

        <div style={{ display: 'flex', alignItems: 'center', gap: 8, justifyContent: 'center', marginTop: 30, color: 'var(--text-muted)', fontSize: 13.5 }}>
          <CheckIcon size={15} />
          <span>Seguimos precios reales, sin descuentos inventados</span>
        </div>

        <div style={{ marginTop: 18 }}>
          <Link to="/" style={{ color: 'var(--accent)', fontWeight: 700, fontSize: 14, textDecoration: 'none' }}>
            Volver al inicio
          </Link>
        </div>
      </div>
    </section>
  );
}
