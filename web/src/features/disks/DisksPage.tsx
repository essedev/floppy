/*
  Disks (concept-e-admin, "Dischi"): each disk is a JuiceFS volume with its own Valkey instance
  and its own bucket (DECISIONS #23), 1 MB blocks (DECISIONS #15), a trash, the cache expiry its
  devices get (each device sizes its own cache) and the people who see it. On a device it is a
  folder, /Volumes/Floppy/<name>; names never change, CapCut keeps full paths. Creating one is
  a state: the instance, the bucket and the format come after the answer, and can fail.
*/

import { Plus } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router'
import { Button } from '@/components/Button'
import { Notice, SlotNote, State, Well } from '@/components/detail'
import { Dialog } from '@/components/Dialog'
import { Field, Input } from '@/components/Field'
import { Faceplate } from '@/components/marks'
import { VersioningState } from '@/components/storage'
import { useErrorText } from '@/components/error-text'
import { EmptyState, PageHeader, QueryView, SkeletonLines, SkeletonRows } from '@/components/states'
import { fmt, plural, useI18n } from '@/i18n'
import type { CachePolicy, Disk } from '@/lib/api'
import { useFormat } from '@/lib/format'
import { useFlash } from '@/lib/hooks'
import { AccessCell } from '@/features/access/AccessControl'
import { accessOf, useAccess } from '@/features/access/hooks'
import { usePeople } from '@/features/people/hooks'
import { useHealth } from '@/features/settings/hooks'
import { DiskState } from './DiskState'
import { NewDiskDialog } from './NewDiskDialog'
import { intIn } from './numbers'
import { useDiskCreation, useDisks, useProbeDisk, useUpdateDisk } from './hooks'

function EditDialog({ disk, onClose, onSaved }: { disk: Disk; onClose(): void; onSaved(): void }) {
  const { t } = useI18n()
  const errorText = useErrorText()
  const update = useUpdateDisk()
  const [expire, setExpire] = useState(String(disk.cache.expireDays))
  const [trash, setTrash] = useState(String(disk.trashDays))
  const [tried, setTried] = useState(false)
  const values = { expire: intIn(expire, 1, 365), trash: intIn(trash, 0, 365) }

  const submit = () => {
    setTried(true)
    if (values.expire === null || values.trash === null) return
    const cache: CachePolicy = { expireDays: values.expire }
    update.mutate({ id: disk.id, body: { cache, trashDays: values.trash } }, { onSuccess: onSaved })
  }
  const num = (label: string, value: string, set: (v: string) => void, ok: number | null, min: number, maxV: number) => (
    <Field label={label} error={tried && ok === null ? fmt(t.disks.numberRange, { min, max: maxV }) : null}>
      {(id, describedBy) => <Input id={id} mono inputMode="numeric" className="!w-[110px]" value={value} invalid={tried && ok === null} aria-describedby={describedBy} onChange={(e) => set(e.target.value)} />}
    </Field>
  )

  return (
    <Dialog
      title={fmt(t.disks.editTitle, { name: disk.name })}
      onClose={onClose}
      onSubmit={submit}
      busy={update.isPending}
      actions={
        <>
          <Button variant="ghost" onClick={onClose} disabled={update.isPending}>
            {t.common.cancel}
          </Button>
          <Button type="submit" variant="ink" busy={update.isPending}>
            {t.common.save}
          </Button>
        </>
      }
    >
      {num(t.disks.editExpire, expire, setExpire, values.expire, 1, 365)}
      {num(t.disks.trashDays, trash, setTrash, values.trash, 0, 365)}
      <SlotNote className="text-[13px]">{t.disks.editNote}</SlotNote>
      {update.isError && <p className="text-[13px] text-bad-text" role="alert">{errorText(update.error)}</p>}
    </Dialog>
  )
}

