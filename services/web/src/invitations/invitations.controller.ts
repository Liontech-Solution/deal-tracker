import { Body, Controller, Delete, Get, HttpCode, Param, ParseIntPipe, Post, UseGuards } from '@nestjs/common';

import type { AuthUser } from '../auth/auth-user.interface';
import { CurrentUser } from '../auth/current-user.decorator';
import { JwtAuthGuard } from '../auth/jwt-auth.guard';
import { AcceptInvitationDto } from './dto/accept-invitation.dto';
import { CreateInvitationDto } from './dto/create-invitation.dto';
import { InvitationsService } from './invitations.service';

/**
 * El alta por invitación (#549).
 *
 * **Este controlador está partido en dos mitades y el corte es lo importante**: las tres rutas de
 * quien invita van con `JwtAuthGuard`, como `InterestsController` y `FavoritesController`; las dos
 * del token van **sin guard**, porque quien las usa todavía no tiene cuenta — que es el sentido de
 * todo esto. Son la primera superficie pública de escritura que este servicio expone a internet, y
 * lo único que las protege es la entropía del token (ver `TOKEN_BYTES`).
 *
 * Los cinco responden `503` si el registro no está configurado; la comprobación vive en el
 * servicio para que sea por petición y no de arranque.
 */
@Controller('invitations')
export class InvitationsController {
  constructor(private readonly invitations: InvitationsService) {}

  @Post()
  @UseGuards(JwtAuthGuard)
  create(@CurrentUser() user: AuthUser, @Body() dto: CreateInvitationDto) {
    return this.invitations.create(user.id, dto);
  }

  @Get()
  @UseGuards(JwtAuthGuard)
  list(@CurrentUser() user: AuthUser) {
    return this.invitations.list(user.id);
  }

  @Delete(':id')
  @UseGuards(JwtAuthGuard)
  @HttpCode(204)
  revoke(@CurrentUser() user: AuthUser, @Param('id', ParseIntPipe) id: number) {
    return this.invitations.revoke(user.id, id);
  }

  /**
   * Qué hay detrás del enlace del correo. **Sin guard y sin sesión.** Contesta 200 siempre, con el
   * estado dentro: ver `InvitationTokenView`.
   */
  @Get('token/:token')
  describeToken(@Param('token') token: string) {
    return this.invitations.describeToken(token);
  }

  /** El alta. **Sin guard**: crea la cuenta que permitirá autenticarse. */
  @Post('token/:token/accept')
  accept(@Param('token') token: string, @Body() dto: AcceptInvitationDto) {
    return this.invitations.accept(token, dto);
  }
}
