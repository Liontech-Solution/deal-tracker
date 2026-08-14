import { describe, expect, it } from 'vitest';

import { obtenerToken } from './refresh';
import type { FuenteDeToken } from './refresh';

/**
 * Doble de la instancia de Keycloak. `fallos` dice cuántos de los primeros `updateToken` fallan;
 * `muereAlFallar` imita lo que hace la librería de verdad cuando el endpoint de token responde 400:
 * `clearToken()`, que deja `authenticated` en `false`.
 */
function fakeKc(opts: {
  authenticated?: boolean;
  token?: string;
  fallos?: number;
  muereAlFallar?: boolean;
}): FuenteDeToken & { llamadas: number[] } {
  const kc = {
    authenticated: opts.authenticated ?? true,
    // `in` y no `??`: el último caso pasa `token: undefined` a propósito y tiene que llegar así.
    token: 'token' in opts ? opts.token : 'token-fresco',
    llamadas: [] as number[],
    async updateToken(minValidity: number): Promise<boolean> {
      kc.llamadas.push(minValidity);
      if (kc.llamadas.length <= (opts.fallos ?? 0)) {
        if (opts.muereAlFallar) {
          kc.authenticated = false;
          kc.token = undefined;
        }
        throw new Error('Failed to refresh token');
      }
      return true;
    },
  };
  return kc;
}

describe('obtenerToken (#262)', () => {
  it('sin instancia no hay sesión: la petición anónima es la correcta', async () => {
    // Es el caso de `/config` durante el arranque de Keycloak, y el del catálogo donde no hay realm.
    expect(await obtenerToken(null)).toEqual({ estado: 'sin-sesion' });
  });

  it('con instancia pero sin sesión tampoco se toca el refresco', async () => {
    const kc = fakeKc({ authenticated: false });
    expect(await obtenerToken(kc)).toEqual({ estado: 'sin-sesion' });
    expect(kc.llamadas).toEqual([]);
  });

  it('el camino normal devuelve el token con un solo refresco', async () => {
    const kc = fakeKc({ token: 'abc' });
    expect(await obtenerToken(kc)).toEqual({ estado: 'token', token: 'abc' });
    expect(kc.llamadas).toEqual([30]);
  });

  it('si el primer refresco falla, el segundo va forzado y salva la petición', async () => {
    const kc = fakeKc({ fallos: 1, token: 'abc' });
    expect(await obtenerToken(kc)).toEqual({ estado: 'token', token: 'abc' });
    // El `-1` no es cosmético: con `30` el segundo intento repetiría la misma comprobación de
    // caducidad y volvería a hacer exactamente lo mismo que acababa de fallar.
    expect(kc.llamadas).toEqual([30, -1]);
  });

  it('dos fallos con la sesión viva son un fallo transitorio, no una sesión caducada', async () => {
    const kc = fakeKc({ fallos: 2 });
    // Este es el caso que producía el 401 de la issue: antes salía `null` y la petición viajaba sin
    // cabecera `Authorization` a un endpoint que exige sesión.
    expect(await obtenerToken(kc)).toEqual({ estado: 'refresco-fallido' });
    expect(kc.llamadas).toEqual([30, -1]);
  });

  it('si la librería limpia el token, la sesión está muerta y no se reintenta más', async () => {
    const kc = fakeKc({ fallos: 2, muereAlFallar: true });
    expect(await obtenerToken(kc)).toEqual({ estado: 'sesion-muerta' });
    expect(kc.llamadas).toEqual([30, -1]);
  });

  it('un refresco que dice que sí pero deja el token vacío no se cuenta como éxito', async () => {
    const kc = fakeKc({ token: undefined });
    expect(await obtenerToken(kc)).toEqual({ estado: 'refresco-fallido' });
  });
});
