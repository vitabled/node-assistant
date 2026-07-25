import '@mantine/core/styles.css';
import './styles/global.css';
import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { createBrowserRouter, RouterProvider } from 'react-router-dom';
import { App } from './app/App';
import { nodeFlowTheme } from './theme/theme';
import { initialiseNodeFlowAppearance } from './lib/appearance';

const demoAppearance = new URLSearchParams(window.location.search).get('demo') === '1'
  || import.meta.env.VITE_NODEFLOW_DEMO === 'true';
initialiseNodeFlowAppearance(demoAppearance);

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { refetchOnWindowFocus: false, gcTime: 5 * 60_000 },
  },
});
const router = createBrowserRouter([{ path: '*', element: <App /> }]);

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <MantineProvider theme={nodeFlowTheme} defaultColorScheme="dark">
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </MantineProvider>
  </StrictMode>,
);
