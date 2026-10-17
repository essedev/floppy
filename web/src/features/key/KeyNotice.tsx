/*
  The "change the key" reminder (DECISIONS #12): after a removal or a revocation it stays on
  the overview and on People, with the time running, until the old key is deleted.
*/

import { Link } from 'react-router'
import { Notice, State } from '@/components/detail'
import { fmt, rich, useI18n } from '@/i18n'
import { useFormat } from '@/lib/format'
import { useNow } from '@/lib/hooks'
import { useKeyStatus } from './hooks'

/** The reminder, or nothing while the window is closed. */
export function KeyNotice({ urgent = false }: { urgent?: boolean }) {
  const { t } = useI18n()
  const f = useFormat()
  const now = useNow(1000)
  const key = useKeyStatus()
  const window = key.data?.window
  if (!window) return null

  const first = window.reasons[0]
  const text =
    window.reasons.length === 1 && first
      ? rich(t.keyNotice.one, { name: <b className="font-semibold">{first.label}</b>, date: f.relative(first.at, now) })
      : fmt(t.keyNotice.many, { n: window.reasons.length, date: f.relative(window.openedAt, now) })

  return (
    <Notice
      className="mb-3.5"
      state={<State led="dl">{t.keyNotice.state}</State>}
      text={text}
      aside={fmt(t.keyNotice.since, { time: f.elapsed(now - Date.parse(window.openedAt)) })}
      action={
        <Link to="/chiave" className="inline-flex h-[34px] items-center rounded-[9px] bg-ink px-3 text-[13px] font-semibold whitespace-nowrap text-surface no-underline hover:opacity-90">
          {urgent ? t.keyNotice.actionNow : t.keyNotice.action}
        </Link>
      }
    />
  )
}
