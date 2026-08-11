import { useState } from 'react';

import { useAuth } from './AuthProvider';
import { useToast } from '../components/Toast';

/**
 * La puerta de entrada al seguimiento: decide si se abre el modal, se lanza el login o se avisa de
 * que en este entorno no hay Keycloak.
 *
 * **Existe porque #301 nació justo de que esta regla no estuviera en un solo sitio.** La ficha tenía
 * el camino bueno —y validado, U33-U37— mientras los otros dos puntos de entrada se quedaron con un
 * `toast` que decía «Inicia sesión para seguir prendas» a gente que ya tenía sesión. Con la decisión
 * aquí, el punto de entrada que se añada mañana no puede volver a prometer una cosa distinta.
 *
 * Vive en `auth/` y no en `components/` porque lo que hace es leer el estado de la sesión: el modal
 * es solo su consecuencia. Y en su propio fichero porque exportar un hook desde un módulo de
 * componentes rompe el fast refresh de Vite (`react-refresh/only-export-components`).
 *
 * El orden de las cuatro ramas importa:
 *
 * 1. Hasta que `/api/config` no resuelve, `enabled` **no es concluyente** (ver `AuthProvider`), así
 *    que no se hace nada: actuar antes mandaría al login a quien sí tiene sesión.
 * 2. Sin realm —dev local, y `dev` por decisión de #23— no hay login que lanzar, así que ese caso se
 *    responde antes de mirar si hay sesión.
 * 3. Sin sesión, al login.
 * 4. Con sesión, se abre el modal.
 */
export function useSeguirPrenda(): { abierto: boolean; abrir: () => void; cerrar: () => void } {
  const toast = useToast();
  const auth = useAuth();
  const [abierto, setAbierto] = useState(false);

  const abrir = () => {
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
    setAbierto(true);
  };

  return { abierto, abrir, cerrar: () => setAbierto(false) };
}
