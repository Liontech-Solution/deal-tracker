import { ServiceUnavailableException } from '@nestjs/common';
import { describe, expect, it, vi } from 'vitest';

import { SettingsService } from './settings.service';

/**
 * Unit de la lógica dependiente de configuración (deep-link / bot no configurado), con `db` y
 * `ConfigService` simulados. El flujo con BD real se cubre en `test/settings.telegram.e2e.spec.ts`.
 */

/** Config falsa que devuelve un `TELEGRAM_BOT_USERNAME` fijo. */
function fakeConfig(botUsername: string) {
  return { get: () => botUsername } as never;
}

/** `db.update(...).set(...).where(...).returning()` -> filas dadas; captura el `set`. */
function fakeUpdateDb(rows: Array<{ id: number }>) {
  const setSpy = vi.fn();
  const db = {
    update: () => ({
      set: (values: Record<string, unknown>) => {
        setSpy(values);
        return { where: () => ({ returning: () => Promise.resolve(rows) }) };
      },
    }),
  } as never;
  return { db, setSpy };
}

describe('SettingsService.startTelegramLink', () => {
  it('devuelve el deep-link t.me/<bot>?start=<token> y persiste el token', async () => {
    const { db, setSpy } = fakeUpdateDb([{ id: 7 }]);
    const service = new SettingsService(db, fakeConfig('mi_bot'));

    const res = await service.startTelegramLink(7);

    expect(res.deepLink).toMatch(/^https:\/\/t\.me\/mi_bot\?start=.+/);
    expect(() => new Date(res.expiresAt).toISOString()).not.toThrow();

    // el token del deep-link es el que se guardó en app_user
    const token = new URL(res.deepLink).searchParams.get('start');
    expect(token).toBeTruthy();
    const saved = setSpy.mock.calls[0][0] as { telegramLinkToken: string };
    expect(saved.telegramLinkToken).toBe(token);
  });

  it('devuelve el token y el bot sueltos, coherentes con el deep-link (#266)', async () => {
    const { db } = fakeUpdateDb([{ id: 7 }]);
    const service = new SettingsService(db, fakeConfig('mi_bot'));

    const res = await service.startTelegramLink(7);

    // La SPA los usa por separado: el token para enseñarlo copiable y el bot para Telegram Web.
    // Si dejaran de cuadrar con el deep-link, el QR y el `/start` pegado a mano llevarían a
    // sitios distintos y solo se notaría con un humano delante.
    expect(res.botUsername).toBe('mi_bot');
    expect(res.token).toBe(new URL(res.deepLink).searchParams.get('start'));
  });

  it('el token caduca dentro de la ventana de una hora (#266)', async () => {
    const { db } = fakeUpdateDb([{ id: 7 }]);
    const service = new SettingsService(db, fakeConfig('mi_bot'));

    const antes = Date.now();
    const res = await service.startTelegramLink(7);
    const despues = Date.now();
    const expira = new Date(res.expiresAt).getTime();

    // Era de 15 min y se subió a 60: con 15 no daba tiempo a ir a por el móvil, y eso caducó dos
    // tokens seguidos durante la validación de v0.1.9.
    //
    // Cada extremo se mide contra el reloj que le toca, y no los dos contra `antes` (#322): el
    // servicio calcula la caducidad con su propio `Date.now()`, que corre entre las dos marcas, así
    // que `expira - antes` es siempre ≥ 60 min y el techo solo pasaba si la llamada duraba menos de
    // un milisegundo. Por construcción `expira` cae en [antes + TTL, despues + TTL].
    expect(expira - despues).toBeLessThanOrEqual(60 * 60_000);
    expect(expira - antes).toBeGreaterThan(55 * 60_000);
  });

  it('lanza 503 si el bot de Telegram no está configurado', async () => {
    const { db } = fakeUpdateDb([{ id: 1 }]);
    const service = new SettingsService(db, fakeConfig(''));

    await expect(service.startTelegramLink(1)).rejects.toBeInstanceOf(ServiceUnavailableException);
  });
});
