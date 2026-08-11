import { createContext, useContext, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';

import { setTokenGetter } from '../api/client';
import { bootstrapAuth, getFreshToken, getKeycloak } from './keycloak';

export interface AuthUser {
  name: string | null;
  email: string | null;
}

export interface AuthContextValue {
  /**
   * `false` en dev local sin realm: la UI ofrece placeholder en vez de login real. Solo es
   * concluyente cuando `ready` es `true` — antes aún no se sabe si hay auth.
   */
  enabled: boolean;
  /** `true` cuando se ha resuelto `/api/config` y Keycloak ha comprobado la sesión. */
  ready: boolean;
  authenticated: boolean;
  user: AuthUser | null;
  /**
   * Arranca el login. Sin argumento vuelve a la URL actual, que es lo que quiere casi todo el
   * mundo; con `redirectTo` (URL absoluta) vuelve a otro sitio — lo usa la página de acceso de
   * #309, a la que se llega redirigido desde el destino real y por tanto no puede usar la suya.
   */
  login: (redirectTo?: string) => void;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  // La config llega por red (`/api/config`), así que nada se sabe de forma síncrona: se arranca
  // "no listo" y sin auth, y `bootstrapAuth()` fija el estado real.
  const [enabled, setEnabled] = useState(false);
  const [ready, setReady] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [user, setUser] = useState<AuthUser | null>(null);

  // Registra el proveedor de token para el cliente HTTP (una vez).
  useEffect(() => {
    setTokenGetter(getFreshToken);
  }, []);

  useEffect(() => {
    let cancelled = false;
    void bootstrapAuth().then(({ enabled: on, authenticated: authed }) => {
      if (cancelled) return;
      const kc = getKeycloak();
      setEnabled(on);
      setAuthenticated(authed);
      const claims = kc?.tokenParsed as
        | { name?: string; preferred_username?: string; email?: string }
        | undefined;
      setUser(
        authed
          ? { name: claims?.name ?? claims?.preferred_username ?? null, email: claims?.email ?? null }
          : null,
      );
      setReady(true);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      enabled,
      ready,
      authenticated,
      user,
      login: (redirectTo?: string) =>
        getKeycloak()?.login({ redirectUri: redirectTo ?? window.location.href }),
      logout: () => getKeycloak()?.logout({ redirectUri: window.location.origin }),
    }),
    [enabled, ready, authenticated, user],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth debe usarse dentro de <AuthProvider>');
  return ctx;
}
