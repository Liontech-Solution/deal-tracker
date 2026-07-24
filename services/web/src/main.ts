import { Logger, ValidationPipe } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { NestFactory } from '@nestjs/core';

import { AppModule } from './app.module';
import { isAuthConfigured } from './config/configuration';
import type { EnvConfig } from './config/configuration';

async function bootstrap(): Promise<void> {
  const app = await NestFactory.create(AppModule);
  app.setGlobalPrefix('api');
  app.useGlobalPipes(
    new ValidationPipe({
      transform: true,
      whitelist: true,
      forbidNonWhitelisted: true,
      transformOptions: { enableImplicitConversion: false },
    }),
  );
  app.enableShutdownHooks();

  // Que la auth esté apagada es una decisión válida por entorno, pero nunca debería sorprender:
  // se deja dicho en el arranque para no depurar a ciegas un 401 inesperado.
  if (!isAuthConfigured()) {
    new Logger('Bootstrap').warn(
      'Keycloak no configurado (sin KEYCLOAK_ISSUER_URL): auth deshabilitada. ' +
        'La SPA funciona como catálogo público y los endpoints de usuario responden 401.',
    );
  }

  const config = app.get(ConfigService<EnvConfig, true>);
  const port = config.get('PORT', { infer: true });
  await app.listen(port);
}

void bootstrap();
