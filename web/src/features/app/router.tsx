/* Routes of the admin web, with Italian paths as the concept draws them. The mail links point
 * at /accedi/link and /invito (server/src/floppy_server/mailtext.py). */

import { createBrowserRouter, Navigate } from 'react-router'
import { AccessPage } from '@/features/access/AccessPage'
import { AuditPage } from '@/features/audit/AuditPage'
import { ClaudePage } from '@/features/claude/ClaudePage'
import { ConsentPage } from '@/features/claude/ConsentPage'
import { InvitationPage } from '@/features/auth/InvitationPage'
import { MagicLinkPage } from '@/features/auth/MagicLinkPage'
import { SignInPage } from '@/features/auth/SignInPage'
import { DisksPage } from '@/features/disks/DisksPage'
import { KeyPage } from '@/features/key/KeyPage'
import { DevicesPage } from '@/features/devices/DevicesPage'
import { PeoplePage } from '@/features/people/PeoplePage'
import { PersonPage } from '@/features/people/PersonPage'
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
  { path: '/invito', element: <InvitationPage /> },
  {
    element: <RequireSession />,
    children: [
      // An MCP client's consent (/authorize sends the browser here), outside the shell.
      { path: 'autorizza', element: <ConsentPage /> },
      {
        element: <AppShell />,
        children: [
          { index: true, element: <Home /> },
          { path: 'dispositivi', element: <DevicesPage /> },
          { path: 'claude', element: <ClaudePage /> },
          // The old address, kept for bookmarks.
          { path: 'mac', element: <Navigate to="/dispositivi" replace /> },
          {
            element: <RequireAdmin />,
            children: [
              { path: 'persone', element: <PeoplePage /> },
              { path: 'persone/:personId', element: <PersonPage /> },
              { path: 'accessi', element: <AccessPage /> },
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
