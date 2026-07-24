/**
 * Configuración desde el entorno, validada al arrancar (fail-fast si falta algo requerido).
 * Sin secretos hardcodeados: todo viene de variables de entorno (ver `.env.example`).
 */

export interface EnvConfig {
  DATABASE_URL: string;
  KEYCLOAK_ISSUER_URL: string;
  KEYCLOAK_AUDIENCE: string;
  // Client-id público de la SPA que expone `GET /api/config`. Opcional: si falta cae en
  // `KEYCLOAK_AUDIENCE`, que en nuestros realms ya vale el propio client-id — así el login se
  // enciende por entorno sin añadir nada a los manifiestos.
  KEYCLOAK_CLIENT_ID: string;
  PORT: number;
  NODE_ENV: 'development' | 'test' | 'production';
  // Usuario del bot de Telegram (sin @), para armar el deep-link t.me/<bot>?start=<token>.
  // Opcional: si falta, la API de vínculo queda deshabilitada (503).
  TELEGRAM_BOT_USERNAME: string;
  // Token del bot (BotFather). Opcional: sin él no se envían avisos (no-op con log) ni se
  // puede hacer long-polling. Se configura a partir de `qa`; en `dev` va vacío a propósito.
  TELEGRAM_BOT_TOKEN: string;
  // Enciende el bucle de long-polling (`getUpdates`) que canjea `/start <token>`. Apagado por
  // defecto: `getUpdates` no admite dos consumidores, así que exige replica 1 del Deployment.
  TELEGRAM_POLLING_ENABLED: boolean;
}

function required(env: Record<string, unknown>, key: keyof EnvConfig): string {
  const value = env[key];
  if (typeof value !== 'string' || value.trim() === '') {
    throw new Error(`Falta la variable de entorno requerida: ${key}`);
  }
  return value.trim();
}

/**
 * Validador para `ConfigModule.forRoot({ validate })`. Devuelve la config tipada; Nest la
 * expone luego por `ConfigService`. Auth (Keycloak) no es requerida en `test` para poder
 * ejercitar catálogo/health sin un realm real.
 */
export function validateEnv(env: Record<string, unknown>): EnvConfig {
  return validate(env, { requireAuth: true });
}

/**
 * Validador para los CLI/jobs (`src/jobs/*`). Misma forma que `EnvConfig` pero **sin exigir
 * `KEYCLOAK_*`**: un CronJob de matching no valida tokens de nadie, y pedirle esa config sería
 * ruido en el manifiesto.
 */
export function validateJobEnv(env: Record<string, unknown>): EnvConfig {
  return validate(env, { requireAuth: false });
}

function validate(env: Record<string, unknown>, opts: { requireAuth: boolean }): EnvConfig {
  const nodeEnv = (env.NODE_ENV as string) ?? 'development';
  if (!['development', 'test', 'production'].includes(nodeEnv)) {
    throw new Error(`NODE_ENV inválido: ${nodeEnv}`);
  }
  // Auth no es requerida en `test` (permite ejercitar catálogo/health sin realm real) ni en los
  // jobs, que no son resource server.
  const skipAuth = nodeEnv === 'test' || !opts.requireAuth;

  const issuer = skipAuth ? ((env.KEYCLOAK_ISSUER_URL as string) ?? '') : required(env, 'KEYCLOAK_ISSUER_URL');
  const audience = skipAuth ? ((env.KEYCLOAK_AUDIENCE as string) ?? '') : required(env, 'KEYCLOAK_AUDIENCE');
  // Nunca requerida: sin ella, el client-id que publica `/api/config` es la audiencia.
  const clientId = ((env.KEYCLOAK_CLIENT_ID as string) ?? '').trim() || audience;

  const portRaw = (env.PORT as string) ?? '3000';
  const port = Number.parseInt(portRaw, 10);
  if (!Number.isInteger(port) || port <= 0) {
    throw new Error(`PORT inválido: ${portRaw}`);
  }

  const telegramBotUsername = ((env.TELEGRAM_BOT_USERNAME as string) ?? '').trim().replace(/^@/, '');
  const telegramBotToken = ((env.TELEGRAM_BOT_TOKEN as string) ?? '').trim();
  const telegramPollingEnabled = ((env.TELEGRAM_POLLING_ENABLED as string) ?? '').trim() === 'true';

  return {
    DATABASE_URL: required(env, 'DATABASE_URL'),
    KEYCLOAK_ISSUER_URL: issuer,
    KEYCLOAK_AUDIENCE: audience,
    KEYCLOAK_CLIENT_ID: clientId,
    PORT: port,
    NODE_ENV: nodeEnv as EnvConfig['NODE_ENV'],
    TELEGRAM_BOT_USERNAME: telegramBotUsername,
    TELEGRAM_BOT_TOKEN: telegramBotToken,
    TELEGRAM_POLLING_ENABLED: telegramPollingEnabled,
  };
}
