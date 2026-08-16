/**
 * Las dos secciones del catálogo, en el orden en que se ofrecen.
 *
 * Estaba escrita dos veces —`SECCIONES` en el panel y `SECTION_NAV` en la cabecera—, con los mismos
 * dos valores y las mismas dos etiquetas. Vive aquí porque el slug y su nombre son **una sola cosa**
 * y la tenían copiada los tres controles del mismo eje (#434).
 *
 * No sale de la faceta `sections`, que existe y nadie consume: el backend la devuelve sin acotar a
 * propósito porque es el eje de navegación, así que las dos pestañas se ofrecen **siempre** —unas
 * que aparecieran y desaparecieran según lo filtrado serían una trampa— y no hay nada que la red
 * pueda añadir a una lista de dos.
 */
export const SECCIONES: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'ropa', label: 'Ropa' },
  { value: 'zapateria', label: 'Zapatería' },
];

/**
 * El nombre de la sección tal y como se escribe, para quien tenga el slug suelto.
 *
 * El slug es `zapateria` **sin tilde** —es un identificador, no un texto—, así que capitalizarlo
 * pinta «Zapateria». Salía así en el chip activo del catálogo desde la v0.1.9.
 */
export function etiquetaSeccion(slug: string): string {
  return SECCIONES.find((s) => s.value === slug)?.label ?? slug.charAt(0).toUpperCase() + slug.slice(1);
}

/** Color de fondo del placeholder de imagen según la sección (patrón diagonal del diseño). */
export function sectionBg(section: string | null): string {
  if (section === 'zapateria') return 'var(--sage-200)';
  if (section === 'ropa') return 'var(--sand-300)';
  return 'var(--clay-400)';
}

/** Fondo diagonal a rayas que usa el diseño para los placeholders de foto. */
export function stripeBg(base: string, step = 14): string {
  return `repeating-linear-gradient(-45deg, ${base} 0 ${step}px, var(--sand-100) ${step}px ${step * 2}px)`;
}
