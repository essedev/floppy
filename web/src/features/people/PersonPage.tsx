/*
  One person: who they are, the disks they see with the access on each (editable, applied
  live), and their devices. Role, resend and removal stay on the people list.
*/

import { ArrowLeft } from 'lucide-react'
import { Link, useParams } from 'react-router'
import { Avatar, Led, Notice, Readout, State, Well } from '@/components/detail'
import { EmptyState, QueryView, SkeletonLines } from '@/components/states'
import { fmt, useI18n } from '@/i18n'
import type { Person } from '@/lib/api'
import { useFormat } from '@/lib/format'
import { useFlash, useNow } from '@/lib/hooks'
import { AccessCell } from '@/features/access/AccessControl'
import { accessOf, useAccess } from '@/features/access/hooks'
import { useDevices } from '@/features/devices/hooks'
import { useDisks } from '@/features/disks/hooks'
import { usePeople } from './hooks'

function Status({ person }: { person: Person }) {
  const { t } = useI18n()
  const now = useNow(60_000)
  const expired = person.status === 'invited' && person.inviteExpiresAt !== null && Date.parse(person.inviteExpiresAt) <= now
  if (person.status === 'active') return <State led="ok">{t.people.active}</State>
  if (person.status === 'invited') return <State led="off">{expired ? t.people.inviteExpired : t.people.invited}</State>
  return <State led="err">{t.people.removed}</State>
}

function DisksPanel({ person, onError }: { person: Person; onError(text: string): void }) {
  const { t } = useI18n()
  const disks = useDisks()
  const access = useAccess()
  const name = person.name ?? person.email
  return (
    <Well title={t.people.accessTitle}>
      {person.status === 'removed' ? (
        <p className="text-sm text-ink-2">{t.people.accessRemoved}</p>
      ) : (
        <QueryView query={disks} loading={<SkeletonLines />} isEmpty={(list) => list.length === 0} empty={<p className="text-sm text-ink-2">{t.people.inviteNoDisks}</p>}>
          {(list) => (
            <QueryView query={access} loading={<SkeletonLines />}>
              {(entries) => (
                <ul className="m-0 grid list-none p-0 text-sm">
                  {list.map((d, i) => (
                    <li key={d.id} className={`flex flex-wrap items-center justify-between gap-x-4 gap-y-1 py-2.5 ${i === 0 ? 'pt-0' : 'border-t border-line'}`}>
                      <span className="min-w-0">
                        <Link to={`/dischi?disco=${encodeURIComponent(d.id)}`} className="block font-semibold text-ink no-underline hover:underline">
                          {d.name}
                        </Link>
                        <span className="num text-xs text-ink-3">{d.mountPath}</span>
                      </span>
                      <AccessCell entry={accessOf(entries, d.id, person.id)} person={name} disk={d.name} onError={onError} align="right" withText />
                    </li>
                  ))}
                </ul>
              )}
            </QueryView>
          )}
        </QueryView>
      )}
    </Well>
  )
}

function DevicesPanel({ person }: { person: Person }) {
  const { t } = useI18n()
  const f = useFormat()
  const devices = useDevices()
  return (
    <Well title={t.people.devicesTitle} className="flex flex-col">
      <QueryView
        query={devices}
        loading={<SkeletonLines />}
        isEmpty={(list) => !list.some((m) => m.owner.id === person.id)}
        empty={<p className="text-sm text-ink-2">{t.people.noDevices}</p>}
      >
        {(list) => (
          <ul className="m-0 grid list-none gap-[13px] p-0 text-sm">
            {list
              .filter((m) => m.owner.id === person.id)
              .map((m) => (
                <li key={m.id} className={`flex items-center gap-2.5 ${m.revokedAt ? 'text-ink-3' : ''}`}>
                  <Led state={m.revokedAt ? 'err' : m.connected ? 'ok' : 'off'} />
                  <span className="min-w-0 truncate">{m.name}</span>
                  <span className="num ml-auto pl-3 text-xs whitespace-nowrap text-ink-2">
                    {m.revokedAt ? fmt(t.devices.revoked, { date: f.relative(m.revokedAt) }) : m.lastSeenAt ? f.relative(m.lastSeenAt) : t.devices.pending}
                  </span>
                </li>
              ))}
          </ul>
        )}
      </QueryView>
      <Link to="/dispositivi" className="mt-auto pt-3 text-[13px] text-ink-2 underline-offset-2 hover:text-ink hover:underline">
        <Readout>{t.people.allDevices}</Readout>
      </Link>
    </Well>
  )
}

/** The page of one person. */
export function PersonPage() {
  const { t } = useI18n()
  const f = useFormat()
  const { personId = '' } = useParams()
  const people = usePeople()
  const [flash, setFlash] = useFlash<string>(8000)

  return (
    <>
      <Link to="/persone" className="mb-4 inline-flex items-center gap-1.5 text-[13px] text-ink-2 no-underline hover:text-ink">
        <ArrowLeft size={15} aria-hidden />
        {t.people.back}
      </Link>
      <QueryView query={people} loading={<SkeletonLines lines={4} />}>
        {(list) => {
          const person = list.find((p) => p.id === personId)
          if (!person) return <EmptyState title={t.people.notFoundTitle} body={t.people.notFoundBody} />
          const name = person.name ?? person.email
          const when =
            person.status === 'removed' && person.removedAt
              ? fmt(t.people.removedOn, { date: f.relative(person.removedAt) })
              : person.status === 'invited' && person.invitedAt
                ? fmt(t.people.invitedOn, { date: f.relative(person.invitedAt) })
                : person.lastSignInAt
                  ? f.relative(person.lastSignInAt)
                  : t.common.never
          return (
            <>
              <header className="mb-[22px] flex flex-wrap items-center gap-x-4 gap-y-3">
                <Avatar name={name} size={48} />
                <div className="min-w-0">
                  <h1 className="text-[28px] leading-[1.15] font-bold tracking-[-0.025em]">
                    {name}
                    {person.isMe && <span className="font-normal text-ink-3"> ({t.common.you})</span>}
                  </h1>
                  <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-ink-2">
                    {person.name && <span>{person.email}</span>}
                    <span>{t.roles[person.role]}</span>
                    <Status person={person} />
                    <span>{when}</span>
                  </p>
                </div>
              </header>
              {flash && <Notice className="mb-3.5" state={<State led="err">{t.common.failed}</State>} text={flash} />}
              <div className="grid items-start gap-3.5 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
                <DisksPanel person={person} onError={setFlash} />
                <DevicesPanel person={person} />
              </div>
            </>
          )
        }}
      </QueryView>
    </>
  )
}
