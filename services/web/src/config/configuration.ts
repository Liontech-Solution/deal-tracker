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
  // Client CONFIDENCIAL con service account, para crear el usuario del alta por la Admin API. No es
  // el de la SPA: aquél es público, con PKCE y sin direct grants a propósito. Opcionales las dos:
  // sin el secreto el registro por invitación queda apagado (`isInvitesConfigured`).
  KEYCLOAK_ADMIN_CLIENT_ID: string;
  KEYCLOAK_ADMIN_CLIENT_SECRET: string;
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
  // Clave de la API de Resend, por la que sale el correo de invitación. Opcional: sin ella el
  // cliente queda deshabilitado (no-op con log), como el de Telegram. En el cluster llega con
  // `optional: true`, así que esa rama es la que corre de verdad hasta que se selle.
  RESEND_API_KEY: string;
  // Remitente de la invitación. Distinto por entorno a propósito, y **no** derivable de
  // APP_PUBLIC_URL: en qa el dominio de correo es compartido por todos los QA del cluster y el de
  // la SPA no. Opcional; sin él el cliente de correo también queda deshabilitado.
  INVITE_FROM_EMAIL: string;
  // URL pública de la SPA, para armar el enlace del alta que viaja en el correo (#549). Vive aquí
  // aunque la consuma otra pieza: sin dueño, el síntoma sería el peor posible — el correo se manda
  // con un enlace roto.
  APP_PUBLIC_URL: string;
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
 * `true` si este entorno puede dar de alta a alguien por invitación. Es el **tercer interruptor**
 * del proyecto, hermano de `isAuthConfigured()`, y hace falta aparte porque la auth puede estar
 * puesta y el registro no: el alta necesita además hablar con Keycloak como administrador y mandar
 * un correo, y cualquiera de las dos piezas puede faltar sola.
 *
 * Las tres condiciones, y ninguna sobra:
 *
 * - **La auth**, porque un usuario recién creado tiene que poder entrar después.
 * - **El secreto del client de administración**, sin el cual no se crea la cuenta.
 * - **La clave de Resend**, sin la cual la invitación no sale del servidor. Descontar el cupo y no
 *   mandar el correo es peor que no dejar invitar.
 *
 * En `dev` queda apagado **por construcción**: su overlay borra las `KEYCLOAK_*` a propósito (#23).
 * O sea que, tal cual la lección de #309, **un `dev` en verde no prueba nada del registro** — que el
 * alta cree de verdad el usuario solo se observa en QA y en prod.
 */
export function isInvitesConfigured(env: Record<string, unknown> = process.env): boolean {
  return (
    isAuthConfigured(env) &&
    ((env.KEYCLOAK_ADMIN_CLIENT_SECRET as string) ?? '').trim() !== '' &&
    ((env.RESEND_API_KEY as string) ?? '').trim() !== ''
  );
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

  // El client confidencial que crea usuarios. El id cae en `deal-tracker-api`, que es como se llama
  // en los dos realms; el secreto no tiene defecto posible y sin él el registro queda apagado.
  const adminClientId = ((env.KEYCLOAK_ADMIN_CLIENT_ID as string) ?? '').trim() || 'deal-tracker-api';
  const adminClientSecret = ((env.KEYCLOAK_ADMIN_CLIENT_SECRET as string) ?? '').trim();

  const portRaw = (env.PORT as string) ?? '3000';
  const port = Number.parseInt(portRaw, 10);
  if (!Number.isInteger(port) || port <= 0) {
    throw new Error(`PORT inválido: ${portRaw}`);
  }

  const telegramBotUsername = ((env.TELEGRAM_BOT_USERNAME as string) ?? '').trim().replace(/^@/, '');
  const telegramBotToken = ((env.TELEGRAM_BOT_TOKEN as string) ?? '').trim();
  const telegramPollingEnabled = ((env.TELEGRAM_POLLING_ENABLED as string) ?? '').trim() === 'true';

  // Correo saliente y enlace del alta. Las tres opcionales: sin ellas el correo queda deshabilitado
  // y el registro por invitación apagado, en vez de impedir el arranque.
  const resendApiKey = ((env.RESEND_API_KEY as string) ?? '').trim();
  const inviteFromEmail = ((env.INVITE_FROM_EMAIL as string) ?? '').trim();
  const appPublicUrl = ((env.APP_PUBLIC_URL as string) ?? '').trim().replace(/\/+$/, '');

  return {
    DATABASE_URL: required(env, 'DATABASE_URL'),
    KEYCLOAK_ISSUER_URL: issuer,
    KEYCLOAK_AUDIENCE: audience,
    KEYCLOAK_CLIENT_ID: clientId,
    KEYCLOAK_ADMIN_CLIENT_ID: adminClientId,
    KEYCLOAK_ADMIN_CLIENT_SECRET: adminClientSecret,
    PORT: port,
    NODE_ENV: nodeEnv as EnvConfig['NODE_ENV'],
    TELEGRAM_BOT_USERNAME: telegramBotUsername,
    TELEGRAM_BOT_TOKEN: telegramBotToken,
    TELEGRAM_POLLING_ENABLED: telegramPollingEnabled,
    RESEND_API_KEY: resendApiKey,
    INVITE_FROM_EMAIL: inviteFromEmail,
    APP_PUBLIC_URL: appPublicUrl,
  };
}
