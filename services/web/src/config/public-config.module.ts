import { Module } from '@nestjs/common';

import { PublicConfigController } from './public-config.controller';

/**
 * Expone `GET /api/config`. Se llama `PublicConfigModule` (y no `ConfigModule`) para no colisionar
 * con el de `@nestjs/config`, que `AppModule` ya importa.
 */
@Module({
  controllers: [PublicConfigController],
})
export class PublicConfigModule {}
