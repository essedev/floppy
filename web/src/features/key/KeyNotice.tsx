/*
  The "change the key" reminder (DECISIONS #12, #18, #24): in manual storage, after someone who
  saw a disk lost access, it stays on the overview, People and Devices with the time running,
  until that disk's old key is deleted. One disk names it; several point to the keys page.
*/

import { Link } from 'react-router'
import { Notice, State } from '@/components/detail'
import { fmt, rich, useI18n } from '@/i18n'
import { useFormat } from '@/lib/format'
import { useNow } from '@/lib/hooks'
import { openWindows, useKeyStatus } from './hooks'

/** The reminder, or nothing while every window is closed. */
export function KeyNotice({ urgent = false }: { urgent?: boolean }) {
  const { t } = useI18n()
  const f = useFormat()
  const now = useNow(1000)
  const key = useKeyStatus()
  const open = openWindows(key.data)
  const first = open[0]
  if (!first) return null

  const reason = first.window.reasons[0]
  const disk = <b className="font-semibold">{first.disk.diskName}</b>
  const text =
    open.length > 1
      ? fmt(t.keyNotice.disks, { n: open.length, date: f.relative(first.window.openedAt, now) })
      : first.window.reasons.length === 1 && reason
        ? rich(t.keyNotice.one, { name: <b className="font-semibold">{reason.label}</b>, date: f.relative(reason.at, now), disk })
        : rich(t.keyNotice.many, { n: first.window.reasons.length, date: f.relative(first.window.openedAt, now), disk })
  const to = open.length > 1 ? '/chiave' : `/chiave?disco=${encodeURIComponent(first.disk.diskId)}`

  return (
    <Notice
      className="mb-3.5"
      state={<State led="dl">{t.keyNotice.state}</State>}
      text={text}
      aside={fmt(t.keyNotice.since, { time: f.elapsed(now - Date.parse(first.window.openedAt)) })}
      action={
        <Link to={to} className="inline-flex h-[34px] items-center rounded-[9px] bg-ink px-3 text-[13px] font-semibold whitespace-nowrap text-surface no-underline hover:opacity-90">
          {open.length > 1 ? t.keyNotice.actionMany : urgent ? t.keyNotice.actionNow : t.keyNotice.action}
        </Link>
      }
    />
  )
}
