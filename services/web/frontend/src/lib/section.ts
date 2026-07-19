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
