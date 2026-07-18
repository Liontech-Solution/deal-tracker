import { Injectable } from '@nestjs/common';
import { AuthGuard } from '@nestjs/passport';

/** Exige un JWT válido de Keycloak. Protege los endpoints de intereses. */
@Injectable()
export class JwtAuthGuard extends AuthGuard('jwt') {}
