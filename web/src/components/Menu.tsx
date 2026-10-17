/*
  A small action menu anchored to its trigger; closes on Escape, outside clicks, scroll and
  choice. Fixed positioning, so a table's horizontal scroll container does not clip it.
*/

import { useEffect, useId, useRef, useState, type CSSProperties, type MouseEvent, type ReactNode } from 'react'

/** One entry of a Menu. */
export interface MenuItem {
  label: ReactNode
  onSelect(): void
  disabled?: boolean
  hint?: string
  destructive?: boolean
  checked?: boolean
}

/** A trigger and its menu; `trigger` receives the props the button needs. */
export function Menu({ items, trigger, align = 'right' }: { items: MenuItem[]; align?: 'left' | 'right'; trigger(props: { 'aria-haspopup': 'menu'; 'aria-expanded': boolean; 'aria-controls': string; onClick(e: MouseEvent<HTMLElement>): void }): ReactNode }) {
  const [open, setOpen] = useState(false)
  const [place, setPlace] = useState<CSSProperties>({})
  const id = useId()
  const root = useRef<HTMLDivElement>(null)
  const list = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    list.current?.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus()
    const onDown = (e: PointerEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpen(false)
        root.current?.querySelector<HTMLButtonElement>('button')?.focus()
      }
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault()
        const buttons = [...(list.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') ?? [])]
        const at = buttons.indexOf(document.activeElement as HTMLButtonElement)
        const next = buttons[(at + (e.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length]
        next?.focus()
      }
    }
    const onMove = () => setOpen(false)
    document.addEventListener('pointerdown', onDown)
    document.addEventListener('keydown', onKey)
    window.addEventListener('resize', onMove)
    document.addEventListener('scroll', onMove, true)
    return () => {
      document.removeEventListener('pointerdown', onDown)
      document.removeEventListener('keydown', onKey)
      window.removeEventListener('resize', onMove)
      document.removeEventListener('scroll', onMove, true)
    }
  }, [open])

  const toggle = (e: MouseEvent<HTMLElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    // Open upwards when the menu would run past the bottom of the window.
    const up = rect.bottom + 12 + items.length * 38 > window.innerHeight
    const vertical = up ? { bottom: window.innerHeight - rect.top + 4 } : { top: rect.bottom + 4 }
    setPlace({ ...vertical, ...(align === 'right' ? { right: window.innerWidth - rect.right } : { left: rect.left }) })
    setOpen((v) => !v)
  }

  return (
    <div ref={root} className="relative inline-block">
      {trigger({ 'aria-haspopup': 'menu', 'aria-expanded': open, 'aria-controls': id, onClick: toggle })}
      {open && (
        <div
          ref={list}
          id={id}
          role="menu"
          style={place}
          className="fixed z-30 grid min-w-[200px] gap-0.5 rounded-xl bg-surface p-1.5 shadow-pop"
        >
          {items.map((item, i) => (
            <button
              key={i}
              type="button"
              role={item.checked === undefined ? 'menuitem' : 'menuitemradio'}
              aria-checked={item.checked}
              disabled={item.disabled}
              title={item.hint}
              onClick={() => {
                setOpen(false)
                item.onSelect()
              }}
              className={`flex h-9 items-center justify-between gap-6 rounded-lg border-0 bg-transparent px-2.5 text-left text-sm whitespace-nowrap hover:bg-sunken focus-visible:bg-sunken disabled:opacity-45 ${item.destructive ? 'text-bad-text' : 'text-ink'}`}
            >
              <span>{item.label}</span>
              {item.checked && <span className="slot-mark m-0" aria-hidden />}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
