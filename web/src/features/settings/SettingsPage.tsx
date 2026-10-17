/*
  Organization settings (concept-e-admin, "Impostazioni"): the name, the SMTP server and the
  language of the mails, with a test email, and the storage: its mode, fixed when the
  organization was created (DECISIONS #24), and the default endpoint and region of new disks,
  locked once a disk exists. Each disk brings its own bucket, tested when it is created. Only
  admins invite: there is no setting for it. One form, saved together.
*/

import { Mail } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { Button } from '@/components/Button'
import { Readout, SlotNote, State } from '@/components/detail'
import { Choice, Field, Input } from '@/components/Field'
import { providerName } from '@/lib/providers'
import { useErrorText } from '@/components/error-text'
import { PageHeader, QueryView, SkeletonLines } from '@/components/states'
import { fmt, useI18n } from '@/i18n'
import type { MailLanguage, Settings } from '@/lib/api'
import { useFlash } from '@/lib/hooks'
import { useCurrentSession } from '@/features/auth/hooks'
import { useSaveSettings, useSettings, useTestMail } from './hooks'

interface Form {
  organizationName: string
  mailLanguage: MailLanguage
  host: string
  port: string
  username: string
  password: string
  sender: string
  endpoint: string
  region: string
}

const toForm = (s: Settings): Form => ({
  organizationName: s.organizationName,
  mailLanguage: s.mailLanguage,
  host: s.mail.host,
  port: String(s.mail.port || 587),
  username: s.mail.username,
  password: '',
  sender: s.mail.sender,
  endpoint: s.storage.endpoint,
  region: s.storage.region,
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

type TextKey = Exclude<keyof Form, 'mailLanguage'>

function SettingsForm({ settings }: { settings: Settings }) {
  const { t } = useI18n()
  const errorText = useErrorText()
  const save = useSaveSettings()
  const testMail = useTestMail()
  const [form, setForm] = useState<Form>(() => toForm(settings))
  const [tried, setTried] = useState(false)
  const [saved, setSaved] = useFlash<boolean>()
  const initial = toForm(settings)
  const dirty = (Object.keys(form) as (keyof Form)[]).some((k) => form[k] !== initial[k])
  const set = <K extends keyof Form>(key: K) => (value: Form[K]) => setForm((f) => ({ ...f, [key]: value }))
  // An empty host returns to the installation's mail server: then nothing else is needed.
  const ownServer = form.host.trim() !== ''
  const port = Number(form.port)
  const portOk = !ownServer || (Number.isInteger(port) && port >= 1 && port <= 65535)
  const required: TextKey[] = ['organizationName', ...(ownServer ? (['sender'] as const) : []), ...(settings.storageLocked ? [] : (['endpoint'] as const))]
  const missing = (k: TextKey) => tried && required.includes(k) && form[k].trim() === ''
  const manual = settings.storageMode === 'manual'

  const submit = () => {
    setTried(true)
    if (!portOk || required.some((k) => form[k].trim() === '')) return
    save.mutate(
      {
        organizationName: form.organizationName.trim(),
        mailLanguage: form.mailLanguage,
        mail: { host: form.host.trim(), port: ownServer ? port : 0, username: form.username.trim(), sender: form.sender.trim(), password: form.password || undefined },
        // The defaults are sent only while no disk exists (storage_in_use otherwise).
        storage: settings.storageLocked ? undefined : { endpoint: form.endpoint.trim(), region: form.region.trim() },
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

  const text = (key: TextKey, label: string, opts: { mono?: boolean; type?: string; hint?: string; autoComplete?: string; disabled?: boolean } = {}) => (
    <Field label={label} hint={opts.hint} error={missing(key) ? t.settings.required : null}>
      {(id, describedBy) => (
        <Input
          id={id}
          mono={opts.mono}
          type={opts.type}
          autoComplete={opts.autoComplete ?? 'off'}
          disabled={opts.disabled}
          value={form[key]}
          invalid={missing(key)}
          aria-describedby={describedBy}
          onChange={(e) => set(key)(e.target.value)}
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
          {text('host', t.settings.host, { mono: true, hint: t.settings.hostHint })}
          <Field label={t.settings.port} error={tried && !portOk ? t.settings.portRange : null}>
            {(id, describedBy) => <Input id={id} mono inputMode="numeric" disabled={!ownServer} value={form.port} invalid={tried && !portOk} aria-describedby={describedBy} onChange={(e) => set('port')(e.target.value)} />}
          </Field>
          {text('username', t.settings.username, { mono: true, disabled: !ownServer })}
          {text('password', t.settings.password, { type: 'password', autoComplete: 'new-password', disabled: !ownServer, hint: settings.mail.passwordSet ? t.settings.passwordSet : t.settings.passwordUnset })}
          {text('sender', t.settings.sender, { disabled: !ownServer })}
        </div>
        <div className="grid gap-1.5">
          <span className="text-[13px] font-semibold">{t.settings.mailLanguage}</span>
          <div className="max-w-[560px]">
            <Choice<MailLanguage>
              label={t.settings.mailLanguage}
              value={form.mailLanguage}
              onChange={set('mailLanguage')}
              options={(['it', 'en'] as const).map((lang) => ({ value: lang, title: t.settings.mailLanguages[lang], hint: t.settings.mailLanguageHints[lang] }))}
            />
          </div>
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
        <dl className="m-0 grid max-w-[72ch] gap-1 text-sm">
          <dt className="text-[13px] font-semibold">{t.settings.storageMode}</dt>
          <dd className="m-0">
            <b className="font-semibold">{manual ? t.settings.storageManual : t.settings.storageManaged}</b>
            <span className="text-ink-2"> · {manual ? t.settings.storageManualHint : fmt(t.settings.storageManagedHint, { provider: providerName(settings.storageProvider) })}</span>
          </dd>
          <dd className="m-0 text-[13px] text-ink-3">{t.settings.storageModeFixed}</dd>
        </dl>
        {settings.storageLocked && <SlotNote className="text-[13.5px] text-ink-2">{t.settings.storageLocked}</SlotNote>}
        <div className="grid gap-3.5 sm:grid-cols-2 xl:grid-cols-3">
          {text('endpoint', t.settings.endpoint, { mono: true, disabled: settings.storageLocked })}
          {text('region', t.settings.region, { mono: true, disabled: settings.storageLocked })}
        </div>
      </Section>

      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" variant="ink" busy={save.isPending} disabled={!dirty}>
          {save.isPending ? t.common.saving : t.common.save}
        </Button>
        <Button
          variant="ghost"
          disabled={!dirty || save.isPending}
          onClick={() => {
            setForm(initial)
            setTried(false)
          }}
        >
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
