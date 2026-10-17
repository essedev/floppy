/* Where the invitation link lands (/invito): accepts it and signs in, or says it no longer works. */

import { useEffect, useRef } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router'
import { State } from '@/components/detail'
import { Wordmark } from '@/components/marks'
import { useErrorText } from '@/components/error-text'
import { useI18n } from '@/i18n'
import { isApiError } from '@/lib/api'
import { AuthFrame } from './SignInPage'
import { useAcceptInvitation } from './hooks'

/** The page behind the link in the invitation email. */
export function InvitationPage() {
  const { t } = useI18n()
  const errorText = useErrorText()
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const accept = useAcceptInvitation()
  const token = params.get('token') ?? ''
  const sent = useRef(false)

  useEffect(() => {
    // StrictMode mounts twice; the token is single use.
    if (sent.current) return
    sent.current = true
    accept.mutate(token, { onSuccess: () => navigate('/', { replace: true }) })
  }, [accept, token, navigate])

  const dead = accept.isError && (isApiError(accept.error, 'link_invalid') || !token)
  return (
    <AuthFrame>
      <Wordmark size={24} />
      {accept.isError ? (
        <div className="grid gap-5">
          <div className="grid gap-3">
            <State led="err">{dead ? t.auth.inviteDeadState : t.states.errorTitle}</State>
            <h1 className="text-[26px] leading-[1.15] font-bold tracking-[-0.025em]">{dead ? t.auth.inviteDeadTitle : t.states.errorTitle}</h1>
            <p className="text-[14.5px] text-ink-2">{dead ? t.auth.inviteDeadBody : errorText(accept.error)}</p>
          </div>
          <Link to="/accedi" className="inline-flex h-10 items-center justify-center rounded-[11px] bg-ink px-4 text-sm font-semibold text-surface no-underline">
            {t.auth.signIn}
          </Link>
        </div>
      ) : (
        <div className="grid gap-3" aria-busy="true">
          <State led="dl" blink>
            {t.auth.inviteState}
          </State>
          <h1 className="text-[26px] leading-[1.15] font-bold tracking-[-0.025em]">{t.auth.inviteTitle}</h1>
        </div>
      )}
    </AuthFrame>
  )
}
