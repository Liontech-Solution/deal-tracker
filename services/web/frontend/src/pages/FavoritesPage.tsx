import { useState } from 'react';
import { Link } from 'react-router-dom';

import { useFavorites, useRemoveFavorite } from '../api/hooks';
import type { FavoriteView } from '../api/types';
import { ApiError } from '../api/client';
import { useAuth } from '../auth/AuthProvider';
import { FollowModal } from '../components/FollowModal';
import type { FollowTarget } from '../components/FollowModal';
import { BellIcon, CloseIcon, HeartIcon } from '../components/icons';
import { ProductImage } from '../components/ProductImage';
import { Centered, Chip, Empty, ErrorState } from '../components/States';
import { useToast } from '../components/Toast';
import { eurStr } from '../lib/format';

/**
 * Página "Mis favoritos": las prendas que el usuario ha guardado **sin pedir aviso** (#435).
 *
 * Hermana de `InterestsPage` y con su misma estructura —cuatro ramas de sesión resueltas dentro de
 * la página, skeletons, `ErrorState` con retry, vacío con CTA—, por eso comparte con ella los
 * helpers de `States.tsx` en vez de copiarlos.
 */
export function FavoritesPage() {
  const auth = useAuth();
  const toast = useToast();
  const { data, isPending, isError, refetch } = useFavorites(auth.authenticated);
  const del = useRemoveFavorite();
  /**
   * **Un solo modal para toda la lista**, con el estado de qué fila lo abrió. Uno por fila sería N
   * copias montadas de un componente que solo puede estar abierto una vez.
   */
  const [objetivo, setObjetivo] = useState<FollowTarget | null>(null);

  if (!auth.ready) {
    return <Centered>Cargando…</Centered>;
  }
  if (!auth.enabled) {
    return (
      <Centered>
        <Empty
          title="Favoritos"
          text="El inicio de sesión con Keycloak estará disponible al desplegar en el cluster. Aquí verás las prendas que guardes."
        />
      </Centered>
    );
  }
  if (!auth.authenticated) {
    return (
      <Centered>
        <Empty title="Inicia sesión" text="Entra para ver las prendas que has guardado.">
          <button onClick={() => auth.login()} className="btn btn-primary" style={{ marginTop: 16, padding: '12px 20px' }}>
            Iniciar sesión
          </button>
        </Empty>
      </Centered>
    );
  }

  const onDelete = (productId: number) => {
    del.mutate(productId, {
      onSuccess: () => toast('Quitado de favoritos'),
      onError: (err) =>
        toast(err instanceof ApiError ? err.message : 'No se pudo quitar de favoritos'),
    });
  };

  const favoritos = data ?? [];

  return (
    <section className="dt-fade" style={{ paddingTop: 22, maxWidth: 760, margin: '0 auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 11, marginBottom: 4 }}>
        <span style={{ width: 40, height: 40, borderRadius: 12, background: 'var(--accent-soft)', color: 'var(--accent)', display: 'grid', placeItems: 'center' }}>
          <HeartIcon size={20} filled />
        </span>
        <div>
          <h1 className="serif" style={{ fontSize: 27, margin: 0, lineHeight: 1.1 }}>Mis favoritos</h1>
          <div style={{ fontSize: 13.5, color: 'var(--text-faint)' }}>Prendas guardadas. Aquí no te avisamos de nada salvo que lo pidas.</div>
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
      ) : favoritos.length === 0 ? (
        <div style={{ marginTop: 22 }}>
          <Empty title="Aún no has guardado nada" text="Explora el catálogo y pulsa el corazón en la prenda que te guste.">
            <Link to="/catalogo" className="btn btn-primary" style={{ marginTop: 16, padding: '12px 20px', textDecoration: 'none' }}>
              Ir al catálogo
            </Link>
          </Empty>
        </div>
      ) : (
        <div style={{ display: 'grid', gap: 12, marginTop: 20 }}>
          {favoritos.map((f) => (
            <FavoriteCard
              key={f.id}
              favorito={f}
              onDelete={() => onDelete(f.productId)}
              deleting={del.isPending}
              onSeguir={() =>
                setObjetivo({ productId: f.productId, productName: f.productName ?? 'esta prenda' })
              }
            />
          ))}
        </div>
      )}

      {/* El modal que ya existe, sin bifurcarlo: acepta `variantId` opcional y con `undefined` cae
          solo en modo «cualquier variante», que es exactamente el alcance de un favorito. */}
      <FollowModal open={objetivo !== null} onClose={() => setObjetivo(null)} target={objetivo} />
    </section>
  );
}

