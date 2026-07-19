import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createBrowserRouter, RouterProvider } from 'react-router-dom';

import { AuthProvider } from './auth/AuthProvider';
import { Layout } from './components/Layout';
import { ThemeProvider } from './components/ThemeProvider';
import { ToastProvider } from './components/Toast';
import { CatalogPage } from './pages/CatalogPage';
import { HomePage } from './pages/HomePage';
import { InterestsPage } from './pages/InterestsPage';
import { ProductPage } from './pages/ProductPage';

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
      { path: 'catalogo', element: <CatalogPage /> },
      { path: 'producto/:id', element: <ProductPage /> },
      { path: 'seguimientos', element: <InterestsPage /> },
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
