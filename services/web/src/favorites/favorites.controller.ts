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
import { CreateFavoriteDto } from './dto/create-favorite.dto';
import { FavoritesService } from './favorites.service';

/** Lista de favoritos del usuario autenticado. Todo el módulo requiere JWT de Keycloak. */
@Controller('favorites')
@UseGuards(JwtAuthGuard)
export class FavoritesController {
  constructor(private readonly favorites: FavoritesService) {}

  @Get()
  list(@CurrentUser() user: AuthUser) {
    return this.favorites.list(user.id);
  }

  @Post()
  create(@CurrentUser() user: AuthUser, @Body() dto: CreateFavoriteDto) {
    return this.favorites.create(user.id, dto);
  }

  /**
   * El parámetro es el **producto**, no el id de la fila de favorito: quien pulsa el corazón sabe
   * qué prenda está mirando y no tiene por qué haberse traído antes su lista entera para saber a
   * qué fila corresponde.
   */
  @Delete(':productId')
  @HttpCode(204)
  remove(@CurrentUser() user: AuthUser, @Param('productId', ParseIntPipe) productId: number) {
    return this.favorites.remove(user.id, productId);
  }
}
