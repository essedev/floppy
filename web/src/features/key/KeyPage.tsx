/*
  The storage keys (concept-e-admin, "Chiave dell'archivio"; DECISIONS #18, #24). Manual: each
  disk has its own bucket and key; the list shows them with their windows, and the selected
  disk's key changes in four steps. Creating and deleting the key at the provider stay manual;
  Floppy tests the new key with a write, applies it with juicefs config and counts the devices
  picking it up. Managed: the adapter owns the buckets and gives every device its own key,
  deleted at the provider on revocation, so there is nothing to rotate: the page lists them.
*/

import { Check } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { useSearchParams } from 'react-router'
import { Button } from '@/components/Button'
import { Led, Notice, Readout, SlotList, SlotNote, State, Well } from '@/components/detail'
import { Checkbox, Field, Input } from '@/components/Field'
import { Faceplate } from '@/components/marks'
import { VersioningState } from '@/components/storage'
import { providerName } from '@/lib/providers'
import { useErrorText } from '@/components/error-text'
import { EmptyState, PageHeader, QueryView, SkeletonLines, SkeletonRows } from '@/components/states'
import { fmt, plural, rich, useI18n } from '@/i18n'
import type { DiskKey, KeyStatus, KeyWindowReason, StepState } from '@/lib/api'
import { useFormat } from '@/lib/format'
import { useFlash, useNow } from '@/lib/hooks'
import { useCloseWindow, useKeyStatus, useStartRotation } from './hooks'

type Look = 'done' | 'cur' | 'todo' | 'ready'

function Step({ n, look, title, body, children }: { n: number; look: Look; title: string; body: ReactNode; children?: ReactNode }) {
  const box = look === 'todo' ? 'bg-transparent shadow-[inset_0_0_0_1px_var(--line)]' : 'bg-raised'
  const dot = look === 'cur' ? 'bg-ink text-surface' : look === 'done' ? 'bg-sunken text-ink' : 'bg-sunken text-ink-2'
  return (
    <li className={`grid grid-cols-[34px_minmax(0,1fr)] gap-3.5 rounded-2xl px-[18px] py-4 ${box}`} aria-current={look === 'cur' ? 'step' : undefined}>
      <span className={`num grid size-[30px] place-items-center rounded-full text-[13px] ${dot}`}>{look === 'done' ? <Check size={15} aria-label={String(n)} /> : n}</span>
      <div className="min-w-0">
        <h3 className="mt-1 mb-1 text-base font-semibold">{title}</h3>
        <p className="max-w-[64ch] text-sm text-ink-2">{body}</p>
        {children && <div className="mt-2.5 grid gap-3.5">{children}</div>}
      </div>
    </li>
  )
}

function Progress({ items }: { items: { label: string; state: StepState; aside?: string }[] }) {
  const { t } = useI18n()
  return (
    <ul className="m-0 grid list-none gap-2 p-0 text-sm" aria-live="polite">
      {items.map((item) => (
        <li key={item.label} className="flex items-center gap-2.5">
          <Led state={item.state === 'done' ? 'ok' : item.state === 'running' ? 'dl' : item.state === 'failed' ? 'err' : 'off'} blink={item.state === 'running'} />
          <span>{item.label}</span>
          <Readout className="ml-auto">{item.aside ?? (item.state === 'done' ? t.key.progressOk : item.state === 'running' ? '...' : '')}</Readout>
        </li>
      ))}
    </ul>
  )
}

/** A disk's key window: closed with the key in use, or open with its clock and reasons. */
function RiskBox({ disk }: { disk: DiskKey }) {
  const { t } = useI18n()
  const f = useFormat()
  const now = useNow(1000)
  const w = disk.window
  const reason = (r: KeyWindowReason) =>
    fmt(r.kind === 'person_removed' ? t.key.reasonPerson : r.kind === 'device_revoked' ? t.key.reasonDevice : t.key.reasonAccess, { label: r.label, date: f.relative(r.at, now) })
  if (!w) {
    const tail = disk.accessKeyTail
    return (
      <div className="notice mb-4 grid gap-2 px-[18px] py-4">
        <State led="ok">{t.key.closedState}</State>
        {tail && (
          <p className="max-w-[66ch] text-sm text-ink-2">
            {disk.rotatedAt ? fmt(t.key.closedBody, { tail, date: f.relative(disk.rotatedAt) }) : fmt(t.key.closedNever, { tail })}
          </p>
        )}
      </div>
    )
  }
  return (
    <div className="notice mb-4 grid gap-2.5 px-[18px] py-4">
      <div className="flex flex-wrap items-baseline justify-between gap-x-[18px] gap-y-1">
        <State led="dl">{t.key.openState}</State>
        <span className="num text-[15px] text-ink" role="timer">
          {fmt(t.keyNotice.since, { time: f.elapsed(now - Date.parse(w.openedAt)) })}
        </span>
      </div>
      <p className="max-w-[66ch] text-sm text-ink-2">{t.key.openBody}</p>
      <SlotList className="text-[13.5px] text-ink-2" items={w.reasons.map(reason)} />
    </div>
  )
}

