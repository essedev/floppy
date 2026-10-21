/*
  How to put Floppy on a Mac, from this page alone: Homebrew, the cask, this server's address
  in the app's sign-in, the person's email, the disk in the Finder. The invitation mail only
  brings people here (server/src/floppy_server/mailtext.py), so nothing else tells them.
*/

import { Check, Copy } from 'lucide-react'
import { useState } from 'react'
import { brand } from '@/brand'
import { Button } from '@/components/Button'
import { fmt, rich, useI18n } from '@/i18n'
import { useCurrentSession } from '@/features/auth/hooks'
import { serverAddress } from './hooks'

/** A value to type somewhere else, with a button that copies it. */
function CopyLine({ value, what }: { value: string; what: string }) {
  const { t } = useI18n()
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle')
  const copy = () =>
    navigator.clipboard.writeText(value).then(
      () => {
        setState('copied')
        window.setTimeout(() => setState('idle'), 2000)
      },
      // No clipboard (an http page, a denied permission): the text stays selectable.
      () => setState('failed'),
    )
  return (
    <div className="mt-2 grid grid-cols-[minmax(0,1fr)] gap-1">
      <div className="flex items-center gap-2 rounded-[11px] bg-sunken py-1.5 pr-1.5 pl-3.5">
        <code className="num min-w-0 flex-1 overflow-x-auto text-[13.5px] whitespace-nowrap select-all">{value}</code>
        <Button size="sm" variant="raised" onClick={() => void copy()} aria-label={fmt(t.start.copyLabel, { what })}>
          {state === 'copied' ? <Check size={15} aria-hidden /> : <Copy size={15} aria-hidden />}
          {state === 'copied' ? t.start.copied : t.start.copy}
        </Button>
      </div>
      {state === 'failed' && <span className="text-[13px] text-ink-2">{t.start.copyFailed}</span>}
    </div>
  )
}

/** The numbered steps from an empty Mac to the disk in the Finder. */
export function InstallSteps() {
  const { t } = useI18n()
  const session = useCurrentSession()
  const s = t.start.steps
  const step = 'grid grid-cols-[26px_minmax(0,1fr)] gap-x-2 text-[14.5px] leading-[1.5]'
  const num = 'num pt-px text-[13px] text-ink-3'
  return (
    <div className="grid gap-4">
      <p className="text-[13.5px] text-ink-2">{t.start.needs}</p>
      <ol className="m-0 grid list-none gap-[18px] p-0">
        <li className={step}>
          <span className={num}>1</span>
          <span>
            {rich(s.brew, {
              link: (
                <a href="https://brew.sh/it/" target="_blank" rel="noreferrer" className="font-semibold text-ink underline underline-offset-2">
                  brew.sh
                </a>
              ),
            })}
          </span>
        </li>
        <li className={step}>
          <span className={num}>2</span>
          <div className="min-w-0">
            {s.install}
            <CopyLine value={`brew install --cask ${brand.caskRef}`} what={t.start.whatCommand} />
          </div>
        </li>
        <li className={step}>
          <span className={num}>3</span>
          <div className="min-w-0">
            {s.server}
            <CopyLine value={serverAddress()} what={t.start.whatServer} />
          </div>
        </li>
        <li className={step}>
          <span className={num}>4</span>
          <span>{rich(s.signIn, { email: <b className="font-semibold">{session.user.email}</b> })}</span>
        </li>
        <li className={step}>
          <span className={num}>5</span>
          <span>{s.disk}</span>
        </li>
      </ol>
    </div>
  )
}
