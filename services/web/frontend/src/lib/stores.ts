/** Color de marca por tienda (punto identificativo del `StoreBadge`). Del diseño. */
const BY_NAME: Record<string, string> = {
  zara: '#2e2a24',
  'mango kids': '#c79a3e',
  mango: '#c79a3e',
  sfera: '#8fa07e',
  'h&m': '#c4694a',
  hm: '#c4694a',
  'springfield kids': '#657558',
  springfield: '#657558',
  'c&a': '#9daeb8',
  hipercor: '#b4674b',
  lefties: '#a79e92',
};

export function storeColor(nameOrSlug: string): string {
  return BY_NAME[nameOrSlug.trim().toLowerCase()] ?? 'var(--ink-500)';
}
