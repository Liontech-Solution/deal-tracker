import { createHash, randomBytes } from 'node:crypto';

import {
  BadGatewayException,
  ConflictException,
  ForbiddenException,
  GoneException,
  Inject,
  Injectable,
  Logger,
  NotFoundException,
  ServiceUnavailableException,
} from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { and, desc, eq, gt, isNull, sql } from 'drizzle-orm';

import { isInvitesConfigured, type EnvConfig } from '../config/configuration';
import { Database, DRIZZLE } from '../database/database.module';
import { appUser, invitation } from '../database/schema';
import { EmailApiClient } from '../email/email-api.client';
import { renderInvitationEmail } from '../email/invitation.template';
import { KeycloakAdminClient } from '../keycloak-admin/keycloak-admin.client';
import type { AcceptInvitationDto } from './dto/accept-invitation.dto';
import type { CreateInvitationDto } from './dto/create-invitation.dto';
import type {
  AcceptedInvitation,
  CreatedInvitation,
  InvitationListView,
  InvitationStatus,
  InvitationTokenView,
} from './invitations.types';

/**
 * Ventana de la invitación: **7 días**. El número es política y se decidió en la `0044` (está
 * argumentado en su cabecera); vive aquí y no en el `DEFAULT` de la tabla exactamente igual que
 * `LINK_TOKEN_TTL_MS` vive en `settings.service.ts` y no en la `0006`.
 *
 * El precedente de Telegram —60 minutos— no vale: allí el usuario acaba de pulsar el botón y está
 * mirando la pantalla, aquí quien invita no controla cuándo lee el correo el invitado.
 */
const INVITATION_TTL_MS = 7 * 24 * 60 * 60 * 1000;

/**
 * Bytes de aleatoriedad del token. 32 son 256 bits: **es la única defensa del endpoint público**,
 * porque en este repo no hay throttler de ningún tipo y `POST …/accept` crea cuentas. Conviene que
 * quede dicho aquí y no deducido de un `randomBytes` suelto.
 */
const TOKEN_BYTES = 32;

/**
 * El alta por invitación: la primera superficie **pública de escritura** del servicio (#549).
 *
 * Junta las tres piezas que la preceden — el esquema (#546), el correo (#547) y la Admin API de
 * Keycloak (#548) — y por eso casi todo lo interesante de este fichero está en los caminos de
 * fallo, no en el feliz.
 */
@Injectable()
export class InvitationsService {
  private readonly logger = new Logger(InvitationsService.name);

  constructor(
    @Inject(DRIZZLE) private readonly db: Database,
    private readonly config: ConfigService<EnvConfig, true>,
    private readonly email: EmailApiClient,
    private readonly keycloak: KeycloakAdminClient,
  ) {}

