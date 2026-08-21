import { Inject, Injectable, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';

import type { EnvConfig } from '../config/configuration';
import type {
  EmailMessage,
  EmailSendResult,
  ResendErrorResponse,
  ResendSendRequest,
  ResendSendResponse,
} from './email.types';

/** Único endpoint que usamos de Resend. */
const RESEND_ENDPOINT = 'https://api.resend.com/emails';

/**
 * Cliente HTTP de la API de correo de Resend.
 *
 * Sin dependencia externa, por la misma razón que el de Telegram: usamos **un** endpoint y `fetch`
 * nativo basta; el SDK no paga su peso. Si falta `RESEND_API_KEY` el cliente queda deshabilitado y
 * **no revienta el arranque** — esa es la rama que corre en `dev` siempre, y en qa/prod hasta que
 * la clave esté sellada (llega al pod con `optional: true`).
 *
 * **Genérico a propósito**: no sabe de invitaciones. El cuerpo del correo lo arma quien llama (ver
 * `invitation.template.ts`), para que el segundo correo que haya reutilice esto tal cual.
 *
 * Y una diferencia con Telegram que es deliberada: `TelegramApiClient.sendMessage()` devuelve
 * `boolean` y se traga el fallo, porque un aviso perdido no debe tumbar el job que lo origina. Aquí
 * no vale — un correo de invitación perdido deja al usuario sin alta y **con el cupo ya
 * descontado**, así que el resultado distingue el fallo del éxito para que quien llama pueda
 * deshacer ese descuento (#547).
 */
@Injectable()
export class EmailApiClient {
  private readonly logger = new Logger(EmailApiClient.name);
  private readonly apiKey: string;
  private readonly from: string;

  constructor(@Inject(ConfigService) config: ConfigService<EnvConfig, true>) {
    this.apiKey = config.get('RESEND_API_KEY', { infer: true });
    this.from = config.get('INVITE_FROM_EMAIL', { infer: true });
  }

  /**
   * Hay clave y remitente: se puede mandar correo de verdad. Hacen falta los dos — con clave pero
   * sin `INVITE_FROM_EMAIL` cada envío moriría en un 422 de Resend, y ese fallo es de configuración,
   * no de red: mejor no salir a la red.
   */
  get enabled(): boolean {
    return this.apiKey !== '' && this.from !== '';
  }

  /**
   * Envía un correo. Nunca lanza: el fallo viaja en el resultado.
   *
   * **Qué se registra, que es una decisión y no un descuido** (#547): el `id` que devuelve Resend
   * basta para rastrear un envío, así que **la dirección del destinatario nunca va al log** — es un
   * dato personal en un fichero cuya retención no ha decidido nadie. Del error solo se registran el
   * `status` y el `name`, porque el `message` de Resend puede echar de vuelta esa misma dirección.
   * La clave, como en Telegram, no se registra jamás.
   */
  async sendEmail(message: EmailMessage): Promise<EmailSendResult> {
    if (!this.enabled) {
      this.logger.warn('Correo deshabilitado (sin RESEND_API_KEY / INVITE_FROM_EMAIL); envío omitido');
      return { ok: false, reason: 'disabled' };
    }

    const body: ResendSendRequest = {
      from: this.from,
      to: [message.to],
      subject: message.subject,
      html: message.html,
      text: message.text,
    };

    try {
      const res = await fetch(RESEND_ENDPOINT, {
        method: 'POST',
        headers: {
          authorization: `Bearer ${this.apiKey}`,
          'content-type': 'application/json',
        },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(15_000),
      });
      const payload = (await res.json()) as ResendSendResponse & ResendErrorResponse;
      if (!res.ok) {
        this.logger.error(`Resend rechazó el envío (${res.status}): ${payload.name ?? 'sin detalle'}`);
        return { ok: false, reason: 'http' };
      }
      if (!payload.id) {
        // 2xx sin `id`: no podemos afirmar que se haya aceptado, y quien llama tiene que poder
        // devolver el cupo. Se trata como fallo, no como éxito mudo.
        this.logger.error(`Resend aceptó el envío (${res.status}) pero no devolvió id`);
        return { ok: false, reason: 'network' };
      }
      this.logger.log(`Correo enviado (id ${payload.id})`);
      return { ok: true, id: payload.id };
    } catch (err) {
      this.logger.error(`Envío de correo falló: ${err instanceof Error ? err.message : String(err)}`);
      return { ok: false, reason: 'network' };
    }
  }
}
