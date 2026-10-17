/*
  Disks (concept-e-admin, "Dischi"): each disk is a JuiceFS volume with its own Valkey
  database, its objects under <bucket>/<volumeName>/, 1 MB blocks (DECISIONS #15), a trash and
  the cache expiry its Macs get (each Mac sizes its own cache). On a Mac it is a folder,
  /Volumes/Floppy/<name>; names never change, CapCut keeps full paths. Creating one is a state:
  the format runs after the answer, and can fail.
*/

import { Plus } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { useSearchParams } from 'react-router'
import { Button } from '@/components/Button'
import { Notice, SlotNote, State, Well } from '@/components/detail'
import { Dialog } from '@/components/Dialog'
import { Field, Input } from '@/components/Field'
import { Faceplate } from '@/components/marks'
import { useErrorText } from '@/components/error-text'
import { EmptyState, PageHeader, QueryView, SkeletonRows } from '@/components/states'
import { fmt, plural, rich, useI18n } from '@/i18n'
import type { CachePolicy, Disk } from '@/lib/api'
import { VOLUME_NAME } from '@/lib/api/types'
import { useFormat } from '@/lib/format'
import { useFlash } from '@/lib/hooks'
import { DiskState } from './DiskState'
import { useCreateDisk, useDiskCreation, useDisks, useUpdateDisk } from './hooks'

const slugify = (s: string) =>
  s
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 63)
    .replace(/-+$/, '')

/** A name that can be a folder in the Finder: no slash or colon, not hidden, up to 60. */
const folderOk = (s: string) => s.length >= 1 && s.length <= 60 && !/[/:]/.test(s) && !s.startsWith('.')
/** Where a disk appears on a Mac. */
const mountPathOf = (name: string) => `/Volumes/Floppy/${name || '...'}`

/** Parses a whole number in a range, or null. */
const intIn = (value: string, min: number, max: number) => {
  const n = Number(value)
  return Number.isInteger(n) && n >= min && n <= max ? n : null
}

function NewDiskDialog({ onClose, onCreated }: { onClose(): void; onCreated(disk: Disk): void }) {
  const { t } = useI18n()
  const errorText = useErrorText()
  const create = useCreateDisk()
  const [name, setName] = useState('')
  const [volumeName, setVolumeName] = useState('')
  const [touched, setTouched] = useState(false)
  const [trash, setTrash] = useState('7')
  const [tried, setTried] = useState(false)
  const clean = name.trim()
  const effectiveVolume = touched ? volumeName : slugify(name)
  const trashDays = intIn(trash, 0, 365)
  const volumeOk = VOLUME_NAME.test(effectiveVolume)
  const nameOk = folderOk(clean)
  const bucket = useDisks().data?.[0]?.bucket ?? 'bucket'

  const submit = () => {
    setTried(true)
    if (!nameOk || !volumeOk || trashDays === null) return
    create.mutate({ name: clean, volumeName: effectiveVolume, trashDays }, { onSuccess: onCreated })
  }

  return (
    <Dialog
      title={t.disks.newTitle}
      onClose={onClose}
      onSubmit={submit}
      busy={create.isPending}
      actions={
        <>
          <Button variant="ghost" onClick={onClose} disabled={create.isPending}>
            {t.common.cancel}
          </Button>
          <Button type="submit" variant="ink" busy={create.isPending}>
            {t.disks.create}
          </Button>
        </>
      }
    >
      <Field
        label={t.disks.name}
        hint={rich(t.disks.nameHint, { path: <span className="num text-ink">{mountPathOf(clean)}</span> })}
        error={tried && !nameOk ? (clean ? t.disks.nameRule : t.disks.required) : null}
      >
        {(id, describedBy) => <Input id={id} autoFocus maxLength={60} value={name} invalid={tried && !nameOk} aria-describedby={describedBy} onChange={(e) => setName(e.target.value)} />}
      </Field>
      <Field label={t.disks.volumeName} hint={fmt(t.disks.volumeNameHint, { bucket, name: effectiveVolume || '...' })} error={tried && !volumeOk ? fmt(t.disks.volumeNameHint, { bucket, name: effectiveVolume || '...' }) : null}>
        {(id, describedBy) => (
          <Input
            id={id}
            mono
            maxLength={63}
            spellCheck={false}
            value={effectiveVolume}
            invalid={tried && !volumeOk}
            aria-describedby={describedBy}
            onChange={(e) => {
              setTouched(true)
              setVolumeName(e.target.value)
            }}
          />
        )}
      </Field>
      <div className="grid gap-1.5">
        <span className="text-[13px] font-semibold text-ink">{t.disks.blockSize}</span>
        <span className="num text-ink">1 MB</span>
        <span className="text-[13px]">{t.disks.blockSizeHint}</span>
      </div>
      <Field label={t.disks.trashDays} error={tried && trashDays === null ? fmt(t.disks.numberRange, { min: 0, max: 365 }) : null}>
        {(id, describedBy) => <Input id={id} mono inputMode="numeric" className="!w-[110px]" value={trash} invalid={tried && trashDays === null} aria-describedby={describedBy} onChange={(e) => setTrash(e.target.value)} />}
      </Field>
      {create.isError && (
        <p className="text-[13px] text-bad-text" role="alert">
          {errorText(create.error)}
        </p>
      )}
    </Dialog>
  )
}

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
        <Row term={t.disks.onMac}><Breakable text={disk.mountPath} /></Row>
        <Row term={t.disks.bucket}><Breakable text={`${disk.bucket}/${disk.volumeName}/`} /></Row>
        <Row term={t.disks.region}><span className="num">{disk.region}</span></Row>
        <Row term={t.disks.endpoint}><Breakable text={disk.endpoint} /></Row>
        <Row term={t.disks.database}><span className="num">{fmt(t.disks.databaseValue, { n: disk.metaDb })}</span></Row>
        <Row term={t.disks.blocks}><span className="num">{disk.blockSizeKb >= 1024 ? `${disk.blockSizeKb / 1024} MB` : `${disk.blockSizeKb} KB`}</span></Row>
        <Row term={t.disks.trash}>{fmt(t.disks.trashValue, { days: disk.trashDays, size: f.bytes(disk.trashBytes) })}</Row>
        <Row term={t.disks.mounted}>{plural(t.disks.mountedValue, disk.mountedBy, locale)}</Row>
        <Row term={t.disks.created}>{fmt(t.disks.createdValue, { date: f.date(disk.createdAt), who: disk.createdBy })}</Row>
      </dl>
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
            <div className="grid items-start gap-[18px] xl:grid-cols-[minmax(0,1fr)_340px]">
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
