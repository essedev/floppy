/*
  Sign-in in the browser (DECISIONS #12, concept-d-onboarding): an email, then a page that
  waits for the magic link and updates by itself, with the 6-digit code from the same email as
  the fallback for mail read on another device. Expired and locked requests say what to do.
*/

import { useQueryClient } from '@tanstack/react-query'
import { Mail } from 'lucide-react'
import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router'
import { Button } from '@/components/Button'
import { State } from '@/components/detail'
import { Field, Input } from '@/components/Field'
import { AppIcon, Wordmark } from '@/components/marks'
import { useErrorText } from '@/components/error-text'
import { fmt, plural, rich, useI18n } from '@/i18n'
import { api, isApiError, type SignInStartResponse } from '@/lib/api'
import { queryKeys } from '@/lib/api/keys'
import { useFormat } from '@/lib/format'
import { useNow } from '@/lib/hooks'
import { FakeBanner } from '@/features/shell/FakeBanner'
import { CodeInput } from './CodeInput'
import { useSignInStatus, useStartSignIn, useVerifyCode } from './hooks'

const PENDING_KEY = 'floppy.signin'

type DeadEnd = 'expired' | 'locked' | 'elsewhere'
type Step = { kind: 'form'; email: string } | { kind: 'waiting'; request: SignInStartResponse } | { kind: DeadEnd; email: string }

function savedStep(): Step {
  try {
    const raw = sessionStorage.getItem(PENDING_KEY)
    if (raw) {
      const request = JSON.parse(raw) as SignInStartResponse
      if (Date.parse(request.expiresAt) > Date.now()) return { kind: 'waiting', request }
    }
  } catch {
    // Nothing saved: start from the form.
  }
  return { kind: 'form', email: '' }
}

function remember(request: SignInStartResponse | null): void {
  try {
    if (request) sessionStorage.setItem(PENDING_KEY, JSON.stringify(request))
    else sessionStorage.removeItem(PENDING_KEY)
  } catch {
    // A reload will show the form again: acceptable.
  }
}

/** The centred card every sign-in step sits in. */
export function AuthFrame({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-dvh flex-col bg-page">
      <FakeBanner />
      <main className="grid flex-1 place-items-center px-4 py-10">
        <div className="grid w-full max-w-[420px] gap-5 rounded-[22px] bg-surface p-8 shadow-pop max-sm:px-5">{children}</div>
      </main>
    </div>
  )
}

/** The sign-in page. */
export function SignInPage() {
  const [step, setStep] = useState<Step>(savedStep)
  const navigate = useNavigate()
  const location = useLocation()
  const from = (location.state as { from?: string } | null)?.from ?? '/'
  const client = useQueryClient()

  // Polling never yields a session: if the link was opened in another browser, this one is
  // still signed out and says so instead of looping back to the form.
  const done = async (email: string) => {
    remember(null)
    const session = await client.fetchQuery({ queryKey: queryKeys.session, queryFn: () => api.auth.session(), staleTime: 0 }).catch(() => null)
    if (session) navigate(from, { replace: true })
    else setStep({ kind: 'elsewhere', email })
  }

  return (
    <AuthFrame>
      <Wordmark size={24} />
      {step.kind === 'form' && (
        <EmailStep
          initial={step.email}
          onSent={(request) => {
            remember(request)
            setStep({ kind: 'waiting', request })
          }}
        />
      )}
      {step.kind === 'waiting' && (
        <WaitingStep
          key={step.request.requestId}
          request={step.request}
          onSignedIn={() => void done(step.request.email)}
          onRestart={(kind) => {
            remember(null)
            setStep(kind === 'form' ? { kind: 'form', email: step.request.email } : { kind, email: step.request.email })
          }}
          onResent={(request) => {
            remember(request)
            setStep({ kind: 'waiting', request })
          }}
        />
      )}
      {(step.kind === 'expired' || step.kind === 'locked' || step.kind === 'elsewhere') && <DeadEndStep kind={step.kind} email={step.email} onSent={(request) => {
        remember(request)
        setStep({ kind: 'waiting', request })
      }} />}
    </AuthFrame>
  )
}

function EmailStep({ initial, onSent }: { initial: string; onSent(request: SignInStartResponse): void }) {
  const { t } = useI18n()
  const errorText = useErrorText()
  const start = useStartSignIn()
  const [email, setEmail] = useState(initial)
  const [invalid, setInvalid] = useState(false)

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.trim())) return setInvalid(true)
    setInvalid(false)
    start.mutate(email.trim(), { onSuccess: onSent })
  }

  return (
    <form className="grid gap-5" onSubmit={submit} noValidate>
      <div>
        <h1 className="text-[26px] leading-[1.15] font-bold tracking-[-0.025em]">{t.auth.title}</h1>
        <p className="mt-1.5 text-[14.5px] text-ink-2">{t.auth.lead}</p>
      </div>
      <Field label={t.auth.email} error={invalid ? t.auth.invalidEmail : start.isError ? errorText(start.error) : null}>
        {(id, describedBy) => (
          <Input
            id={id}
            type="email"
            autoComplete="email"
            autoFocus
            placeholder={t.auth.emailPlaceholder}
            value={email}
            invalid={invalid}
            aria-describedby={describedBy}
            onChange={(e) => setEmail(e.target.value)}
          />
        )}
      </Field>
      <Button type="submit" variant="ink" busy={start.isPending}>
        <Mail size={17} aria-hidden />
        {start.isPending ? t.auth.sending : t.auth.send}
      </Button>
      <div className="flex items-center gap-3 text-sm text-ink-2">
        <AppIcon size={40} />
        <span>{t.auth.appline}</span>
      </div>
    </form>
  )
}

