import { Module } from '@nestjs/common';

import { KeycloakAdminClient } from './keycloak-admin.client';

/**
 * Administración de Keycloak. Sin controladores: la única entrada es el alta por invitación (#549),
 * que importa este módulo para crear la cuenta. Separado de `AuthModule` a propósito — aquél
 * *valida* tokens con el client público de la SPA, éste *escribe* con un client confidencial
 * distinto, y confundirlos sería dar credenciales de administración a la ruta de validación.
 */
@Module({
  providers: [KeycloakAdminClient],
  exports: [KeycloakAdminClient],
})
export class KeycloakAdminModule {}
