import { Inject, Injectable, Logger } from '@nestjs/common';
import { and, eq, gt, isNotNull, sql } from 'drizzle-orm';

import { Database, DRIZZLE } from '../database/database.module';
import { appUser } from '../database/schema';
import type { RedeemResult } from './telegram.types';

/**
 * Canje del token de vínculo que emite `SettingsService.startTelegramLink`.
 *
 * Contrapartida del deep-link `t.me/<bot>?start=<token>`: el usuario pulsa «Start», Telegram nos
 * manda `/start <token>` y aquí lo cambiamos por el `chat_id` real. El token es de un solo uso;
 * su caducidad la fija `LINK_TOKEN_TTL_MS` en `settings.service.ts` (hoy 60 min).
 *
 * El mismo canje sirve para el `/start <token>` que el usuario teclea a mano en Telegram Web: la
 * SPA enseña el token precisamente para eso (#266), y a este servicio le llega igual.
 */
@Injectable()
export class TelegramLinkService {
  private readonly logger = new Logger(TelegramLinkService.name);

  constructor(@Inject(DRIZZLE) private readonly db: Database) {}

  /**
   * Vincula el chat al usuario dueño del token.
   *
   * El UPDATE condicional resuelve token-inexistente y carrera de doble canje en una sola
   * sentencia: si no toca ninguna fila, el token no servía. Distinguimos `expired` de `invalid`
   * con una segunda consulta, solo para poder dar un mensaje útil en Telegram.
   */
  async redeemStartToken(token: string, chatId: number, username?: string): Promise<RedeemResult> {
    const now = new Date();

    const linked = await this.db
      .update(appUser)
      .set({
        telegramChatId: chatId,
        telegramUsername: username ?? null,
        telegramLinkedAt: now,
        telegramLinkToken: null,
        telegramLinkTokenExpiresAt: null,
        updatedAt: now,
      })
      .where(
        and(
          eq(appUser.telegramLinkToken, token),
          isNotNull(appUser.telegramLinkTokenExpiresAt),
          gt(appUser.telegramLinkTokenExpiresAt, now),
        ),
      )
      .returning({ id: appUser.id });

    if (linked.length > 0) {
      this.logger.log(`Telegram vinculado para el usuario ${linked[0].id}`);
      return 'linked';
    }

    // No se vinculó: ¿el token existe pero caducó, o directamente no existe?
    const [stale] = await this.db
      .select({ id: appUser.id })
      .from(appUser)
      .where(eq(appUser.telegramLinkToken, token));
    return stale ? 'expired' : 'invalid';
  }

  /**
   * Un mismo `chat_id` no puede quedar colgado de dos cuentas: `telegram_chat_id` es UNIQUE, así
   * que antes de vincular se desvincula de quien lo tuviera. Devuelve cuántas filas se limpiaron.
   */
  async releaseChat(chatId: number): Promise<number> {
    const released = await this.db
      .update(appUser)
      .set({
        telegramChatId: null,
        telegramUsername: null,
        telegramLinkedAt: null,
        updatedAt: new Date(),
      })
      .where(eq(appUser.telegramChatId, chatId))
      .returning({ id: appUser.id });
    return released.length;
  }

  /** `chat_id` del usuario, o `null` si no tiene Telegram vinculado. Lo usará el job de avisos. */
  async chatIdForUser(userId: number): Promise<number | null> {
    const [row] = await this.db
      .select({ chatId: appUser.telegramChatId })
      .from(appUser)
      .where(and(eq(appUser.id, userId), sql`${appUser.telegramChatId} IS NOT NULL`));
    return row?.chatId ?? null;
  }
}
