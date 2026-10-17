/*
  The strip that marks the fake API, on every page while it runs: no one should mistake the
  seeded organization for real data. It also stands in for the mailbox (the last link and
  code "sent") and can take the fake network down to show the error states.
*/

import { useQueryClient } from '@tanstack/react-query'
import { useSyncExternalStore } from 'react'
import { Link } from 'react-router'
import { Led, Readout } from '@/components/detail'
import { fmt, useI18n } from '@/i18n'
import { fakeControl } from '@/lib/api'

const noop = () => () => {}

/** The fake API's banner; renders nothing when the app talks HTTP. */
export function FakeBanner() {
  const { t } = useI18n()
  const client = useQueryClient()
  const control = fakeControl
  // Re-render on every change the fake announces; the snapshot is a cheap string.
  useSyncExternalStore(control?.subscribe ?? noop, () => (control ? `${control.isFailing()}|${control.outbox()?.token ?? ''}` : ''))
  if (!control) return null

  const mail = control.outbox()
  const failing = control.isFailing()
  const button = 'h-7 rounded-md border-0 bg-raised px-2.5 text-[12.5px] text-ink hover:bg-line'

  return (
    <aside className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line bg-sunken px-4 py-2 text-[13px] text-ink-2" aria-label={t.fake.label}>
      <span className="inline-flex items-center gap-2">
        <Led state={failing ? 'err' : 'off'} />
        <Readout>{t.fake.label}</Readout>
      </span>
      <span className="max-sm:hidden">{t.fake.detail}</span>
      {mail && (
        <span className="inline-flex flex-wrap items-center gap-x-2">
          <span>{fmt(t.fake.mail, { to: mail.to })}:</span>
          <Link
            to={`${mail.kind === 'invite' ? '/invito' : '/accedi/link'}?token=${encodeURIComponent(mail.token)}`}
            target="_blank"
            className="font-semibold text-ink underline underline-offset-2"
          >
            {t.fake.openLink}
          </Link>
          {mail.code && (
            <>
              <span>·</span>
              <span className="num text-ink">{fmt(t.fake.code, { code: mail.code })}</span>
            </>
          )}
        </span>
      )}
      <span className="ml-auto flex flex-wrap gap-1.5">
        <button
          type="button"
          className={button}
          aria-pressed={failing}
          onClick={() => {
            control.setFailing(!failing)
            void client.invalidateQueries()
          }}
        >
          {failing ? t.fake.failOff : t.fake.failOn}
        </button>
        <button
          type="button"
          className={button}
          onClick={() => {
            control.reset('empty')
            location.assign('/accedi')
          }}
        >
          {t.fake.empty}
        </button>
        <button
          type="button"
          className={button}
          onClick={() => {
            control.reset('managed')
            location.assign('/accedi')
          }}
        >
          {t.fake.managed}
        </button>
        <button
          type="button"
          className={button}
          onClick={() => {
            control.reset('default')
            location.assign('/accedi')
          }}
        >
          {t.fake.reset}
        </button>
      </span>
    </aside>
  )
}