/** The four steps of changing one disk's key. Keyed by disk, so its form starts fresh. */
function Steps({ disk }: { disk: DiskKey }) {
  const { t, locale } = useI18n()
  const errorText = useErrorText()
  const start = useStartRotation()
  const close = useCloseWindow()
  const [accessKey, setAccessKey] = useState('')
  const [secret, setSecret] = useState('')
  const [tried, setTried] = useState(false)
  const [deleted, setDeleted] = useState(false)
  const [flash, setFlash] = useFlash<string>(8000)
  const bucket = <b className="num font-normal text-ink">{disk.bucket}</b>
  const r = disk.rotation
  const stage = !r || r.state === 'failed' ? 2 : r.state === 'applying' ? 3 : 4
  // Step 1 happens at the provider, out of sight: it shows as done only once a key was applied.
  // Exposures after the rotation started stay open when it closes (DATABASE_SCHEMA.md, "Key window").
  const later = r ? (disk.window?.reasons.filter((x) => x.at >= r.startedAt).length ?? 0) : 0
  const look = (n: number): Look => (n === 1 && stage === 2 ? 'ready' : n < stage ? 'done' : n === stage ? 'cur' : 'todo')
  const configured = r?.config === 'done'

  const submit = () => {
    setTried(true)
    if (!accessKey.trim() || !secret.trim()) return
    start.mutate({ diskId: disk.diskId, accessKeyId: accessKey.trim(), secretKey: secret.trim() }, {
      onSuccess: () => {
        // The secret is not kept on the page once sent.
        setSecret('')
        setTried(false)
      },
    })
  }

  return (
    <>
      {flash && <Notice className="mb-3.5" state={<State led="ok">{t.key.closedState}</State>} text={flash} />}
      <ol className="m-0 grid list-none gap-2.5 p-0">
        <Step n={1} look={look(1)} title={t.key.step1Title} body={rich(t.key.step1Body, { bucket })} />
        <Step n={2} look={look(2)} title={t.key.step2Title} body={t.key.step2Body}>
          {stage === 2 && (
            <form
              className="grid gap-3.5"
              noValidate
              onSubmit={(e) => {
                e.preventDefault()
                submit()
              }}
            >
              <div className="grid gap-3.5 sm:grid-cols-2">
                <Field label={t.key.accessKey} error={tried && !accessKey.trim() ? t.key.required : null}>
                  {(id, describedBy) => <Input id={id} mono autoComplete="off" spellCheck={false} value={accessKey} invalid={tried && !accessKey.trim()} aria-describedby={describedBy} onChange={(e) => setAccessKey(e.target.value)} />}
                </Field>
                <Field label={t.key.secretKey} error={tried && !secret.trim() ? t.key.required : null}>
                  {(id, describedBy) => <Input id={id} mono type="password" autoComplete="new-password" value={secret} invalid={tried && !secret.trim()} aria-describedby={describedBy} onChange={(e) => setSecret(e.target.value)} />}
                </Field>
              </div>
              {r?.state === 'failed' && r.error && (
                <div className="notice grid gap-1.5 px-4 py-3.5 text-sm" role="alert">
                  <State led="err">{r.error.step === 'probe' ? t.key.probeFailed : t.key.configFailed}</State>
                  <span className="text-ink-2">{r.error.step === 'probe' ? rich(t.key.probeFailedBody, { bucket }) : t.key.configFailedBody}</span>
                  <code className="num text-[12.5px] text-ink-2">
                    {r.error.code} · {r.error.request}
                  </code>
                </div>
              )}
              {start.isError && <p className="text-[13px] text-bad-text" role="alert">{errorText(start.error)}</p>}
              <div>
                <Button type="submit" variant="ink" busy={start.isPending}>
                  {t.key.apply}
                </Button>
              </div>
            </form>
          )}
        </Step>
        <Step n={3} look={look(3)} title={t.key.step3Title} body={t.key.step3Body}>
          {r && r.state !== 'failed' && (
            <>
              <Progress
                items={[
                  { label: t.key.progressProbe, state: r.probe },
                  { label: fmt(t.key.progressDisk, { name: disk.diskName }), state: r.config },
                  {
                    label: t.key.progressDevices,
                    state: configured && r.devices.updated >= r.devices.total ? 'done' : configured ? 'running' : 'pending',
                    aside: configured ? fmt(t.key.progressCount, { n: r.devices.updated, total: r.devices.total }) : '',
                  },
                ]}
              />
              {r.state === 'awaiting_old_key_deletion' && r.devices.updated < r.devices.total && <SlotNote className="text-[13px] text-ink-2">{t.key.devicesInformative}</SlotNote>}
            </>
          )}
        </Step>
        <Step n={4} look={look(4)} title={t.key.step4Title} body={t.key.step4Body}>
          {r?.state === 'awaiting_old_key_deletion' && (
            <>
              <Checkbox
                label={
                  r.oldKeyTails.length === 1
                    ? rich(t.key.confirmDeleted, { tail: <span className="num">{r.oldKeyTails[0]}</span> })
                    : rich(t.key.confirmDeletedMany, { tails: <span className="num">{r.oldKeyTails.join(', ')}</span> })
                }
                checked={deleted}
                onChange={(e) => setDeleted(e.target.checked)}
              />
              {later > 0 && <SlotNote className="text-[13px] text-ink-2">{plural(t.key.laterExposures, later, locale)}</SlotNote>}
              {close.isError && <p className="text-[13px] text-bad-text" role="alert">{errorText(close.error)}</p>}
              <div>
                <Button
                  variant="ink"
                  disabled={!deleted}
                  busy={close.isPending}
                  onClick={() =>
                    close.mutate(r.id, {
                      onSuccess: (next) => {
                        setDeleted(false)
                        setAccessKey('')
                        setFlash(next.disks.find((d) => d.diskId === disk.diskId)?.window ? t.key.stillOpen : t.key.closedDone)
                      },
                    })
                  }
                >
                  {t.key.closeWindow}
                </Button>
              </div>
            </>
          )}
        </Step>
      </ol>
    </>
  )
}

