/*
  Access (DECISIONS #23): who sees each disk and how, as a matrix of people by disks. Every
  cell saves on change and applies live; a cell whose change has not reached the person's
  devices yet carries a lit LED until the disk's instance takes it.
*/

import { Plus } from 'lucide-react'
import { Link } from 'react-router'
import { Avatar, Notice, Readout, SlotList, State } from '@/components/detail'
import { Faceplate } from '@/components/marks'
import { EmptyState, PageHeader, QueryView, SkeletonRows } from '@/components/states'
import { useI18n } from '@/i18n'
import type { Disk, DiskAccess, Person } from '@/lib/api'
import { useFlash } from '@/lib/hooks'
import { usePeople } from '@/features/people/hooks'
import { useDisks } from '@/features/disks/hooks'
import { AccessCell } from './AccessControl'
import { accessOf, useAccess } from './hooks'

const linkClass = 'inline-flex h-10 items-center gap-2 rounded-[11px] bg-ink px-4 text-sm font-semibold text-surface no-underline hover:opacity-90'

function Matrix({ disks, people, access, onError }: { disks: Disk[]; people: Person[]; access: DiskAccess[]; onError(text: string): void }) {
  const { t } = useI18n()
  return (
    <div className="relative overflow-x-auto">
      <table className="tbl">
        <thead>
          <tr>
            <th className="pin">{t.access.colPerson}</th>
            {disks.map((d) => (
              <th key={d.id}>
                <Link to={`/dischi?disco=${encodeURIComponent(d.id)}`} className="text-ink-2 no-underline hover:text-ink hover:underline">
                  {d.name}
                </Link>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {people.map((p) => {
            const name = p.name ?? p.email
            return (
              <tr key={p.id}>
                <td className="pin">
                  <Link to={`/persone/${encodeURIComponent(p.id)}`} className="flex min-w-0 items-center gap-3 text-ink no-underline">
                    <span className="max-sm:hidden"><Avatar name={name} size={30} /></span>
                    <span className="min-w-0">
                      <b className="block font-semibold hover:underline">{name}</b>
                      {p.status === 'invited' ? <Readout>{t.access.invitedReadout}</Readout> : p.name && <span className="text-[13px] text-ink-3 max-sm:hidden">{p.email}</span>}
                    </span>
                  </Link>
                </td>
                {disks.map((d) => (
                  <td key={d.id}>
                    <AccessCell entry={accessOf(access, d.id, p.id)} person={name} disk={d.name} onError={onError} />
                  </td>
                ))}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

/** The access page: people by disks. */
export function AccessPage() {
  const { t } = useI18n()
  const disks = useDisks()
  const people = usePeople()
  const access = useAccess()
  const [flash, setFlash] = useFlash<string>(8000)
  const loading = (
    <table className="tbl">
      <tbody>
        <SkeletonRows rows={5} widths={[180, 110, 110, 110]} />
      </tbody>
    </table>
  )

  return (
    <>
      <PageHeader title={t.access.title} lead={t.access.lead} />
      {flash && <Notice className="mb-3.5" state={<State led="err">{t.common.failed}</State>} text={flash} />}
      <QueryView
        query={disks}
        loading={loading}
        isEmpty={(list) => list.length === 0}
        empty={
          <EmptyState
            art={<Faceplate state="off" />}
            title={t.access.noDisksTitle}
            body={t.access.noDisksBody}
            action={
              <Link to="/dischi?nuovo=1" className={linkClass}>
                <Plus size={17} aria-hidden />
                {t.disks.new}
              </Link>
            }
          />
        }
      >
        {(diskList) => (
          <QueryView query={people} loading={loading}>
            {(peopleList) => (
              <QueryView query={access} loading={loading}>
                {(accessList) => (
                  <>
                    <Matrix disks={diskList} people={peopleList.filter((p) => p.status !== 'removed')} access={accessList} onError={setFlash} />
                    <div className="mt-6 grid max-w-[72ch] gap-3 text-[13.5px] text-ink-2">
                      <p>{t.access.legend}</p>
                      <SlotList items={[t.access.notes.none, t.access.notes.read, t.access.notes.lower]} />
                    </div>
                  </>
                )}
              </QueryView>
            )}
          </QueryView>
        )}
      </QueryView>
    </>
  )
}
