import { useEffect, useMemo, useState } from 'react';

import type { InvitationView, TelegramLinkResult } from '../api/types';
import { ApiError } from '../api/client';
import {
  useCreateInvitation,
  useInvitations,
  useLinkTelegram,
  useRevokeInvitation,
  useTelegramSettings,
  useUnlinkTelegram,
} from '../api/hooks';
import { useAuth } from '../auth/AuthProvider';
import {
  CheckIcon,
  CopyIcon,
  ExternalIcon,
  MailIcon,
  QrIcon,
  SendIcon,
  SettingsIcon,
} from '../components/icons';
import { ErrorState } from '../components/States';
import { useToast } from '../components/Toast';
import { etiquetaDeEstado, mensajeDelErrorAlInvitar, puedeRevocarse } from '../lib/invitation';
import { qrModules } from '../lib/qr';

/** Página "Ajustes": vincular la cuenta con Telegram, y repartir las invitaciones propias (#551). */
export function SettingsPage() {
  const auth = useAuth();
  const toast = useToast();
  const { data, isPending, isError, refetch } = useTelegramSettings(auth.authenticated);
  const link = useLinkTelegram();
  const unlink = useUnlinkTelegram();

  // Estados de sesión (mismo patrón que Mis seguimientos).
  if (!auth.ready) {
    return <Centered>Cargando…</Centered>;
  }
  if (!auth.enabled) {
    return (
      <Centered>
        <Empty
          title="Ajustes"
          text="El inicio de sesión con Keycloak estará disponible al desplegar en el cluster. Aquí vincularás tu Telegram para recibir avisos."
        />
      </Centered>
    );
  }
  if (!auth.authenticated) {
    return (
      <Centered>
        <Empty title="Inicia sesión" text="Entra para vincular tu Telegram y gestionar tus avisos.">
          <button onClick={() => auth.login()} className="btn btn-primary" style={{ marginTop: 16, padding: '12px 20px' }}>
            Iniciar sesión
          </button>
        </Empty>
      </Centered>
    );
  }

  const onLink = () => {
    link.mutate(undefined, {
      // Nada de `window.open` aquí (#266). Este callback corre después de hasta dos saltos de red
      // —el refresco del token de Keycloak en `getFreshToken()` y el propio POST—, o sea fuera del
      // gesto del usuario, que es exactamente la cadena que cortan los bloqueadores de pop-ups.
      // Ahora el enlace se pinta y lo pulsa el usuario: un clic sobre un ancla siempre es gesto.
      onSuccess: () => toast('Enlace listo: escanea el QR o abre Telegram'),
      onError: (err) =>
        toast(
          err instanceof ApiError && err.status === 503
            ? 'El vínculo de Telegram no está configurado todavía'
            : err instanceof ApiError
              ? err.message
              : 'No se pudo iniciar el vínculo',
        ),
    });
  };

  const onUnlink = () => {
    unlink.mutate(undefined, {
      onSuccess: () => toast('Telegram desvinculado'),
      onError: (err) => toast(err instanceof ApiError ? err.message : 'No se pudo desvincular'),
    });
  };

  return (
    <section className="dt-fade" style={{ paddingTop: 22, maxWidth: 620, margin: '0 auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 11, marginBottom: 18 }}>
        <span style={{ width: 40, height: 40, borderRadius: 12, background: 'var(--accent-soft)', color: 'var(--accent)', display: 'grid', placeItems: 'center' }}>
          <SettingsIcon size={20} />
        </span>
        <div>
          <h1 className="serif" style={{ fontSize: 27, margin: 0, lineHeight: 1.1 }}>Ajustes</h1>
          <div style={{ fontSize: 13.5, color: 'var(--text-faint)' }}>Tus avisos por Telegram y las invitaciones que puedes repartir.</div>
        </div>
      </div>

      <div style={{ display: 'grid', gap: 16 }}>
        {isPending ? (
          <div className="dt-skel" style={{ height: 160, borderRadius: 'var(--r-lg)' }} />
        ) : isError ? (
          <ErrorState onRetry={() => refetch()} />
        ) : (
          <TelegramCard
            linked={data.linked}
            username={data.telegramUsername}
            pending={data.pendingLink}
            enlace={link.data ?? null}
            onLink={onLink}
            onUnlink={onUnlink}
            linking={link.isPending}
            unlinking={unlink.isPending}
          />
        )}

        {/*
          La quinta rama de esta página es de LA TARJETA, no de la página: que este servidor no dé
          altas no apaga Telegram, así que `invitesEnabled` no puede decidir un `return` de arriba.

          Y llega aquí ya concluyente sin comprobar nada más, porque la página sale antes con
          `!auth.ready`: mientras eso es falso el flag vale `false` porque **no se sabe**, no porque
          no lo haya, y colgar un texto de él sin `ready` hace que la pantalla afirme la rama
          negativa durante el arranque (le pasó a `/acceso`). Si alguien mueve esta tarjeta por
          encima de esas guardas, ese bug vuelve.
        */}
        <InvitationsCard enabled={auth.invitesEnabled} />
      </div>
    </section>
  );
}

/**
 * Las invitaciones de quien invita (#551): cuánto cupo le queda, a quién ha invitado y revocar.
 *
 * Dos cosas que no se leen en el código y mandan sobre toda la tarjeta:
 *
 * **El cupo a cero es el estado normal, no un error.** `app_user.invites_remaining` arranca a 0 para
 * todo el mundo (`0044`) y se reparte a mano por SQL: al estrenar, esta pantalla la ve una persona
 * por entorno y la ve a cero. Por eso el vacío se explica con palabras en vez de enseñar un 0 seco,
 * que se leería como una avería.
 *
 * **La lista se pinta aunque el cupo sea cero**, que es justo cuando más falta hace: revocar es lo
 * único que devuelve cupo, y también la única salida de un correo bloqueado por una invitación
 * caducada (ver `puedeRevocarse`).
 */
function InvitationsCard({ enabled }: { enabled: boolean }) {
  const toast = useToast();
  const { data, isPending, isError, refetch } = useInvitations(enabled);
  const crear = useCreateInvitation();
  const revocar = useRevokeInvitation();
  const [email, setEmail] = useState('');

  const onInvitar = (e: React.FormEvent) => {
    e.preventDefault();
    const destino = email.trim();
    if (!destino) return;
    crear.mutate(destino, {
      onSuccess: (creada) => {
        setEmail('');
        toast(`Invitación enviada a ${creada.email}`);
      },
      // Los cuatro códigos de este endpoint dicen cosas distintas y solo uno es un fallo nuestro:
      // la traducción vive en un módulo puro para poder testearla (#551).
      onError: (err) =>
        toast(
          err instanceof ApiError
            ? mensajeDelErrorAlInvitar(err.status, err.message)
            : 'No se ha podido enviar la invitación.',
        ),
    });
  };

  const onRevocar = (invitacion: InvitationView) => {
    revocar.mutate(invitacion.id, {
      onSuccess: () => toast('Invitación revocada: recuperas el cupo'),
      onError: (err) =>
        toast(err instanceof ApiError ? err.message : 'No se ha podido revocar la invitación'),
    });
  };

  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--r-lg)', padding: '20px 20px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 14 }}>
        <span style={{ width: 42, height: 42, borderRadius: 12, background: 'var(--accent-soft)', color: 'var(--accent)', display: 'grid', placeItems: 'center', flex: 'none' }}>
          <MailIcon size={21} />
        </span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 800, fontSize: 16 }}>Invitaciones</div>
          <div style={{ fontSize: 13.5, color: 'var(--text-muted)' }}>
            Aquí solo se entra por invitación. Estas son las tuyas.
          </div>
        </div>
      </div>

      {!enabled ? (
        <Aviso>
          Este servidor no da altas: el registro por invitación no está configurado aquí. Es lo
          normal en el entorno de desarrollo.
        </Aviso>
      ) : isPending ? (
        <div className="dt-skel" style={{ height: 120, borderRadius: 'var(--r-md)' }} />
      ) : isError ? (
        <ErrorState onRetry={() => refetch()} />
      ) : (
        <div style={{ display: 'grid', gap: 16 }}>
          <Cupo restantes={data.invitesRemaining} />

          {/* Con cupo 0 no se pinta: un formulario que solo puede contestar 403 es una trampa. */}
          {data.invitesRemaining > 0 && (
            <form onSubmit={onInvitar} style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="correo@ejemplo.com"
                aria-label="Correo al que invitar"
                style={{ flex: '1 1 220px', minWidth: 0, fontSize: 14, padding: '11px 13px', borderRadius: 'var(--r-md)', border: '1px solid var(--border)', background: 'var(--surface-2)', color: 'var(--text)' }}
              />
              <button
                type="submit"
                disabled={crear.isPending}
                className="btn btn-primary"
                style={{ display: 'inline-flex', alignItems: 'center', gap: 8, padding: '11px 18px', fontSize: 14, flex: 'none' }}
              >
                <MailIcon size={16} />
                {crear.isPending ? 'Enviando…' : 'Invitar'}
              </button>
            </form>
          )}

          {data.invitations.length === 0 ? (
            <div style={{ fontSize: 13.5, color: 'var(--text-muted)', lineHeight: 1.5 }}>
              Todavía no has invitado a nadie.
            </div>
          ) : (
            <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'grid', gap: 8 }}>
              {data.invitations.map((inv) => (
                <FilaDeInvitacion
                  key={inv.id}
                  invitacion={inv}
                  onRevocar={() => onRevocar(inv)}
                  revocando={revocar.isPending && revocar.variables === inv.id}
                />
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * El cupo. **Cero no es un error**, así que se explica de dónde sale en vez de enseñar el número a
 * secas: las invitaciones se reparten a mano, no se ganan usando la aplicación.
 */
function Cupo({ restantes }: { restantes: number }) {
  if (restantes === 0) {
    return (
      <Aviso>
        No te quedan invitaciones. No es un fallo: se reparten a mano, así que si necesitas alguna,
        pídela. Revocar una pendiente también te devuelve su cupo.
      </Aviso>
    );
  }
  return (
    <div style={{ fontSize: 14, color: 'var(--text)' }}>
      Te {restantes === 1 ? 'queda' : 'quedan'}{' '}
      <strong>{restantes}</strong> {restantes === 1 ? 'invitación' : 'invitaciones'}.
    </div>
  );
}

function FilaDeInvitacion({
  invitacion,
  onRevocar,
  revocando,
}: {
  invitacion: InvitationView;
  onRevocar: () => void;
  revocando: boolean;
}) {
  const etiqueta = etiquetaDeEstado(invitacion.status);
  const colores = {
    vivo: { fondo: 'var(--accent-soft)', texto: 'var(--accent)' },
    exito: { fondo: 'var(--good-soft)', texto: 'var(--good-text)' },
    neutro: { fondo: 'var(--surface-2)', texto: 'var(--text-muted)' },
  }[etiqueta.tono];

  return (
    <li style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 'var(--r-md)', padding: '10px 12px' }}>
      <span style={{ flex: '1 1 180px', minWidth: 0, fontSize: 13.5, overflowWrap: 'anywhere' }}>
        {invitacion.email}
      </span>
      <span style={{ fontSize: 12, fontWeight: 800, borderRadius: 'var(--r-pill)', padding: '4px 10px', background: colores.fondo, color: colores.texto, flex: 'none' }}>
        {etiqueta.texto}
      </span>
      {puedeRevocarse(invitacion.status) && (
        <button
          onClick={onRevocar}
          disabled={revocando}
          className="btn-ghost"
          style={{ padding: '7px 13px', fontSize: 12.5, fontWeight: 700, borderRadius: 'var(--r-pill)', border: '1px solid var(--border)', color: 'var(--text-muted)', flex: 'none' }}
        >
          {revocando ? 'Revocando…' : 'Revocar'}
        </button>
      )}
    </li>
  );
}

/** El recuadro de «esto es así», que aquí se usa tres veces y nunca para un error. */
function Aviso({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ fontSize: 13.5, color: 'var(--text-muted)', background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 'var(--r-md)', padding: '11px 13px', lineHeight: 1.5 }}>
      {children}
    </div>
  );
}

function TelegramCard({
  linked,
  username,
  pending,
  enlace,
  onLink,
  onUnlink,
  linking,
  unlinking,
}: {
  linked: boolean;
  username: string | null;
  pending: boolean;
  /** El enlace recién emitido, si esta pestaña lo pidió. Se pierde al recargar: ver más abajo. */
  enlace: TelegramLinkResult | null;
  onLink: () => void;
  onUnlink: () => void;
  linking: boolean;
  unlinking: boolean;
}) {
  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--r-lg)', padding: '20px 20px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 14 }}>
        <span style={{ width: 42, height: 42, borderRadius: 12, background: 'var(--accent-soft)', color: 'var(--accent)', display: 'grid', placeItems: 'center', flex: 'none' }}>
          <SendIcon size={21} />
        </span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 800, fontSize: 16 }}>Telegram</div>
          <div style={{ fontSize: 13.5, color: 'var(--text-muted)' }}>
            {linked
              ? 'Conectado: te avisaremos por aquí.'
              : 'No conectado. Vincúlalo para recibir avisos al instante.'}
          </div>
        </div>
        {linked && (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12.5, fontWeight: 800, color: 'var(--accent)', background: 'var(--accent-soft)', borderRadius: 'var(--r-pill)', padding: '5px 11px', flex: 'none' }}>
            <CheckIcon size={14} /> Conectado
          </span>
        )}
      </div>

      {linked ? (
        <>
          {username && (
            <div style={{ fontSize: 13.5, color: 'var(--text-muted)', marginBottom: 14 }}>
              Cuenta: <strong style={{ color: 'var(--text)' }}>@{username}</strong>
            </div>
          )}
          <button
            onClick={onUnlink}
            disabled={unlinking}
            className="btn-ghost"
            style={{ padding: '11px 18px', fontSize: 14, fontWeight: 700, borderRadius: 'var(--r-pill)', border: '1px solid var(--border)', color: 'var(--text-muted)' }}
          >
            {unlinking ? 'Desvinculando…' : 'Desvincular Telegram'}
          </button>
        </>
      ) : (
        <>
          {enlace ? (
            <PanelDeEnlace enlace={enlace} onRegenerar={onLink} regenerando={linking} />
          ) : (
            <>
              {pending && (
                <div style={{ fontSize: 13.5, color: 'var(--text-muted)', background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 'var(--r-md)', padding: '11px 13px', marginBottom: 14 }}>
                  Hay un enlace en curso, pero se pidió desde otra pestaña o antes de recargar, así
                  que su código ya no está aquí. Genera otro: es gratis y anula el anterior.
                </div>
              )}
              <button
                onClick={onLink}
                disabled={linking}
                className="btn btn-primary"
                style={{ display: 'inline-flex', alignItems: 'center', gap: 8, padding: '12px 20px', fontSize: 14.5 }}
              >
                <SendIcon size={17} />
                {linking ? 'Generando…' : pending ? 'Generar otro enlace' : 'Vincular Telegram'}
              </button>
            </>
          )}
        </>
      )}
    </div>
  );
}

