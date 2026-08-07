import { useEffect, useMemo, useState } from 'react';

import type { TelegramLinkResult } from '../api/types';
import { ApiError } from '../api/client';
import { useLinkTelegram, useTelegramSettings, useUnlinkTelegram } from '../api/hooks';
import { useAuth } from '../auth/AuthProvider';
import { CheckIcon, CopyIcon, ExternalIcon, QrIcon, SendIcon, SettingsIcon } from '../components/icons';
import { ErrorState } from '../components/States';
import { useToast } from '../components/Toast';
import { qrModules } from '../lib/qr';

/** Página "Ajustes": por ahora, vincular la cuenta con Telegram para recibir avisos. */
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
          <button onClick={auth.login} className="btn btn-primary" style={{ marginTop: 16, padding: '12px 20px' }}>
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
          <div style={{ fontSize: 13.5, color: 'var(--text-faint)' }}>Vincula Telegram para recibir los avisos de bajada.</div>
        </div>
      </div>

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
    </section>
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
