/*
  The marks of the identity (DECISIONS #11): the wordmark (the menu bar icon's slot and LED in
  colour, then the name), the app icon from design/mark/, and the Grafite faceplate, dark in
  both themes with the LED on the slot's axis. Geometry copied from docs/concepts/concept-e-admin.html.
*/

import { useId } from 'react'
import appIcon from '@design/mark/app-icon.svg'
import { brand } from '@/brand'

/** The slot and LED followed by the product name. */
export function Wordmark({ size = 21 }: { size?: number }) {
  return (
    <span className="inline-flex items-center gap-[9px] leading-none font-extrabold tracking-[-0.035em] text-ink" style={{ fontSize: size }}>
      <svg viewBox="0 0 24 16" className="h-[1em] w-[1.5em] shrink-0" aria-hidden>
        <rect x="2" y="6.5" width="13" height="3" rx="1.5" fill="var(--ink)" />
        <circle cx="19.75" cy="8" r="3.4" fill="var(--bezel)" />
        <circle cx="19.75" cy="8" r="2.25" fill="var(--brand)" />
        <ellipse cx="19.75" cy="7.05" rx="1.05" ry=".55" fill="#fff" opacity=".5" />
      </svg>
      <span>{brand.wordmark}</span>
    </span>
  )
}

/** The app icon, where the text talks about installing the app. */
export function AppIcon({ size = 40 }: { size?: number }) {
  return <img src={appIcon} width={size} height={size} alt="" className="shrink-0" />
}

/** LED colours of the dark faceplate, from design/tokens.css (--led-*-plate). */
const PLATE_LED = { ok: 'var(--led-ok-plate)', dl: 'var(--led-dl-plate)', err: 'var(--led-err-plate)', off: 'var(--led-off-plate)' }

/** A disk's Grafite faceplate: `fill` (0 to 1) fills the slot, the LED says the state. */
export function Faceplate({ state = 'ok', fill = 0, label, width = 96 }: { state?: keyof typeof PLATE_LED; fill?: number; label?: string; width?: number }) {
  const slot = Math.max(0, Math.min(1, fill)) * 49
  const gradient = useId()
  return (
    <svg width={width} height={(width * 58) / 96} viewBox="0 0 96 58" role={label ? 'img' : undefined} aria-label={label} aria-hidden={label ? undefined : true} className="shrink-0">
      <defs>
        <linearGradient id={gradient} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="var(--plate-hi)" />
          <stop offset="1" stopColor="var(--plate-lo)" />
        </linearGradient>
      </defs>
      <rect x=".5" y=".5" width="95" height="57" rx="10" fill={`url(#${gradient})`} stroke="var(--plate-edge)" />
      <rect x="1.5" y="1.5" width="93" height="55" rx="9" fill="none" stroke="#fff" strokeOpacity=".08" />
      <rect x="11" y="24" width="54" height="10" rx="5" fill="var(--plate-slot)" />
      {slot > 0 && <rect x="13.5" y="26.5" width={Math.max(5, slot).toFixed(1)} height="5" rx="2.5" fill="var(--plate-fill)" />}
      <circle cx="80" cy="29" r="8" fill="var(--plate-slot)" />
      <circle cx="80" cy="29" r="4.5" fill={PLATE_LED[state]} />
      {state !== 'off' && <ellipse cx="80" cy="27" rx="2.1" ry="1.1" fill="#fff" opacity=".5" />}
    </svg>
  )
}
