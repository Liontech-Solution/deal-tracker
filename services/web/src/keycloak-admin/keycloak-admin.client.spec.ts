import { afterEach, describe, expect, it, vi } from 'vitest';

import { KeycloakAdminClient } from './keycloak-admin.client';

/**
 * Unit del cliente de la Admin API con `fetch` simulado. Cubre la mitad de la tabla de #548 que se
 * puede probar aquí: sin secreto, `invitesEnabled` apagado y ninguna llamada; el 409 distinguible;
 * y el caché del token. **La otra mitad —que el alta cree de verdad el usuario en el realm— solo se
 * observa en QA y en prod**, porque `dev` no trae ninguna `KEYCLOAK_*` por construcción (#23).
 */

const ISSUER = 'https://kc.example/realms/deal-tracker-qa';

/** Config falsa con el issuer, el client de administración y su secreto. */
function fakeConfig(secret: string, issuer = ISSUER, clientId = 'deal-tracker-api') {
  return {
    get: (key: string) => {
      if (key === 'KEYCLOAK_ADMIN_CLIENT_SECRET') return secret;
      if (key === 'KEYCLOAK_ADMIN_CLIENT_ID') return clientId;
      return issuer;
    },
  } as never;
}

/** Una respuesta cualquiera del doble de `fetch`. */
function respuesta(status: number, body: unknown = {}, headers: Record<string, string> = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (h: string) => headers[h.toLowerCase()] ?? null },
    json: () => Promise.resolve(body),
  };
}

