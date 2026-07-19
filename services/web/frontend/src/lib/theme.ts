/** Tema claro/oscuro: `data-theme` en <html>, persistido en localStorage. */
export type Theme = 'light' | 'dark';

const KEY = 'dt-theme';

export function getInitialTheme(): Theme {
  const stored = localStorage.getItem(KEY);
  if (stored === 'light' || stored === 'dark') return stored;
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

export function applyTheme(theme: Theme): void {
  document.documentElement.setAttribute('data-theme', theme);
  localStorage.setItem(KEY, theme);
}
