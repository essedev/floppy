/* The two small pages of the router: the home redirect and "not found". */

import { Link, Navigate } from 'react-router'
import { EmptyState } from '@/components/states'
import { useI18n } from '@/i18n'
import { useSession } from '@/features/auth/hooks'
import { OverviewPage } from '@/features/overview/OverviewPage'

/** An address that leads nowhere. */
export function NotFound() {
  const { t } = useI18n()
  return (
    <EmptyState
      title={t.notFound.title}
      body={t.notFound.body}
      action={
        <Link to="/" className="inline-flex h-10 items-center rounded-[11px] bg-sunken px-4 text-sm font-semibold text-ink no-underline">
          {t.notFound.back}
        </Link>
      }
    />
  )
}

/** The overview for admins; members have only their devices. */
export function Home() {
  const session = useSession()
  return session.data?.role === 'admin' ? <OverviewPage /> : <Navigate to="/dispositivi" replace />
}
