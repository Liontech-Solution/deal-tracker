import { Controller, Get } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';

import type { EnvConfig } from './configuration';
import { buildPublicConfig, type PublicAuthConfig } from './public-config';

/**
 * `GET /api/config` — config OIDC que la SPA lee **en runtime** al arrancar, en vez de recibirla
 * horneada en el build. Público a propósito (sin `JwtAuthGuard`): el navegador lo necesita
 * *antes* de poder autenticarse, y no expone nada secreto — solo la URL del realm y el client-id
 * público de la SPA, que de todas formas viajan en la barra de direcciones durante el login.
 */
@Controller('config')
export class PublicConfigController {
  constructor(private readonly config: ConfigService<EnvConfig, true>) {}

  @Get()
  get(): PublicAuthConfig {
    return buildPublicConfig(
      this.config.get('KEYCLOAK_ISSUER_URL', { infer: true }),
      this.config.get('KEYCLOAK_CLIENT_ID', { infer: true }),
    );
  }
}
