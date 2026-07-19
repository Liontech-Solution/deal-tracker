import { ApiError } from '../api/client';
import { useLinkTelegram, useTelegramSettings, useUnlinkTelegram } from '../api/hooks';
import { useAuth } from '../auth/AuthProvider';
import { CheckIcon, SendIcon, SettingsIcon } from '../components/icons';
import { ErrorState } from '../components/States';
import { useToast } from '../components/Toast';

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
      onSuccess: (res) => {
        // Abre el bot con el token; el usuario pulsa Start y el vínculo se confirma solo (poll).
        window.open(res.deepLink, '_blank', 'noopener,noreferrer');
        toast('Abriendo Telegram… pulsa «Start» en el bot');
      },
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
  onLink,
  onUnlink,
  linking,
  unlinking,
}: {
  linked: boolean;
  username: string | null;
  pending: boolean;
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
          {pending && (
            <div style={{ fontSize: 13.5, color: 'var(--text-muted)', background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 'var(--r-md)', padding: '11px 13px', marginBottom: 14 }}>
              Enlace en curso: abre el bot en Telegram y pulsa <strong style={{ color: 'var(--text)' }}>«Start»</strong>. Esta página se actualizará sola al confirmarlo.
            </div>
          )}
          <button
            onClick={onLink}
            disabled={linking}
            className="btn btn-primary"
            style={{ display: 'inline-flex', alignItems: 'center', gap: 8, padding: '12px 20px', fontSize: 14.5 }}
          >
            <SendIcon size={17} />
            {linking ? 'Abriendo…' : pending ? 'Reabrir Telegram' : 'Vincular Telegram'}
          </button>
        </>
      )}
    </div>
  );
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
