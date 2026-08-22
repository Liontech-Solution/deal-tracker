/**
 * Lo que deciden las dos pantallas de la invitación, fuera de ellas: la del invitado (#550,
 * `/registro`) arriba, y la de quien invita (#551, `/ajustes`) en el bloque de abajo.
 *
 * No es una separación estética: `vitest.config.ts` corre con `environment: 'node'` y sin jsdom ni
 * testing-library, así que **un componente de este repo no se puede testear**. Lo único que puede
 * quedar cubierto es la lógica que viva en un módulo puro, y aquí lo que se decide no es cosmético
 * — de estas funciones salen qué pantalla ve alguien que acaba de recibir una invitación, si se le
 * ofrece reintentar algo que no puede salir bien, y si a quien invita se le da la única salida que
 * tiene un correo bloqueado.
 */
import type { InvitationStatus, InvitationTokenView } from '../api/types';

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

// ─────────────────────────────────────────────────────────────────────────────────────────────
// La otra punta: lo que decide la pantalla de QUIEN INVITA (#551, `/ajustes`).
//
// Va en este mismo fichero y no en uno nuevo porque es el mismo dominio visto desde el otro lado, y
// porque el motivo de que estas funciones existan es idéntico: sin jsdom, esto es lo único de la
// pantalla que un test puede tocar.
// ─────────────────────────────────────────────────────────────────────────────────────────────

/** Cómo se pinta un estado: su etiqueta y el tono con el que la tarjeta la colorea. */
export interface EtiquetaDeEstado {
  texto: string;
  tono: 'vivo' | 'neutro' | 'exito';
}

/**
 * El rótulo de cada estado. `caducada` y `revocada` comparten tono —las dos son «ya no sirve»— pero
 * **no texto**: una se murió sola y la otra la retiró quien invitaba, y confundirlas haría pensar
 * que el sistema retira invitaciones por su cuenta.
 */
export function etiquetaDeEstado(status: InvitationStatus): EtiquetaDeEstado {
  switch (status) {
    case 'viva':
      return { texto: 'Pendiente', tono: 'vivo' };
    case 'caducada':
      return { texto: 'Caducada', tono: 'neutro' };
    case 'canjeada':
      return { texto: 'Aceptada', tono: 'exito' };
    case 'revocada':
      return { texto: 'Revocada', tono: 'neutro' };
  }
}

/**
 * Si esa fila puede ofrecer el botón de revocar.
 *
 * **Las caducadas sí**, y es lo menos obvio de esta pantalla: el índice `ux_invitation_email_viva`
 * es parcial y su predicado **no puede mirar `expires_at`** (Postgres no admite `now()` ahí), así
 * que una invitación caducada **sigue ocupando ese correo** y volver a invitarlo da 409. Revocar es
 * el único gesto que lo libera, y no hay ningún job de limpieza que lo haga por su cuenta. Si esta
 * función devolviera `false` aquí, quien invita se quedaría sin salida.
 *
 * Las `canjeada` y las `revocada` no: su `DELETE` responde `404` —la primera se gastó de verdad y la
 * segunda ya está cerrada— y ofrecer un botón que solo puede fallar es peor que no ofrecerlo.
 */
export function puedeRevocarse(status: InvitationStatus): boolean {
  return status === 'viva' || status === 'caducada';
}

/**
 * Qué decirle a quien acaba de intentar invitar y no ha podido.
 *
 * Los cuatro códigos que devuelve `POST /invitations` significan cosas distintas y **solo uno es un
 * fallo nuestro**. El del `409` es el que más se cuida: su mensaje del servidor ya dirige a revocar
 * —que es la salida real, también para las caducadas— así que se propaga tal cual en vez de
 * sustituirlo por un «ese correo ya está invitado» que dejaría al usuario sin saber qué hacer.
 */
export function mensajeDelErrorAlInvitar(status: number, mensajeDelServidor?: string): string {
  switch (status) {
    case 403:
      return 'No te quedan invitaciones.';
    case 409:
      return (
        mensajeDelServidor ??
        'Ese correo ya tiene una invitación pendiente. Revócala para volver a invitarlo.'
      );
    case 502:
      // Importa decir que no se ha perdido nada: el servicio borra la fila y devuelve el cupo, así
      // que el reintento es literalmente el mismo gesto. Sin esta frase, el usuario cuenta sus
      // invitaciones, ve el mismo número y no sabe si se le ha ido una.
      return 'No hemos podido enviar el correo. No se ha gastado ninguna invitación: inténtalo de nuevo en unos minutos.';
    case 503:
      return 'Este servidor no tiene el registro por invitación configurado.';
    default:
      // El 400 del correo mal formado entra aquí: la validación es del servidor y su mensaje es más
      // concreto que nada que podamos inventar, igual que en `desenlaceDelAlta()`.
      return mensajeDelServidor ?? 'No se ha podido enviar la invitación.';
  }
}