/** The state of a disk's key in the list: a rotation first, then the window. */
function KeyState({ disk }: { disk: DiskKey }) {
  const { t } = useI18n()
  if (disk.rotation?.state === 'applying') return <State led="dl" blink>{t.key.rotating}</State>
  if (disk.rotation?.state === 'failed') return <State led="err">{t.key.rotationFailed}</State>
  if (disk.window) return <State led="dl">{t.key.windowOpen}</State>
  return <State led="ok">{t.key.windowClosed}</State>
}

function ManualKeys({ status }: { status: KeyStatus }) {
  const { t } = useI18n()
  const f = useFormat()
  const [params, setParams] = useSearchParams()
  const disks = status.disks
  if (disks.length === 0) return <EmptyState art={<Faceplate state="off" />} title={t.key.noDisksTitle} body={t.key.noDisksBody} />
  const selected = disks.find((d) => d.diskId === params.get('disco')) ?? disks.find((d) => d.window) ?? disks[0]
  const select = (id: string) =>
    setParams((p) => {
      p.set('disco', id)
      return p
    })

  return (
    <>
      <div className="relative mb-7 overflow-x-auto">
        <table className="tbl">
          <thead>
            <tr>
              <th>{t.key.colDisk}</th>
              <th>{t.key.colKey}</th>
              <th>{t.key.colWindow}</th>
              <th>{t.key.colVersioning}</th>
              <th>{t.key.colChanged}</th>
            </tr>
          </thead>
          <tbody>
            {disks.map((d) => (
              <tr key={d.diskId} aria-selected={d.diskId === selected?.diskId} className="cursor-pointer" onClick={() => select(d.diskId)}>
                <td>
                  <button type="button" className="border-0 bg-transparent p-0 font-semibold" aria-label={fmt(t.key.show, { name: d.diskName })} onClick={() => select(d.diskId)}>
                    {d.diskName}
                  </button>
                </td>
                <td className="num">{d.accessKeyTail ? fmt(t.key.tail, { tail: d.accessKeyTail }) : <span className="text-ink-3">{t.key.noKey}</span>}</td>
                <td>
                  <KeyState disk={d} />
                </td>
                <td>
                  <VersioningState versioning={d.versioning} />
                </td>
                <td>{d.rotatedAt ? f.relative(d.rotatedAt) : t.common.never}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {selected && (
        <section aria-labelledby="key-disk-title">
          <h2 id="key-disk-title" className="mb-3 text-lg font-bold tracking-[-0.015em]">
            {fmt(t.key.diskTitle, { name: selected.diskName })}
          </h2>
          <RiskBox disk={selected} />
          {selected.versioning.state !== 'enabled' && <SlotNote className="mb-4 max-w-[72ch] text-[13px] text-ink-2">{t.disks.versioningNote}</SlotNote>}
          {selected.accessKeyTail || selected.rotation ? <Steps key={selected.diskId} disk={selected} /> : <SlotNote className="text-[13.5px] text-ink-2">{t.key.notReady}</SlotNote>}
        </section>
      )}
    </>
  )
}

function ManagedKeys({ status }: { status: KeyStatus }) {
  const { t } = useI18n()
  const f = useFormat()
  const provider = providerName(status.storageProvider)
  const keyLed = { pending: 'dl', active: 'ok', retiring: 'dl' } as const
  return (
    <div className="grid gap-3.5">
      <p className="max-w-[72ch] text-sm text-ink-2">
        {status.controlPlaneKeyTail
          ? rich(t.key.managedProvider, { provider, tail: <span className="num text-ink">{status.controlPlaneKeyTail}</span> })
          : fmt(t.key.managedProviderNoKey, { provider })}
      </p>
      <Well title={t.key.deviceKeysTitle} className="min-w-0">
        {status.keys.length === 0 ? (
          <p className="text-sm text-ink-2">{t.key.noDeviceKeys}</p>
        ) : (
          <div className="relative -mx-[14px] overflow-x-auto">
            <table className="tbl">
              <thead>
                <tr>
                  <th>{t.key.colOwner}</th>
                  <th>{t.key.colKey}</th>
                  <th>{t.key.colState}</th>
                  <th>{t.key.colSince}</th>
                </tr>
              </thead>
              <tbody>
                {status.keys.map((k) => (
                  <tr key={k.id}>
                    <td>{k.owner}</td>
                    <td className="num">{fmt(t.key.tail, { tail: k.accessKeyTail })}</td>
                    <td>
                      <State led={keyLed[k.state]} blink={k.state === 'retiring'}>
                        {t.key.keyState[k.state]}
                      </State>
                    </td>
                    <td>{k.state === 'retiring' && k.retiringAt ? f.relative(k.retiringAt) : k.activatedAt ? f.relative(k.activatedAt) : t.common.never}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Well>
      <Well title={t.key.bucketsTitle} className="min-w-0">
        {status.disks.length === 0 ? (
          <p className="text-sm text-ink-2">{t.key.noDisksBody}</p>
        ) : (
          <div className="relative -mx-[14px] overflow-x-auto">
            <table className="tbl">
              <thead>
                <tr>
                  <th>{t.key.colDisk}</th>
                  <th>{t.key.colBucket}</th>
                  <th>{t.key.colVersioning}</th>
                </tr>
              </thead>
              <tbody>
                {status.disks.map((d) => (
                  <tr key={d.diskId}>
                    <td className="font-semibold">{d.diskName}</td>
                    <td className="num">{d.bucket}</td>
                    <td>
                      <VersioningState versioning={d.versioning} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Well>
    </div>
  )
}

/** The keys page. */
export function KeyPage() {
  const { t } = useI18n()
  const key = useKeyStatus()
  const managed = key.data?.storageMode === 'managed'
  return (
    <>
      <PageHeader title={t.key.title} lead={managed ? t.key.leadManaged : t.key.lead} />
      <QueryView
        query={key}
        loading={
          <>
            <table className="tbl mb-7">
              <tbody>
                <SkeletonRows rows={3} widths={[120, 70, 90, 90, 80]} />
              </tbody>
            </table>
            <div className="notice px-[18px] py-4">
              <SkeletonLines />
            </div>
          </>
        }
      >
        {(status) => (status.storageMode === 'managed' ? <ManagedKeys status={status} /> : <ManualKeys status={status} />)}
      </QueryView>
    </>
  )
}
