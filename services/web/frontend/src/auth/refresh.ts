/**
 * La decisión de qué hacer cuando hace falta un access token y el refresco no coopera (#262).
 *
 * Vive aparte de `keycloak.ts` por dos motivos. El primero es de fondo: `getFreshToken()` colapsaba
 * tres desenlaces distintos en un solo `null` —«no hay sesión», «el refresco falló» y «la sesión
 * está muerta»— y `authHeaders()` traduce `null` a *no mandar la cabecera*. O sea que un refresco
 * fallido con sesión viva mandaba la petición **anónima** a un endpoint que exige sesión, y el 401
 * estaba garantizado. Se curaba sola al ciclo siguiente, que es exactamente el síntoma que #262
 * observó en el sondeo de `/ajustes`.
 *
 * El segundo es de carril: el `vitest` de `services/web` corre estos ficheros con `environment:
 * 'node'` y sin jsdom, así que aquí **no se importa `keycloak-js`**. La instancia entra por una
 * interfaz estructural y el spec le pasa un doble.
 */

/** Lo poco de `Keycloak` que hace falta para decidir. */
export interface FuenteDeToken {
  authenticated?: boolean;
  token?: string;
  updateToken(minValidity: number): Promise<boolean>;
}

export type ResultadoToken =
  /** Auth deshabilitada o nunca hubo sesión. La petición anónima es lo correcto. */
  | { estado: 'sin-sesion' }
  | { estado: 'token'; token: string }
  /** La librería hizo `clearToken()`: el refresh token está muerto y hay que volver a `/acceso`. */
  | { estado: 'sesion-muerta' }
  /** Transitorio: ni token ni certeza de que la sesión haya muerto. */
  | { estado: 'refresco-fallido' };

/** Margen con el que se pide el token: si le quedan menos segundos, `keycloak-js` lo renueva. */
const MARGEN_S = 30;

/**
 * Resuelve el token de la petición que va a salir.
 *
 * El reintento es del **refresco**, que no es lo mismo que reintentar una petición que ya volvió
 * 401: un 401 del servidor sigue propagándose intacto hasta quien lo interpreta. Y es uno solo, y
 * forzado (`updateToken(-1)`): el segundo intento tiene que saltarse la comprobación de caducidad,
 * porque si el token está vencido y el primer refresco falló, un `updateToken(30)` volvería a
 * intentar exactamente lo mismo.
 *
 * No hace falta protegerlo contra la estampida del sondeo: `keycloak-js` encola los refrescos
 * concurrentes (`#refreshQueue`) y solo el primero sale a la red. Lo que sí implica esa cola es que
 * un fallo rechaza a **todos** los encolados a la vez — de ahí que valga la pena que el desenlace
 * no sea «manda la petición sin cabecera».
 */
export async function obtenerToken(kc: FuenteDeToken | null): Promise<ResultadoToken> {
  // `apiGet` sirve también a `/config`, que se pide **durante** el arranque de Keycloak (todavía no
  // hay instancia), y al catálogo, que sigue siendo público donde no hay realm (así corre `dev`).
  // Esta rama es la que mantiene esos dos casos funcionando.
  if (!kc?.authenticated) return { estado: 'sin-sesion' };

  try {
    await kc.updateToken(MARGEN_S);
    return kc.token ? { estado: 'token', token: kc.token } : { estado: 'refresco-fallido' };
  } catch {
    /* al reintento */
  }

  try {
    await kc.updateToken(-1);
    if (kc.token) return { estado: 'token', token: kc.token };
  } catch {
    /* a distinguir abajo */
  }

  // Aquí está la cautela que pedía la issue: reintentar a ciegas enmascara una sesión de verdad
  // caducada. No hace falta adivinarlo — `keycloak-js` solo llama a `clearToken()` (y con él pone
  // `authenticated` en `false`) cuando el endpoint de token responde **400**, o sea cuando el
  // refresh token ya no vale. Un fallo de red deja la sesión declarada viva.
  return kc.authenticated ? { estado: 'refresco-fallido' } : { estado: 'sesion-muerta' };
}