/**
 * Lo que ve el usuario en cuanto pide el enlace: el QR para saltar al móvil, el token copiable
 * para Telegram Web y la app de escritorio, los dos enlaces directos y cuánto queda de validez.
 *
 * Ninguno de los caminos usa `window.open`: son anclas que pulsa el usuario (#266).
 */
function PanelDeEnlace({
  enlace,
  onRegenerar,
  regenerando,
}: {
  enlace: TelegramLinkResult;
  onRegenerar: () => void;
  regenerando: boolean;
}) {
  const restante = useCuentaAtras(enlace.expiresAt);
  const caducado = restante === null;

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <div style={{ fontSize: 13.5, color: 'var(--text-muted)', lineHeight: 1.5 }}>
        Falta que pulses <strong style={{ color: 'var(--text)' }}>«Start»</strong> en el bot: un bot
        de Telegram no puede escribirte primero. Esta página se actualizará sola al confirmarlo.
      </div>

      {caducado ? (
        <div style={{ fontSize: 13.5, color: 'var(--text-muted)', background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 'var(--r-md)', padding: '11px 13px' }}>
          Este enlace ha caducado. Genera otro para seguir.
        </div>
      ) : (
        <>
          <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', alignItems: 'flex-start' }}>
            <CodigoQr texto={enlace.deepLink} />
            <div style={{ flex: '1 1 220px', minWidth: 200, display: 'grid', gap: 10, alignContent: 'start' }}>
              <div style={{ display: 'inline-flex', alignItems: 'center', gap: 7, fontSize: 13.5, fontWeight: 800 }}>
                <QrIcon size={16} /> Desde el móvil
              </div>
              <div style={{ fontSize: 13, color: 'var(--text-muted)', lineHeight: 1.5 }}>
                Enfoca el código con la cámara. Telegram se abrirá en el bot con el enlace ya
                dentro, y solo tendrás que pulsar «Start».
              </div>
              <a
                href={enlace.deepLink}
                target="_blank"
                rel="noopener noreferrer"
                className="btn btn-primary"
                style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 8, padding: '11px 16px', fontSize: 14, textDecoration: 'none' }}
              >
                <SendIcon size={16} /> Abrir en la app de Telegram
              </a>
              <a
                href={`https://web.telegram.org/k/#@${enlace.botUsername}`}
                target="_blank"
                rel="noopener noreferrer"
                style={{ display: 'inline-flex', alignItems: 'center', gap: 7, fontSize: 13.5, fontWeight: 700, color: 'var(--accent)', textDecoration: 'none' }}
              >
                <ExternalIcon size={15} /> Abrir en Telegram Web
              </a>
            </div>
          </div>

          <TokenCopiable token={enlace.token} />
        </>
      )}

      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', fontSize: 12.5, color: 'var(--text-faint)' }}>
        <span>{caducado ? 'Enlace caducado' : `Caduca en ${restante}`}</span>
        <button
          onClick={onRegenerar}
          disabled={regenerando}
          className="btn-ghost"
          style={{ padding: '7px 13px', fontSize: 12.5, fontWeight: 700, borderRadius: 'var(--r-pill)', border: '1px solid var(--border)', color: 'var(--text-muted)' }}
        >
          {regenerando ? 'Generando…' : 'Generar otro'}
        </button>
      </div>
    </div>
  );
}

