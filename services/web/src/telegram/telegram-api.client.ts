import { Inject, Injectable, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';

import type { EnvConfig } from '../config/configuration';
import type { TelegramResponse, TelegramUpdate } from './telegram.types';

/**
 * Cliente HTTP de la Bot API de Telegram.
 *
 * Sin dependencia externa: solo usamos dos métodos (`sendMessage`, `getUpdates`) y `fetch` nativo
 * basta. Si falta `TELEGRAM_BOT_TOKEN` el cliente queda deshabilitado y **no lanza**: registra lo
 * que habría enviado y devuelve `false`. Así el job de avisos corre en `dev` sin bot real.
 */
@Injectable()
export class TelegramApiClient {
  private readonly logger = new Logger(TelegramApiClient.name);
  private readonly token: string;

  constructor(@Inject(ConfigService) config: ConfigService<EnvConfig, true>) {
    this.token = config.get('TELEGRAM_BOT_TOKEN', { infer: true });
  }

  /** Hay token configurado: se puede hablar con Telegram de verdad. */
  get enabled(): boolean {
    return this.token !== '';
  }

  /**
   * Envía un mensaje de texto. Devuelve si se entregó. Nunca lanza: un aviso perdido no debe
   * tumbar el job que lo origina.
   */
  async sendMessage(chatId: number, text: string): Promise<boolean> {
    if (!this.enabled) {
      this.logger.warn(`Telegram deshabilitado (sin TELEGRAM_BOT_TOKEN); aviso no enviado a ${chatId}`);
      return false;
    }
    const res = await this.call<TelegramMessageResult>('sendMessage', {
      chat_id: chatId,
      text,
      parse_mode: 'HTML',
      disable_web_page_preview: true,
    });
    return res !== null;
  }

  /**
   * Long-polling: espera hasta `timeoutSec` a que haya novedades a partir de `offset`.
   * Devuelve `[]` ante error o si el cliente está deshabilitado; el bucle decide el backoff.
   */
  async getUpdates(offset: number, timeoutSec: number): Promise<TelegramUpdate[]> {
    if (!this.enabled) {
      return [];
    }
    const res = await this.call<TelegramUpdate[]>(
      'getUpdates',
      // Solo nos interesan los mensajes: reduce el ruido de otros tipos de update.
      { offset, timeout: timeoutSec, allowed_updates: ['message'] },
      // El request debe sobrevivir al long-poll: margen sobre el timeout que pide Telegram.
      (timeoutSec + 10) * 1000,
    );
    return res ?? [];
  }

  /**
   * Llamada cruda a la Bot API. Devuelve `null` ante cualquier fallo (red, HTTP, `ok:false`),
   * dejando traza. El token nunca se registra.
   */
  private async call<T>(method: string, body: unknown, timeoutMs = 15_000): Promise<T | null> {
    try {
      const res = await fetch(`https://api.telegram.org/bot${this.token}/${method}`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(timeoutMs),
      });
      const payload = (await res.json()) as TelegramResponse<T>;
      if (!res.ok || !payload.ok) {
        this.logger.error(`Telegram ${method} falló (${res.status}): ${payload.description ?? 'sin detalle'}`);
        return null;
      }
      return payload.result ?? null;
    } catch (err) {
      this.logger.error(`Telegram ${method} falló: ${err instanceof Error ? err.message : String(err)}`);
      return null;
    }
  }
}

/** `sendMessage` devuelve el mensaje creado; solo nos importa que exista. */
interface TelegramMessageResult {
  message_id: number;
}
