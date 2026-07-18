import {
  Body,
  Controller,
  Delete,
  Get,
  HttpCode,
  Param,
  ParseIntPipe,
  Post,
  UseGuards,
} from '@nestjs/common';

import type { AuthUser } from '../auth/auth-user.interface';
import { CurrentUser } from '../auth/current-user.decorator';
import { JwtAuthGuard } from '../auth/jwt-auth.guard';
import { CreateInterestDto } from './dto/create-interest.dto';
import { InterestsService } from './interests.service';

/** Gestión de intereses del usuario autenticado. Todo el módulo requiere JWT de Keycloak. */
@Controller('interests')
@UseGuards(JwtAuthGuard)
export class InterestsController {
  constructor(private readonly interests: InterestsService) {}

  @Get()
  list(@CurrentUser() user: AuthUser) {
    return this.interests.list(user.id);
  }

  @Post()
  create(@CurrentUser() user: AuthUser, @Body() dto: CreateInterestDto) {
    return this.interests.create(user.id, dto);
  }

  @Delete(':id')
  @HttpCode(204)
  remove(@CurrentUser() user: AuthUser, @Param('id', ParseIntPipe) id: number) {
    return this.interests.remove(user.id, id);
  }
}
