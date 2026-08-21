import { Logger } from '@nestjs/common';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { EmailApiClient } from './email-api.client';

/**
 * Unit del cliente de Resend con `fetch` simulado: sin red y sin clave real, para que CI no dependa
 * de Resend. Las dos reglas que se ejercitan aquí son las que separan este cliente del de Telegram:
 * **nunca lanza, pero el fallo es inequívoco** (quien llama tiene que poder devolver el cupo), y
 * **ni la clave ni el destinatario llegan al log**.
 */

/** Config falsa: devuelve la clave y el remitente dados, en el orden en que los pide el cliente. */
function fakeConfig(apiKey: string, from = 'deal-tracker@qa.liontechsolution.com') {
  return {
    get: (key: string) => (key === 'RESEND_API_KEY' ? apiKey : from),
  } as never;
}

/** Sustituye `fetch` por un stub que devuelve el payload dado. */
function stubFetch(payload: unknown, ok = true, status = ok ? 200 : 422) {
  const spy = vi.fn().mockResolvedValue({
    ok,
    status,
    json: () => Promise.resolve(payload),
  });
  vi.stubGlobal('fetch', spy);
  return spy;
}

const MENSAJE = {
  to: 'invitada@example.com',
  subject: 'Te invitan',
  html: '<p>hola</p>',
  text: 'hola',
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('EmailApiClient', () => {
  it('sin clave: queda deshabilitado y no llega a llamar a Resend', async () => {
    const spy = stubFetch({ id: 'no-deberia-usarse' });
    const client = new EmailApiClient(fakeConfig(''));

    expect(client.enabled).toBe(false);
    await expect(client.sendEmail(MENSAJE)).resolves.toEqual({ ok: false, reason: 'disabled' });
    expect(spy).not.toHaveBeenCalled();
  });

  it('con clave pero sin remitente: también deshabilitado (sería un 422 seguro)', async () => {
    const spy = stubFetch({ id: 'no-deberia-usarse' });
    const client = new EmailApiClient(fakeConfig('re_123', ''));

    expect(client.enabled).toBe(false);
    await expect(client.sendEmail(MENSAJE)).resolves.toEqual({ ok: false, reason: 'disabled' });
    expect(spy).not.toHaveBeenCalled();
  });

  it('envía al endpoint, con la clave en la cabecera y el sobre completo', async () => {
    const spy = stubFetch({ id: 'e1b2' });
    const client = new EmailApiClient(fakeConfig('re_123'));

    await expect(client.sendEmail(MENSAJE)).resolves.toEqual({ ok: true, id: 'e1b2' });

    const [url, init] = spy.mock.calls[0] as [string, { method: string; headers: Record<string, string>; body: string }];
    expect(url).toBe('https://api.resend.com/emails');
    expect(init.method).toBe('POST');
    expect(init.headers.authorization).toBe('Bearer re_123');
    expect(JSON.parse(init.body)).toEqual({
      from: 'deal-tracker@qa.liontechsolution.com',
      to: ['invitada@example.com'],
      subject: 'Te invitan',
      html: '<p>hola</p>',
      text: 'hola',
    });
  });

  it('un rechazo de Resend es `http`, no una excepción', async () => {
    stubFetch({ statusCode: 422, name: 'validation_error', message: 'invitada@example.com no vale' }, false);
    const client = new EmailApiClient(fakeConfig('re_123'));

    await expect(client.sendEmail(MENSAJE)).resolves.toEqual({ ok: false, reason: 'http' });
  });

  it('un fallo de red o un timeout es `network`, y tampoco lanza', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockRejectedValue(Object.assign(new Error('The operation was aborted'), { name: 'TimeoutError' })),
    );
    const client = new EmailApiClient(fakeConfig('re_123'));

    await expect(client.sendEmail(MENSAJE)).resolves.toEqual({ ok: false, reason: 'network' });
  });

  it('un 2xx sin id no cuenta como enviado: quien llama tiene que poder devolver el cupo', async () => {
    stubFetch({});
    const client = new EmailApiClient(fakeConfig('re_123'));

    await expect(client.sendEmail(MENSAJE)).resolves.toEqual({ ok: false, reason: 'network' });
  });

  it('no registra ni la clave ni la dirección del destinatario, ni al enviar ni al fallar', async () => {
    const trazas: string[] = [];
    for (const nivel of ['log', 'warn', 'error'] as const) {
      vi.spyOn(Logger.prototype, nivel).mockImplementation((...args: unknown[]) => {
        trazas.push(args.map(String).join(' '));
      });
    }

    stubFetch({ id: 'e1b2' });
    await new EmailApiClient(fakeConfig('re_123')).sendEmail(MENSAJE);
    // El `message` de Resend puede echar de vuelta la dirección: por eso solo se registra `name`.
    stubFetch({ statusCode: 422, name: 'validation_error', message: 'invitada@example.com no vale' }, false);
    await new EmailApiClient(fakeConfig('re_123')).sendEmail(MENSAJE);
    await new EmailApiClient(fakeConfig('')).sendEmail(MENSAJE);

    const todo = trazas.join('\n');
    expect(todo).not.toContain('re_123');
    expect(todo).not.toContain('invitada@example.com');
    // Y sí registra el id, que es lo que basta para rastrear un envío.
    expect(todo).toContain('e1b2');
  });
});
