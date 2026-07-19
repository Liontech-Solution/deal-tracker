import { createContext, useContext, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';

import { setTokenGetter } from '../api/client';
import { authEnabled, getFreshToken, initKeycloak, keycloak } from './keycloak';

export interface AuthUser {
  name: string | null;
  email: string | null;
}

export interface AuthContextValue {
  /** `false` en dev local sin realm: la UI ofrece placeholder en vez de login real. */
  enabled: boolean;
  /** `true` cuando Keycloak ha terminado de comprobar la sesión (o auth deshabilitada). */
  ready: boolean;
  authenticated: boolean;
  user: AuthUser | null;
  login: () => void;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(!authEnabled);
  const [authenticated, setAuthenticated] = useState(false);
  const [user, setUser] = useState<AuthUser | null>(null);

  // Registra el proveedor de token para el cliente HTTP (una vez).
  useEffect(() => {
    setTokenGetter(getFreshToken);
  }, []);

  useEffect(() => {
    const kc = keycloak;
    if (!authEnabled || !kc) return;
    let cancelled = false;
    void initKeycloak().then((ok) => {
      if (cancelled) return;
      setAuthenticated(Boolean(ok && kc.authenticated));
      const claims = kc.tokenParsed as
        | { name?: string; preferred_username?: string; email?: string }
        | undefined;
      setUser(
        kc.authenticated
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
      enabled: authEnabled,
      ready,
      authenticated,
      user,
      login: () => keycloak?.login({ redirectUri: window.location.href }),
      logout: () => keycloak?.logout({ redirectUri: window.location.origin }),
    }),
    [ready, authenticated, user],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth debe usarse dentro de <AuthProvider>');
  return ctx;
}
