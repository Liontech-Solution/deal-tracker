import { Controller, Get, Param, ParseIntPipe, Query } from '@nestjs/common';

import { CatalogService } from './catalog.service';
import { FacetQueryDto } from './dto/facet-query.dto';
import { ProductQueryDto } from './dto/product-query.dto';

/** Catálogo de solo lectura. Público (browsing sin login); los intereses sí piden auth. */
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
