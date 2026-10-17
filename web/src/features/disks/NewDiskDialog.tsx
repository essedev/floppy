/*
  Creating a disk (DECISIONS #20, #23, #24): its name, which is its folder in the Finder and
  never changes; in manual storage its own bucket and key, made by the admin at the provider
  and tested with a write; in managed storage nothing, the adapter makes them; the trash; and
  who sees it, closed by default: the creator write, everyone else none.
*/

import { useState, type ReactNode } from 'react'
import { Button } from '@/components/Button'
import { SlotNote, State } from '@/components/detail'
import { Dialog } from '@/components/Dialog'
import { Field, Input } from '@/components/Field'
import { providerName } from '@/lib/providers'
import { useErrorText } from '@/components/error-text'
import { SkeletonLines } from '@/components/states'
import { fmt, rich, useI18n } from '@/i18n'
import { isApiError, type AccessLevel, type CreateDiskRequest, type Disk, type Settings } from '@/lib/api'
import { BUCKET_NAME, DISK_NAME } from '@/lib/api/types'
import { AccessList } from '@/features/access/AccessControl'
import { useCurrentSession } from '@/features/auth/hooks'
import { usePeople } from '@/features/people/hooks'
import { useProbeStorage, useSettings } from '@/features/settings/hooks'
import { useCreateDisk } from './hooks'
import { intIn } from './numbers'

/** Where a disk appears on a device. */
const mountPathOf = (name: string) => `/Volumes/Floppy/${name || '...'}`

/** An S3 access key or secret as the server accepts it. */
const KEY = /^\S{8,}$/

function Section({ title, lead, children }: { title: string; lead?: string; children: ReactNode }) {
  return (
    <section className="grid gap-3 border-t border-line pt-4">
      <div>
        <h3 className="text-[15px] font-semibold text-ink">{title}</h3>
        {lead && <p className="mt-0.5 text-[13px]">{lead}</p>}
      </div>
      {children}
    </section>
  )
}