const TOKEN_OK = respuesta(200, { access_token: 'tok-1', expires_in: 300 });
const CREADO = respuesta(201, {}, { location: 'https://kc.example/admin/realms/deal-tracker-qa/users/u-42' });
const USUARIO = { email: 'invitada@example.com', firstName: 'Ada', password: 's3creta' };

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('KeycloakAdminClient', () => {
  it('sin secreto: deshabilitado y sin llegar a llamar a Keycloak', async () => {
    const spy = vi.fn();
    vi.stubGlobal('fetch', spy);
    const client = new KeycloakAdminClient(fakeConfig(''));

    expect(client.enabled).toBe(false);
    await expect(client.createUser(USUARIO)).resolves.toEqual({ ok: false, reason: 'disabled' });
    expect(spy).not.toHaveBeenCalled();
  });

  it('sin issuer (el caso de dev): también deshabilitado', async () => {
    const spy = vi.fn();
    vi.stubGlobal('fetch', spy);
    const client = new KeycloakAdminClient(fakeConfig('sh', ''));

    expect(client.enabled).toBe(false);
    expect(spy).not.toHaveBeenCalled();
  });

  it('pide token por client_credentials y crea el usuario con emailVerified', async () => {
    const spy = vi.fn().mockResolvedValueOnce(TOKEN_OK).mockResolvedValueOnce(CREADO);
    vi.stubGlobal('fetch', spy);

    await expect(new KeycloakAdminClient(fakeConfig('sh')).createUser(USUARIO)).resolves.toEqual({
      ok: true,
      userId: 'u-42',
    });

    const [tokenUrl, tokenInit] = spy.mock.calls[0] as [string, { body: string }];
    expect(tokenUrl).toBe('https://kc.example/realms/deal-tracker-qa/protocol/openid-connect/token');
    const form = new URLSearchParams(tokenInit.body);
    expect(form.get('grant_type')).toBe('client_credentials');
    expect(form.get('client_id')).toBe('deal-tracker-api');
    expect(form.get('client_secret')).toBe('sh');

    const [userUrl, userInit] = spy.mock.calls[1] as [string, { headers: Record<string, string>; body: string }];
    expect(userUrl).toBe('https://kc.example/admin/realms/deal-tracker-qa/users');
    expect(userInit.headers.authorization).toBe('Bearer tok-1');
    expect(JSON.parse(userInit.body)).toEqual({
      username: 'invitada@example.com',
      email: 'invitada@example.com',
      firstName: 'Ada',
      enabled: true,
      // La decisión de #548: el token del alta llegó a ese buzón y a ningún otro, así que el correo
      // ya está probado y el alta es un solo correo con nuestra marca.
      emailVerified: true,
      credentials: [{ type: 'password', value: 's3creta', temporary: false }],
    });
  });

  it('cachea el token: dos altas, una sola petición de token', async () => {
    const spy = vi
      .fn()
      .mockResolvedValueOnce(TOKEN_OK)
      .mockResolvedValueOnce(CREADO)
      .mockResolvedValueOnce(CREADO);
    vi.stubGlobal('fetch', spy);
    const client = new KeycloakAdminClient(fakeConfig('sh'));

    await client.createUser(USUARIO);
    await client.createUser(USUARIO);

    expect(spy).toHaveBeenCalledTimes(3);
    const tokenCalls = spy.mock.calls.filter(([url]) => String(url).endsWith('/token'));
    expect(tokenCalls).toHaveLength(1);
  });

  it('no cachea un token sin expires_in: mejor pedir otro que arrastrar uno muerto', async () => {
    const sinVida = respuesta(200, { access_token: 'tok-1' });
    const spy = vi
      .fn()
      .mockResolvedValueOnce(sinVida)
      .mockResolvedValueOnce(CREADO)
      .mockResolvedValueOnce(sinVida)
      .mockResolvedValueOnce(CREADO);
    vi.stubGlobal('fetch', spy);
    const client = new KeycloakAdminClient(fakeConfig('sh'));

    await client.createUser(USUARIO);
    await client.createUser(USUARIO);

    expect(spy.mock.calls.filter(([url]) => String(url).endsWith('/token'))).toHaveLength(2);
  });

  it('el 409 se distingue de un error genérico: ya existe esa cuenta', async () => {
    const spy = vi.fn().mockResolvedValueOnce(TOKEN_OK).mockResolvedValueOnce(respuesta(409));
    vi.stubGlobal('fetch', spy);

    await expect(new KeycloakAdminClient(fakeConfig('sh')).createUser(USUARIO)).resolves.toEqual({
      ok: false,
      reason: 'exists',
    });
  });

  it('otro rechazo de Keycloak es `http`', async () => {
    const spy = vi.fn().mockResolvedValueOnce(TOKEN_OK).mockResolvedValueOnce(respuesta(400));
    vi.stubGlobal('fetch', spy);

    await expect(new KeycloakAdminClient(fakeConfig('sh')).createUser(USUARIO)).resolves.toEqual({
      ok: false,
      reason: 'http',
    });
  });

  it('un 201 sin Location no cuenta como alta: no sabríamos con qué enlazarla', async () => {
    const spy = vi.fn().mockResolvedValueOnce(TOKEN_OK).mockResolvedValueOnce(respuesta(201));
    vi.stubGlobal('fetch', spy);

    await expect(new KeycloakAdminClient(fakeConfig('sh')).createUser(USUARIO)).resolves.toEqual({
      ok: false,
      reason: 'http',
    });
  });

  it('si Keycloak no da token, el alta falla como `auth` y no se intenta crear nada', async () => {
    const spy = vi.fn().mockResolvedValueOnce(respuesta(401, { error: 'invalid_client' }));
    vi.stubGlobal('fetch', spy);

    await expect(new KeycloakAdminClient(fakeConfig('mala')).createUser(USUARIO)).resolves.toEqual({
      ok: false,
      reason: 'auth',
    });
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it('un timeout es `network`, y tampoco lanza', async () => {
    const spy = vi
      .fn()
      .mockResolvedValueOnce(TOKEN_OK)
      .mockRejectedValueOnce(Object.assign(new Error('The operation was aborted'), { name: 'TimeoutError' }));
    vi.stubGlobal('fetch', spy);

    await expect(new KeycloakAdminClient(fakeConfig('sh')).createUser(USUARIO)).resolves.toEqual({
      ok: false,
      reason: 'network',
    });
  });
});
