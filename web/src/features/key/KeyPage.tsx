/*
  The storage key and its rotation (concept-e-admin, "Chiave dell'archivio"; DECISIONS #4):
  the risk window with its clock, then four steps. Creating and deleting the key at the
  provider stay manual (no provider API in the core); Floppy tests the new key with a write,
  applies it to every disk with juicefs config, and waits for the Macs to pick it up.
*/

import { Check } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { Button } from '@/components/Button'
import { Led, Notice, Readout, SlotList, State } from '@/components/detail'
import { Checkbox, Field, Input } from '@/components/Field'
import { useErrorText } from '@/components/error-text'
import { PageHeader, QueryView, SkeletonLines } from '@/components/states'
import { fmt, rich, useI18n } from '@/i18n'
import type { KeyStatus, StepState } from '@/lib/api'
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

function RiskBox({ status }: { status: KeyStatus }) {
  const { t } = useI18n()
  const f = useFormat()
  const now = useNow(1000)
  const w = status.window
  if (!w) {
    return (
      <div className="notice mb-4 grid max-w-[760px] gap-2 px-[18px] py-4">
        <State led="ok">{t.key.closedState}</State>
        <p className="max-w-[66ch] text-sm text-ink-2">
          {status.rotatedAt ? fmt(t.key.closedBody, { tail: status.accessKeyTail, date: f.relative(status.rotatedAt) }) : fmt(t.key.closedNever, { tail: status.accessKeyTail })}
        </p>
      </div>
    )
  }
  return (
    <div className="notice mb-4 grid max-w-[760px] gap-2.5 px-[18px] py-4">
      <div className="flex flex-wrap items-baseline justify-between gap-x-[18px] gap-y-1">
        <State led="dl">{t.key.openState}</State>
        <span className="num text-[15px] text-ink" role="timer">
          {fmt(t.keyNotice.since, { time: f.elapsed(now - Date.parse(w.openedAt)) })}
        </span>
      </div>
      <p className="max-w-[66ch] text-sm text-ink-2">{t.key.openBody}</p>
      <SlotList
        className="text-[13.5px] text-ink-2"
        items={w.reasons.map((r) => fmt(r.kind === 'person_removed' ? t.key.reasonPerson : t.key.reasonMac, { label: r.label, date: f.relative(r.at, now) }))}
      />
    </div>
  )
}

function Steps({ status }: { status: KeyStatus }) {
  const { t } = useI18n()
  const errorText = useErrorText()
  const start = useStartRotation()
  const close = useCloseWindow()
  const [accessKey, setAccessKey] = useState('')
  const [secret, setSecret] = useState('')
  const [tried, setTried] = useState(false)
  const [deleted, setDeleted] = useState(false)
  const [flash, setFlash] = useFlash<string>(8000)
  const bucket = status.bucket
  const r = status.rotation
  const stage = !r || r.state === 'failed' ? 2 : r.state === 'applying' ? 3 : 4
  // Step 1 happens at the provider, out of sight: it shows as done only once a key was applied.
  const look = (n: number): Look => (n === 1 && stage === 2 ? 'ready' : n < stage ? 'done' : n === stage ? 'cur' : 'todo')

  const submit = () => {
    setTried(true)
    if (!accessKey.trim() || !secret.trim()) return
    start.mutate({ accessKeyId: accessKey.trim(), secretKey: secret.trim() }, {
      onSuccess: () => {
        // The secret is not kept on the page once sent.
        setSecret('')
        setTried(false)
      },
    })
  }

  return (
    <>
      {flash && <Notice className="mb-3.5 max-w-[760px]" state={<State led="ok">{t.key.closedState}</State>} text={flash} />}
      <ol className="m-0 grid max-w-[760px] list-none gap-2.5 p-0">
        <Step n={1} look={look(1)} title={t.key.step1Title} body={rich(t.key.step1Body, { bucket: <b className="num font-normal text-ink">{bucket}</b> })} />
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
                  <State led="err">{t.key.probeFailed}</State>
                  <span className="text-ink-2">{rich(t.key.probeFailedBody, { bucket: <b className="num font-normal text-ink">{bucket}</b> })}</span>
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
            <Progress
              items={[
                { label: t.key.progressProbe, state: r.probe },
                ...r.disks.map((d) => ({ label: fmt(t.key.progressDisk, { name: d.name }), state: d.state })),
                {
                  label: t.key.progressMacs,
                  state: r.macs.updated >= r.macs.total && r.disks.every((d) => d.state === 'done') ? 'done' : r.disks.every((d) => d.state === 'done') ? 'running' : 'pending',
                  aside: r.disks.every((d) => d.state === 'done') ? fmt(t.key.progressCount, { n: r.macs.updated, total: r.macs.total }) : '',
                },
              ]}
            />
          )}
        </Step>
        <Step n={4} look={look(4)} title={t.key.step4Title} body={t.key.step4Body}>
          {r?.state === 'awaiting_old_key_deletion' && (
            <>
              <Checkbox label={rich(t.key.confirmDeleted, { tail: <span className="num">{r.oldKeyTail}</span> })} checked={deleted} onChange={(e) => setDeleted(e.target.checked)} />
              {close.isError && <p className="text-[13px] text-bad-text" role="alert">{errorText(close.error)}</p>}
              <div>
                <Button variant="ink" disabled={!deleted} busy={close.isPending} onClick={() => close.mutate(r.id, { onSuccess: () => setFlash(t.key.closedDone) })}>
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

/** The key page. */
export function KeyPage() {
  const { t } = useI18n()
  const key = useKeyStatus()
  return (
    <>
      <PageHeader title={t.key.title} lead={t.key.lead} />
      <QueryView query={key} loading={<div className="notice max-w-[760px] px-[18px] py-4"><SkeletonLines /></div>}>
        {(status) => (
          <>
            <RiskBox status={status} />
            <Steps status={status} />
          </>
        )}
      </QueryView>
    </>
  )
}