/**
 * El token a la vista, que es lo que hoy no se enseña en ninguna parte. Es el único camino que
 * funciona siempre: en Telegram Web y en la app de escritorio el usuario ya tiene sesión y lo
 * único que le falta es esto, sin depender de que un `tg://` se resuelva bien ni de acertar en
 * la interstitial de telegram.org.
 */
function TokenCopiable({ token }: { token: string }) {
  const toast = useToast();
  const comando = `/start ${token}`;

  const copiar = async () => {
    try {
      await navigator.clipboard.writeText(comando);
      toast('Copiado: pégalo en el chat del bot');
    } catch {
      // Sin permiso de portapapeles (o sin HTTPS) el texto sigue siendo seleccionable a mano.
      toast('No se pudo copiar: selecciona el texto y cópialo');
    }
  };

  return (
    <div style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 'var(--r-md)', padding: '12px 13px', display: 'grid', gap: 8 }}>
      <div style={{ fontSize: 13, color: 'var(--text-muted)', lineHeight: 1.5 }}>
        ¿Ya tienes Telegram abierto? Abre el chat del bot y pega esto:
      </div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <code
          style={{ flex: '1 1 240px', minWidth: 0, overflowWrap: 'anywhere', fontSize: 12.5, lineHeight: 1.45, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--r-sm)', padding: '8px 10px', userSelect: 'all' }}
        >
          {comando}
        </code>
        <button
          onClick={copiar}
          className="btn-ghost"
          style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '9px 14px', fontSize: 13, fontWeight: 700, borderRadius: 'var(--r-pill)', border: '1px solid var(--border)', color: 'var(--text-muted)', flex: 'none' }}
        >
          <CopyIcon size={15} /> Copiar
        </button>
      </div>
    </div>
  );
}