function WaitingStep({ request, onSignedIn, onRestart, onResent }: {
  request: SignInStartResponse
  onSignedIn(): void
  onRestart(kind: 'form' | 'expired' | 'locked'): void
  onResent(request: SignInStartResponse): void
}) {
  const { t, locale } = useI18n()
  const f = useFormat()
  const now = useNow(1000)
  const errorText = useErrorText()
  const status = useSignInStatus(request.requestId)
  const verify = useVerifyCode()
  const resend = useStartSignIn()
  const [code, setCode] = useState('')
  const codeLabel = useId()

  // The poll and the code can both finish the request: act on the first only.
  const finished = useRef(false)
  const finish = (then: () => void) => {
    if (finished.current) return
    finished.current = true
    then()
  }
  const state = status.data?.status
  useEffect(() => {
    if (state === 'completed') finish(onSignedIn)
    else if (state === 'expired' || state === 'locked') finish(() => onRestart(state))
  })

  const submitCode = (value: string) => {
    if (value.length !== 6 || verify.isPending) return
    verify.mutate(
      { requestId: request.requestId, code: value },
      {
        onSuccess: () => finish(onSignedIn),
        onError: (error) => {
          setCode('')
          if (isApiError(error, 'request_locked')) finish(() => onRestart('locked'))
          else if (isApiError(error, 'request_expired')) finish(() => onRestart('expired'))
        },
      },
    )
  }

  const resendIn = Date.parse(request.resendAt) - now
  const attemptsLeft = isApiError(verify.error, 'invalid_code') ? Number(verify.error.details.attemptsLeft ?? 0) : null

  return (
    <div className="grid gap-5">
      <div className="grid gap-3">
        <State led="dl" blink>
          {t.auth.waitingState}
        </State>
        <h1 className="text-[26px] leading-[1.15] font-bold tracking-[-0.025em]">{t.auth.waitingTitle}</h1>
        <p className="text-[14.5px] text-ink-2">{rich(t.auth.waitingBody, { email: <b className="font-semibold text-ink">{request.email}</b> })}</p>
        <p className="text-sm text-ink-2">{t.auth.waitingAuto}</p>
      </div>

      <form
        className="notice grid gap-3 px-4 py-4"
        onSubmit={(e) => {
          e.preventDefault()
          submitCode(code)
        }}
      >
        <div>
          <p id={codeLabel} className="text-sm font-semibold">
            {t.auth.codeTitle}
          </p>
          <p className="text-[13px] text-ink-2">{t.auth.codeLead}</p>
        </div>
        <CodeInput value={code} onChange={setCode} onComplete={submitCode} disabled={verify.isPending} invalid={attemptsLeft !== null && code === ''} labelledBy={codeLabel} />
        {verify.isError && (
          <div className="grid gap-1 text-sm" role="alert">
            <State led="err">{t.auth.wrongState}</State>
            <span className="text-ink-2">{attemptsLeft !== null ? plural(t.auth.attemptsLeft, attemptsLeft, locale) : errorText(verify.error)}</span>
          </div>
        )}
        <div>
          <Button type="submit" size="sm" variant="ink" busy={verify.isPending} disabled={code.length !== 6}>
            {verify.isPending ? t.auth.verifying : t.auth.verify}
          </Button>
        </div>
      </form>

      <div className="grid gap-2">
        <div className="flex flex-wrap gap-2">
          <Button
            className="flex-1"
            disabled={resendIn > 0}
            busy={resend.isPending}
            onClick={() => resend.mutate(request.email, { onSuccess: onResent })}
          >
            {resendIn > 0 ? fmt(t.auth.resendIn, { time: f.countdown(resendIn) }) : t.auth.resend}
          </Button>
          <Button variant="ghost" onClick={() => onRestart('form')}>
            {t.auth.otherEmail}
          </Button>
        </div>
        {resend.isError && <p className="text-[13px] text-bad-text">{errorText(resend.error)}</p>}
        {status.isError && <p className="text-[13px] text-ink-2">{errorText(status.error)}</p>}
      </div>
    </div>
  )
}

function DeadEndStep({ kind, email, onSent }: { kind: DeadEnd; email: string; onSent(request: SignInStartResponse): void }) {
  const { t } = useI18n()
  const errorText = useErrorText()
  const start = useStartSignIn()
  const text = {
    expired: [t.auth.expiredState, t.auth.expiredTitle, t.auth.expiredBody],
    locked: [t.auth.lockedState, t.auth.lockedTitle, t.auth.lockedBody],
    elsewhere: [t.auth.elsewhereState, t.auth.elsewhereTitle, t.auth.elsewhereBody],
  }[kind]
  return (
    <div className="grid gap-5">
      <div className="grid gap-3">
        <State led={kind === 'elsewhere' ? 'off' : 'err'}>{text[0]}</State>
        <h1 className="text-[26px] leading-[1.15] font-bold tracking-[-0.025em]">{text[1]}</h1>
        <p className="text-[14.5px] text-ink-2">{text[2]}</p>
      </div>
      <Button variant="ink" busy={start.isPending} onClick={() => start.mutate(email, { onSuccess: onSent })}>
        <Mail size={17} aria-hidden />
        {t.auth.again}
      </Button>
      {start.isError && <p className="text-[13px] text-bad-text">{errorText(start.error)}</p>}
    </div>
  )
}
