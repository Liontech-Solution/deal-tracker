import { Inject, Injectable, Logger, OnModuleDestroy, OnModuleInit } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';

import type { EnvConfig } from '../config/configuration';
import { TelegramApiClient } from './telegram-api.client';
import { TelegramLinkService } from './telegram-link.service';
import type { TelegramUpdate } from './telegram.types';

/** Segundos que Telegram mantiene abierta cada `getUpdates` sin novedades. */
const POLL_TIMEOUT_SEC = 30;
/** Espera tras un fallo de red, para no martillear la Bot API. */
const ERROR_BACKOFF_MS = 5_000;

/**
 * Bucle de long-polling que atiende `/start <token>` y cierra el vínculo de Telegram.
 *
 * **Apagado por defecto.** Arranca solo con `TELEGRAM_BOT_TOKEN` presente y
 * `TELEGRAM_POLLING_ENABLED=true`: en `dev` el bot queda planteado pero inerte, y se enciende a
 * partir de `qa`. La doble condición es deliberada — con token pero sin bucle se pueden enviar
 * avisos sin consumir `getUpdates`.
 *
 * ⚠️ `getUpdates` no admite dos consumidores a la vez: con el bucle encendido, el Deployment `web`
 * debe quedarse en **replica 1**.
 */
@Injectable()
export class TelegramPollingService implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(TelegramPollingService.name);
  private stopped = false;
  private offset = 0;
  /** Handle del bucle, para poder esperarlo al apagar. `null` si nunca arrancó. */
  private loop: Promise<void> | null = null;

  constructor(
    @Inject(ConfigService) private readonly config: ConfigService<EnvConfig, true>,
    private readonly api: TelegramApiClient,
    private readonly links: TelegramLinkService,
  ) {}

  onModuleInit(): void {
    if (!this.config.get('TELEGRAM_POLLING_ENABLED', { infer: true })) {
      this.logger.log('Bot de Telegram apagado (TELEGRAM_POLLING_ENABLED != true)');
      return;
    }
    if (!this.api.enabled) {
      this.logger.warn('Bot de Telegram apagado: falta TELEGRAM_BOT_TOKEN');
      return;
    }
    this.logger.log('Bot de Telegram: iniciando long-polling');
    // Deliberadamente sin `await`: el bucle vive todo el proceso, no puede bloquear el arranque.
    this.loop = this.run();
  }

  async onModuleDestroy(): Promise<void> {
    this.stopped = true;
    // El `getUpdates` en vuelo termina solo al vencer su timeout; esperarlo evita logs de
    // conexiones cortadas durante el apagado.
    await this.loop;
  }

  /** Bucle principal. Nunca lanza: un fallo se registra y se reintenta tras el backoff. */
  private async run(): Promise<void> {
    while (!this.stopped) {
      try {
        const updates = await this.api.getUpdates(this.offset, POLL_TIMEOUT_SEC);
        for (const update of updates) {
          // Avanzar el offset aunque el update falle: reprocesarlo daría el mismo error en bucle.
          this.offset = update.update_id + 1;
          await this.handle(update);
        }
      } catch (err) {
        this.logger.error(`Bucle de Telegram: ${err instanceof Error ? err.message : String(err)}`);
        await sleep(ERROR_BACKOFF_MS);
      }
    }
    this.logger.log('Bot de Telegram: long-polling detenido');
  }

  /** Único comando que entendemos: `/start <token>`. Todo lo demás recibe una ayuda breve. */
  private async handle(update: TelegramUpdate): Promise<void> {
    const message = update.message;
    const text = message?.text?.trim();
    if (!message || !text) {
      return;
    }

    const token = /^\/start(?:@\w+)?\s+(\S+)$/.exec(text)?.[1];
    if (!token) {
      await this.api.sendMessage(
        message.chat.id,
        'Para recibir avisos de bajadas de precio, entra en <b>Ajustes</b> en la web y pulsa «Vincular Telegram».',
      );
      return;
    }

    // `telegram_chat_id` es UNIQUE: si este chat estaba vinculado a otra cuenta hay que soltarlo
    // antes, o el canje chocaría contra la restricción.
    await this.links.releaseChat(message.chat.id);

    const username = message.chat.username ?? message.from?.username;
    const result = await this.links.redeemStartToken(token, message.chat.id, username);
    await this.api.sendMessage(message.chat.id, REPLIES[result]);
  }
}

const REPLIES = {
  linked: '✅ ¡Listo! Te avisaré por aquí cuando bajen de precio las prendas que sigues.',
  expired: '⏳ Ese enlace ha caducado. Vuelve a <b>Ajustes</b> en la web y genera uno nuevo.',
  invalid: '❌ Ese enlace no es válido. Genera uno nuevo desde <b>Ajustes</b> en la web.',
} as const;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
