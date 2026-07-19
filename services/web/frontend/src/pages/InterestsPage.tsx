import { Link } from 'react-router-dom';

import { useDeleteInterest, useInterests } from '../api/hooks';
import type { InterestView } from '../api/types';
import { ApiError } from '../api/client';
import { useAuth } from '../auth/AuthProvider';
import { BellIcon, CloseIcon } from '../components/icons';
import { ErrorState } from '../components/States';
import { useToast } from '../components/Toast';
import { capitalize } from '../lib/format';

/** Página "Mis seguimientos": lista y borra los intereses del usuario autenticado. */
export function InterestsPage() {
  const auth = useAuth();
  const toast = useToast();
  const { data, isPending, isError, refetch } = useInterests(auth.authenticated);
  const del = useDeleteInterest();

  // Estados de sesión.
  if (!auth.ready) {
    return <Centered>Cargando…</Centered>;
  }
  if (!auth.enabled) {
    return (
      <Centered>
        <Empty
          title="Seguimientos"
          text="El inicio de sesión con Keycloak estará disponible al desplegar en el cluster. Aquí verás y gestionarás tus avisos."
        />
      </Centered>
    );
  }
  if (!auth.authenticated) {
    return (
      <Centered>
        <Empty title="Inicia sesión" text="Entra para ver y configurar los avisos de bajada de precio.">
          <button onClick={auth.login} className="btn btn-primary" style={{ marginTop: 16, padding: '12px 20px' }}>
            Iniciar sesión
          </button>
        </Empty>
      </Centered>
    );
  }

  const onDelete = (id: number) => {
    del.mutate(id, {
      onSuccess: () => toast('Seguimiento eliminado'),
      onError: (err) =>
        toast(err instanceof ApiError ? err.message : 'No se pudo eliminar el seguimiento'),
    });
  };

  const interests = data ?? [];

  return (
    <section className="dt-fade" style={{ paddingTop: 22, maxWidth: 760, margin: '0 auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 11, marginBottom: 4 }}>
        <span style={{ width: 40, height: 40, borderRadius: 12, background: 'var(--accent-soft)', color: 'var(--accent)', display: 'grid', placeItems: 'center' }}>
          <BellIcon size={20} />
        </span>
        <div>
          <h1 className="serif" style={{ fontSize: 27, margin: 0, lineHeight: 1.1 }}>Mis seguimientos</h1>
          <div style={{ fontSize: 13.5, color: 'var(--text-faint)' }}>Te avisamos por Telegram cuando bajen de precio de verdad.</div>
        </div>
      </div>

      {isPending ? (
        <div style={{ display: 'grid', gap: 12, marginTop: 20 }}>
          {[0, 1, 2].map((i) => (
            <div key={i} className="dt-skel" style={{ height: 92, borderRadius: 'var(--r-lg)' }} />
          ))}
        </div>
      ) : isError ? (
        <div style={{ marginTop: 20 }}>
          <ErrorState onRetry={() => refetch()} />
        </div>
      ) : interests.length === 0 ? (
        <div style={{ marginTop: 22 }}>
          <Empty title="Aún no sigues nada" text="Explora el catálogo y pulsa “Seguir” en la prenda que te interese.">
            <Link to="/catalogo" className="btn btn-primary" style={{ marginTop: 16, padding: '12px 20px', textDecoration: 'none' }}>
              Ir al catálogo
            </Link>
          </Empty>
        </div>
      ) : (
        <div style={{ display: 'grid', gap: 12, marginTop: 20 }}>
          {interests.map((it) => (
            <InterestCard key={it.id} interest={it} onDelete={() => onDelete(it.id)} deleting={del.isPending} />
          ))}
        </div>
      )}
    </section>
  );
}

function InterestCard({
  interest,
  onDelete,
  deleting,
}: {
  interest: InterestView;
  onDelete: () => void;
  deleting: boolean;
}) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--r-lg)', padding: '15px 17px' }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: 800, fontSize: 15.5, marginBottom: 5 }}>{scopeTitle(interest)}</div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          <Chip>−{formatPct(interest.minDiscountPct)}% mínimo</Chip>
          <Chip>{interest.compareBase === 'list_price' ? 'vs PVP' : 'vs mínimo reciente'}</Chip>
          <Chip>Últimos {interest.windowDays} días</Chip>
          {interest.retailerName && <Chip>{interest.retailerName}</Chip>}
        </div>
      </div>
      <button
        onClick={onDelete}
        disabled={deleting}
        aria-label="Eliminar seguimiento"
        className="btn-ghost"
        style={{ width: 40, height: 40, borderRadius: 'var(--r-pill)', display: 'grid', placeItems: 'center', flex: 'none', color: 'var(--text-muted)' }}
      >
        <CloseIcon size={17} />
      </button>
    </div>
  );
}

/** Título del alcance: nombre del producto/variante, o resumen de los filtros. */
function scopeTitle(it: InterestView): string {
  if (it.productName) {
    return it.variantLabel ? `${it.productName} · ${it.variantLabel}` : it.productName;
  }
  const parts = [it.gender, it.section, it.category, it.size, it.color]
    .filter((p): p is string => !!p)
    .map(capitalize);
  return parts.length ? parts.join(' · ') : 'Seguimiento';
}

/** `min_discount_pct` llega como NUMERIC string ("20.00"): lo mostramos sin decimales sobrantes. */
function formatPct(v: string): string {
  const n = Number(v);
  return Number.isFinite(n) ? String(n) : v;
}

function Chip({ children }: { children: React.ReactNode }) {
  return (
    <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-muted)', background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 'var(--r-pill)', padding: '4px 10px' }}>
      {children}
    </span>
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
