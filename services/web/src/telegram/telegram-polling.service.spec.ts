import { describe, expect, it, vi } from 'vitest';

import { TelegramApiClient } from './telegram-api.client';
import { TelegramLinkService } from './telegram-link.service';
import { TelegramPollingService } from './telegram-polling.service';
import type { RedeemResult } from './telegram.types';

/**
 * Unit del bucle del bot. No toca red ni BD: el cliente y el servicio de vínculo son dobles.
 * Lo que más importa aquí es el gating — en `dev` el bot debe quedarse quieto.
 */

function fakeConfig(pollingEnabled: boolean) {
  return { get: () => pollingEnabled } as never;
}

/** Doble del cliente: cuenta llamadas y captura los mensajes enviados. */
function fakeApi(enabled: boolean, updates: unknown[] = []) {
  const sent: Array<{ chatId: number; text: string }> = [];
  const getUpdates = vi.fn().mockResolvedValue(updates);
  const api = {
    enabled,
    getUpdates,
    sendMessage: (chatId: number, text: string) => {
      sent.push({ chatId, text });
      return Promise.resolve(true);
    },
  } as unknown as TelegramApiClient;
  return { api, sent, getUpdates };
}

/** Doble del servicio de vínculo con el veredicto fijado. */
function fakeLinks(result: RedeemResult) {
  const redeem = vi.fn().mockResolvedValue(result);
  const release = vi.fn().mockResolvedValue(0);
  const links = { redeemStartToken: redeem, releaseChat: release } as unknown as TelegramLinkService;
  return { links, redeem, release };
}

/** Procesa un texto entrante como si llegara de Telegram, sin arrancar el bucle. */
async function handleText(service: TelegramPollingService, text: string, chatId = 55) {
  const update = { update_id: 1, message: { message_id: 1, chat: { id: chatId, username: 'papa' }, text } };
  // `handle` es privado: el bucle es la única vía pública y no queremos arrancarlo en un test.
  await (service as unknown as { handle: (u: unknown) => Promise<void> }).handle(update);
}

describe('TelegramPollingService · gating', () => {
  it('no arranca si TELEGRAM_POLLING_ENABLED no está activo', async () => {
    const { api, getUpdates } = fakeApi(true);
    const service = new TelegramPollingService(fakeConfig(false), api, fakeLinks('linked').links);

    service.onModuleInit();
    await service.onModuleDestroy();

    expect(getUpdates).not.toHaveBeenCalled();
  });

  it('no arranca si falta el token aunque el polling esté activo', async () => {
    const { api, getUpdates } = fakeApi(false);
    const service = new TelegramPollingService(fakeConfig(true), api, fakeLinks('linked').links);

    service.onModuleInit();
    await service.onModuleDestroy();

    expect(getUpdates).not.toHaveBeenCalled();
  });
});

describe('TelegramPollingService · bucle', () => {
  it('procesa el update, avanza el offset y se detiene limpio al apagar', async () => {
    const update = {
      update_id: 41,
      message: { message_id: 1, chat: { id: 55, username: 'papa' }, text: '/start tok-abc' },
    };
    const sent: Array<{ chatId: number; text: string }> = [];
    // Primera vuelta: un update. Siguientes: la promesa queda colgada, como un long-poll real
    // esperando novedades, hasta que la soltamos a mano al apagar.
    let releasePoll: () => void = () => {};
    const getUpdates = vi
      .fn()
      .mockResolvedValueOnce([update])
      .mockImplementation(
        () =>
          new Promise((resolve) => {
            releasePoll = () => resolve([]);
          }),
      );
    const api = {
      enabled: true,
      getUpdates,
      sendMessage: (chatId: number, text: string) => {
        sent.push({ chatId, text });
        return Promise.resolve(true);
      },
    } as unknown as TelegramApiClient;
    const { links, redeem } = fakeLinks('linked');
    const service = new TelegramPollingService(fakeConfig(true), api, links);

    service.onModuleInit();
    await vi.waitFor(() => expect(redeem).toHaveBeenCalledWith('tok-abc', 55, 'papa'));

    expect(getUpdates.mock.calls[0][0]).toBe(0);
    // El offset avanzó a update_id + 1: ese update ya no se reprocesa.
    await vi.waitFor(() => expect(getUpdates.mock.calls[1][0]).toBe(42));
    expect(sent[0].text).toContain('¡Listo!');

    const destroy = service.onModuleDestroy();
    releasePoll(); // el getUpdates en vuelo termina, como al vencer su timeout
    await expect(destroy).resolves.toBeUndefined();
  });
});

describe('TelegramPollingService · /start', () => {
  it('canjea el token, soltando antes el chat de cualquier otra cuenta', async () => {
    const { api, sent } = fakeApi(true);
    const { links, redeem, release } = fakeLinks('linked');
    const service = new TelegramPollingService(fakeConfig(false), api, links);

    await handleText(service, '/start tok-abc');

    expect(release).toHaveBeenCalledWith(55);
    expect(redeem).toHaveBeenCalledWith('tok-abc', 55, 'papa');
    expect(sent[0].text).toContain('¡Listo!');
  });

  it('acepta el comando con sufijo de bot (/start@mi_bot tok)', async () => {
    const { api } = fakeApi(true);
    const { links, redeem } = fakeLinks('linked');
    const service = new TelegramPollingService(fakeConfig(false), api, links);

    await handleText(service, '/start@mi_bot tok-abc');

    expect(redeem).toHaveBeenCalledWith('tok-abc', 55, 'papa');
  });

  it('token caducado: avisa de que genere otro y no confirma vínculo', async () => {
    const { api, sent } = fakeApi(true);
    const service = new TelegramPollingService(fakeConfig(false), api, fakeLinks('expired').links);

    await handleText(service, '/start tok-viejo');

    expect(sent[0].text).toContain('caducado');
  });

  it('mensaje sin token: responde con la ayuda y no intenta canjear', async () => {
    const { api, sent } = fakeApi(true);
    const { links, redeem } = fakeLinks('linked');
    const service = new TelegramPollingService(fakeConfig(false), api, links);

    await handleText(service, 'hola');

    expect(redeem).not.toHaveBeenCalled();
    expect(sent[0].text).toContain('Ajustes');
  });
});
