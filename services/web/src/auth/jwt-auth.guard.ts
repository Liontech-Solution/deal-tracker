import { Injectable, UnauthorizedException } from '@nestjs/common';
import type { ExecutionContext } from '@nestjs/common';
import { AuthGuard } from '@nestjs/passport';

import { isAuthConfigured } from '../config/configuration';

/** Exige un JWT válido de Keycloak. Protege los endpoints de intereses. */
@Injectable()
export class JwtAuthGuard extends AuthGuard('jwt') {
  canActivate(context: ExecutionContext) {
    // En un entorno sin Keycloak la estrategia `jwt` no se registra (ver `AuthModule`), y delegar
    // en passport daría un 500 por estrategia desconocida. Un recurso de usuario al que nadie
    // puede autenticarse es exactamente un 401.
    if (!isAuthConfigured()) {
      throw new UnauthorizedException('Autenticación no configurada en este entorno');
    }
    return super.canActivate(context);
  }
}
