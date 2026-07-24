/**
 * Configuración desde el entorno, validada al arrancar (fail-fast si falta algo requerido).
 * Sin secretos hardcodeados: todo viene de variables de entorno (ver `.env.example`).
 */

export interface EnvConfig {
  DATABASE_URL: string;
  // Las tres son opcionales: un entorno sin Keycloak (p.ej. `dev`, que no lo necesita) las deja
  // vacías y la auth queda apagada. Vacío = sin auth, no un fallo de arranque.
  KEYCLOAK_ISSUER_URL: string;
  KEYCLOAK_AUDIENCE: string;
  // Client-id público de la SPA que expone `GET /api/config`. Si falta cae en `KEYCLOAK_AUDIENCE`,
  // que en nuestros realms ya vale el propio client-id.
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
 * `true` si el entorno trae Keycloak configurado. Es el interruptor real de la auth: sin issuer
 * no se registra la estrategia JWT, `GET /api/config` publica `null` y la SPA se queda como
 * catálogo público. Permite que un entorno como `dev`, que no se conecta a Keycloak, arranque
 * sin ninguna `KEYCLOAK_*` en su manifiesto.
 */
export function isAuthConfigured(env: Record<string, unknown> = process.env): boolean {
  return ((env.KEYCLOAK_ISSUER_URL as string) ?? '').trim() !== '';
}

/**
 * Validador para `ConfigModule.forRoot({ validate })`. Devuelve la config tipada; Nest la
 * expone luego por `ConfigService`. La auth (Keycloak) es **opcional**: los entornos que no la
 * usan simplemente no definen `KEYCLOAK_*` (ver `isAuthConfigured`).
 */
export function validateEnv(env: Record<string, unknown>): EnvConfig {
  return validate(env);
}

/**
 * Validador para los CLI/jobs (`src/jobs/*`). Misma forma que `EnvConfig`; un CronJob de matching
 * no valida tokens de nadie, así que nunca lleva `KEYCLOAK_*`. Se mantiene como punto de entrada
 * propio para que el contrato de los jobs quede explícito en su call site.
 */
export function validateJobEnv(env: Record<string, unknown>): EnvConfig {
  return validate(env);
}

function validate(env: Record<string, unknown>): EnvConfig {
  const nodeEnv = (env.NODE_ENV as string) ?? 'development';
  if (!['development', 'test', 'production'].includes(nodeEnv)) {
    throw new Error(`NODE_ENV inválido: ${nodeEnv}`);
  }

  // Nunca requeridas: sin ellas la auth queda apagada en ese entorno, en vez de impedir el
  // arranque. El client-id cae en la audiencia, que en nuestros realms ya vale el propio id.
  const issuer = ((env.KEYCLOAK_ISSUER_URL as string) ?? '').trim();
  const audience = ((env.KEYCLOAK_AUDIENCE as string) ?? '').trim();
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
