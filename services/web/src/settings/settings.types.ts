/** Estado del vínculo de Telegram del usuario, tal como lo consume la SPA (Ajustes). */
export interface TelegramSettingsView {
  /** Hay un chat de Telegram vinculado y activo. */
  linked: boolean;
  /** @usuario de Telegram, si se conoce (solo para mostrar). */
  telegramUsername: string | null;
  /** Cuándo se vinculó (ISO), si está vinculado. */
  linkedAt: string | null;
  /** Hay un enlace en curso: token vivo aún sin confirmar por el bot. */
  pendingLink: boolean;
}

/** Resultado de iniciar un enlace: deep-link a abrir en Telegram y caducidad del token. */
export interface TelegramLinkResult {
  /** URL `https://t.me/<bot>?start=<token>` para abrir el bot con el token. */
  deepLink: string;
  /** Caducidad del token (ISO). */
  expiresAt: string;
}
