import { Module } from '@nestjs/common';

import { AuthModule } from '../auth/auth.module';
import { EmailModule } from '../email/email.module';
import { KeycloakAdminModule } from '../keycloak-admin/keycloak-admin.module';
import { InvitationsController } from './invitations.controller';
import { InvitationsService } from './invitations.service';

/**
 * El alta por invitación. Es el único sitio del servicio que importa los dos módulos que la v0.8.0
 * estrenó —`EmailModule` (#547) y `KeycloakAdminModule` (#548)— porque es el único que manda un
 * correo y el único que escribe en Keycloak. `DatabaseModule` es `@Global` y no se importa.
 */
@Module({
  imports: [AuthModule, EmailModule, KeycloakAdminModule],
  controllers: [InvitationsController],
  providers: [InvitationsService],
})
export class InvitationsModule {}