  /**
   * Invita a un correo: gasta 1 de cupo, guarda el hash del token y manda el enlace.
   *
   * El orden de las cuatro operaciones importa, y cada fallo deshace lo anterior:
   *
   * 1. normalizar el correo — en la aplicación, ver `normalizarCorreo()`;
   * 2. **consumir el cupo** en una sola sentencia condicional;
   * 3. insertar la invitación, contando con el `23505` del índice parcial;
   * 4. mandar el correo, y si no sale, borrar la fila y devolver el cupo.
   *
   * Lo que ninguna de las cuatro hace es abrir una transacción: el paso 4 habla con otro sistema y
   * no participaría del `COMMIT` de todas formas (el mismo motivo que en `accept()`).
   */
  async create(userId: number, dto: CreateInvitationDto): Promise<CreatedInvitation> {
    this.exigirRegistroConfigurado();
    const baseUrl = this.exigirUrlPublica();
    const email = normalizarCorreo(dto.email);

    // Consumir cupo. Si no toca ninguna fila, no había: mismo truco de una sola sentencia que
    // `TelegramLinkService.redeemStartToken()`, y resuelve la carrera entre dos peticiones a la vez
    // sin transacción explícita. De paso trae el nombre de quien invita, que hace falta para el
    // correo y evita una segunda consulta.
    const [inviter] = await this.db
      .update(appUser)
      .set({ invitesRemaining: sql`${appUser.invitesRemaining} - 1`, updatedAt: new Date() })
      .where(and(eq(appUser.id, userId), gt(appUser.invitesRemaining, 0)))
      .returning({
        invitesRemaining: appUser.invitesRemaining,
        displayName: appUser.displayName,
        email: appUser.email,
      });
    if (!inviter) {
      // No distingue «no te queda cupo» de «no existes»: quien pregunta está autenticado, así que
      // lo segundo no puede pasar sin que algo esté muy roto.
      throw new ForbiddenException('No te quedan invitaciones.');
    }

    const token = randomBytes(TOKEN_BYTES).toString('base64url');
    const expiresAt = new Date(Date.now() + INVITATION_TTL_MS);

    let id: number;
    try {
      const [fila] = await this.db
        .insert(invitation)
        .values({ inviterUserId: userId, email, tokenHash: hashToken(token), expiresAt })
        .returning({ id: invitation.id });
      id = fila.id;
    } catch (err) {
      await this.devolverCupo(userId);
      if (esCorreoYaInvitado(err)) {
        // `ux_invitation_email_viva` es UNIQUE PARCIAL (`WHERE accepted_at IS NULL AND revoked_at
        // IS NULL`), así que esto llega como un 23505 y no como una fila que no aparece.
        //
        // Y la viva que estorba **puede estar caducada**: el predicado del índice no puede mirar
        // `expires_at` porque Postgres no admite `now()` ahí. Por eso el mensaje no es «ya tiene
        // una invitación» a secas —que dejaría a quien invita sin salida visible— sino el gesto
        // que la libera. Revocar es además lo que devuelve el cupo, así que un solo gesto arregla
        // las dos cosas y no hace falta ningún job de limpieza.
        throw new ConflictException(
          'Ese correo ya tiene una invitación pendiente. Revócala en tus ajustes para volver a ' +
            'invitarlo: al revocarla recuperas el cupo, y sirve también si ya ha caducado.',
        );
      }
      throw err;
    }

    const correo = renderInvitationEmail({
      inviterName: inviter.displayName ?? inviter.email ?? '',
      url: `${baseUrl}/registro?token=${token}`,
      expiresAt,
    });
    const enviado = await this.email.sendEmail({ to: email, ...correo });
    if (!enviado.ok) {
      // Nada salió del servidor, así que la invitación **se borra** en vez de revocarse: una fila
      // «revocada» en la lista de quien invita contaría algo que no llegó a ocurrir, y borrarla
      // libera el correo para que el reintento no choque con el índice.
      await this.db.delete(invitation).where(eq(invitation.id, id));
      await this.devolverCupo(userId);
      this.logger.error(`No se pudo mandar la invitación ${id} (${enviado.reason}); cupo devuelto`);
      throw new BadGatewayException(
        'No se ha podido enviar el correo de invitación. No se ha gastado ninguna invitación; ' +
          'inténtalo de nuevo en unos minutos.',
      );
    }

    this.logger.log(`Invitación ${id} enviada por el usuario ${userId}`);
    return {
      id,
      email,
      expiresAt: expiresAt.toISOString(),
      invitesRemaining: inviter.invitesRemaining,
    };
  }

