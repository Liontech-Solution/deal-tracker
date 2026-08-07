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

/**
 * Resultado de iniciar un enlace. `token` y `botUsername` van sueltos además de dentro de
 * `deepLink` porque la SPA los necesita por separado (#266): el token para enseñarlo copiable
 * —única vía si el usuario está en Telegram Web o en la app de escritorio— y el usuario del bot
 * para armar el enlace a Telegram Web. No exponen nada nuevo: el token ya viajaba dentro de la URL.
 */
export interface TelegramLinkResult {
  /** URL `https://t.me/<bot>?start=<token>` para abrir el bot con el token. */
  deepLink: string;
  /** El token de un solo uso, para enseñarlo y que el usuario pueda pegar `/start <token>`. */
  token: string;
  /** Usuario del bot, sin `@`. */
  botUsername: string;
  /** Caducidad del token (ISO). */
  expiresAt: string;
}