function FavoriteCard({
  favorito,
  onDelete,
  deleting,
  onSeguir,
}: {
  favorito: FavoriteView;
  onDelete: () => void;
  deleting: boolean;
  onSeguir: () => void;
}) {
  const prenda = `/producto/${favorito.productId}`;
  const precio = eurStr(favorito.priceFrom);

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 13,
        background: 'var(--surface)',
        border: '1px solid var(--border)',
        borderRadius: 'var(--r-lg)',
        padding: '14px 16px',
        // La prenda de baja se atenúa entera, pero su botón de quitar sigue siendo pulsable: es
        // justo lo que el usuario querrá hacer con ella.
        opacity: favorito.delisted ? 0.55 : 1,
      }}
    >
      <Link
        to={prenda}
        aria-label={`Ver ${favorito.productName ?? 'la prenda'}`}
        style={{ flex: 'none', width: 64, borderRadius: 'var(--r-md)', overflow: 'hidden', display: 'block' }}
      >
        {/* El doble del hueco: la miniatura son 64 px y así no se ve borrosa en pantallas 2x. */}
        <ProductImage src={favorito.imageUrl} alt="" section={favorito.productSection} width={128} />
      </Link>

      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: 800, fontSize: 15.5, marginBottom: 5 }}>
          <Link to={prenda} style={{ color: 'inherit', textDecoration: 'none' }}>
            {favorito.productName ?? 'Prenda guardada'}
          </Link>
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
          {favorito.retailerName && <Chip>{favorito.retailerName}</Chip>}
          {precio && <Chip>desde {precio}</Chip>}
          {/* «Ya no disponible» y no «retirada»: el dato no distingue baja temporal de definitiva —
              solo dice que lleva N pasadas sin aparecer— y se deshace sola si el producto vuelve.
              Prometer lo segundo sería afirmar algo que no sabemos. */}
          {favorito.delisted && <Chip>Ya no disponible</Chip>}
        </div>
      </div>

      {favorito.seguido ? (
        // Ya hay un seguimiento activo de esta prenda: se lleva a su lista en vez de invitar a
        // configurar por segunda vez el mismo aviso.
        <Link
          to="/seguimientos"
          className="btn-ghost"
          aria-label="Ya tienes un seguimiento de esta prenda"
          title="Ya tienes un seguimiento de esta prenda"
          style={{ width: 40, height: 40, borderRadius: 'var(--r-pill)', display: 'grid', placeItems: 'center', flex: 'none', color: 'var(--accent)', textDecoration: 'none' }}
        >
          <BellIcon size={17} />
        </Link>
      ) : (
        <button
          onClick={onSeguir}
          // Sobre una prenda de baja no hay bajada de precio que esperar: ofrecer el aviso sería
          // prometer algo que no va a llegar.
          disabled={favorito.delisted}
          aria-label="Avisarme si baja"
          title="Avisarme si baja"
          className="btn-ghost"
          style={{ width: 40, height: 40, borderRadius: 'var(--r-pill)', display: 'grid', placeItems: 'center', flex: 'none', color: 'var(--text-muted)' }}
        >
          <BellIcon size={17} />
        </button>
      )}

      <button
        onClick={onDelete}
        disabled={deleting}
        aria-label="Quitar de favoritos"
        className="btn-ghost"
        style={{ width: 40, height: 40, borderRadius: 'var(--r-pill)', display: 'grid', placeItems: 'center', flex: 'none', color: 'var(--text-muted)' }}
      >
        <CloseIcon size={17} />
      </button>
    </div>
  );
}