  /**
   * Las invitaciones que ha mandado este usuario, de la más reciente a la más antigua, **con su
   * cupo**.
   *
   * Las dos consultas van juntas y en este orden a propósito: primero el cupo, luego la lista. Si
   * una revocación entra en medio, lo peor que se ve es un cupo de menos junto a una fila ya
   * revocada —un número conservador al lado de un estado nuevo—, y no un cupo ya devuelto junto a
   * una invitación que todavía se pinta viva, que es la combinación que invitaría a revocar dos
   * veces. Ninguna transacción hace falta para eso: la pantalla se refresca sola tras cada gesto.
   */
  async list(userId: number): Promise<InvitationListView> {
    this.exigirRegistroConfigurado();
    const [usuario] = await this.db
      .select({ invitesRemaining: appUser.invitesRemaining })
      .from(appUser)
      .where(eq(appUser.id, userId));

    const filas = await this.db
      .select({
        id: invitation.id,
        email: invitation.email,
        createdAt: invitation.createdAt,
        expiresAt: invitation.expiresAt,
        acceptedAt: invitation.acceptedAt,
        revokedAt: invitation.revokedAt,
      })
      .from(invitation)
      .where(eq(invitation.inviterUserId, userId))
      .orderBy(desc(invitation.createdAt), desc(invitation.id));

    return {
      // `usuario` no puede faltar —quien pregunta está autenticado y su fila nace en la primera
      // petición— pero el `?? 0` evita que un imposible se convierta en un `undefined` viajando por
      // HTTP hasta la pantalla, donde se pintaría como cupo en blanco.
      invitesRemaining: usuario?.invitesRemaining ?? 0,
      invitations: filas.map((f) => ({
        id: f.id,
        email: f.email,
        status: estadoDe(f),
        createdAt: f.createdAt.toISOString(),
        expiresAt: f.expiresAt.toISOString(),
      })),
    };
  }

  /**
   * Revoca una invitación propia y **devuelve el cupo**.
   *
   * El `WHERE` lleva las cuatro condiciones juntas a propósito: sin `inviter_user_id` se podrían
   * revocar las de otro, y sin las dos marcas nulas se devolvería cupo dos veces al revocar dos
   * veces lo mismo — o se devolvería por una invitación **ya aceptada**, que sí se gastó de verdad.
   * Cero filas es un 404 sin distinguir «no es tuya» de «ya estaba cerrada»: son la misma respuesta
   * para quien pregunta, y distinguirlas diría si existe una invitación ajena.
   */
  async revoke(userId: number, id: number): Promise<void> {
    this.exigirRegistroConfigurado();
    const revocadas = await this.db
      .update(invitation)
      .set({ revokedAt: new Date() })
      .where(
        and(
          eq(invitation.id, id),
          eq(invitation.inviterUserId, userId),
          isNull(invitation.acceptedAt),
          isNull(invitation.revokedAt),
        ),
      )
      .returning({ id: invitation.id });
    if (revocadas.length === 0) {
      throw new NotFoundException('Esa invitación no existe o ya no se puede revocar.');
    }
    await this.devolverCupo(userId);
  }

  /**
   * Qué hay detrás del token del correo, para la pantalla de #550. **Sin sesión**: quien pregunta
   * todavía no tiene cuenta, que es el sentido de todo esto.
   *
   * Contesta `200` siempre, con el estado en el cuerpo. Ver `InvitationTokenView` para el porqué.
   */
  async describeToken(token: string): Promise<InvitationTokenView> {
    this.exigirRegistroConfigurado();
    const fila = await this.porToken(token);
    if (!fila || fila.revokedAt !== null) return { status: 'desconocida' };
    if (fila.acceptedAt !== null) return { status: 'canjeada' };
    if (fila.expiresAt.getTime() <= Date.now()) return { status: 'caducada' };
    return {
      status: 'valida',
      email: fila.email,
      inviterName: fila.inviterName ?? fila.inviterEmail ?? '',
      expiresAt: fila.expiresAt.toISOString(),
    };
  }

