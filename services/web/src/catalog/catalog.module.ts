import { Module } from '@nestjs/common';

import { AuthModule } from '../auth/auth.module';
import { CatalogController } from './catalog.controller';
import { CatalogService } from './catalog.service';

// `AuthModule` entra porque el catálogo ya pide sesión (#309): trae `PassportModule` y, cuando el
// entorno tiene Keycloak, la estrategia `jwt` que `CatalogAuthGuard` usa.
@Module({
  imports: [AuthModule],
  controllers: [CatalogController],
  providers: [CatalogService],
})
export class CatalogModule {}
