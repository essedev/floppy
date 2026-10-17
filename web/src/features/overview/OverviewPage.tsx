/*
  What an admin sees on opening the site (concept-e-admin, "Panoramica"): the key reminder,
  the disks, the services as the control plane sees them, the Macs connected now and the
  latest activity. Each panel loads and fails on its own.
*/

import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { Led, Readout, SlotNote, State, Well } from '@/components/detail'
import { Faceplate } from '@/components/marks'
import { EmptyState, PageHeader, QueryView, SkeletonLines, StaleNotice } from '@/components/states'
import { fmt, useI18n } from '@/i18n'
import type { Disk, Health } from '@/lib/api'
import { useFormat } from '@/lib/format'
import { useNow } from '@/lib/hooks'
import { useCurrentSession } from '@/features/auth/hooks'
import { useRecentActivity } from '@/features/audit/hooks'
import { useDescribe } from '@/features/audit/describe'
import { DiskState } from '@/features/disks/DiskState'
import { useDisks } from '@/features/disks/hooks'
import { KeyNotice } from '@/features/key/KeyNotice'
import { useMacs } from '@/features/macs/hooks'
import { useHealth } from '@/features/settings/hooks'

const linkClass = 'inline-flex h-10 items-center gap-2 rounded-[11px] bg-ink px-4 text-sm font-semibold text-surface no-underline hover:opacity-90'

// The slot fills with the bytes the disk stores, on a log scale from 1 GB (empty) to
// 100 TB (full): a disk has no capacity, and sizes span several orders of magnitude.
const FILL_FROM = 1e9
const FILL_TO = 100e12
const usedFill = (bytes: number) =>
  bytes <= 0 ? 0 : Math.max(0.06, Math.min(1, Math.log10(Math.max(bytes, FILL_FROM) / FILL_FROM) / Math.log10(FILL_TO / FILL_FROM)))

function DiskCard({ disk }: { disk: Disk }) {
  const { t } = useI18n()
  const f = useFormat()
  const online = disk.status === 'online'
  return (
    <Well className="grid grid-cols-[auto_minmax(0,1fr)] items-center gap-[18px] max-sm:grid-cols-1">
      <div className="grid justify-items-center gap-1.5 max-sm:justify-items-start">
        <Faceplate state={disk.state === 'creating' ? 'dl' : online ? 'ok' : 'err'} fill={usedFill(disk.usedBytes)} label={fmt(t.overview.faceLabel, { name: disk.name, used: f.bytes(disk.usedBytes) })} />
        <Readout className="whitespace-nowrap">{fmt(t.overview.usedReadout, { used: f.bytes(disk.usedBytes) })}</Readout>
      </div>
      <div className="min-w-0">
        <h3 className="flex items-center gap-2.5 text-lg font-bold tracking-[-0.015em]">{disk.name}</h3>
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[13.5px] text-ink-2">
          <DiskState disk={disk} />
          <span>{disk.lastWriteAt ? fmt(t.overview.lastWrite, { time: f.relative(disk.lastWriteAt) }) : t.overview.noWrites}</span>
        </div>
        <dl className="mt-2.5 flex flex-wrap gap-x-[22px] gap-y-2">
          {[
            [f.number(disk.fileCount), t.overview.files],
            [f.number(disk.mountedBy), t.overview.macs],
          ].map(([value, label]) => (
            <div key={label} className="flex flex-col-reverse text-[12.5px] whitespace-nowrap text-ink-2">
              <dt>{label}</dt>
              <dd className="num m-0 text-xl text-ink">{value}</dd>
            </div>
          ))}
        </dl>
      </div>
    </Well>
  )
}

function Disks() {
  const { t } = useI18n()
  const disks = useDisks()
  return (
    <QueryView
      query={disks}
      staleNotice={false}
      loading={
        <div className="mb-3.5 grid gap-3.5 md:grid-cols-2">
          <Well><SkeletonLines /></Well>
          <Well><SkeletonLines /></Well>
        </div>
      }
      isEmpty={(list) => list.length === 0}
      empty={
        <Well className="mb-3.5">
          <EmptyState
            art={<Faceplate state="off" />}
            title={t.overview.noDisksTitle}
            body={t.overview.noDisksBody}
            action={<Link to="/dischi?nuovo=1" className={linkClass}>{t.disks.new}</Link>}
          />
        </Well>
      }
    >
      {(list) => (
        <div className="mb-3.5 grid gap-3.5 md:grid-cols-2">
          {list.map((d) => (
            <DiskCard key={d.id} disk={d} />
          ))}
        </div>
      )}
    </QueryView>
  )
}

function Service({ name, state, note }: { name: string; state: ReactNode; note: string }) {
  return (
    <div className="grid content-start gap-1 text-sm">
      <b className="font-semibold">{name}</b>
      {state}
      <span className="text-[13.5px] text-ink-2">{note}</span>
    </div>
  )
}

