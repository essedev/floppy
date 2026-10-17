/*
  Organization settings (concept-e-admin, "Impostazioni"): the name, the SMTP server for
  sign-in links with a test email, and the S3 storage with a test write that changes nothing.
  Only admins invite: there is no setting for it. One form, saved together; tests run on what is typed, before saving.
*/

import { Mail } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { Button } from '@/components/Button'
import { Readout, SlotNote, State } from '@/components/detail'
import { Field, Input } from '@/components/Field'
import { useErrorText } from '@/components/error-text'
import { PageHeader, QueryView, SkeletonLines } from '@/components/states'
import { fmt, rich, useI18n } from '@/i18n'
import { isApiError, type Settings } from '@/lib/api'
import { useFlash } from '@/lib/hooks'
import { useCurrentSession } from '@/features/auth/hooks'
import { useProbeStorage, useSaveSettings, useSettings, useTestMail } from './hooks'

interface Form {
  organizationName: string
  host: string
  port: string
  username: string
  password: string
  sender: string
  endpoint: string
  region: string
  bucket: string
}

const toForm = (s: Settings): Form => ({
  organizationName: s.organizationName,
  host: s.mail.host,
  port: String(s.mail.port),
  username: s.mail.username,
  password: '',
  sender: s.mail.sender,
  endpoint: s.storage.endpoint,
  region: s.storage.region,
  bucket: s.storage.bucket,
})

function Section({ title, lead, children }: { title: string; lead?: string; children: ReactNode }) {
  return (
    <section className="grid gap-3.5 border-t border-line pt-[22px] first:border-t-0 first:pt-0">
      <div>
        <h2 className="text-base font-semibold">{title}</h2>
        {lead && <p className="mt-0.5 text-sm text-ink-2">{lead}</p>}
      </div>
      {children}
    </section>
  )
}

