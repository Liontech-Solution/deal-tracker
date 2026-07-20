/**
 * Subconjunto mínimo de la Bot API de Telegram que usamos. No modelamos el esquema completo:
 * solo los campos que el bot lee (mensajes de texto para `/start <token>`).
 *
 * Referencia: https://core.telegram.org/bots/api#update
 */

export interface TelegramChat {
  id: number;
  username?: string;
}

export interface TelegramMessage {
  message_id: number;
  chat: TelegramChat;
  text?: string;
  from?: { username?: string };
}

export interface TelegramUpdate {
  update_id: number;
  message?: TelegramMessage;
}

/** Sobre común de todas las respuestas de la Bot API. */
export interface TelegramResponse<T> {
  ok: boolean;
  result?: T;
  description?: string;
}

/** Resultado del canje de un token de vínculo (`/start <token>`). */
export type RedeemResult = 'linked' | 'invalid' | 'expired';