/** Módulos de silencio alrededor del código. El estándar pide 4; con menos, muchos lectores fallan. */
const QR_MARGEN = 4;

/**
 * El QR, pintado en JSX desde la matriz de `qrModules`.
 *
 * Va **siempre oscuro sobre blanco**, sin seguir el tema de la página a propósito: un QR invertido
 * (claro sobre oscuro) lo leen algunos lectores y otros no, y un código que solo funciona en la
 * mitad de los móviles es peor que uno que desentona en modo oscuro.
 */
function CodigoQr({ texto }: { texto: string }) {
  const modulos = useMemo(() => qrModules(texto), [texto]);
  const lado = modulos.length + QR_MARGEN * 2;

  // Un solo `path` con todos los módulos: mucho más barato que un `rect` por módulo (son ~1.500).
  const d = modulos
    .flatMap((fila, r) =>
      fila.map((oscuro, c) => (oscuro ? `M${c + QR_MARGEN} ${r + QR_MARGEN}h1v1h-1z` : '')),
    )
    .join('');

  return (
    <svg
      viewBox={`0 0 ${lado} ${lado}`}
      width={188}
      height={188}
      shapeRendering="crispEdges"
      role="img"
      aria-label="Código QR para abrir el bot de Telegram"
      style={{ flex: 'none', borderRadius: 'var(--r-md)', border: '1px solid var(--border)' }}
    >
      <rect width={lado} height={lado} fill="#ffffff" />
      <path d={d} fill="#000000" />
    </svg>
  );
}

