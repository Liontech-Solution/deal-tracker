import { Transform } from 'class-transformer';
import { IsEmail, MaxLength } from 'class-validator';

/**
 * Alta de una invitación. Solo el correo: todo lo demás (token, caducidad, quién invita) lo pone el
 * servidor.
 *
 * ── EL `trim()` DE AQUÍ NO ES LA NORMALIZACIÓN, Y LA DIFERENCIA IMPORTA ──
 *
 * Recortar aquí es **higiene de entrada**: una dirección pegada del portapapeles arrastra un espacio
 * con muchísima facilidad, y sin esto `@IsEmail` la rechazaría con un 400 incomprensible.
 *
 * La normalización de la que depende el invariante —minúsculas y recortado— vive en el servicio
 * (`normalizarCorreo()`), y ahí seguirá: `ux_invitation_email_viva` indexa la columna desnuda porque
 * con el ctype `C` del cluster `lower()` no baja las acentuadas (#105), así que lo que se guarda
 * tiene que venir ya normalizado **por cualquier camino que escriba en la tabla**, no solo por este
 * DTO. Si algún día hay un segundo camino, el DTO no lo cubriría y el servicio sí.
 */
export class CreateInvitationDto {
  // 254 es el máximo de una dirección de correo (RFC 5321). No hay CHECK equivalente en la 0044: el
  // límite es de forma, no del esquema.
  @Transform(({ value }) => (typeof value === 'string' ? value.trim() : value))
  @IsEmail()
  @MaxLength(254)
  email!: string;
}
