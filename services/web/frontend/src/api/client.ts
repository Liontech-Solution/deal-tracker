/** Cliente HTTP mínimo contra la API NestJS (prefijo `/api`). En dev, Vite hace proxy. */

const BASE = '/api';

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

/**
 * Proveedor del access token para las peticiones autenticadas. Lo registra el `AuthProvider`
 * (evita acoplar el cliente HTTP al contexto de React/Keycloak). Devuelve `null` si no hay
 * sesión o si la auth está deshabilitada (dev local sin realm) → petición sin `Authorization`.
 */
type TokenGetter = () => Promise<string | null>;
let tokenGetter: TokenGetter = async () => null;

export function setTokenGetter(getter: TokenGetter): void {
  tokenGetter = getter;
}

function buildUrl(path: string, params?: Record<string, unknown>): string {
  const url = new URL(BASE + path, window.location.origin);
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null && value !== '') {
        url.searchParams.set(key, String(value));
      }
    }
  }
  return url.toString();
}

async function parseError(res: Response): Promise<never> {
  let detail = res.statusText;
  try {
    const body = (await res.json()) as { message?: string | string[] };
    if (body?.message) detail = Array.isArray(body.message) ? body.message.join(', ') : body.message;
  } catch {
    /* respuesta sin cuerpo JSON */
  }
  throw new ApiError(res.status, detail);
}

/** GET público (catálogo). No adjunta token. */
export async function apiGet<T>(path: string, params?: Record<string, unknown>): Promise<T> {
  const res = await fetch(buildUrl(path, params), { headers: { Accept: 'application/json' } });
  if (!res.ok) await parseError(res);
  return (await res.json()) as T;
}

async function authHeaders(): Promise<Record<string, string>> {
  const token = await tokenGetter();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** GET autenticado (recursos del usuario, p.ej. `/interests`). Adjunta `Bearer` si hay sesión. */
export async function apiGetAuth<T>(path: string, params?: Record<string, unknown>): Promise<T> {
  const res = await fetch(buildUrl(path, params), {
    headers: { Accept: 'application/json', ...(await authHeaders()) },
  });
  if (!res.ok) await parseError(res);
  return (await res.json()) as T;
}

/** POST/PUT/PATCH/DELETE autenticado. Devuelve `T` o `void` si la respuesta es 204. */
export async function apiSend<T>(
  method: 'POST' | 'PUT' | 'PATCH' | 'DELETE',
  path: string,
  body?: unknown,
): Promise<T> {
  const res = await fetch(buildUrl(path), {
    method,
    headers: {
      Accept: 'application/json',
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      ...(await authHeaders()),
    },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });
  if (!res.ok) await parseError(res);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}
