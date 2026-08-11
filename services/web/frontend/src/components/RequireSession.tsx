import { Navigate, useLocation } from 'react-router-dom';
import type { ReactNode } from 'react';

import { useAuth } from '../auth/AuthProvider';

/**
 * Candado de las rutas que enseñan catálogo (#309). Va **en la ruta y no en los hooks** por dos
 * motivos: no pisar `useProducts`, que reescribe #292, y porque envolver la página impide que sus
 * hooks lleguen a dispararse — si dispararan antes de que `AuthProvider` resuelva la sesión, el
 * token todavía sería `null` y el catálogo respondería 401 en la primera carga.
 *
 * De ahí que el orden de las ramas importe y que `!ready` no sea un detalle cosmético.
 */
export function RequireSession({ children }: { children: ReactNode }) {
  const auth = useAuth();
  const location = useLocation();

  // Aún no se sabe si hay auth ni si hay sesión: `/api/config` viaja por red.
  if (!auth.ready) {
    return <section style={{ padding: '60px 0', display: 'grid', placeItems: 'center' }}>Cargando…</section>;
  }

  // Entorno sin Keycloak (así corre `dev`, que borra las `KEYCLOAK_*` a propósito, #23): el
  // catálogo sigue siendo público. El candado solo se ejerce de verdad donde hay realm.
  if (!auth.enabled) {
    return <>{children}</>;
  }

  if (!auth.authenticated) {
    // El destino viaja en el `state` para que la página de acceso pueda devolver ahí tras el
    // login: quien abre un enlace compartido a una ficha tiene que volver a esa ficha.
    return <Navigate to="/acceso" state={{ from: location.pathname + location.search }} replace />;
  }

  return <>{children}</>;
}