function Services() {
  const { t } = useI18n()
  const f = useFormat()
  const health = useHealth()
  const body = (h: Health) => (
    <div className="grid gap-x-7 gap-y-4 md:grid-cols-3">
      <Service
        name={t.overview.metadata}
        state={<State led={h.metadata.state === 'ok' ? 'ok' : 'err'}>{h.metadata.state === 'ok' ? fmt(t.overview.metaOk, { ms: h.metadata.latencyMs ?? 0 }) : t.overview.metaDown}</State>}
        note={fmt(t.overview.checked, { time: f.relative(h.metadata.checkedAt) })}
      />
      <Service
        name={t.overview.storage}
        state={<State led={h.storage.state === 'ok' ? 'ok' : 'err'}>{h.storage.state === 'ok' ? t.overview.storageOk : t.overview.storageFail}</State>}
        note={h.storage.lastProbeAt ? fmt(t.overview.probeAt, { time: f.relative(h.storage.lastProbeAt) }) : t.overview.neverProbed}
      />
      <Service
        name={t.overview.mail}
        state={
          <State led={h.mail.state === 'ok' ? 'ok' : h.mail.state === 'failing' ? 'err' : 'off'}>
            {h.mail.state === 'ok' ? t.overview.mailOk : h.mail.state === 'failing' ? t.overview.mailFail : t.overview.mailUnconfigured}
          </State>
        }
        note={h.mail.lastSentAt ? fmt(t.overview.lastSent, { time: f.relative(h.mail.lastSentAt) }) : t.overview.neverSent}
      />
    </div>
  )
  return (
    <Well title={t.overview.servicesTitle} className="mb-3.5">
      <QueryView query={health} loading={<SkeletonLines />} staleNotice={false}>
        {body}
      </QueryView>
      <SlotNote className="mt-4 rounded-xl bg-sunken px-3.5 py-3 text-[13.5px] text-ink-2">{t.overview.offPath}</SlotNote>
    </Well>
  )
}

function ConnectedMacs() {
  const { t } = useI18n()
  const f = useFormat()
  const macs = useMacs()
  useNow(30_000)
  const live = macs.data?.filter((m) => !m.revokedAt) ?? []
  const connected = live.filter((m) => m.connected).length
  return (
    <Well title={macs.data ? fmt(t.overview.connectedTitle, { n: connected, total: live.length }) : t.nav.macs} className="flex flex-col">
      <QueryView query={macs} staleNotice={false} loading={<SkeletonLines lines={5} />} isEmpty={() => live.length === 0} empty={<p className="text-sm text-ink-2">{t.overview.noneConnected}</p>}>
        {() => (
          <ul className="m-0 grid list-none gap-[13px] p-0 text-sm">
            {live.map((m) => (
              <li key={m.id} className="flex items-center gap-2.5">
                <Led state={m.connected ? 'ok' : 'off'} />
                <span className="min-w-0 truncate">{m.name}</span>
                <span className="num ml-auto pl-3 text-xs whitespace-nowrap text-ink-2">{m.lastSeenAt ? f.relative(m.lastSeenAt) : t.common.never}</span>
              </li>
            ))}
          </ul>
        )}
      </QueryView>
    </Well>
  )
}

function Activity() {
  const { t } = useI18n()
  const f = useFormat()
  const describe = useDescribe()
  const recent = useRecentActivity()
  return (
    <Well title={t.overview.activityTitle} className="flex flex-col">
      <QueryView query={recent} staleNotice={false} loading={<SkeletonLines lines={5} />} isEmpty={(page) => page.items.length === 0} empty={<p className="text-sm text-ink-2">{t.overview.noActivity}</p>}>
        {(page) => (
          <ol className="m-0 grid list-none p-0 text-sm">
            {page.items.map((e, i) => (
              <li key={e.id} className={`grid grid-cols-[minmax(84px,auto)_1fr] gap-3 py-[9px] ${i === 0 ? 'pt-0' : 'border-t border-line'}`}>
                <time dateTime={e.at} className="num pt-0.5 text-xs whitespace-nowrap text-ink-2">
                  {f.relative(e.at)}
                </time>
                <span>{describe.sentence(e)}</span>
              </li>
            ))}
          </ol>
        )}
      </QueryView>
      <Link to="/registro" className="mt-auto pt-3 text-[13px] text-ink-2 underline-offset-2 hover:text-ink hover:underline">
        <Readout>{t.overview.allActivity}</Readout>
      </Link>
    </Well>
  )
}

/** One stale notice for the page when any panel keeps old data after a failed refresh. */
function OverviewStale() {
  const queries = [useDisks(), useHealth(), useMacs(), useRecentActivity()]
  const failed = queries.filter((q) => q.isError && q.data !== undefined)
  if (failed.length === 0) return null
  return <StaleNotice updatedAt={Math.min(...failed.map((q) => q.dataUpdatedAt))} onRetry={() => failed.forEach((q) => void q.refetch())} />
}

/** The overview page. */
export function OverviewPage() {
  const { t } = useI18n()
  const session = useCurrentSession()
  return (
    <>
      <PageHeader title={t.overview.title} lead={session.organization.name} />
      <OverviewStale />
      <KeyNotice />
      <Disks />
      <Services />
      <div className="grid gap-3.5 lg:grid-cols-2">
        <ConnectedMacs />
        <Activity />
      </div>
    </>
  )
}
