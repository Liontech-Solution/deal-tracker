import { Controller, Delete, Get, HttpCode, Post, UseGuards } from '@nestjs/common';

import type { AuthUser } from '../auth/auth-user.interface';
import { CurrentUser } from '../auth/current-user.decorator';
import { JwtAuthGuard } from '../auth/jwt-auth.guard';
import { SettingsService } from './settings.service';

/** Ajustes del usuario autenticado. Por ahora, el vínculo con Telegram. Requiere JWT de Keycloak. */
@Controller('settings')
@UseGuards(JwtAuthGuard)
export class SettingsController {
  constructor(private readonly settings: SettingsService) {}

  @Get('telegram')
  status(@CurrentUser() user: AuthUser) {
    return this.settings.getTelegramStatus(user.id);
  }

  @Post('telegram/link')
  link(@CurrentUser() user: AuthUser) {
    return this.settings.startTelegramLink(user.id);
  }

  @Delete('telegram')
  @HttpCode(204)
  unlink(@CurrentUser() user: AuthUser) {
    return this.settings.unlinkTelegram(user.id);
  }
}
