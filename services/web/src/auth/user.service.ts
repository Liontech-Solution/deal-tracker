import { Inject, Injectable } from '@nestjs/common';
import { and, eq, isNotNull, isNull, sql } from 'drizzle-orm';

import { Database, DRIZZLE } from '../database/database.module';
import { appUser, invitation } from '../database/schema';
import type { AuthUser, KeycloakClaims } from './auth-user.interface';

/** Aprovisionamiento JIT: crea/actualiza el `app_user` a partir de los claims de Keycloak. */
@Injectable()
export class UserService {
  constructor(@Inject(DRIZZLE) private readonly db: Database) {}

  async provisionFromClaims(claims: KeycloakClaims): Promise<AuthUser> {
    const email = claims.email ?? null;
    const displayName = claims.name ?? claims.preferred_username ?? null;

    const [row] = await this.db
      .insert(appUser)
      .values({ keycloakSub: claims.sub, email, displayName })
      .onConflictDoUpdate({
        target: appUser.keycloakSub,
        set: { email, displayName, updatedAt: new Date() },
      })
      .returning({
        id: appUser.id,
        keycloakSub: appUser.keycloakSub,
        email: appUser.email,
        displayName: appUser.displayName,
        /**
         * `true` si esta fila la acaba de **insertar** este `INSERT … ON CONFLICT`, y `false` si lo
         * que hizo fue actualizar la que ya existía. En una fila recién insertada `xmax` vale 0; en
         * una actualizada lleva el id de la transacción que la bloqueó. Es la única forma de
         * distinguir las dos ramas de un upsert sin una consulta más.
         *
         * Y hace falta distinguirlas por una razón concreta: **esto corre en cada petición
         * autenticada** (`JwtStrategy.validate`), así que lo que cuelgue de aquí se paga por
         * petición. Ver `linkInvitation()`.
         */
        insertado: sql<boolean>`(xmax = 0)`,
      });

    if (row.insertado) {
      await this.linkInvitation(row.id, email);
    }

    return {
      id: row.id,
      keycloakSub: row.keycloakSub,
      email: row.email,
      displayName: row.displayName,
    };
  }

  /**
   * Cierra el círculo del alta por invitación: apunta la invitación aceptada a la fila de
   * `app_user` que acaba de nacer.
   *
   * **Es el único momento en que existen las dos puntas.** El endpoint del alta (#549) crea la
   * cuenta en Keycloak y marca `accepted_at`, pero no puede rellenar `accepted_user_id` porque en
   * ese instante no hay fila: el aprovisionamiento es JIT y ocurre aquí, en la primera petición
   * autenticada. Sin esto la columna no tendría ningún escritor y sería siempre `null`, cuando la
   * `0044` la declara justo para saber «a quién apuntaba».
   *
   * **Solo se llama cuando la fila es nueva**, y eso no es una optimización cosmética: el
   * aprovisionamiento corre en cada petición autenticada, así que hacerlo incondicional metería un
   * `UPDATE` por petición contra la base que #540 ya describe como el eslabón frágil. Una cuenta se
   * crea una vez.
   *
   * El `WHERE` la deja **idempotente y sin robar nada ajeno**: solo toca invitaciones ya aceptadas
   * y todavía sin dueño. La comparación es contra el correo del token **en minúsculas y recortado**,
   * la misma normalización que aplica el servicio al guardarlo — deliberadamente en TypeScript y no
   * con un `lower()` en SQL, porque bajo el ctype `C` del cluster `lower()` no baja las acentuadas
   * (#105), que es por lo que la `0044` indexa la columna desnuda.
   */
  private async linkInvitation(userId: number, email: string | null): Promise<void> {
    const normalizado = email?.trim().toLowerCase();
    if (!normalizado) return;
    await this.db
      .update(invitation)
      .set({ acceptedUserId: userId })
      .where(
        and(
          eq(invitation.email, normalizado),
          isNotNull(invitation.acceptedAt),
          isNull(invitation.acceptedUserId),
        ),
      );
  }
}