/** A path or host with break opportunities after its separators, never inside a word. */
function Breakable({ text }: { text: string }) {
  const parts = text.split(/(?<=[/.-])/)
  return (
    <span className="num">
      {parts.map((part, i) => (
        <span key={i}>
          {part}
          {i < parts.length - 1 && <wbr />}
        </span>
      ))}
    </span>
  )
}

function Row({ term, children }: { term: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-ink-2">{term}</dt>
      <dd className="m-0 min-w-0 break-words">{children}</dd>
    </>
  )
}

/** The disk's bucket as the control plane last tested it, with a new test on demand. */
function StoragePart({ disk }: { disk: Disk }) {
  const { t } = useI18n()
  const f = useFormat()
  const errorText = useErrorText()
  const health = useHealth()
  const probe = useProbeDisk()
  const ready = disk.state === 'ready'
  const status = health.data?.disks.find((d) => d.id === disk.id)?.storage
  const state = status?.state ?? 'unknown'
  return (
    <>
      <div className="mt-5 flex items-center justify-between gap-3 border-t border-line pt-4">
        <h3 className="eyebrow m-0">{t.disks.storageCheck}</h3>
        <Button size="sm" busy={probe.isPending} disabled={!ready} title={ready ? undefined : t.disks.creatingNote} onClick={() => probe.mutate(disk.id)}>
          {t.disks.probeDisk}
        </Button>
      </div>
      <dl className="m-0 mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-[18px] gap-y-1.5 text-sm">
        <Row term={t.overview.storage}>
          <State led={state === 'ok' ? 'ok' : state === 'failing' ? 'err' : 'off'}>
            {state === 'ok' ? t.overview.storageOk : state === 'failing' ? t.overview.storageFail : t.overview.storageUnknown}
          </State>
        </Row>
        <Row term={t.disks.versioning}><VersioningState versioning={status?.versioning ?? { state: 'unknown', oldVersionsDays: null }} /></Row>
      </dl>
      {status?.lastProbeAt && <p className="mt-2 text-[13px] text-ink-2">{fmt(t.disks.lastProbe, { time: f.relative(status.lastProbeAt) })}</p>}
      {probe.isSuccess && <div className="mt-2"><State led="ok">{t.disks.probeDiskOk}</State></div>}
      {probe.isError && <p className="mt-2 text-[13px] text-bad-text" role="alert">{errorText(probe.error)}</p>}
    </>
  )
}

/** Who sees the disk, each access editable and applied live. */
function AccessPart({ disk }: { disk: Disk }) {
  const { t } = useI18n()
  const people = usePeople()
  const access = useAccess()
  const [flash, setFlash] = useFlash<string>(8000)
  return (
    <>
      <div className="mt-5 flex items-center justify-between gap-3 border-t border-line pt-4">
        <h3 className="eyebrow m-0">{t.disks.accessTitle}</h3>
        <Link to="/accessi" className="text-[13px] text-ink-2 underline-offset-2 hover:text-ink hover:underline">
          {t.nav.access}
        </Link>
      </div>
      {flash && <p className="mt-2 text-[13px] text-bad-text" role="alert">{flash}</p>}
      <QueryView query={people} loading={<SkeletonLines />} staleNotice={false}>
        {(list) => (
          <QueryView query={access} loading={<SkeletonLines />} staleNotice={false}>
            {(entries) => (
              <ul className="m-0 mt-1 grid list-none p-0 text-sm">
                {list
                  .filter((p) => p.status !== 'removed')
                  .map((p) => {
                    const name = p.name ?? p.email
                    return (
                      <li key={p.id} className="flex items-center justify-between gap-3 py-1">
                        <Link to={`/persone/${encodeURIComponent(p.id)}`} className="min-w-0 truncate text-ink no-underline hover:underline">
                          {name}
                        </Link>
                        <AccessCell entry={accessOf(entries, disk.id, p.id)} person={name} disk={disk.name} onError={setFlash} align="right" withText />
                      </li>
                    )
                  })}
              </ul>
            )}
          </QueryView>
        )}
      </QueryView>
    </>
  )
}

