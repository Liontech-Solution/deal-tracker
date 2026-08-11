import { Injectable } from '@nestjs/common';
import type { ExecutionContext } from '@nestjs/common';
import { AuthGuard } from '@nestjs/passport';

import { isAuthConfigured } from '../config/configuration';

/**
 * Exige sesión para el catálogo, pero **solo si el entorno trae Keycloak** (#309).
 *
 * Es la regla simétrica a la de `JwtAuthGuard`, y por eso es un guard aparte y no una
 * reutilización: aquel devuelve 401 cuando *no* hay auth configurada, porque un recurso de usuario
 * al que nadie puede autenticarse es exactamente un 401. Aquí no: el catálogo sin Keycloak es el
 * catálogo público de siempre.
 *
 * El motivo de que la condición exista es el overlay de `dev`, que borra las `KEYCLOAK_*` a
 * propósito (#23). Un candado incondicional dejaría ese entorno sin nada visible. Consecuencia
 * asumida: **el candado solo se ejerce de verdad en QA y prod**, nunca en dev, así que un dev verde
 * no dice nada sobre el acceso.
 */
@Injectable()
export class CatalogAuthGuard extends AuthGuard('jwt') {
  canActivate(context: ExecutionContext) {
    if (!isAuthConfigured()) {
      return true;
    }
    return super.canActivate(context);
  }
}
