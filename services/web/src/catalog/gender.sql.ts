/**
 * Cómo casa el género de un producto con el que se busca. Compartido por el catálogo y el aviso.
 *
 * **No es una igualdad, y esa es toda la razón de que este fichero exista.** Un producto `unisex`
 * tiene que salir tanto al filtrar por niño como al filtrar por niña. Con `p.gender = 'niño'` a
 * secas no salía en ninguno de los dos: quedaba fuera del catálogo por los dos lados.
 *
 * No es un caso de borde. El calzado barefoot infantil se diseña unisex, y Cacles —la primera
 * tienda barefoot nativa del catálogo (#32)— publica así 342 de sus 428 referencias, con **ninguna**
 * marcada solo de niño. Filtrar por "Niño" devolvía cero productos suyos, que es justo lo contrario
 * de lo que esa tienda vino a arreglar.
 *
 * Vive en un módulo aparte, y no dentro de `catalog.service.ts`, porque la regla la comparten dos
 * sitios que no deben poder separarse: el listado del catálogo y el emparejamiento del job de
 * matching. Si el catálogo enseñara un zapato unisex bajo "Niño" y luego el aviso configurado para
 * niño no disparase con él, el usuario vería una promesa incumplida sin saber por qué. Mismo trato
 * que `matching/deal-rule.sql.ts`: la regla en un sitio y los dos consumidores mirándola.
 */

import { sql, type SQL } from 'drizzle-orm';

/** Género de lo que sirve igual para niño y para niña. Lo escriben los scrapers, no la UI. */
export const GENERO_UNISEX = 'unisex';

/**
 * Condición de coincidencia de género.
 *
 * Los dos lados son fragmentos SQL porque los dos usos tienen forma distinta: en el catálogo lo
 * buscado es un parámetro de la query string y lo comparado la columna `product.gender`; en el
 * matching lo buscado es la columna `interest.gender` (con NULL = "cualquiera") y lo comparado el
 * género del producto del lote.
 *
 * `buscado` nulo devuelve todo, que es lo que significan tanto "sin filtro" como "cualquiera".
 */
export function generoCondition(buscado: SQL, producto: SQL): SQL {
  return sql`(${buscado} IS NULL OR ${producto} = ${buscado} OR ${producto} = ${GENERO_UNISEX})`;
}
