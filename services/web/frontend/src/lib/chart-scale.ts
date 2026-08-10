/**
 * La escala vertical de la gráfica de histórico (#236).
 *
 * Vive aquí y no en `PriceHistoryChart.tsx` por el mismo motivo que `variants.ts`: de `frontend/`
 * el `vitest.config.ts` solo recoge helpers puros, así que esto es lo único de la gráfica que se
 * puede cubrir con test. Lo que queda en el componente es dibujar.
 */

/** Los multiplicadores que dan una marca «redonda» en euros. El 2,5 saca los pasos de 25 y 250. */
const PASOS = [1, 2, 2.5, 5];

/**
 * Marcas «redondas» dentro de [min, max], sin salirse por ningún extremo.
 *
 * El dominio de esta gráfica está RECORTADO a los datos (no arranca en 0), así que las marcas no
 * pueden calcularse desde cero como en un eje normal: se busca el paso redondo cuyo número de
 * marcas más se acerque a `objetivo`, y se emiten solo las que caen dentro del dominio.
 *
 * A igualdad de cercanía gana el paso MAYOR, que da menos marcas y más redondas — en un espacio de
 * 200 px de alto, tres marcas legibles valen más que seis apretadas.
 */
export function marcasRedondas(min: number, max: number, objetivo = 4): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max) || max <= min) return [];

  const span = max - min;
  const magnitud = Math.pow(10, Math.floor(Math.log10(span / objetivo)));

  let mejor: { paso: number; marcas: number[] } | null = null;
  // Tres órdenes de magnitud alrededor del tanteo cubren de sobra el rango de precios de una
  // prenda (de céntimos a cientos de euros) sin depender de que el tanteo caiga fino.
  for (const escala of [magnitud / 10, magnitud, magnitud * 10]) {
    for (const m of PASOS) {
      const paso = m * escala;
      if (paso <= 0) continue;
      const marcas = marcasCon(min, max, paso);
      if (marcas.length < 2) continue;
      if (
        mejor === null ||
        Math.abs(marcas.length - objetivo) < Math.abs(mejor.marcas.length - objetivo) ||
        // El empate lo rompe el paso mayor: menos marcas y más redondas.
        (Math.abs(marcas.length - objetivo) === Math.abs(mejor.marcas.length - objetivo) &&
          paso > mejor.paso)
      ) {
        mejor = { paso, marcas };
      }
    }
  }

  return mejor?.marcas ?? [];
}

/** Las marcas de un paso concreto que caen dentro del dominio, ya redondeadas al céntimo. */
function marcasCon(min: number, max: number, paso: number): number[] {
  // Los múltiplos de `paso` se generan por índice y no acumulando, que es lo que evita que el error
  // en coma flotante se vaya sumando marca a marca (0,1 + 0,1 + 0,1 ≠ 0,3).
  const primera = Math.ceil(min / paso);
  const ultima = Math.floor(max / paso);
  if (ultima < primera) return [];
  // Un paso absurdamente pequeño llenaría el eje de marcas: se descarta antes de construirlas.
  if (ultima - primera > 40) return [];

  const marcas: number[] = [];
  for (let i = primera; i <= ultima; i++) {
    marcas.push(Math.round(i * paso * 100) / 100);
  }
  return marcas;
}
