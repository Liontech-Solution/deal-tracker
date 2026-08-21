/**
 * Lo que decide la página de alta (#550), fuera de la página.
 *
 * No es una separación estética: `vitest.config.ts` corre con `environment: 'node'` y sin jsdom ni
 * testing-library, así que **un componente de este repo no se puede testear**. Lo único que puede
 * quedar cubierto es la lógica que viva en un módulo puro, y aquí lo que se decide no es cosmético
 * — de estas tres funciones salen qué pantalla ve alguien que acaba de recibir una invitación y si
 * se le ofrece reintentar algo que no puede salir bien.
 */
import type { InvitationTokenView } from '../api/types';

/**
 * El suelo de la contraseña, **nuestro y provisional**.
 *
 * Es el mismo número que el `@MinLength(12)` del `AcceptInvitationDto`, y está aquí para que la
 * pantalla pueda anunciar la regla antes de enviar. La autoridad de verdad será la `passwordPolicy`
 * del realm, que hoy **no existe en ninguno de los dos** (medido el 20/08/2026) y que instala #347.
 * Cuando llegue, este número y el del realm tienen que decir lo mismo: si el realm pide más y el
 * formulario sigue anunciando 12, el usuario se entera al enviar.
 */
export const LONGITUD_MINIMA_CONTRASENA = 12;

/**
 * El token que viaja en la query del enlace del correo (`/registro?token=…`).
 *
 * Vacío y ausente son lo mismo: los dos significan que no hay nada que consultar. Se recorta porque
 * un enlace copiado a mano desde un cliente de correo llega con espacios más veces de las que
 * parece, y un token con un espacio detrás es un 200 `desconocida` que no se explica solo.
 */
export function leerToken(search: string): string | null {
  const crudo = new URLSearchParams(search).get('token');
  if (crudo === null) return null;
  const token = crudo.trim();
  return token === '' ? null : token;
}

/**
 * Las siete pantallas de `/registro`.
 *
 * La issue enumera cuatro, que son los cuatro estados del token. Las otras tres no las trae el
 * backend y hay que pintarlas igual: el arranque (`/api/config` viaja por red, no se sabe nada de
 * forma síncrona), el entorno con el registro apagado —el que se ve en `dev`, y el que se olvida—
 * y el que no escribe nadie: entrar a `/registro` **sin token**, que pasa en cuanto alguien recorta
 * la URL o el cliente de correo parte el enlace.
 */
export type EstadoDelRegistro =
  | 'cargando'
  | 'apagado'
  | 'sin-token'
  | 'error-consulta'
  | InvitationTokenView['status'];

export interface EntradaDelRegistro {
  /** `auth.ready`: hasta que no lo es, `invitesEnabled` no significa nada. */
  ready: boolean;
  invitesEnabled: boolean;
  token: string | null;
  /** La respuesta del `GET` del token, mientras no haya llegado `undefined`. */
  vista: InvitationTokenView | undefined;
  /**
   * Que la consulta del token **falle**, que no es lo mismo que decir que el token no vale: el
   * endpoint contesta 200 con el estado dentro, así que un error aquí es de red o un 503. Sin esta
   * entrada, `vista` se quedaría en `undefined` para siempre y la pantalla giraría eternamente.
   */
  errorConsulta: boolean;
}

export function estadoDelRegistro({
  ready,
  invitesEnabled,
  token,
  vista,
  errorConsulta,
}: EntradaDelRegistro): EstadoDelRegistro {
  if (!ready) return 'cargando';
  // El apagado va **antes** que el token: en un entorno sin registro los dos endpoints contestan
  // 503 sea cual sea el token, así que preguntar por él solo sirve para enseñar un error peor que
  // la verdad. Es la lección de #309 aplicada tal cual — un control condicionado al entorno solo se
  // verifica donde lo tiene.
  if (!invitesEnabled) return 'apagado';
  if (token === null) return 'sin-token';
  if (errorConsulta) return 'error-consulta';
  if (vista === undefined) return 'cargando';
  return vista.status;
}

/**
 * Qué se pinta cuando el alta no sale, y —lo que ningún tipo puede fijar— **si se ofrece volver a
 * intentarlo**.
 *
 * Los dos códigos que importan son asimétricos y se parecen desde fuera: en el **409** el servidor
 * cierra la invitación al contestar, o sea que el mismo enlace ya solo puede dar 410 y un botón de
 * reintento sería una trampa; en el **502** la invitación **sigue viva** y reintentar es
 * exactamente lo que hay que hacer. Equivocar cuál es cuál no rompe ningún test de tipos y deja a
 * alguien pulsando un botón que nunca podrá funcionar.
 */
export interface DesenlaceDelAlta {
  titulo: string;
  texto: string;
  permiteReintento: boolean;
  /** Si tiene sentido ofrecerle la pantalla de acceso: ya hay (o ya habrá) una cuenta con ese correo. */
  llevaAAcceso: boolean;
}

export function desenlaceDelAlta(status: number, mensajeDelServidor?: string): DesenlaceDelAlta {
  switch (status) {
    case 409:
      return {
        titulo: 'Ya tienes cuenta',
        texto: 'Ya existe una cuenta con este correo. Entra con ella; si no recuerdas la contraseña, recupérala desde la pantalla de acceso.',
        permiteReintento: false,
        llevaAAcceso: true,
      };
    case 410:
      return {
        titulo: 'Esta invitación ya no vale',
        texto: 'Puede que haya caducado, que ya se haya usado o que quien te invitó la haya retirado. Pídele una nueva.',
        permiteReintento: false,
        llevaAAcceso: true,
      };
    case 502:
      return {
        titulo: 'No hemos podido crear la cuenta',
        texto: 'Ha fallado algo por nuestro lado, no por el tuyo. Tu invitación sigue siendo válida: vuelve a intentarlo con este mismo enlace.',
        permiteReintento: true,
        llevaAAcceso: false,
      };
    case 503:
      return {
        titulo: 'El registro no está disponible',
        texto: 'Este entorno no tiene el alta por invitación configurada.',
        permiteReintento: false,
        llevaAAcceso: false,
      };
    default:
      // El 400 y cualquier cosa que no esperábamos. El mensaje del servidor es más concreto que
      // nada que podamos inventar aquí, y es la convención del resto del frontend: la validación
      // es suya y su error se propaga tal cual.
      return {
        titulo: 'No hemos podido completar el alta',
        texto: mensajeDelServidor ?? 'Revisa los datos e inténtalo de nuevo.',
        permiteReintento: true,
        llevaAAcceso: false,
      };
  }
}
