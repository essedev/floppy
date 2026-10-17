/* Routes of the admin web, with Italian paths as the concept draws them. */

import { createBrowserRouter, Navigate } from 'react-router'
import { AuditPage } from '@/features/audit/AuditPage'
import { MagicLinkPage } from '@/features/auth/MagicLinkPage'
import { SignInPage } from '@/features/auth/SignInPage'
import { DisksPage } from '@/features/disks/DisksPage'
import { KeyPage } from '@/features/key/KeyPage'
import { DevicesPage } from '@/features/devices/DevicesPage'
import { PeoplePage } from '@/features/people/PeoplePage'
import { SettingsPage } from '@/features/settings/SettingsPage'
import { AppShell } from '@/features/shell/AppShell'
import { RedirectIfSignedIn, RequireAdmin, RequireSession } from './guards'
import { Home, NotFound } from './pages'

/** The router. */
export const router = createBrowserRouter([
  {
    element: <RedirectIfSignedIn />,
    children: [{ path: '/accedi', element: <SignInPage /> }],
  },
  { path: '/accedi/link', element: <MagicLinkPage /> },
  {
    element: <RequireSession />,
    children: [
      {
        element: <AppShell />,
        children: [
          { index: true, element: <Home /> },
          { path: 'dispositivi', element: <DevicesPage /> },
          // The old address, kept for bookmarks.
          { path: 'mac', element: <Navigate to="/dispositivi" replace /> },
          {
            element: <RequireAdmin />,
            children: [
              { path: 'persone', element: <PeoplePage /> },
              { path: 'dischi', element: <DisksPage /> },
              { path: 'chiave', element: <KeyPage /> },
              { path: 'registro', element: <AuditPage /> },
              { path: 'impostazioni', element: <SettingsPage /> },
            ],
          },
          { path: '*', element: <NotFound /> },
        ],
      },
    ],
  },
])
