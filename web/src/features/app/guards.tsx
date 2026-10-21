/*
  Route guards: signed-in pages need a session, admin pages an admin. A member who opens an
  admin address lands on their start page (DECISIONS #12).
*/

import { Navigate, Outlet, useLocation } from 'react-router'
import { State } from '@/components/detail'
import { Wordmark } from '@/components/marks'
import { ErrorPanel } from '@/components/states'
import { useI18n } from '@/i18n'
import { useSession } from '@/features/auth/hooks'
import { FakeBanner } from '@/features/shell/FakeBanner'

function Splash() {
  const { t } = useI18n()
  return (
    <div className="grid min-h-dvh place-items-center" aria-busy="true">
      <div className="grid justify-items-center gap-4">
        <Wordmark size={24} />
        <State led="dl" blink>
          {t.states.sessionLoading}
        </State>
      </div>
    </div>
  )
}

/** Renders its routes only with a session; otherwise sends to sign-in and back. */
export function RequireSession() {
  const session = useSession()
  const location = useLocation()
  if (session.data === undefined) {
    if (session.isError) {
      return (
        <div className="flex min-h-dvh flex-col">
          <FakeBanner />
          <main className="mx-auto grid w-full max-w-[560px] flex-1 content-center gap-6 px-4">
            <Wordmark size={24} />
            <ErrorPanel error={session.error} onRetry={() => void session.refetch()} />
          </main>
        </div>
      )
    }
    return <Splash />
  }
  if (session.data === null) return <Navigate to="/accedi" replace state={{ from: location.pathname + location.search }} />
  return <Outlet />
}

/** Renders its routes for admins; members go to their start page. */
export function RequireAdmin() {
  const session = useSession()
  if (session.data?.role !== 'admin') return <Navigate to="/" replace />
  return <Outlet />
}

/** The sign-in pages, skipped when already signed in. */
export function RedirectIfSignedIn() {
  const session = useSession()
  if (session.data) return <Navigate to="/" replace />
  return <Outlet />
}
