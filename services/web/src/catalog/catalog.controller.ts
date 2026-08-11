import { Controller, Get, Param, ParseIntPipe, Query, UseGuards } from '@nestjs/common';

import { CatalogAuthGuard } from '../auth/catalog-auth.guard';
import { CatalogService } from './catalog.service';
import { FacetQueryDto } from './dto/facet-query.dto';
import { ProductQueryDto } from './dto/product-query.dto';

/**
 * Catálogo de solo lectura. **Exige sesión** desde v0.3.0 (#309): sin cuenta no se ve ni un
 * producto ni una tienda. Hasta entonces era público a propósito, y el cambio revierte esa
 * decisión a sabiendas — está escrito en la issue para que no se lea como una regresión.
 *
 * El candado es condicional: `CatalogAuthGuard` deja pasar en un entorno sin Keycloak, que es
 * como corre `dev`. `GET /api/config` y `GET /health` siguen públicos, y tienen que seguirlo:
 * el navegador necesita el primero *antes* de poder autenticarse.
 */
@UseGuards(CatalogAuthGuard)
@Controller('catalog')
export class CatalogController {
  constructor(private readonly catalog: CatalogService) {}

  @Get('products')
  listProducts(@Query() query: ProductQueryDto) {
    return this.catalog.listProducts(query);
  }

  @Get('products/:id')
  getProduct(@Param('id', ParseIntPipe) id: number) {
    return this.catalog.getProduct(id);
  }

  @Get('variants/:id/price-history')
  getPriceHistory(@Param('id', ParseIntPipe) id: number) {
    return this.catalog.getPriceHistory(id);
  }

  @Get('facets')
  getFacets(@Query() query: FacetQueryDto) {
    return this.catalog.getFacets(query.barefoot, query.section ?? null, query.deportiva ?? false);
  }
}
