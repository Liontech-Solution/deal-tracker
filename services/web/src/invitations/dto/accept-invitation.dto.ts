import { IsOptional, IsString, MaxLength, MinLength } from 'class-validator';

/**
 * El alta propiamente dicha.
 *
 * **No hay campo de correo, y su ausencia es la regla de seguridad de todo esto**: el correo lo fija
 * la invitación y nunca el formulario. Con `forbidNonWhitelisted` en el `ValidationPipe` global,
 * mandar uno no es que se ignore — la petición se rechaza con un 400, que es aún más explícito.
 * Aceptarlo convertiría una invitación en un alta libre, que es justo lo que esta versión existe
 * para no hacer.
 */
export class AcceptInvitationDto {
  /**
   * Mínimo de 12 caracteres. Es un suelo nuestro y provisional: la autoridad sobre la contraseña es
   * la `passwordPolicy` del realm, que **hoy no existe en ninguno de los dos** y que #347 tiene que
   * declarar. Cuando exista, su rechazo llegará como un `http` genérico desde
   * `KeycloakAdminClient.createUser()` —solo distingue el 409— y se traducirá en un 502; afinar eso
   * es trabajo de aquella issue, no de ésta, porque hasta entonces la rama es inalcanzable.
   */
  @IsString()
  @MinLength(12)
  @MaxLength(128)
  password!: string;

  /** Cómo quiere que le llamen. Va a `firstName` de Keycloak y de ahí al `name` del token. */
  @IsOptional()
  @IsString()
  @MaxLength(60)
  firstName?: string;
}
