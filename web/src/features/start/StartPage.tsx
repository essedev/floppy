/*
  A member's first page (DECISIONS #12): how to install the app until one of their Macs is
  connected, then a line saying where it is with the steps folded away; and the disks they
  see, which is what they will find in the Finder.
*/

import { Link } from 'react-router'
import { Readout, State, Well } from '@/components/detail'
import { Faceplate } from '@/components/marks'
import { EmptyState, PageHeader, QueryView, SkeletonLines } from '@/components/states'
import { fmt, useI18n } from '@/i18n'
import { useCurrentSession } from '@/features/auth/hooks'
import { useDevices } from '@/features/devices/hooks'
import { InstallSteps } from './InstallSteps'
import { ownLive, useMyDisks } from './hooks'

/** The install steps, or once a Mac is connected the line that says so and the steps folded. */
export function Install({ title }: { title: string }) {
  const { t } = useI18n()
  const session = useCurrentSession()
  const devices = useDevices()
  const own = ownLive(devices.data, session.user.id)
  if (devices.isPending) {
    return (
      <Well title={title} className="mb-3.5">
        <SkeletonLines />
      </Well>
    )
  }
  if (own.length === 0) {
    return (
      <Well title={title} className="mb-3.5">
        <InstallSteps />
      </Well>
    )
  }
  return (
    <Well title={title} className="mb-3.5">
      <div className="grid gap-3">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
          <State led="ok">{t.start.installedState}</State>
          <span>{fmt(t.start.installedOn, { devices: own.map((d) => d.name).join(', ') })}</span>
          <Link to="/dispositivi" className="text-[13px] text-ink-2 underline-offset-2 hover:text-ink hover:underline">
            <Readout>{t.start.seeDevices}</Readout>
          </Link>
        </div>
        <details className="group">
          <summary className="cursor-pointer text-[13.5px] font-semibold text-ink-2 hover:text-ink">{t.start.otherMac}</summary>
          <div className="mt-4">
            <InstallSteps />
          </div>
        </details>
      </div>
    </Well>
  )
}

function MyDisks() {
  const { t } = useI18n()
  const session = useCurrentSession()
  const disks = useMyDisks()
  return (
    <Well title={t.start.disksTitle}>
      <QueryView
        query={disks}
        staleNotice={false}
        loading={<SkeletonLines />}
        isEmpty={(list) => list.length === 0}
        empty={<EmptyState art={<Faceplate state="off" />} title={t.start.noDisksTitle} body={fmt(t.start.noDisksBody, { org: session.organization.name })} />}
      >
        {(list) => (
          <>
            <p className="mb-3 text-[13.5px] text-ink-2">{t.start.disksLead}</p>
            <ul className="m-0 grid list-none gap-2.5 p-0">
              {list.map((d) => (
                <li key={d.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl bg-sunken px-4 py-3">
                  <b className="min-w-0 truncate text-[15px] font-semibold">{d.name}</b>
                  <span className="num text-xs text-ink-3">{fmt(t.start.path, { name: d.name })}</span>
                  <span className="ml-auto text-[13.5px] text-ink-2">{d.access === 'write' ? t.start.write : t.start.read}</span>
                </li>
              ))}
            </ul>
          </>
        )}
      </QueryView>
    </Well>
  )
}

/** The start page. */
export function StartPage() {
  const { t } = useI18n()
  const session = useCurrentSession()
  return (
    <>
      <PageHeader title={t.start.title} lead={fmt(t.start.lead, { org: session.organization.name })} />
      <Install title={t.start.installTitle} />
      <MyDisks />
    </>
  )
}
