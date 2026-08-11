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
      // Los ejes multiseleccionables (#329) viajan como parámetro REPETIDO. Con `set`, que es lo
      // que hacía esto cuando todos eran de un valor, solo habría llegado el último de la lista y
      // el filtro habría mentido sin dar ningún error. Una lista vacía no escribe nada, que es lo
      // mismo que no filtrar por ese eje.
      if (Array.isArray(value)) {
        for (const v of value) {
          if (v !== undefined && v !== null && v !== '') url.searchParams.append(key, String(v));
        }
        continue;
      }
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

async function authHeaders(): Promise<Record<string, string>> {
  const token = await tokenGetter();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/**
 * GET que adjunta el token **si lo hay**. Antes era el GET público del catálogo y nunca lo
 * adjuntaba; desde #309 el catálogo pide sesión, así que sus cuatro hooks tienen que ir firmados.
 *
 * Sigue tolerando la ausencia de token porque su otro llamante es `/config`, que se pide durante
 * el arranque de Keycloak — ahí todavía no hay instancia y `getFreshToken()` devuelve `null`, o
 * sea que esa petición sale igual que siempre. El endpoint es público y tiene que seguirlo: sin
 * él el navegador no sabe ni contra qué realm autenticarse.
 *
 * El cambio vive aquí y no en los hooks a propósito, para no tocar `hooks.ts` (#292).
 */
export async function apiGet<T>(path: string, params?: Record<string, unknown>): Promise<T> {
  const res = await fetch(buildUrl(path, params), {
    headers: { Accept: 'application/json', ...(await authHeaders()) },
  });
  if (!res.ok) await parseError(res);
  return (await res.json()) as T;
}

/**
 * GET de un recurso **del usuario** (`/interests`, `/settings/*`). Desde #309 hace exactamente lo
 * mismo que `apiGet`, y se queda como alias por lo que declara en el call site: estos endpoints
 * responden 401 sin token siempre, mientras que los de `apiGet` solo lo hacen si el entorno trae
 * Keycloak.
 */
export async function apiGetAuth<T>(path: string, params?: Record<string, unknown>): Promise<T> {
  return apiGet<T>(path, params);
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
