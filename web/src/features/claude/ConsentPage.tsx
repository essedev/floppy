/*
  The consent of an MCP client (DECISIONS #29): the control plane's /authorize sends the browser
  here with `?richiesta=<id>`, behind the session guard, so signing in by mail comes first and
  brings the person back. The person ticks what the client may do; reading is always granted.
  The answer is the client's redirect, with the code or the refusal.
*/

import { useState } from 'react'
import { useSearchParams } from 'react-router'
import { Button } from '@/components/Button'
import { State } from '@/components/detail'
import { Checkbox } from '@/components/Field'
import { Wordmark } from '@/components/marks'
import { useErrorText } from '@/components/error-text'
import { SkeletonLines } from '@/components/states'
import { fmt, rich, useI18n } from '@/i18n'
import { isApiError, type ConsentRequest, type McpScope } from '@/lib/api'
import { useCurrentSession } from '@/features/auth/hooks'
import { AuthFrame } from '@/features/auth/SignInPage'
import { useConsentRequest, useDecide } from './hooks'

const ORDER: McpScope[] = ['floppy:read', 'floppy:write', 'floppy:admin']

function Expired() {
  const { t } = useI18n()
  return (
    <>
      <State led="err">{t.claude.consentExpiredTitle}</State>
      <p className="text-sm text-ink-2">{t.claude.consentExpiredBody}</p>
    </>
  )
}

function Form({ request }: { request: ConsentRequest }) {
  const { t } = useI18n()
  const errorText = useErrorText()
  const session = useCurrentSession()
  const decide = useDecide(request.id)
  const offered = ORDER.filter((s) => request.scopes.includes(s))
  const [ticked, setTicked] = useState<Set<McpScope>>(new Set(offered))
  const [leaving, setLeaving] = useState(false)
  const name = request.client.name ?? t.claude.consentUnnamed

  const send = (approve: boolean) =>
    decide.mutate(approve ? { approve: true, scopes: [...ticked] } : { approve: false }, {
      onSuccess: ({ redirectTo }) => {
        setLeaving(true)
        window.location.assign(redirectTo)
      },
    })

  if (decide.isError && isApiError(decide.error, 'request_expired')) return <Expired />
  return (
    <>
      <h1 className="text-[22px] leading-[1.2] font-bold tracking-[-0.02em]">{fmt(t.claude.consentTitle, { name })}</h1>
      <p className="text-sm text-ink-2">
        {fmt(t.claude.consentLead, { org: session.organization.name, role: t.roles[request.role].toLowerCase() })}
      </p>
      <div className="grid gap-3">
        {offered.map((scope) => (
          <Checkbox
            key={scope}
            label={t.claude.scopes[scope]}
            hint={scope === 'floppy:read' ? t.claude.consentAlways : undefined}
            checked={scope === 'floppy:read' || ticked.has(scope)}
            disabled={scope === 'floppy:read'}
            onChange={(event) => {
              const next = new Set(ticked)
              if (event.target.checked) next.add(scope)
              else next.delete(scope)
              setTicked(next)
            }}
          />
        ))}
      </div>
      {request.role === 'member' && <p className="text-[13px] text-ink-2">{t.claude.consentMember}</p>}
      <p className="text-[13px] text-ink-2">{rich(t.claude.consentReturn, { host: <code className="num">{request.client.redirectHost}</code> })}</p>
      {request.client.loopbackOnly && (
        <div className="notice px-4 py-3 text-[13px]" role="note">
          {t.claude.consentLoopback}
        </div>
      )}
      {decide.isError && (
        <div className="notice grid gap-1 px-4 py-3" role="alert">
          <State led="err">{t.common.failed}</State>
          <span>{errorText(decide.error)}</span>
        </div>
      )}
      {leaving ? (
        <State led="dl" blink>
          {t.claude.consentRedirecting}
        </State>
      ) : (
        <div className="flex justify-end gap-2">
          <Button variant="ghost" disabled={decide.isPending} onClick={() => send(false)}>
            {t.claude.consentDeny}
          </Button>
          <Button variant="ink" busy={decide.isPending} onClick={() => send(true)}>
            {t.claude.consentApprove}
          </Button>
        </div>
      )}
    </>
  )
}

/** The consent page. */
export function ConsentPage() {
  const [params] = useSearchParams()
  const id = params.get('richiesta') ?? ''
  const request = useConsentRequest(id)
  return (
    <AuthFrame>
      <Wordmark size={24} />
      {id === '' || (request.isError && isApiError(request.error, 'request_expired')) ? (
        <Expired />
      ) : request.data ? (
        <Form request={request.data} />
      ) : request.isError ? (
        <ErrorLine error={request.error} />
      ) : (
        <SkeletonLines />
      )}
    </AuthFrame>
  )
}

function ErrorLine({ error }: { error: unknown }) {
  const { t } = useI18n()
  const errorText = useErrorText()
  return (
    <div className="notice grid gap-1 px-4 py-3" role="alert">
      <State led="err">{t.states.errorTitle}</State>
      <span>{errorText(error)}</span>
    </div>
  )
}
