import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createBrowserRouter, RouterProvider } from 'react-router-dom';

import { AuthProvider } from './auth/AuthProvider';
import { Layout } from './components/Layout';
import { RequireSession } from './components/RequireSession';
import { ThemeProvider } from './components/ThemeProvider';
import { ToastProvider } from './components/Toast';
import { AccessPage } from './pages/AccessPage';
import { CatalogPage } from './pages/CatalogPage';
import { FavoritesPage } from './pages/FavoritesPage';
import { HomePage } from './pages/HomePage';
import { InterestsPage } from './pages/InterestsPage';
import { ProductPage } from './pages/ProductPage';
import { RegisterPage } from './pages/RegisterPage';
import { SettingsPage } from './pages/SettingsPage';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 30_000 },
  },
});

const router = createBrowserRouter([
  {
    path: '/',
    element: <Layout />,
    children: [
      { index: true, element: <HomePage /> },
      // Las dos rutas que enseñan catálogo van tras el candado de #309. `/seguimientos`,
      // `/favoritos` y `/ajustes` no lo necesitan: ya resuelven la sesión dentro de la página, y
      // subirlas aquí significaría borrar esas ramas, que es trabajo de #302.
      {
        path: 'catalogo',
        element: (
          <RequireSession>
            <CatalogPage />
          </RequireSession>
        ),
      },
      {
        path: 'producto/:id',
        element: (
          <RequireSession>
            <ProductPage />
          </RequireSession>
        ),
      },
      { path: 'acceso', element: <AccessPage /> },
      // El alta por invitación (#550). Pública y **fuera de `RequireSession`** por definición:
      // quien abre este enlace desde su correo todavía no tiene cuenta.
      { path: 'registro', element: <RegisterPage /> },
      { path: 'seguimientos', element: <InterestsPage /> },
      { path: 'favoritos', element: <FavoritesPage /> },
      { path: 'ajustes', element: <SettingsPage /> },
    ],
  },
]);

export function App() {
  return (
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <ToastProvider>
            <RouterProvider router={router} />
          </ToastProvider>
        </AuthProvider>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
