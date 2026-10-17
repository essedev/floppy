/* Entry point: tokens first, then Tailwind; the query client, the language, the router. */

import '@design/tokens.css'
import './styles/index.css'

import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { RouterProvider } from 'react-router/dom'
import { I18nProvider } from '@/i18n/I18nProvider'
import { isApiError } from '@/lib/api'
import { queryKeys } from '@/lib/api/keys'
import { applySavedTheme } from '@/lib/theme'
import { router } from '@/features/app/router'

applySavedTheme()

// A 401 anywhere means the session is gone: forget it, and the guard sends to sign-in.
const onError = (error: unknown) => {
  if (isApiError(error, 'unauthorized')) client.setQueryData(queryKeys.session, null)
}

const client: QueryClient = new QueryClient({
  queryCache: new QueryCache({ onError }),
  mutationCache: new MutationCache({ onError }),
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      // Retry once, and only what a retry can fix: the network, not a 4xx.
      retry: (count, error) => count < 1 && (!isApiError(error) || error.status === 0 || error.status >= 500),
    },
  },
})

const root = document.getElementById('root')
if (!root) throw new Error('No #root element')

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={client}>
      <I18nProvider>
        <RouterProvider router={router} />
      </I18nProvider>
    </QueryClientProvider>
  </StrictMode>,
)
