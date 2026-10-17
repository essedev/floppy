/*
  Dates, sizes and durations in the UI's language: "3 min fa", "ieri, 18:40", "4,82 TB",
  "2 h 14 min 05 s". One hook so every screen writes them the same way.
*/

import { useMemo } from 'react'
import { fmt, useI18n } from '@/i18n'

const MIN = 60_000
const HOUR = 60 * MIN
const DAY = 24 * HOUR

/** Formatters bound to the current language. */
export interface Format {
  /** "adesso", "12 min fa", "3 h fa", "ieri, 18:40", "14 ott, 17:02". */
  relative(iso: string, now?: number): string
  /** "14 ott 2026". */
  date(iso: string): string
  /** "4,82 TB", "212 GB". */
  bytes(n: number): string
  /** "38.214". */
  number(n: number): string
  /** "12 ms", "3,4 s". */
  latency(ms: number): string
  /** A running duration: "2 h 14 min 05 s", or "3 g 2 h 14 min" past a day. */
  elapsed(ms: number): string
  /** "0:42". */
  countdown(ms: number): string
}

const pad = (n: number) => String(n).padStart(2, '0')

/** The formatters for the current language. */
export function useFormat(): Format {
  const { t, locale } = useI18n()
  return useMemo(() => {
    const time = new Intl.DateTimeFormat(locale, { hour: '2-digit', minute: '2-digit' })
    const dayMonth = new Intl.DateTimeFormat(locale, { day: 'numeric', month: 'short' })
    const full = new Intl.DateTimeFormat(locale, { day: 'numeric', month: 'short', year: 'numeric' })
    const num = new Intl.NumberFormat(locale)
    const dec = new Intl.NumberFormat(locale, { maximumFractionDigits: 2, minimumFractionDigits: 2 })
    const one = new Intl.NumberFormat(locale, { maximumFractionDigits: 1 })
    const startOfDay = (ms: number) => new Date(new Date(ms).toDateString()).getTime()
    // Intl puts a dot in Italian short months ("ott."); the UI writes them without.
    const short = (d: Date) => dayMonth.format(d).replace('.', '')

    return {
      relative(iso, now = Date.now()) {
        const at = Date.parse(iso)
        const diff = Math.max(0, now - at)
        if (diff < MIN) return t.time.now
        if (diff < HOUR) return fmt(t.time.minutesAgo, { n: Math.floor(diff / MIN) })
        if (diff < 6 * HOUR) return fmt(t.time.hoursAgo, { n: Math.floor(diff / HOUR) })
        const today = startOfDay(now)
        if (at >= today) return fmt(t.time.today, { time: time.format(at) })
        if (at >= today - DAY) return fmt(t.time.yesterday, { time: time.format(at) })
        const d = new Date(at)
        if (d.getFullYear() !== new Date(now).getFullYear()) return full.format(d).replace('.', '')
        return `${short(d)}, ${time.format(at)}`
      },
      date: (iso) => full.format(new Date(iso)).replace('.', ''),
      bytes(n) {
        if (n >= 1e12) return `${dec.format(n / 1e12)} TB`
        if (n >= 1e9) return `${num.format(Math.round(n / 1e9))} GB`
        if (n >= 1e6) return `${num.format(Math.round(n / 1e6))} MB`
        return `${num.format(Math.round(n / 1e3))} KB`
      },
      number: (n) => num.format(n),
      latency: (ms) => (ms < 1000 ? `${num.format(Math.round(ms))} ms` : `${one.format(ms / 1000)} s`),
      elapsed(ms) {
        const s = Math.floor(Math.max(0, ms) / 1000)
        const d = Math.floor(s / 86_400)
        const h = Math.floor((s % 86_400) / 3600)
        const m = Math.floor((s % 3600) / 60)
        if (d > 0) return fmt(t.time.elapsedDays, { d, h, m: pad(m) })
        if (h > 0) return fmt(t.time.elapsedHours, { h, m: pad(m), s: pad(s % 60) })
        return fmt(t.time.elapsedMinutes, { m, s: pad(s % 60) })
      },
      countdown(ms) {
        const s = Math.ceil(Math.max(0, ms) / 1000)
        return `${Math.floor(s / 60)}:${pad(s % 60)}`
      },
    }
  }, [t, locale])
}
