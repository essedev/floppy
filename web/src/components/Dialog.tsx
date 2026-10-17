/*
  A modal on the native <dialog>: focus trap, Escape and the top layer come from the browser.
  Rendered only while open, so its form state starts fresh every time.
*/

import { useEffect, useId, useRef, type FormEvent, type ReactNode } from 'react'

/** Props of Dialog. With onSubmit the body is a form and Enter submits it. */
export interface DialogProps {
  title: ReactNode
  children: ReactNode
  actions: ReactNode
  onClose(): void
  onSubmit?(): void
  /** While a request runs, Escape and the backdrop do not close it. */
  busy?: boolean
}

/** A modal dialog, open while mounted. */
export function Dialog({ title, children, actions, onClose, onSubmit, busy = false }: DialogProps) {
  const ref = useRef<HTMLDialogElement>(null)
  const titleId = useId()

  useEffect(() => {
    const dialog = ref.current
    if (dialog && !dialog.open) dialog.showModal()
    return () => dialog?.close()
  }, [])

  const submit = (e: FormEvent) => {
    e.preventDefault()
    onSubmit?.()
  }
  const body = (
    <>
      <h2 id={titleId} className="text-[21px] leading-tight font-bold tracking-[-0.02em]">
        {title}
      </h2>
      <div className="grid gap-4 text-[14.5px] text-ink-2">{children}</div>
      <div className="flex flex-wrap justify-end gap-2">{actions}</div>
    </>
  )

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      className="dlg m-auto w-[min(480px,calc(100vw-32px))] max-w-none rounded-[20px] border-0 bg-surface p-0 text-ink shadow-pop"
      onCancel={(e) => {
        e.preventDefault()
        if (!busy) onClose()
      }}
      onClick={(e) => {
        if (e.target === ref.current && !busy) onClose()
      }}
    >
      {onSubmit ? (
        <form className="grid gap-4 p-6" onSubmit={submit} noValidate>
          {body}
        </form>
      ) : (
        <div className="grid gap-4 p-6">{body}</div>
      )}
    </dialog>
  )
}
