import { Module } from '@nestjs/common';
import type { Provider } from '@nestjs/common';
import { PassportModule } from '@nestjs/passport';

import { isAuthConfigured } from '../config/configuration';
import { JwtStrategy } from './jwt.strategy';
import { UserService } from './user.service';

/**
 * La estrategia solo se registra si el entorno trae Keycloak. Sin issuer no hay JWKS que
 * consultar, así que instanciarla sería montar un validador contra una URL inexistente; en su
 * lugar `JwtAuthGuard` responde 401 directamente.
 */
function jwtStrategy(): Provider[] {
  return isAuthConfigured() ? [JwtStrategy] : [];
}

/** Auth por JWT de Keycloak (resource server) + aprovisionamiento JIT de usuarios. */
@Module({
  imports: [PassportModule],
  providers: [...jwtStrategy(), UserService],
  exports: [UserService],
})
export class AuthModule {}