  /**
   * El alta: crea la cuenta en Keycloak y cierra la invitación. **Sin sesión**, por lo mismo.
   *
   * ── EL ORDEN DE LAS ESCRITURAS, QUE NO HAY TRANSACCIÓN QUE CUBRA ──
   *
   * Crear el usuario en Keycloak es una llamada HTTP a otro sistema: no participa del `COMMIT`, y
   * eso obliga a elegir cuál de los dos desenlaces malos se prefiere.
   *
   * Si se marcase la invitación **antes** y Keycloak fallara, el invitado se queda sin cuenta *y*
   * sin token: no tiene ninguna salida, y quien le invitó ya gastó el cupo. Marcándola **después**,
   * lo peor que puede quedar es un usuario creado en Keycloak con su invitación aún viva — y de eso
   * sí se sale, porque un reintento con el mismo token reconoce el 409 de Keycloak y cierra la
   * invitación en vez de fallar (ver el caso `exists`). Se elige el segundo.
   */
  async accept(token: string, dto: AcceptInvitationDto): Promise<AcceptedInvitation> {
    this.exigirRegistroConfigurado();
    const fila = await this.porToken(token);
    const usable =
      fila !== null &&
      fila.revokedAt === null &&
      fila.acceptedAt === null &&
      fila.expiresAt.getTime() > Date.now();
    if (!fila || !usable) {
      // Uniforme para caducada, canjeada, revocada e inexistente: quien llega aquí ya ha pasado
      // por `describeToken()`, que sí las distingue para pintar la pantalla, así que un fallo en
      // este punto es una carrera y no la vía normal de enterarse.
      throw new GoneException('Esta invitación ya no es válida.');
    }

    // El correo lo pone la invitación, NUNCA el cuerpo de la petición (el DTO ni siquiera tiene
    // campo para ello). Lo contrario convierte una invitación en un alta libre.
    const creado = await this.keycloak.createUser({
      email: fila.email,
      firstName: dto.firstName,
      password: dto.password,
    });

    if (!creado.ok && creado.reason === 'exists') {
      // Ya hay cuenta con ese correo. Es un caso real —se puede invitar a alguien que ya la tiene—
      // y también el desenlace del reintento descrito arriba, que es indistinguible desde aquí.
      // En los dos, lo correcto es lo mismo: cerrar la invitación (el token deja de servir) y
      // mandar a esa persona a la pantalla de acceso.
      await this.marcarAceptada(fila.id);
      throw new ConflictException({
        code: 'ya_registrado',
        message: 'Ya existe una cuenta con este correo. Entra con ella o recupera la contraseña.',
      });
    }
    if (!creado.ok) {
      if (creado.reason === 'disabled') {
        // No debería llegar aquí: `exigirRegistroConfigurado()` lo habría parado antes. Se cubre
        // porque las dos condiciones no son la misma —el interruptor mira el entorno, el cliente
        // mira lo suyo— y responder 502 a un entorno sin configurar sería mentir.
        throw new ServiceUnavailableException('El registro no está disponible en este servidor.');
      }
      this.logger.error(`El alta de la invitación ${fila.id} falló en Keycloak (${creado.reason})`);
      throw new BadGatewayException('No se ha podido crear la cuenta. Inténtalo de nuevo.');
    }

    const marcadas = await this.marcarAceptada(fila.id);
    if (marcadas === 0) {
      // Alguien canjeó el mismo token entre la comprobación y ahora. El usuario ya existe en
      // Keycloak, que es el desenlace elegido: se deja pasar el alta en vez de dejarle sin cuenta.
      this.logger.warn(`La invitación ${fila.id} se cerró en paralelo; el usuario ya existe`);
    }
    this.logger.log(`Alta consumada de la invitación ${fila.id}`);
    return { email: fila.email };
  }

  /**
   * El tercer interruptor del proyecto (#548). Se comprueba **por petición y en el servicio**, no
   * en el constructor: así los cinco endpoints responden 503 —lo mismo que hace `SettingsService`
   * cuando falta la config del bot— en vez de que el módulo falle al arrancar en `dev`, que no
   * trae ninguna `KEYCLOAK_*` a propósito.
   */
  private exigirRegistroConfigurado(): void {
    if (!isInvitesConfigured()) {
      throw new ServiceUnavailableException('El registro por invitación no está configurado en el servidor.');
    }
  }

  /**
   * `APP_PUBLIC_URL` **no** está en `isInvitesConfigured()`, y aun así sin ella no se puede invitar:
   * el enlace del correo se arma con ella. Su comentario en `configuration.ts` avisa de que el
   * síntoma sería el peor posible —un correo enviado con un enlace roto—, así que quien lo consume
   * lo comprueba antes de gastar cupo. Un 503 aquí es infinitamente mejor que ese correo.
   */
  private exigirUrlPublica(): string {
    const baseUrl = this.config.get('APP_PUBLIC_URL', { infer: true });
    if (!baseUrl) {
      this.logger.error('Falta APP_PUBLIC_URL: el enlace del alta no se puede armar');
      throw new ServiceUnavailableException('El registro por invitación no está configurado en el servidor.');
    }
    return baseUrl;
  }