function SettingsForm({ settings }: { settings: Settings }) {
  const { t } = useI18n()
  const errorText = useErrorText()
  const save = useSaveSettings()
  const testMail = useTestMail()
  const probe = useProbeStorage()
  const [form, setForm] = useState<Form>(() => toForm(settings))
  const [tried, setTried] = useState(false)
  const [saved, setSaved] = useFlash<boolean>()
  const initial = toForm(settings)
  const dirty = (Object.keys(form) as (keyof Form)[]).some((k) => form[k] !== initial[k])
  const set = <K extends keyof Form>(key: K) => (value: Form[K]) => setForm((f) => ({ ...f, [key]: value }))
  const port = Number(form.port)
  const portOk = Number.isInteger(port) && port >= 1 && port <= 65535
  const required: (keyof Form)[] = ['organizationName', 'host', 'username', 'sender', 'endpoint', 'region', 'bucket']
  const missing = (k: keyof Form) => tried && String(form[k]).trim() === ''

  const submit = () => {
    setTried(true)
    if (!portOk || required.some((k) => String(form[k]).trim() === '')) return
    save.mutate(
      {
        organizationName: form.organizationName.trim(),
        mail: { host: form.host.trim(), port, username: form.username.trim(), sender: form.sender.trim(), password: form.password || undefined },
        // Storage is sent only while no disk holds it in its format (storage_in_use otherwise).
        storage: settings.storageLocked ? undefined : { endpoint: form.endpoint.trim(), region: form.region.trim(), bucket: form.bucket.trim() },
      },
      {
        onSuccess: (next) => {
          setForm(toForm(next))
          setTried(false)
          setSaved(true)
        },
      },
    )
  }

  const text = (key: keyof Form, label: string, opts: { mono?: boolean; type?: string; hint?: string; autoComplete?: string; disabled?: boolean } = {}) => (
    <Field label={label} hint={opts.hint} error={missing(key) ? t.settings.required : null}>
      {(id, describedBy) => (
        <Input
          id={id}
          mono={opts.mono}
          type={opts.type}
          autoComplete={opts.autoComplete ?? 'off'}
          disabled={opts.disabled}
          value={String(form[key])}
          invalid={missing(key)}
          aria-describedby={describedBy}
          onChange={(e) => set(key)(e.target.value as never)}
        />
      )}
    </Field>
  )

  return (
    <form
      className="grid gap-[30px]"
      noValidate
      onSubmit={(e) => {
        e.preventDefault()
        submit()
      }}
    >
      <Section title={t.settings.orgTitle}>{text('organizationName', t.settings.orgName)}</Section>

      <Section title={t.settings.mailTitle} lead={t.settings.mailLead}>
        <div className="grid gap-3.5 sm:grid-cols-2 xl:grid-cols-3">
          {text('host', t.settings.host, { mono: true })}
          <Field label={t.settings.port} error={tried && !portOk ? t.settings.portRange : null}>
            {(id, describedBy) => <Input id={id} mono inputMode="numeric" value={form.port} invalid={tried && !portOk} aria-describedby={describedBy} onChange={(e) => set('port')(e.target.value)} />}
          </Field>
          {text('username', t.settings.username, { mono: true })}
          {text('password', t.settings.password, { type: 'password', autoComplete: 'new-password', hint: settings.mail.passwordSet ? t.settings.passwordSet : t.settings.passwordUnset })}
          {text('sender', t.settings.sender)}
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Button busy={testMail.isPending} onClick={() => testMail.mutate()}>
            <Mail size={17} aria-hidden />
            {t.settings.testMail}
          </Button>
          {testMail.isSuccess && <State led="ok">{fmt(t.settings.testSent, { email: testMail.data.sentTo })}</State>}
          {testMail.isError && (
            <span className="inline-flex items-center gap-2 text-sm">
              <State led="err">{t.settings.testFailed}</State>
              <span className="text-ink-2">{errorText(testMail.error)}</span>
            </span>
          )}
        </div>
      </Section>

      <Section title={t.settings.storageTitle} lead={t.settings.storageLead}>
        {settings.storageLocked && <SlotNote className="text-[13.5px] text-ink-2">{t.settings.storageLocked}</SlotNote>}
        <div className="grid gap-3.5 sm:grid-cols-2 xl:grid-cols-3">
          {text('endpoint', t.settings.endpoint, { mono: true, disabled: settings.storageLocked })}
          {text('region', t.settings.region, { mono: true, disabled: settings.storageLocked })}
        </div>
        {text('bucket', t.settings.bucket, { mono: true, disabled: settings.storageLocked })}
        <div>
          <Button busy={probe.isPending} onClick={() => probe.mutate({ endpoint: form.endpoint, region: form.region, bucket: form.bucket })}>
            {t.settings.probe}
          </Button>
        </div>
        {probe.isSuccess && <div><State led="ok">{t.settings.probeOk}</State></div>}
        {probe.isError && (
          <div className="notice grid gap-1.5 px-4 py-3.5 text-sm" role="alert">
            <State led="err">{t.settings.probeFail}</State>
            <span className="text-ink-2">
              {isApiError(probe.error, 'probe_failed')
                ? rich(t.settings.probeFailBody, { bucket: <b className="num font-normal text-ink">{probe.variables?.bucket}</b> })
                : errorText(probe.error)}
            </span>
            {isApiError(probe.error, 'probe_failed') && (
              <code className="num text-[12.5px] text-ink-2">
                {String(probe.error.details.code ?? '')} · {String(probe.error.details.request ?? '')}
              </code>
            )}
          </div>
        )}
      </Section>


      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" variant="ink" busy={save.isPending} disabled={!dirty}>
          {save.isPending ? t.common.saving : t.common.save}
        </Button>
        <Button variant="ghost" disabled={!dirty || save.isPending} onClick={() => {
          setForm(initial)
          setTried(false)
        }}>
          {t.settings.discard}
        </Button>
        {dirty && !save.isPending && <Readout className="ml-2">{t.settings.unsaved}</Readout>}
        {saved && !dirty && <State led="ok">{t.common.saved}</State>}
        {save.isError && <span className="text-[13px] text-bad-text" role="alert">{errorText(save.error)}</span>}
      </div>
    </form>
  )
}

/** The settings page. */
export function SettingsPage() {
  const { t } = useI18n()
  const session = useCurrentSession()
  const settings = useSettings()
  return (
    <>
      <PageHeader title={t.settings.title} lead={session.organization.name} />
      <QueryView query={settings} loading={<SkeletonLines lines={6} />}>
        {(data) => <SettingsForm settings={data} />}
      </QueryView>
    </>
  )
}
