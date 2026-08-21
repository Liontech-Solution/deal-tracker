import { Module } from '@nestjs/common';

import { EmailApiClient } from './email-api.client';

/**
 * Correo saliente. Sin controladores ni estado: solo el cliente HTTP de Resend, que se exporta para
 * que lo use quien mande un correo — hoy el alta por invitación (#549). La plantilla
 * (`invitation.template.ts`) es una función pura y no necesita pasar por el inyector.
 */
@Module({
  providers: [EmailApiClient],
  exports: [EmailApiClient],
})
export class EmailModule {}
