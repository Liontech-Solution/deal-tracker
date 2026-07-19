/**
 * Configuración desde el entorno, validada al arrancar (fail-fast si falta algo requerido).
 * Sin secretos hardcodeados: todo viene de variables de entorno (ver `.env.example`).
 */

export interface EnvConfig {
  DATABASE_URL: string;
  KEYCLOAK_ISSUER_URL: string;
  KEYCLOAK_AUDIENCE: string;
  PORT: number;
  NODE_ENV: 'development' | 'test' | 'production';
  // Usuario del bot de Telegram (sin @), para armar el deep-link t.me/<bot>?start=<token>.
  // Opcional: si falta, la API de vínculo queda deshabilitada (503). El token del bot llega
  // con el propio bot (PR2b-2), aquí solo hace falta el nombre para el deep-link.
  TELEGRAM_BOT_USERNAME: string;
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
  const nodeEnv = (env.NODE_ENV as string) ?? 'development';
  if (!['development', 'test', 'production'].includes(nodeEnv)) {
    throw new Error(`NODE_ENV inválido: ${nodeEnv}`);
  }
  const isTest = nodeEnv === 'test';

  const issuer = isTest ? ((env.KEYCLOAK_ISSUER_URL as string) ?? '') : required(env, 'KEYCLOAK_ISSUER_URL');
  const audience = isTest ? ((env.KEYCLOAK_AUDIENCE as string) ?? '') : required(env, 'KEYCLOAK_AUDIENCE');

  const portRaw = (env.PORT as string) ?? '3000';
  const port = Number.parseInt(portRaw, 10);
  if (!Number.isInteger(port) || port <= 0) {
    throw new Error(`PORT inválido: ${portRaw}`);
  }

  const telegramBotUsername = ((env.TELEGRAM_BOT_USERNAME as string) ?? '').trim().replace(/^@/, '');

  return {
    DATABASE_URL: required(env, 'DATABASE_URL'),
    KEYCLOAK_ISSUER_URL: issuer,
    KEYCLOAK_AUDIENCE: audience,
    PORT: port,
    NODE_ENV: nodeEnv as EnvConfig['NODE_ENV'],
    TELEGRAM_BOT_USERNAME: telegramBotUsername,
  };
}
