import { useAddFavorite, useFavoriteIds, useRemoveFavorite } from '../api/hooks';
import { useToast } from '../components/Toast';
import { useAuth } from './AuthProvider';

/**
 * La puerta de entrada a los favoritos: decide si se marca el corazón, se lanza el login o se avisa
 * de que en este entorno no hay Keycloak.
 *
 * **Gemelo de `useSeguirPrenda`, y a propósito.** Ese hook nació de #301 justo porque esta regla no
 * estaba en un solo sitio: la ficha tenía el camino bueno mientras los otros puntos de entrada
 * decían «Inicia sesión» a gente que ya tenía sesión. Escribir aquí un tercer criterio —o repetir
 * las cuatro ramas inline en cada corazón— sería reabrir ese mismo agujero, así que el orden de las
 * ramas es el mismo y por los mismos motivos:
 *
 * 1. Hasta que `/api/config` no resuelve, `enabled` **no es concluyente** (ver `AuthProvider`), así
 *    que no se hace nada: actuar antes mandaría al login a quien sí tiene sesión.
 * 2. Sin realm —dev local, y `dev` por decisión de #23— no hay login que lanzar, así que ese caso se
 *    responde antes de mirar si hay sesión.
 * 3. Sin sesión, al login.
 * 4. Con sesión, se muta.
 *
 * Lo que cambia respecto al gemelo es solo el desenlace: allí se abre un modal, aquí se escribe. Por
 * eso este devuelve además `esFavorito`, que es estado y no acción — el corazón se pinta relleno o
 * vacío antes de que nadie lo pulse.
 *
 * **La rama 3 ya no es alcanzable, y se queda igual.** #435 pedía comprobar que «sin sesión el
 * corazón lleva al login», y desde #309 eso no se puede observar: los tres únicos sitios que pintan
 * corazón exigen sesión donde hay Keycloak —`HomePage.tsx` tras su `conCatalogo` (sin sesión la home
 * pinta `DealsTrasLaPuerta`, que no tiene tarjetas), y `CatalogPage.tsx` y `ProductPage.tsx` tras
 * `RequireSession`—, así que un anónimo no llega a ver ningún corazón que pulsar. Donde no hay realm
 * (`dev`, #23) la que se ejerce es la 2, el toast, no el login. La rama se conserva **por ser el
 * criterio compartido con la campana**, no por uso: borrarla reabriría #301 el día que un corazón
 * vuelva a una superficie pública. Y no está cubierta por test porque no puede estarlo —
 * `vitest.config.ts` solo corre del frontend los helpers puros, sin jsdom ni testing-library.
 *
 * Vive en `auth/` y no en `components/` por lo mismo que su gemelo: lo que hace es leer el estado de
 * la sesión. Y en su propio fichero porque exportar un hook desde un módulo de componentes rompe el
 * fast refresh de Vite (`react-refresh/only-export-components`).
 */
export function useFavorito(productId: number | undefined): {
  esFavorito: boolean;
  alternar: () => void;
  ocupado: boolean;
} {
  const toast = useToast();
  const auth = useAuth();
  // Una sola query para toda la pantalla: comparte `queryKey` con la lista, así que N corazones no
  // son N peticiones.
  const { data: favoritos } = useFavoriteIds(auth.authenticated);
  const add = useAddFavorite();
  const remove = useRemoveFavorite();

  const esFavorito = productId !== undefined && (favoritos?.has(productId) ?? false);

  const alternar = () => {
    // El id puede no estar resuelto todavía (la ficha lo saca de la URL y el producto se está
    // cargando). Los hooks no pueden llamarse condicionalmente, así que la rama vive aquí.
    if (productId === undefined) return;
    if (!auth.ready) return;
    if (!auth.enabled) {
      toast('Inicio de sesión con Keycloak · disponible al desplegar');
      return;
    }
    if (!auth.authenticated) {
      // Sin argumento vuelve a la URL actual, que es lo que se quiere aquí. OJO: `auth.login`
      // acepta destino, así que cablearlo pelado a un `onClick` le colaría el `MouseEvent` como
      // `redirectUri` — ya mordió en `SettingsPage` e `InterestsPage` durante #309.
      auth.login();
      return;
    }
    if (esFavorito) {
      remove.mutate(productId, {
        onError: () => toast('No se pudo quitar de favoritos'),
      });
    } else {
      add.mutate(productId, {
        onError: () => toast('No se pudo guardar en favoritos'),
      });
    }
  };

  return { esFavorito, alternar, ocupado: add.isPending || remove.isPending };
}
