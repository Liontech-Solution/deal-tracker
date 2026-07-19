import { Module } from '@nestjs/common';
import { PassportModule } from '@nestjs/passport';

import { JwtStrategy } from './jwt.strategy';
import { UserService } from './user.service';

/** Auth por JWT de Keycloak (resource server) + aprovisionamiento JIT de usuarios. */
@Module({
  imports: [PassportModule],
  providers: [JwtStrategy, UserService],
  exports: [UserService],
})
export class AuthModule {}