/**
 * Cuánto queda de validez, en texto ya formateado, o `null` si caducó. Refresca cada 30 s: el
 * token vive una hora, así que al segundo no le mira nadie.
 */
function useCuentaAtras(expiresAt: string): string | null {
  const fin = useMemo(() => new Date(expiresAt).getTime(), [expiresAt]);
  const [ahora, setAhora] = useState(() => Date.now());

  useEffect(() => {
    const id = setInterval(() => setAhora(Date.now()), 30_000);
    return () => clearInterval(id);
  }, []);

  const restanteMs = fin - ahora;
  if (!Number.isFinite(fin) || restanteMs <= 0) return null;

  const minutos = Math.ceil(restanteMs / 60_000);
  if (minutos < 60) return `${minutos} min`;
  const horas = Math.floor(minutos / 60);
  const resto = minutos % 60;
  return resto === 0 ? `${horas} h` : `${horas} h ${resto} min`;
}

function Centered({ children }: { children: React.ReactNode }) {
  return <section style={{ padding: '60px 0', display: 'grid', placeItems: 'center' }}>{children}</section>;
}

function Empty({ title, text, children }: { title: string; text: string; children?: React.ReactNode }) {
  return (
    <div style={{ textAlign: 'center', maxWidth: 400 }}>
      <div className="serif" style={{ fontSize: 22, fontWeight: 700, marginBottom: 6 }}>{title}</div>
      <div style={{ color: 'var(--text-muted)', fontSize: 14.5, lineHeight: 1.5 }}>{text}</div>
      {children}
    </div>
  );
}
