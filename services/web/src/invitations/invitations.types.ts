/**
 * Vistas del alta por invitación (#549). Lo que sale por HTTP, no lo que hay en la tabla: aquí no
 * viaja nunca el token ni su hash.
 */

/**
 * Estado de una invitación, calculado y no almacenado: la tabla guarda tres marcas de tiempo
 * (`accepted_at`, `revoked_at`, `expires_at`) y el estado es lo que se deduce de ellas mirando el
 * reloj. Guardarlo como columna obligaría a un job que pasase las vivas a caducadas, que es justo
 * lo que la `0044` decidió no tener.
 */
export type InvitationStatus = 'viva' | 'caducada' | 'canjeada' | 'revocada';

/** Una invitación en la lista de quien la mandó (`GET /api/invitations`). */
export interface InvitationView {
  id: number;
  /**
   * El correo **entero**. No se enmascara: es una dirección que quien invita tecleó él mismo, así
   * que no le revelamos nada, y con `a****z@…` no podría distinguir dos invitaciones suyas ni
   * revocar la que quiere (que es la pantalla de #551).
   */
  email: string;
  status: InvitationStatus;
  createdAt: string;
  expiresAt: string;
}

/**
 * Lo que contesta `GET /api/invitations`: la lista **y el cupo**, juntos (#551).
 *
 * El cupo va aquí y no en una ruta aparte porque es la única forma de que la pantalla no pueda
 * enseñar un número que no cuadre con lo que tiene debajo: los dos se leen en el mismo handler, así
 * que una revocación no puede quedar reflejada en la lista y no en el cupo, ni al revés.
 *
 * Antes de #551 esto era un array pelado. Se envolvió porque `invites_remaining` **no salía por HTTP
 * en ninguna parte** salvo dentro de la respuesta de `create()`, o sea solo *después* de gastarlo:
 * no había forma de contestar «cuánto me queda» sin invitar a alguien.
 */
export interface InvitationListView {
  /** Lo que le queda a quien pregunta. **Cero es lo normal**: ver `app_user.invites_remaining`. */
  invitesRemaining: number;
  invitations: InvitationView[];
}

/** Lo que devuelve crear una invitación. El cupo va dentro para que la SPA no tenga que releerlo. */
export interface CreatedInvitation {
  id: number;
  email: string;
  expiresAt: string;
  /** Cupo que le queda a quien invita **después** de gastar éste. */
  invitesRemaining: number;
}

/**
 * Lo que ve quien abre el enlace del correo (`GET /api/invitations/token/:token`), que todavía no
 * tiene cuenta.
 *
 * **Siempre viaja con un `200`**, y el estado va en el cuerpo. Dos razones: #550 tiene que pintar
 * cuatro pantallas distintas y el cuerpo de un 404 no es un contrato que nadie deba parsear; y con
 * todas las respuestas iguales de forma, el código de estado no dice si un token existe.
 *
 * `revocada` se colapsa en `desconocida` a propósito: quien invitó se la quitó, y no hay nada útil
 * —ni que nos corresponda— que contarle al que la recibió.
 */
export interface InvitationTokenView {
  status: 'valida' | 'caducada' | 'canjeada' | 'desconocida';
  /** Solo en `valida`. Es el correo que el alta va a usar, y el formulario lo enseña de solo lectura. */
  email?: string;
  /** Solo en `valida`. Quién invita: sin eso, un enlace por correo es indistinguible de un phishing. */
  inviterName?: string;
  /** Solo en `valida`. */
  expiresAt?: string;
}

/** Lo que devuelve un alta consumada. El correo, para que la SPA lo lleve a la pantalla de acceso. */
export interface AcceptedInvitation {
  email: string;
}