function DiskDetail({ disk, onEdit }: { disk: Disk; onEdit(): void }) {
  const { t, locale } = useI18n()
  const f = useFormat()
  const ready = disk.state === 'ready'
  return (
    <Well>
      <div className="mb-4 flex items-center gap-3.5">
        <Faceplate state={!ready ? 'dl' : disk.status === 'online' ? 'ok' : 'err'} />
        <div>
          <h2 className="text-lg font-bold tracking-[-0.015em]">{disk.name}</h2>
          <div className="mt-1">
            <DiskState disk={disk} />
          </div>
        </div>
      </div>
      <dl className="m-0 grid grid-cols-[auto_minmax(0,1fr)] gap-x-[18px] gap-y-1.5 text-sm">
        <Row term={t.disks.inFinder}><Breakable text={disk.mountPath} /></Row>
        <Row term={t.disks.bucket}><Breakable text={disk.bucket} /></Row>
        <Row term={t.disks.region}><span className="num">{disk.region}</span></Row>
        <Row term={t.disks.endpoint}><Breakable text={disk.endpoint} /></Row>
        <Row term={t.disks.database}>{disk.metaUrl ? <Breakable text={disk.metaUrl} /> : <span className="text-ink-3">{t.disks.databasePending}</span>}</Row>
        <Row term={t.disks.blocks}><span className="num">{disk.blockSizeKb >= 1024 ? `${disk.blockSizeKb / 1024} MB` : `${disk.blockSizeKb} KB`}</span></Row>
        <Row term={t.disks.trash}>{fmt(t.disks.trashValue, { days: disk.trashDays, size: f.bytes(disk.trashBytes) })}</Row>
        <Row term={t.disks.mounted}>{plural(t.disks.mountedValue, disk.mountedBy, locale)}</Row>
        <Row term={t.disks.created}>{fmt(t.disks.createdValue, { date: f.date(disk.createdAt), who: disk.createdBy })}</Row>
      </dl>
      <StoragePart disk={disk} />
      <AccessPart disk={disk} />
      <div className="mt-5 flex items-center justify-between gap-3 border-t border-line pt-4">
        <h3 className="eyebrow m-0">{t.disks.cacheTitle}</h3>
        <Button size="sm" onClick={onEdit} disabled={!ready} title={ready ? undefined : t.disks.creatingNote}>
          {t.common.edit}
        </Button>
      </div>
      <dl className="m-0 mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-[18px] gap-y-1.5 text-sm">
        <Row term={t.disks.expire}>{plural(t.disks.expireValue, disk.cache.expireDays, locale)}</Row>
      </dl>
      <SlotNote className="mt-3 text-[13px] text-ink-2">{t.disks.capNote}</SlotNote>
      <SlotNote className="mt-4 text-[13px] text-ink-2">{t.disks.renameNote}</SlotNote>
    </Well>
  )
}

/** Follows a disk's creation to its end: creating, created, or failed with the reason. */
function CreationNotice({ id, onClose }: { id: string; onClose(): void }) {
  const { t } = useI18n()
  const creation = useDiskCreation(id)
  const disk = creation.data
  if (!disk) return null
  if (disk.state === 'creating') {
    return <Notice className="mb-3.5" state={<State led="dl" blink>{t.disks.creatingState}</State>} text={fmt(t.disks.creatingBody, { name: disk.name })} />
  }
  if (disk.state === 'failed') {
    return (
      <Notice
        className="mb-3.5"
        state={<State led="err">{t.disks.failedState}</State>}
        text={fmt(t.disks.failedBody, { name: disk.name, message: disk.error?.message ?? '' })}
        action={
          <Button size="sm" variant="raised" onClick={onClose}>
            {t.common.close}
          </Button>
        }
      />
    )
  }
  return (
    <Notice
      className="mb-3.5"
      state={<State led="ok">{t.common.done}</State>}
      text={
        <>
          {fmt(t.disks.createdDone, { name: disk.name })} · <span className="num">{disk.mountPath}</span>
        </>
      }
      action={
        <Button size="sm" variant="raised" onClick={onClose}>
          {t.common.close}
        </Button>
      }
    />
  )
}

