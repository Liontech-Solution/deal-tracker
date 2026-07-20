import { afterEach, describe, expect, it, vi } from 'vitest';

import { TelegramApiClient } from './telegram-api.client';

/**
 * Unit del cliente de la Bot API con `fetch` simulado: sin red y sin token real, para que CI no
 * dependa de Telegram. La regla clave es que **nunca lanza**: un aviso perdido no debe tumbar el
 * job que lo origina.
 */

/** Config falsa que devuelve el token dado para `TELEGRAM_BOT_TOKEN`. */
function fakeConfig(token: string) {
  return { get: () => token } as never;
}

/** Sustituye `fetch` por un stub que devuelve el payload dado. */
function stubFetch(payload: unknown, ok = true) {
  const spy = vi.fn().mockResolvedValue({
    ok,
    status: ok ? 200 : 500,
    json: () => Promise.resolve(payload),
  });
  vi.stubGlobal('fetch', spy);
  return spy;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('TelegramApiClient', () => {
  it('sin token: queda deshabilitado y no llega a llamar a Telegram', async () => {
    const spy = stubFetch({ ok: true, result: { message_id: 1 } });
    const client = new TelegramApiClient(fakeConfig(''));

    expect(client.enabled).toBe(false);
    await expect(client.sendMessage(42, 'hola')).resolves.toBe(false);
    await expect(client.getUpdates(0, 30)).resolves.toEqual([]);
    expect(spy).not.toHaveBeenCalled();
  });

  it('con token: envía el mensaje al endpoint y payload correctos', async () => {
    const spy = stubFetch({ ok: true, result: { message_id: 7 } });
    const client = new TelegramApiClient(fakeConfig('123:ABC'));

    await expect(client.sendMessage(42, 'hola')).resolves.toBe(true);

    const [url, init] = spy.mock.calls[0] as [string, { body: string; method: string }];
    expect(url).toBe('https://api.telegram.org/bot123:ABC/sendMessage');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toMatchObject({ chat_id: 42, text: 'hola', parse_mode: 'HTML' });
  });

  it('devuelve los updates y solo pide mensajes', async () => {
    const updates = [{ update_id: 10, message: { message_id: 1, chat: { id: 5 }, text: '/start t' } }];
    const spy = stubFetch({ ok: true, result: updates });
    const client = new TelegramApiClient(fakeConfig('123:ABC'));

    await expect(client.getUpdates(9, 30)).resolves.toEqual(updates);

    const [, init] = spy.mock.calls[0] as [string, { body: string }];
    expect(JSON.parse(init.body)).toMatchObject({ offset: 9, timeout: 30, allowed_updates: ['message'] });
  });

  it('error de la Bot API (ok:false): no lanza, devuelve false/[]', async () => {
    stubFetch({ ok: false, description: 'chat not found' });
    const client = new TelegramApiClient(fakeConfig('123:ABC'));

    await expect(client.sendMessage(42, 'hola')).resolves.toBe(false);
    await expect(client.getUpdates(0, 30)).resolves.toEqual([]);
  });

  it('fallo de red: no lanza', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('ECONNREFUSED')));
    const client = new TelegramApiClient(fakeConfig('123:ABC'));

    await expect(client.sendMessage(42, 'hola')).resolves.toBe(false);
    await expect(client.getUpdates(0, 30)).resolves.toEqual([]);
  });
});
