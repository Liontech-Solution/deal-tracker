import { existsSync } from 'node:fs';
import { join } from 'node:path';

import { Module } from '@nestjs/common';
import type { DynamicModule } from '@nestjs/common';
import { ConfigModule } from '@nestjs/config';
import { ServeStaticModule } from '@nestjs/serve-static';

import { AuthModule } from './auth/auth.module';
import { CatalogModule } from './catalog/catalog.module';
import { validateEnv } from './config/configuration';
import { PublicConfigModule } from './config/public-config.module';
import { DatabaseModule } from './database/database.module';
import { EmailModule } from './email/email.module';
import { FavoritesModule } from './favorites/favorites.module';
import { HealthModule } from './health/health.module';
import { InterestsModule } from './interests/interests.module';
import { SettingsModule } from './settings/settings.module';
import { TelegramModule } from './telegram/telegram.module';

/**
 * Sirve la SPA compilada (frontend/dist) desde la misma imagen, con fallback a index.html
 * para las rutas de cliente. Se excluye `/api/*` (la API la resuelven los controladores) y solo
 * se activa si el build existe (en dev usamos el server de Vite; en test no servimos estáticos).
 */
function staticModule(): DynamicModule[] {
  const rootPath = process.env.WEB_STATIC_DIR ?? join(process.cwd(), 'frontend', 'dist');
  if (process.env.NODE_ENV === 'test' || !existsSync(join(rootPath, 'index.html'))) {
    return [];
  }
  return [
    ServeStaticModule.forRoot({
      rootPath,
      exclude: ['/api/{*path}'],
    }),
  ];
}

@Module({
  imports: [
    ConfigModule.forRoot({
      isGlobal: true,
      cache: true,
      validate: validateEnv,
    }),
    ...staticModule(),
    DatabaseModule,
    AuthModule,
    PublicConfigModule,
    CatalogModule,
    InterestsModule,
    FavoritesModule,
    SettingsModule,
    TelegramModule,
    EmailModule,
    HealthModule,
  ],
})
export class AppModule {}
