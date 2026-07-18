import { createParamDecorator, ExecutionContext } from '@nestjs/common';

import type { AuthUser } from './auth-user.interface';

/** Extrae el `AuthUser` inyectado por `JwtStrategy` en `req.user`. */
export const CurrentUser = createParamDecorator(
  (_data: unknown, ctx: ExecutionContext): AuthUser => {
    const request = ctx.switchToHttp().getRequest<{ user: AuthUser }>();
    return request.user;
  },
);
