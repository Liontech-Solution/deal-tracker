import { Injectable, UnauthorizedException } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { PassportStrategy } from '@nestjs/passport';
import { passportJwtSecret } from 'jwks-rsa';
import { ExtractJwt, Strategy } from 'passport-jwt';

import type { EnvConfig } from '../config/configuration';
import type { AuthUser, KeycloakClaims } from './auth-user.interface';
import { UserService } from './user.service';

/**
 * Resource server: valida el JWT de acceso emitido por Keycloak contra su JWKS (claves
 * públicas del realm, cacheadas). No hay sesión de servidor; el frontend (SPA) obtiene el
 * token por PKCE y lo manda como `Authorization: Bearer`.
 */
@Injectable()
export class JwtStrategy extends PassportStrategy(Strategy) {
  constructor(
    config: ConfigService<EnvConfig, true>,
    private readonly users: UserService,
  ) {
    const issuer = config.get('KEYCLOAK_ISSUER_URL', { infer: true });
    const audience = config.get('KEYCLOAK_AUDIENCE', { infer: true });
    super({
      jwtFromRequest: ExtractJwt.fromAuthHeaderAsBearerToken(),
      ignoreExpiration: false,
      algorithms: ['RS256'],
      issuer,
      // Solo se exige audiencia si está configurada (Keycloak no siempre la incluye).
      ...(audience ? { audience } : {}),
      secretOrKeyProvider: passportJwtSecret({
        cache: true,
        rateLimit: true,
        jwksRequestsPerMinute: 10,
        jwksUri: `${issuer}/protocol/openid-connect/certs`,
      }),
    });
  }

  /** passport llama aquí con el payload ya verificado; devolvemos el usuario aprovisionado. */
  async validate(payload: KeycloakClaims): Promise<AuthUser> {
    if (!payload?.sub) {
      throw new UnauthorizedException('Token sin sub');
    }
    return this.users.provisionFromClaims(payload);
  }
}
