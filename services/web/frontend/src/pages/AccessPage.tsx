import { Link, useLocation } from 'react-router-dom';

import { useAuth } from '../auth/AuthProvider';
import { CheckIcon } from '../components/icons';

/**
 * Página de acceso (#309). Aquí aterriza quien intenta ver catálogo sin sesión.
 *
 * Dice tres cosas y las tres tienen que ser verdad: hace falta cuenta, cómo se consigue, y que
 * quien ya la tenga puede entrar. **La segunda dejó de ser una sola frase en #550**: hasta la
 * v0.8.0 el registro estaba cerrado y las cuentas se daban de alta a mano, y desde ella hay alta
 * —por invitación, nunca abierta—, pero solo *donde el entorno la tiene configurada*. Por eso el
 * copy cuelga de `auth.invitesEnabled` y no de una constante: en `dev` sigue sin haber registro.
 *
 * Lo que **no** ha cambiado es el realm: `registrationAllowed` se queda en `false` y el alta la
 * ejecuta nuestro backend contra la Admin API. Esta página no ofrece registrarse desde Keycloak, y
 * si algún día lo publicara, seguiría siendo falsable en dos clics.
 *
 * La recuperación de contraseña no se pinta aquí: sale sola en la pantalla de Keycloak en cuanto el
 * realm tenga `resetPasswordAllowed` y SMTP (#347, hoy los dos realms sin ninguna de las dos cosas).
 */
const DESTINO_POR_DEFECTO = '/catalogo';

export function AccessPage() {
  const auth = useAuth();
  const location = useLocation();

  // `RequireSession` deja aquí el destino real. Se puede llegar sin él (el CTA de la home enlaza
  // a /acceso a pelo), y entonces se entra al catálogo, que es lo que venía buscando.
  // `altaEmail` lo deja `/registro` cuando el alta acaba de consumarse: no se auto-inicia sesión,
  // así que esta pantalla es el siguiente paso y conviene que diga con qué correo entrar.
  const state = location.state as { from?: string; altaEmail?: string } | null;
  const destino = state?.from ?? DESTINO_POR_DEFECTO;
  const altaEmail = state?.altaEmail;

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
          {altaEmail ? (
            <>
              Tu cuenta ya <em style={{ color: 'var(--accent)' }}>existe</em>
            </>
          ) : (
            <>
              Hace falta <em style={{ color: 'var(--accent)' }}>cuenta</em>
            </>
          )}
        </h1>
        {altaEmail ? (
          <p style={{ color: 'var(--text-muted)', fontSize: 15, lineHeight: 1.6, margin: '0 0 26px' }}>
            La hemos creado con <strong>{altaEmail}</strong>. Entra con ese correo y la contraseña que
            acabas de elegir.
          </p>
        ) : (
          <>
            <p style={{ color: 'var(--text-muted)', fontSize: 15, lineHeight: 1.6, margin: '0 0 8px' }}>
              El catálogo y las fichas de producto solo se ven con la sesión iniciada.
            </p>
            {/*
              Atado a `ready` y no solo a `invitesEnabled`: hasta que `/api/config` no contesta,
              `invitesEnabled` es `false` porque no se sabe, no porque no lo haya. Sin esto la
              pantalla afirmaba «el registro está cerrado» durante el arranque — que es exactamente
              la frase falsa que esta issue viene a quitar, enseñada en el peor momento posible.
              Mientras no se sepa, no se dice ninguna de las dos: se reserva el hueco y ya está.
            */}
            <p style={{ color: 'var(--text-muted)', fontSize: 15, lineHeight: 1.6, margin: '0 0 26px', minHeight: 48 }}>
              {!auth.ready ? null : auth.invitesEnabled ? (
                <>
                  <strong>El alta es por invitación.</strong> Si alguien te ha invitado, tienes un
                  enlace en tu correo; no se puede crear una cuenta sin él.
                </>
              ) : (
                <>
                  <strong>El registro está cerrado por ahora.</strong> Las cuentas se dan de alta a mano.
                </>
              )}
            </p>
          </>
        )}

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