/** The disks page: the list on the left, the selected disk on the right. */
export function DisksPage() {
  const { t } = useI18n()
  const f = useFormat()
  const disks = useDisks()
  const [params, setParams] = useSearchParams()
  const [editing, setEditing] = useState<Disk | null>(null)
  const [flash, setFlash] = useFlash<string>()
  const [tracking, setTracking] = useState<string | null>(null)
  const creating = params.get('nuovo') === '1'
  const setCreating = (open: boolean) =>
    setParams((p) => {
      if (open) p.set('nuovo', '1')
      else p.delete('nuovo')
      return p
    })
  const select = (id: string) =>
    setParams((p) => {
      p.set('disco', id)
      return p
    })
  const newButton = (
    <Button variant="ink" onClick={() => setCreating(true)}>
      <Plus size={17} aria-hidden />
      {t.disks.new}
    </Button>
  )

  return (
    <>
      <PageHeader title={t.disks.title} lead={t.disks.lead} actions={newButton} />
      {tracking && <CreationNotice id={tracking} onClose={() => setTracking(null)} />}
      {flash && <Notice className="mb-3.5" state={<State led="ok">{t.common.done}</State>} text={flash} />}
      <QueryView
        query={disks}
        loading={
          <table className="tbl">
            <tbody>
              <SkeletonRows rows={3} widths={[140, 80, 60, 60, 90]} />
            </tbody>
          </table>
        }
        isEmpty={(list) => list.length === 0}
        empty={<EmptyState art={<Faceplate state="off" />} title={t.disks.emptyTitle} body={t.disks.emptyBody} action={newButton} />}
      >
        {(list) => {
          const selected = list.find((d) => d.id === params.get('disco')) ?? list[0]
          return (
            <div className="grid items-start gap-[18px] xl:grid-cols-[minmax(0,1fr)_380px]">
              <div className="relative overflow-x-auto">
                <table className="tbl">
                  <thead>
                    <tr>
                      <th>{t.disks.colDisk}</th>
                      <th>{t.disks.colStatus}</th>
                      <th className="text-right">{t.disks.colUsed}</th>
                      <th className="text-right">{t.disks.colFiles}</th>
                      <th>{t.disks.colLastWrite}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {list.map((d) => (
                      <tr key={d.id} aria-selected={d.id === selected?.id} className="cursor-pointer" onClick={() => select(d.id)}>
                        <td>
                          <button type="button" className="border-0 bg-transparent p-0 font-semibold" aria-label={fmt(t.disks.show, { name: d.name })} onClick={() => select(d.id)}>
                            {d.name}
                          </button>
                        </td>
                        <td>
                          <DiskState disk={d} />
                        </td>
                        <td className="num text-right">{f.bytes(d.usedBytes)}</td>
                        <td className="num text-right">{f.number(d.fileCount)}</td>
                        <td>{d.lastWriteAt ? f.relative(d.lastWriteAt) : t.common.never}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {selected && <DiskDetail disk={selected} onEdit={() => setEditing(selected)} />}
            </div>
          )
        }}
      </QueryView>
      {creating && (
        <NewDiskDialog
          onClose={() => setCreating(false)}
          onCreated={(disk) => {
            setParams({ disco: disk.id })
            setTracking(disk.id)
          }}
        />
      )}
      {editing && (
        <EditDialog
          disk={editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null)
            setFlash(fmt(t.disks.editTitle, { name: editing.name }) + ': ' + t.common.saved.toLowerCase())
          }}
        />
      )}
    </>
  )
}
