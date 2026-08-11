import { Link } from 'react-router-dom';

import { useDeleteInterest, useInterests } from '../api/hooks';
import type { InterestView } from '../api/types';
import { ApiError } from '../api/client';
import { useAuth } from '../auth/AuthProvider';
import { BellIcon, CloseIcon } from '../components/icons';
import { ProductImage } from '../components/ProductImage';
import { ErrorState } from '../components/States';
import { useToast } from '../components/Toast';
import { capitalize, etiquetaVariante } from '../lib/format';

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
          <button onClick={() => auth.login()} className="btn btn-primary" style={{ marginTop: 16, padding: '12px 20px' }}>
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
  // Un interés por filtros no apunta a ninguna prenda ('toda la ropa de niña rebajada un 30 %'),
  // así que no hay foto ni ficha a la que ir: la tarjeta se queda como estaba. Manda
  // `targetProductId` y no `imageUrl`, para que una prenda cuya galería aún no se ha traído siga
  // siendo enlazable y enseñe el hueco de «SIN FOTO» en vez de desaparecer.
  const prenda = interest.targetProductId !== null ? `/producto/${interest.targetProductId}` : null;

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 13, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--r-lg)', padding: '14px 16px' }}>
      {prenda && (
        <Link
          to={prenda}
          aria-label={`Ver ${interest.productName ?? 'la prenda'}`}
          style={{ flex: 'none', width: 64, borderRadius: 'var(--r-md)', overflow: 'hidden', display: 'block' }}
        >
          {/* El doble del hueco: la miniatura son 64 px y así no se ve borrosa en pantallas 2x. */}
          <ProductImage src={interest.imageUrl} alt="" section={interest.productSection} width={128} />
        </Link>
      )}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: 800, fontSize: 15.5, marginBottom: 5 }}>
          {prenda ? (
            <Link to={prenda} style={{ color: 'inherit', textDecoration: 'none' }}>
              {scopeTitle(interest)}
            </Link>
          ) : (
            scopeTitle(interest)
          )}
        </div>
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
    // Se compone aquí en vez de pintar `it.variantLabel` (#297): aquella es la etiqueta del aviso
    // de Telegram y lleva el color crudo, así que esta lista decía 'rosa' donde la ficha dice
    // 'Rosa'. Las dos salen ahora de `etiquetaVariante`.
    const variante = etiquetaVariante(it.variantSize, it.variantColor);
    return variante ? `${it.productName} · ${variante}` : it.productName;
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
