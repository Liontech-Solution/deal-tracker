import { Inject, Injectable } from '@nestjs/common';

import { Database, DRIZZLE } from '../database/database.module';
import { appUser } from '../database/schema';
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
      .returning();

    return {
      id: row.id,
      keycloakSub: row.keycloakSub,
      email: row.email,
      displayName: row.displayName,
    };
  }
}
