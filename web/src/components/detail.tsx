/*
  Detail set A (DECISIONS #11, design/tokens.css): the LED in its bezel for state, the
  Departure Mono readout for state text, the slot as the mark of lists and notes, notices on a
  sunken surface. No pills, no badges, no glowing dots, no coloured side stripes.
*/

import type { ReactNode } from 'react'

/** The state an LED shows: connected, activity, error, off. */
export type LedState = 'ok' | 'dl' | 'err' | 'off'

/** An LED in its bezel. Blinks only for real activity. */
export function Led({ state, blink = false, label }: { state: LedState; blink?: boolean; label?: string }) {
  return (
    <span
      className="led"
      data-state={state === 'off' ? undefined : state}
      data-blink={blink || undefined}
      role={label ? 'img' : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      title={label}
    />
  )
}

/** State text: uppercase Departure Mono, never coloured. */
export function Readout({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <span className={`readout ${className}`}>{children}</span>
}

/** A state: the LED followed by its readout. */
export function State({ led, blink, children }: { led: LedState; blink?: boolean; children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-[9px] whitespace-nowrap">
      <Led state={led} blink={blink} />
      <Readout>{children}</Readout>
    </span>
  )
}

/** The slot mark before a note or a list item, centred on the first line. */
export function SlotMark() {
  return <span className="slot-mark -translate-y-0.5" aria-hidden />
}

/** A note led by the slot mark. */
export function SlotNote({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <p className={`flex items-baseline ${className}`}>
      <SlotMark />
      <span>{children}</span>
    </p>
  )
}

/** A list whose items are led by the slot mark. */
export function SlotList({ items, className = '' }: { items: ReactNode[]; className?: string }) {
  return (
    <ul className={`m-0 grid list-none gap-2 p-0 ${className}`}>
      {items.map((item, i) => (
        <li key={i} className="flex items-baseline">
          <SlotMark />
          <span>{item}</span>
        </li>
      ))}
    </ul>
  )
}

/** A notice on the sunken surface: a state, its text, an optional action on the right. */
export function Notice({ state, text, aside, action, className = '' }: { state?: ReactNode; text: ReactNode; aside?: ReactNode; action?: ReactNode; className?: string }) {
  return (
    <div className={`notice flex flex-wrap items-center gap-x-4 gap-y-2 px-[18px] py-3.5 text-sm ${className}`} role="status">
      {state}
      <p className="min-w-[16rem] flex-1">{text}</p>
      {aside && <span className="num text-[13px] whitespace-nowrap text-ink-2">{aside}</span>}
      {action}
    </div>
  )
}

/** Initials in a circle, for people. */
export function Avatar({ name, size = 34 }: { name: string; size?: number }) {
  const initials = name
    .split(/[\s@.]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w.charAt(0).toUpperCase())
    .join('')
  return (
    <span
      className="grid shrink-0 place-items-center rounded-full bg-sunken text-[13px] font-bold text-ink-2"
      style={{ width: size, height: size }}
      aria-hidden
    >
      {initials}
    </span>
  )
}

/** A raised panel, the "well" of the concept. */
export function Well({ title, children, className = '' }: { title?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-2xl bg-raised px-5 py-[18px] ${className}`}>
      {title && <h2 className="eyebrow">{title}</h2>}
      {children}
    </section>
  )
}
