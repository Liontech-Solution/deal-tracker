import { randomBytes } from 'node:crypto';

import { Inject, Injectable, NotFoundException, ServiceUnavailableException } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { eq } from 'drizzle-orm';

import type { EnvConfig } from '../config/configuration';
import { Database, DRIZZLE } from '../database/database.module';
import { appUser } from '../database/schema';
import type { TelegramLinkResult, TelegramSettingsView } from './settings.types';

/** Validez del token de enlace: corta, es de un solo uso y se regenera al pedir otro. */
const LINK_TOKEN_TTL_MS = 15 * 60 * 1000;

@Injectable()
export class SettingsService {
  constructor(
    @Inject(DRIZZLE) private readonly db: Database,
    private readonly config: ConfigService<EnvConfig, true>,
  ) {}

  /** Estado del vínculo de Telegram del usuario. */
  async getTelegramStatus(userId: number): Promise<TelegramSettingsView> {
    const row = await this.load(userId);
    return this.toView(row);
  }

  /**
   * Inicia un enlace: genera un token de un solo uso, lo persiste con caducidad y devuelve el
   * deep-link `t.me/<bot>?start=<token>`. Re-vincular está permitido (sobrescribe el token).
   * Requiere `TELEGRAM_BOT_USERNAME`; sin él no se puede armar el deep-link (503).
   */
  async startTelegramLink(userId: number): Promise<TelegramLinkResult> {
    const botUsername = this.config.get('TELEGRAM_BOT_USERNAME', { infer: true });
    if (!botUsername) {
      throw new ServiceUnavailableException('El vínculo de Telegram no está configurado en el servidor.');
    }

    const token = randomBytes(24).toString('base64url');
    const expiresAt = new Date(Date.now() + LINK_TOKEN_TTL_MS);

    const updated = await this.db
      .update(appUser)
      .set({ telegramLinkToken: token, telegramLinkTokenExpiresAt: expiresAt, updatedAt: new Date() })
      .where(eq(appUser.id, userId))
      .returning({ id: appUser.id });
    if (updated.length === 0) {
      throw new NotFoundException('Usuario no encontrado');
    }

    return {
      deepLink: `https://t.me/${botUsername}?start=${token}`,
      expiresAt: expiresAt.toISOString(),
    };
  }

  /** Desvincula Telegram: limpia chat, token y metadatos. Idempotente (204 aunque no hubiera vínculo). */
  async unlinkTelegram(userId: number): Promise<void> {
    const updated = await this.db
      .update(appUser)
      .set({
        telegramChatId: null,
        telegramUsername: null,
        telegramLinkedAt: null,
        telegramLinkToken: null,
        telegramLinkTokenExpiresAt: null,
        updatedAt: new Date(),
      })
      .where(eq(appUser.id, userId))
      .returning({ id: appUser.id });
    if (updated.length === 0) {
      throw new NotFoundException('Usuario no encontrado');
    }
  }

  private async load(userId: number) {
    const [row] = await this.db
      .select({
        chatId: appUser.telegramChatId,
        username: appUser.telegramUsername,
        linkedAt: appUser.telegramLinkedAt,
        token: appUser.telegramLinkToken,
        tokenExpiresAt: appUser.telegramLinkTokenExpiresAt,
      })
      .from(appUser)
      .where(eq(appUser.id, userId));
    if (!row) {
      throw new NotFoundException('Usuario no encontrado');
    }
    return row;
  }

  private toView(row: Awaited<ReturnType<SettingsService['load']>>): TelegramSettingsView {
    const linked = row.chatId !== null;
    const pendingLink =
      !linked &&
      row.token !== null &&
      row.tokenExpiresAt !== null &&
      row.tokenExpiresAt.getTime() > Date.now();
    return {
      linked,
      telegramUsername: row.username ?? null,
      linkedAt: row.linkedAt ? row.linkedAt.toISOString() : null,
      pendingLink,
    };
  }
}
