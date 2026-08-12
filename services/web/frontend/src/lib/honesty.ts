import type { Honesty } from '../api/types';

/**
 * Qué veredictos de honestidad se pintan como badge.
 *
 * `unverified` **no lleva**, y es el fondo de #332: significa «la tienda enseña un tachado que no
 * podemos corroborar», o sea ausencia de prueba. Un badge ahí acusaría de lo que no sabemos —que
 * es justo lo que el catálogo hacía 15.928 veces en producción, apoyándose en una media de 2,3
 * días de observación—. `none` es que no hay nada que decir.
 *
 * Es una guarda de tipo para que `HonestyBadge` siga aceptando solo los dos veredictos que sabe
 * pintar: así, si mañana aparece un quinto veredicto, el compilador obliga a decidir de qué lado
 * cae en vez de dejarlo colarse en el badge.
 */
export function llevaBadge(honesty: Honesty): honesty is 'real' | 'suspicious' {
  return honesty === 'real' || honesty === 'suspicious';
}
