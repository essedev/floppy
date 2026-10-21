/* The two small pages of the router: the home page by role and "not found". */

import { Link } from 'react-router'
import { EmptyState } from '@/components/states'
import { useI18n } from '@/i18n'
import { useSession } from '@/features/auth/hooks'
import { OverviewPage } from '@/features/overview/OverviewPage'
import { StartPage } from '@/features/start/StartPage'

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

/** The overview for admins; for members the start page, with the install steps and their disks. */
export function Home() {
  const session = useSession()
  return session.data?.role === 'admin' ? <OverviewPage /> : <StartPage />
}
