/**
 * Tipos del sobre de la API HTTP de Resend (`POST /emails`). Solo lo que consumimos: no es un
 * espejo del contrato entero, y por eso no hay dependencia del SDK.
 */

/** Lo que se le manda a Resend. `text` viaja siempre junto al `html` (ver `invitation.template.ts`). */
export interface ResendSendRequest {
  from: string;
  to: string[];
  subject: string;
  html: string;
  text: string;
}

/** Respuesta de un envío aceptado. El `id` es lo único que registramos de un correo. */
export interface ResendSendResponse {
  id?: string;
}

/**
 * Error de Resend. `message` puede devolver la dirección del destinatario, así que **no se
 * registra**: al log solo van el `statusCode` y el `name`.
 */
export interface ResendErrorResponse {
  statusCode?: number;
  name?: string;
  message?: string;
}

/**
 * - `disabled`: no hay `RESEND_API_KEY` en este entorno (es la rama que corre en `dev` siempre).
 * - `http`: Resend contestó, y dijo que no.
 * - `network`: no hubo respuesta (red caída, timeout), o la respuesta no trae `id`.
 */
export type EmailFailure = 'disabled' | 'http' | 'network';

/**
 * Resultado de un envío. A diferencia de Telegram —que devuelve `boolean` porque un aviso perdido
 * no debe tumbar su job—, aquí el fallo tiene que ser **inequívoco y accionable**: un correo de
 * invitación perdido deja al usuario sin poder darse de alta y con el cupo ya descontado, así que
 * quien llama necesita poder devolver ese cupo (#547).
 */
export type EmailSendResult = { ok: true; id: string } | { ok: false; reason: EmailFailure };

/** Lo que pide `EmailApiClient.sendEmail()`. El cliente no sabe de invitaciones: solo de correos. */
export interface EmailMessage {
  to: string;
  subject: string;
  html: string;
  text: string;
}