  /** Devuelve una unidad de cupo. Contrapartida del `- 1` condicional de `create()`. */
  private async devolverCupo(userId: number): Promise<void> {
    await this.db
      .update(appUser)
      .set({ invitesRemaining: sql`${appUser.invitesRemaining} + 1`, updatedAt: new Date() })
      .where(eq(appUser.id, userId));
  }

  /**
   * Marca aceptada, condicionalmente. Devuelve cuántas filas tocó: cero significa que otro canje
   * ganó la carrera.
   *
   * `accepted_user_id` se queda **null** aquí a propósito y no es un olvido: la fila de `app_user`
   * nace en la primera petición autenticada (aprovisionamiento JIT), así que en este instante no
   * existe todavía. La rellena `UserService.provisionFromClaims()`, que es el único momento en que
   * existen las dos puntas.
   */
  private async marcarAceptada(id: number): Promise<number> {
    const filas = await this.db
      .update(invitation)
      .set({ acceptedAt: new Date() })
      .where(and(eq(invitation.id, id), isNull(invitation.acceptedAt), isNull(invitation.revokedAt)))
      .returning({ id: invitation.id });
    return filas.length;
  }

  /** Busca por el hash del token. El token en claro no está en ninguna parte de la base. */
  private async porToken(token: string) {
    const [fila] = await this.db
      .select({
        id: invitation.id,
        email: invitation.email,
        expiresAt: invitation.expiresAt,
        acceptedAt: invitation.acceptedAt,
        revokedAt: invitation.revokedAt,
        inviterName: appUser.displayName,
        inviterEmail: appUser.email,
      })
      .from(invitation)
      .innerJoin(appUser, eq(appUser.id, invitation.inviterUserId))
      .where(eq(invitation.tokenHash, hashToken(token)));
    return fila ?? null;
  }
}

/**
 * Minúsculas y recortado. **Esto lo hace el servicio o no lo hace nadie**: `ux_invitation_email_viva`
 * indexa la columna desnuda porque con el ctype `C` del cluster `lower()` no baja las acentuadas
 * (#105), así que la unicidad solo funciona si lo que se guarda ya viene normalizado. Es la decisión
 * 1 de la cabecera de la `0044`, escrita también aquí porque aquí es donde se cumple.
 */
export function normalizarCorreo(email: string): string {
  return email.trim().toLowerCase();
}

/** El token se guarda como `sha256` en hex. Ver la decisión 2 de la `0044`. */
function hashToken(token: string): string {
  return createHash('sha256').update(token).digest('hex');
}

/**
 * `23505` sobre `ux_invitation_email_viva`: ese correo ya tiene una invitación viva.
 *
 * Hay que **desenvolver la cadena de `cause`**: drizzle envuelve lo que le sube el driver en un
 * `DrizzleQueryError`, así que el `code` de postgres.js no está en el error que se atrapa sino un
 * nivel —o más— por dentro. Mirar solo el de fuera hace que este `catch` no reconozca nunca su caso
 * y el 409 salga como un 500, que es exactamente lo que pasó al escribirlo.
 */
function esCorreoYaInvitado(err: unknown): boolean {
  let actual: unknown = err;
  while (typeof actual === 'object' && actual !== null) {
    const { code, constraint_name: constraint } = actual as { code?: unknown; constraint_name?: unknown };
    if (code === '23505') {
      // El otro UNIQUE de la tabla es el del hash del token, y ahí un choque sería un token
      // repetido de 256 bits: no se confunde con esto, y tratarlo como «correo ya invitado» sería
      // mentir.
      return constraint !== 'invitation_token_hash_uniq';
    }
    actual = (actual as { cause?: unknown }).cause;
  }
  return false;
}

/** El estado sale de las tres marcas y del reloj; ver `InvitationStatus`. */
function estadoDe(f: { expiresAt: Date; acceptedAt: Date | null; revokedAt: Date | null }): InvitationStatus {
  if (f.acceptedAt !== null) return 'canjeada';
  if (f.revokedAt !== null) return 'revocada';
  return f.expiresAt.getTime() <= Date.now() ? 'caducada' : 'viva';
}
