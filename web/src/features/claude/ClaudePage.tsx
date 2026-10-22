/*
  Claude (DECISIONS #29): how to connect Claude Code and claude.ai to this control plane, and
  the person's connected clients, each disconnectable. For admins and members alike: a
  connection acts as its person, with their role.
*/

import { Check, Copy } from 'lucide-react'
import { useState } from 'react'
import { useSearchParams } from 'react-router'
import { Button } from '@/components/Button'
import { Notice, Readout, State, Well } from '@/components/detail'
import { Dialog } from '@/components/Dialog'
import { useErrorText } from '@/components/error-text'
import { EmptyState, PageHeader, QueryView, SkeletonLines, SkeletonRows } from '@/components/states'
import { fmt, useI18n } from '@/i18n'
import type { Connection, McpScope } from '@/lib/api'
import { useFormat } from '@/lib/format'
import { useFlash } from '@/lib/hooks'
import { useConnections, useMcpInstall, useRevokeConnection } from './hooks'

function CopyLine({ label, value }: { label?: string; value: string }) {
  const { t } = useI18n()
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      setCopied(false)
    }
  }
  return (
    <div className="grid gap-1.5">
      {label && <span className="text-[13px] text-ink-2">{label}</span>}
      <div className="flex items-start gap-2">
        <code className="num min-w-0 flex-1 rounded-[10px] bg-sunken px-3 py-2.5 text-[13px] break-all">{value}</code>
        <Button size="sm" onClick={() => void copy()} aria-label={`${t.claude.copy}: ${label ?? value}`}>
          {copied ? <Check size={15} aria-hidden /> : <Copy size={15} aria-hidden />}
          <span className="ml-1.5">{copied ? t.claude.copied : t.claude.copy}</span>
        </Button>
      </div>
    </div>
  )
}

/** The widest scope of a connection, as one word. */
function ScopeWord({ scopes }: { scopes: McpScope[] }) {
  const { t } = useI18n()
  const widest = (['floppy:admin', 'floppy:write', 'floppy:read'] as const).find((s) => scopes.includes(s)) ?? 'floppy:read'
  return <Readout>{t.claude.scopeShort[widest]}</Readout>
}

function DisconnectDialog({ connection, onClose, onDone }: { connection: Connection; onClose(): void; onDone(): void }) {
  const { t } = useI18n()
  const errorText = useErrorText()
  const revoke = useRevokeConnection()
  return (
    <Dialog
      title={fmt(t.claude.disconnectTitle, { name: connection.clientName })}
      onClose={onClose}
      busy={revoke.isPending}
      actions={
        <>
          <Button variant="ghost" onClick={onClose} disabled={revoke.isPending}>
            {t.common.cancel}
          </Button>
          <Button variant="danger" busy={revoke.isPending} onClick={() => revoke.mutate(connection.id, { onSuccess: onDone })}>
            {t.claude.disconnect}
          </Button>
        </>
      }
    >
      <p>{t.claude.disconnectBody}</p>
      {revoke.isError && (
        <div className="notice grid gap-1 px-4 py-3" role="alert">
          <State led="err">{t.common.failed}</State>
          <span>{errorText(revoke.error)}</span>
        </div>
      )}
    </Dialog>
  )
}

/** The Claude page. */
export function ClaudePage() {
  const { t } = useI18n()
  const f = useFormat()
  const install = useMcpInstall()
  const connections = useConnections()
  const [params] = useSearchParams()
  const [leaving, setLeaving] = useState<Connection | null>(null)
  const [flash, setFlash] = useFlash<string>()

  return (
    <>
      <PageHeader title={t.claude.title} lead={t.claude.lead} />
      {params.get('collegato') && <Notice className="mb-3.5" state={<State led="ok">{t.common.done}</State>} text={t.claude.connectedNotice} />}
      {flash && <Notice className="mb-3.5" state={<State led="ok">{t.common.done}</State>} text={fmt(t.claude.disconnected, { name: flash })} />}
      <div className="grid gap-4 lg:grid-cols-2">
        <QueryView query={install} loading={<Well><SkeletonLines /></Well>}>
          {(info) =>
            info.enabled ? (
              <>
                <Well title={t.claude.setupTitle}>
                  <p className="mb-3 text-sm text-ink-2">{t.claude.setupBody}</p>
                  <CopyLine value={info.setupCommand} />
                  <details className="mt-4">
                    <summary className="cursor-pointer text-[13px] text-ink-2">{t.claude.manualTitle}</summary>
                    <div className="mt-3 grid gap-3">
                      <CopyLine label={t.claude.teamCommand} value={info.claudeCodeCommand} />
                      <CopyLine label={t.claude.localCommand} value={info.localCommand} />
                    </div>
                  </details>
                </Well>
                <Well title={t.claude.webTitle}>
                  <p className="mb-3 text-sm text-ink-2">{t.claude.webBody}</p>
                  <CopyLine value={info.url} />
                </Well>
              </>
            ) : (
              <Notice state={<State led="off">{t.claude.title}</State>} text={t.claude.off} />
            )
          }
        </QueryView>
      </div>
      <h2 className="eyebrow mt-8 mb-3">{t.claude.connectionsTitle}</h2>
      <QueryView
        query={connections}
        loading={
          <table className="tbl">
            <tbody>
              <SkeletonRows widths={[160, 80, 90, 90, 60]} rows={2} />
            </tbody>
          </table>
        }
        isEmpty={(list) => list.length === 0}
        empty={<EmptyState title={t.claude.connectionsTitle} body={t.claude.connectionsEmpty} />}
      >
        {(list) => (
          <div className="relative overflow-x-auto">
            <table className="tbl">
              <thead>
                <tr>
                  <th>{t.claude.colClient}</th>
                  <th>{t.claude.colScopes}</th>
                  <th>{t.claude.colSince}</th>
                  <th>{t.claude.colUsed}</th>
                  <th>
                    <span className="sr-only">{t.claude.disconnect}</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {list.map((c) => (
                  <tr key={c.id}>
                    <td>
                      <b className="block font-semibold">{c.clientName}</b>
                    </td>
                    <td>
                      <ScopeWord scopes={c.scopes} />
                    </td>
                    <td>{f.relative(c.grantedAt)}</td>
                    <td>{c.lastUsedAt ? f.relative(c.lastUsedAt) : t.common.never}</td>
                    <td className="text-right">
                      <Button size="sm" onClick={() => setLeaving(c)}>
                        {t.claude.disconnect}
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </QueryView>
      {leaving && (
        <DisconnectDialog
          connection={leaving}
          onClose={() => setLeaving(null)}
          onDone={() => {
            setFlash(leaving.clientName)
            setLeaving(null)
          }}
        />
      )}
    </>
  )
}