function Form({ settings, onClose, onCreated }: { settings: Settings; onClose(): void; onCreated(disk: Disk): void }) {
  const { t } = useI18n()
  const errorText = useErrorText()
  const session = useCurrentSession()
  const people = usePeople()
  const create = useCreateDisk()
  const probe = useProbeStorage()
  const manual = settings.storageMode === 'manual'
  const [name, setName] = useState('')
  const [trash, setTrash] = useState('7')
  const [bucket, setBucket] = useState('')
  const [accessKey, setAccessKey] = useState('')
  const [secret, setSecret] = useState('')
  const [endpoint, setEndpoint] = useState(settings.storage.endpoint)
  const [region, setRegion] = useState(settings.storage.region)
  // Closed by default: the creator writes, nobody else sees it (DECISIONS #23).
  const [access, setAccess] = useState<Record<string, AccessLevel>>({ [session.user.id]: 'write' })
  const [tried, setTried] = useState(false)

  const clean = name.trim()
  const trashDays = intIn(trash, 0, 365)
  const nameOk = DISK_NAME.test(clean)
  const bucketOk = BUCKET_NAME.test(bucket.trim())
  const keyOk = KEY.test(accessKey.trim())
  const secretOk = KEY.test(secret.trim())
  const storageOk = !manual || (bucketOk && keyOk && secretOk && endpoint.trim() !== '')
  const error = create.error
  const nameTaken = isApiError(error, 'disk_name_taken')
  const bucketTaken = isApiError(error, 'bucket_taken')
  const keyTaken = isApiError(error, 'validation') && error.details.field === 'accessKeyId'
  const probeFailed = isApiError(error, 'probe_failed') ? error : isApiError(probe.error, 'probe_failed') ? probe.error : null
  const fieldError = nameTaken || bucketTaken || keyTaken || isApiError(error, 'probe_failed')
  const live = (people.data ?? []).filter((p) => p.status !== 'removed')

  const storage = () => ({ endpoint: endpoint.trim(), region: region.trim(), bucket: bucket.trim(), accessKeyId: accessKey.trim(), secretKey: secret.trim() })
  // A field changed: answers about the old values no longer apply.
  const edit = (set: (v: string) => void) => (value: string) => {
    set(value)
    if (create.isError) create.reset()
    if (probe.isSuccess || probe.isError) probe.reset()
  }

  const submit = () => {
    setTried(true)
    if (!nameOk || trashDays === null || !storageOk) return
    // The creator is sent even at none: left out, the server would give them write.
    const entries = live
      .filter((p) => (access[p.id] ?? 'none') !== 'none' || p.id === session.user.id)
      .map((p) => ({ personId: p.id, access: access[p.id] ?? 'none' }))
    const body: CreateDiskRequest = { name: clean, trashDays, access: entries, ...(manual ? { storage: storage() } : {}) }
    create.mutate(body, { onSuccess: onCreated })
  }

  const text = (label: string, value: string, set: (v: string) => void, invalid: boolean, message: string | null, extra: { type?: string; autoComplete?: string; hint?: string } = {}) => (
    <Field label={label} hint={extra.hint} error={message}>
      {(id, describedBy) => (
        <Input
          id={id}
          mono
          type={extra.type}
          autoComplete={extra.autoComplete ?? 'off'}
          spellCheck={false}
          value={value}
          invalid={invalid}
          aria-describedby={describedBy}
          onChange={(e) => edit(set)(e.target.value)}
        />
      )}
    </Field>
  )

  return (
    <Dialog
      wide
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
        hint={
          <>
            {rich(t.disks.nameHint, { path: <span className="num text-ink">{mountPathOf(clean)}</span> })} {t.disks.nameRule}
          </>
        }
        error={tried && !nameOk ? (clean ? t.disks.nameRule : t.disks.required) : nameTaken ? fmt(t.disks.nameTaken, { name: clean }) : null}
      >
        {(id, describedBy) => (
          <Input id={id} autoFocus maxLength={60} value={name} invalid={(tried && !nameOk) || nameTaken} aria-describedby={describedBy} onChange={(e) => edit(setName)(e.target.value)} />
        )}
      </Field>

      {manual ? (
        <Section title={t.disks.storageTitle} lead={t.disks.storageLead}>
          {text(t.disks.storageBucket, bucket, setBucket, (tried && !bucketOk) || bucketTaken, tried && !bucketOk ? t.disks.bucketRule : bucketTaken ? t.errors.bucket_taken : null)}
          <div className="grid gap-3 sm:grid-cols-2">
            {text(t.key.accessKey, accessKey, setAccessKey, (tried && !keyOk) || keyTaken, tried && !keyOk ? t.disks.keyRule : keyTaken ? t.disks.keyTaken : null)}
            {text(t.key.secretKey, secret, setSecret, tried && !secretOk, tried && !secretOk ? t.disks.keyRule : null, { type: 'password', autoComplete: 'new-password' })}
            {text(t.disks.storageEndpoint, endpoint, setEndpoint, tried && !endpoint.trim(), tried && !endpoint.trim() ? t.disks.required : null)}
            {text(t.disks.storageRegion, region, setRegion, false, null)}
          </div>
          <SlotNote className="text-[13px]">{t.disks.storageDefaults}</SlotNote>
          <div className="flex flex-wrap items-center gap-3">
            <Button
              size="sm"
              busy={probe.isPending}
              disabled={!bucketOk || !keyOk || !secretOk || !endpoint.trim()}
              onClick={() => probe.mutate(storage())}
            >
              {t.disks.probe}
            </Button>
            {probe.isSuccess && <State led="ok">{t.disks.probeOk}</State>}
            {probe.isError && !probeFailed && <span className="text-[13px] text-bad-text">{errorText(probe.error)}</span>}
          </div>
          {probeFailed && (
            <div className="notice grid gap-1.5 px-4 py-3.5 text-sm" role="alert">
              <State led="err">{t.disks.probeFail}</State>
              <span>{rich(t.disks.probeFailBody, { bucket: <b className="num font-normal text-ink">{bucket.trim()}</b> })}</span>
              <code className="num text-[12.5px]">
                {String(probeFailed.details.code ?? '')} · {String(probeFailed.details.request ?? '')}
              </code>
            </div>
          )}
        </Section>
      ) : (
        <SlotNote className="text-[13px]">{fmt(t.disks.managedNote, { provider: providerName(settings.storageProvider) })}</SlotNote>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="grid content-start gap-1.5">
          <span className="text-[13px] font-semibold text-ink">{t.disks.blockSize}</span>
          <span className="num text-ink">1 MB</span>
          <span className="text-[13px]">{t.disks.blockSizeHint}</span>
        </div>
        <Field label={t.disks.trashDays} error={tried && trashDays === null ? fmt(t.disks.numberRange, { min: 0, max: 365 }) : null}>
          {(id, describedBy) => <Input id={id} mono inputMode="numeric" className="!w-[110px]" value={trash} invalid={tried && trashDays === null} aria-describedby={describedBy} onChange={(e) => setTrash(e.target.value)} />}
        </Field>
      </div>

      <Section title={t.disks.accessTitle} lead={t.disks.accessLead}>
        {people.data ? (
          <AccessList
            rows={live.map((p) => ({ id: p.id, label: p.isMe ? `${p.name ?? p.email} (${t.common.you})` : (p.name ?? p.email), sub: p.status === 'invited' ? t.access.invitedReadout : undefined }))}
            value={access}
            onChange={setAccess}
            labelFor={(row) => fmt(t.access.change, { person: row.label, disk: clean || t.disks.newTitle })}
          />
        ) : people.isError ? (
          <span className="text-[13px] text-bad-text">{errorText(people.error)}</span>
        ) : (
          <SkeletonLines lines={2} />
        )}
      </Section>

      {create.isError && !fieldError && (
        <p className="text-[13px] text-bad-text" role="alert">
          {errorText(create.error)}
        </p>
      )}
    </Dialog>
  )
}

/** The new disk dialog; waits for the settings, which say the storage mode and its defaults. */
export function NewDiskDialog({ onClose, onCreated }: { onClose(): void; onCreated(disk: Disk): void }) {
  const { t } = useI18n()
  const errorText = useErrorText()
  const settings = useSettings()
  if (settings.data) return <Form settings={settings.data} onClose={onClose} onCreated={onCreated} />
  return (
    <Dialog
      wide
      title={t.disks.newTitle}
      onClose={onClose}
      actions={
        <Button variant="ghost" onClick={onClose}>
          {t.common.cancel}
        </Button>
      }
    >
      {settings.isError ? <p className="text-[13px] text-bad-text" role="alert">{errorText(settings.error)}</p> : <SkeletonLines lines={4} />}
    </Dialog>
  )
}
