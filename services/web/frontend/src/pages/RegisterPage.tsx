import { useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';

import { ApiError } from '../api/client';
import { useAcceptInvitation, useInvitationToken } from '../api/hooks';
import { useAuth } from '../auth/AuthProvider';
import { KeyIcon, MailIcon } from '../components/icons';
import { Centered, Empty } from '../components/States';
import { desenlaceDelAlta, estadoDelRegistro, leerToken, LONGITUD_MINIMA_CONTRASENA } from '../lib/invitation';
import type { DesenlaceDelAlta } from '../lib/invitation';

/**
 * El alta por invitación (#550). Ruta **pública**, hermana de `/acceso`: quien llega no tiene
 * sesión, y ése es justamente el punto.
 *
 * Se llega desde el enlace del correo, `…/registro?token=…`, que arma el backend con
 * `APP_PUBLIC_URL`. Las pantallas que hay que pintar son siete y solo cuatro vienen del token; las
 * otras tres —arranque, entorno apagado, y llegar aquí sin token— las decide `lib/invitation.ts`,
 * que es donde vive todo lo comprobable de esta página: aquí no hay nada que un test pueda mirar,
 * porque este repo no monta jsdom.
 *
 * Dos reglas que no son de estilo:
 *
 * - **El correo no se edita.** Lo fija la invitación y el formulario solo lo enseña. Si fuera
 *   editable, una invitación dejaría de ser una invitación y pasaría a ser un alta libre. El DTO
 *   del backend ni siquiera tiene campo para él, así que mandarlo sería un 400.
 * - **La política de contraseña no se reimplementa.** El único corte de aquí es el suelo de 12 que
 *   el DTO ya exige, anunciado en pantalla; lo que decida la `passwordPolicy` del realm (#347)
 *   llega como mensaje del servidor y se enseña tal cual, que es la convención del frontend.
 *
 * Tras el alta **no se auto-inicia sesión**: se lleva a `/acceso`, porque el camino que hay que
 * dejar ejercido es el PKCE de siempre y no uno que solo ocurre una vez en la vida de la cuenta.
 */
const fmtCaducidad = new Intl.DateTimeFormat('es-ES', { day: 'numeric', month: 'long' });

const estiloCampo: CSSProperties = {
  width: '100%',
  border: '1px solid var(--border)',
  background: 'var(--surface)',
  borderRadius: 'var(--r-sm)',
  padding: '11px 12px',
  fontSize: 15,
  color: 'var(--text)',
  fontFamily: 'inherit',
};

export function RegisterPage() {
  const auth = useAuth();
  const location = useLocation();
  const navigate = useNavigate();

  const token = leerToken(location.search);
  // Sin registro configurado los dos endpoints dan 503 sea cual sea el token: no se pregunta.
  const consultable = auth.ready && auth.invitesEnabled ? token : null;
  const { data: vista, isError } = useInvitationToken(consultable);
  const aceptar = useAcceptInvitation(token);

  const [nombre, setNombre] = useState('');
  const [password, setPassword] = useState('');
  const [fallo, setFallo] = useState<DesenlaceDelAlta | null>(null);

  const estado = estadoDelRegistro({
    ready: auth.ready,
    invitesEnabled: auth.invitesEnabled,
    token,
    vista,
    errorConsulta: isError,
  });

  const enviar = () => {
    setFallo(null);
    aceptar.mutate(
      { password, ...(nombre.trim() !== '' ? { firstName: nombre.trim() } : {}) },
      {
        onSuccess: (r) => navigate('/acceso', { state: { altaEmail: r.email }, replace: true }),
        onError: (err) => {
          // `ApiError` trae el status, y el status basta para discriminar los cinco desenlaces: el
          // 409 del alta es el único cuerpo no estándar del módulo y no hace falta leerlo.
          const status = err instanceof ApiError ? err.status : 0;
          setFallo(desenlaceDelAlta(status, err instanceof Error ? err.message : undefined));
        },
      },
    );
  };

  if (estado === 'cargando') return <Centered>Cargando…</Centered>;

  if (estado === 'apagado') {
    return (
      <Centered>
        <Empty
          title="El registro no está disponible"
          text="Este entorno no tiene configurada el alta por invitación. Estará disponible al desplegar en el cluster."
        >
          <VolverAlInicio />
        </Empty>
      </Centered>
    );
  }

  if (estado === 'sin-token') {
    return (
      <Centered>
        <Empty
          title="Falta la invitación"
          text="Para darte de alta hace falta el enlace que te llegó por correo. Ábrelo entero: el que va sin código no sirve."
        >
          <VolverAlInicio />
        </Empty>
      </Centered>
    );
  }

  if (estado === 'error-consulta') {
    return (
      <Centered>
        <Empty
          title="No hemos podido comprobar tu invitación"
          text="Ha fallado algo por nuestro lado. Vuelve a abrir el enlace del correo dentro de un rato."
        >
          <VolverAlInicio />
        </Empty>
      </Centered>
    );
  }

  if (estado === 'caducada') {
    return (
      <Centered>
        <Empty
          title="Esta invitación ha caducado"
          text="Las invitaciones duran siete días. Pídele otra a quien te invitó: puede retirar ésta y mandarte una nueva."
        >
          <VolverAlInicio />
        </Empty>
      </Centered>
    );
  }

  if (estado === 'canjeada') {
    return (
      <Centered>
        <Empty title="Esta invitación ya se ha usado" text="La cuenta ya existe: entra con ella.">
          <EnlaceAcceso />
        </Empty>
      </Centered>
    );
  }

  if (estado === 'desconocida') {
    // Cubre a la vez el token inexistente y el revocado, colapsados a propósito por el backend: si
    // quien invitó se la quitó, no hay nada útil que contarle a quien recibió el correo.
    return (
      <Centered>
        <Empty
          title="Esta invitación no vale"
          text="El enlace no es correcto o quien te invitó lo ha retirado. Comprueba que lo has copiado entero, o pídele uno nuevo."
        >
          <VolverAlInicio />
        </Empty>
      </Centered>
    );
  }

  // A partir de aquí el token es válido, así que los tres campos opcionales han llegado.
  if (fallo && !fallo.permiteReintento) {
    return (
      <Centered>
        <Empty title={fallo.titulo} text={fallo.texto}>
          {fallo.llevaAAcceso ? <EnlaceAcceso /> : <VolverAlInicio />}
        </Empty>
      </Centered>
    );
  }

  const corta = password.length < LONGITUD_MINIMA_CONTRASENA;
  const invitador = vista?.inviterName?.trim();

  return (
    <section className="dt-fade" style={{ padding: '60px 0', display: 'grid', placeItems: 'center' }}>
      <div
        style={{
          background: 'var(--surface)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--r-lg)',
          padding: '40px 32px',
          maxWidth: 520,
          width: '100%',
        }}
      >
        <h1 className="serif" style={{ fontSize: 30, margin: '0 0 10px', textAlign: 'center' }}>
          Crea tu <em style={{ color: 'var(--accent)' }}>cuenta</em>
        </h1>
        <p
          style={{
            color: 'var(--text-muted)',
            fontSize: 15,
            lineHeight: 1.6,
            margin: '0 0 26px',
            textAlign: 'center',
          }}
        >
          {invitador ? <strong>{invitador}</strong> : 'Alguien'} te ha invitado a Deal Tracker
          {vista?.expiresAt && `. La invitación vale hasta el ${fmtCaducidad.format(new Date(vista.expiresAt))}`}.
        </p>

        {fallo && (
          <div
            role="alert"
            style={{
              padding: '10px 12px',
              borderRadius: 'var(--r-sm)',
              background: 'var(--warn-soft)',
              color: 'var(--warn-text)',
              fontSize: 13.5,
              lineHeight: 1.5,
              marginBottom: 18,
            }}
          >
            {fallo.texto}
          </div>
        )}

        <Etiqueta icono={<MailIcon size={15} />} texto="Tu correo" />
        {/* De solo lectura: lo fija la invitación, no el formulario. */}
        <input
          type="email"
          value={vista?.email ?? ''}
          readOnly
          aria-label="Correo al que se te ha invitado"
          style={{ ...estiloCampo, color: 'var(--text-muted)', background: 'var(--surface-2)' }}
        />

        <div style={{ height: 16 }} />

        <Etiqueta texto="Cómo quieres que te llamemos (opcional)" />
        <input
          type="text"
          value={nombre}
          maxLength={60}
          onChange={(e) => setNombre(e.target.value)}
          placeholder="Tu nombre"
          style={estiloCampo}
        />

        <div style={{ height: 16 }} />

        <Etiqueta icono={<KeyIcon size={15} />} texto="Contraseña" />
        <input
          type="password"
          value={password}
          maxLength={128}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="new-password"
          style={estiloCampo}
        />
        <p style={{ color: 'var(--text-muted)', fontSize: 13, margin: '7px 0 0' }}>
          Mínimo {LONGITUD_MINIMA_CONTRASENA} caracteres.
        </p>

        <button
          onClick={enviar}
          disabled={corta || aceptar.isPending}
          className="btn btn-primary"
          style={{
            width: '100%',
            marginTop: 24,
            padding: '13px 24px',
            fontSize: 15,
            opacity: corta || aceptar.isPending ? 0.6 : 1,
          }}
        >
          {aceptar.isPending ? 'Creando la cuenta…' : 'Crear mi cuenta'}
        </button>

        <p style={{ color: 'var(--text-muted)', fontSize: 13, margin: '18px 0 0', textAlign: 'center' }}>
          ¿Ya tienes cuenta? <Link to="/acceso" style={{ color: 'var(--accent)', fontWeight: 700 }}>Entra por aquí</Link>
        </p>
      </div>
    </section>
  );
}

function Etiqueta({ icono, texto }: { icono?: ReactNode; texto: string }) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        marginBottom: 6,
        fontSize: 13.5,
        fontWeight: 700,
        color: 'var(--text-muted)',
      }}
    >
      {icono}
      <span>{texto}</span>
    </div>
  );
}

function EnlaceAcceso() {
  return (
    <div style={{ marginTop: 18 }}>
      <Link to="/acceso" style={{ color: 'var(--accent)', fontWeight: 700, fontSize: 14, textDecoration: 'none' }}>
        Ir a iniciar sesión
      </Link>
    </div>
  );
}

function VolverAlInicio() {
  return (
    <div style={{ marginTop: 18 }}>
      <Link to="/" style={{ color: 'var(--accent)', fontWeight: 700, fontSize: 14, textDecoration: 'none' }}>
        Volver al inicio
      </Link>
    </div>
  );
}
