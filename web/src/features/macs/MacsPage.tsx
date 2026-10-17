/*
  Macs (concept-e-admin, "Mac"; DECISIONS #7, #12): one Valkey user per Mac, its last contact
  and how its replica keeps up; revoking one leaves the others alone. Members see only their
  own Macs, as "I miei Mac", and can revoke a lost one themselves.
*/

import { Users } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'
import { Button } from '@/components/Button'
import { Led, Notice, Readout, State } from '@/components/detail'
import { Dialog } from '@/components/Dialog'
import { Faceplate } from '@/components/marks'
import { useErrorText } from '@/components/error-text'
import { EmptyState, PageHeader, QueryView, SkeletonRows } from '@/components/states'
import { fmt, rich, useI18n } from '@/i18n'
import type { Mac } from '@/lib/api'
import { useFormat } from '@/lib/format'
import { useFlash, useNow } from '@/lib/hooks'
import { useCurrentSession } from '@/features/auth/hooks'
import { KeyNotice } from '@/features/key/KeyNotice'
import { useMacs, useRevokeMac } from './hooks'

function ReplicaCell({ mac }: { mac: Mac }) {
  const { t } = useI18n()
  const f = useFormat()
  if (!mac.replica) return <span className="text-ink-3">{t.macs.notConnected}</span>
  const { mode, lagMs } = mac.replica
  const lag = lagMs === null ? '' : f.latency(lagMs)
  const text = mode === 'replica' ? fmt(t.macs.replica, { lag }) : mode === 'master' ? fmt(t.macs.master, { lag }) : t.macs.offline
  const help = mode === 'replica' ? t.macs.replicaHelp : mode === 'master' ? t.macs.masterHelp : t.macs.offlineHelp
  return (
    <span title={help}>
      <Readout>{text}</Readout>
      <span className="sr-only">. {help}</span>
    </span>
  )
}

function RevokeDialog({ mac, all, admin, onClose, onDone }: { mac: Mac; all: Mac[]; admin: boolean; onClose(): void; onDone(name: string): void }) {
  const { t } = useI18n()
  const errorText = useErrorText()
  const revoke = useRevokeMac()
  const other = all.find((m) => m.owner.id === mac.owner.id && m.id !== mac.id && !m.revokedAt)
  return (
    <Dialog
      title={fmt(t.macs.revokeTitle, { name: mac.name })}
      onClose={onClose}
      busy={revoke.isPending}
      actions={
        <>
          <Button variant="ghost" onClick={onClose} disabled={revoke.isPending}>
            {t.common.cancel}
          </Button>
          <Button variant="danger" busy={revoke.isPending} onClick={() => revoke.mutate(mac.id, { onSuccess: () => onDone(mac.name) })}>
            {t.macs.revokeConfirm}
          </Button>
        </>
      }
    >
      <p>{t.macs.revokeBody}</p>
      {other && <p>{rich(t.macs.revokeOther, { owner: mac.owner.name, other: <b className="font-semibold text-ink">{other.name}</b> })}</p>}
      <p>{admin ? t.macs.revokeKeyAdmin : t.macs.revokeKeyMember}</p>
      {revoke.isError && (
        <div className="notice grid gap-1 px-4 py-3" role="alert">
          <State led="err">{t.common.failed}</State>
          <span>{errorText(revoke.error)}</span>
        </div>
      )}
    </Dialog>
  )
}

/** The Macs page, for admins and members. */
export function MacsPage() {
  const { t } = useI18n()
  const f = useFormat()
  const session = useCurrentSession()
  const admin = session.role === 'admin'
  const macs = useMacs()
  useNow(30_000)
  const [revoking, setRevoking] = useState<Mac | null>(null)
  const [flash, setFlash] = useFlash<string>()
  const cols = admin ? [190, 110, 50, 50, 110, 80, 60] : [190, 50, 50, 110, 80, 60]

  return (
    <>
      <PageHeader
        title={admin ? t.macs.titleAdmin : t.macs.titleMember}
        lead={admin ? t.macs.leadAdmin : fmt(t.macs.leadMember, { org: session.organization.name })}
      />
      {admin && <KeyNotice />}
      {flash && <Notice className="mb-3.5" state={<State led="ok">{t.macs.revokedState}</State>} text={fmt(t.macs.revokedDone, { name: flash })} />}
      <QueryView
        query={macs}
        loading={
          <table className="tbl">
            <tbody>
              <SkeletonRows widths={cols} />
            </tbody>
          </table>
        }
        isEmpty={(list) => list.length === 0}
        empty={
          admin ? (
            <EmptyState
              art={<Faceplate state="off" />}
              title={t.macs.emptyTitle}
              body={t.macs.emptyBody}
              action={
                <Link to="/persone" className="inline-flex h-10 items-center gap-2 rounded-[11px] bg-ink px-4 text-sm font-semibold text-surface no-underline hover:opacity-90">
                  <Users size={17} aria-hidden />
                  {t.macs.emptyAction}
                </Link>
              }
            />
          ) : (
            <EmptyState art={<Faceplate state="off" />} title={t.macs.emptyMemberTitle} body={t.macs.emptyMemberBody} />
          )
        }
      >
        {(list) => (
          <div className="relative overflow-x-auto">
            <table className="tbl">
              <thead>
                <tr>
                  <th>{t.macs.colMac}</th>
                  {admin && <th>{t.macs.colOwner}</th>}
                  <th>{t.macs.colMacos}</th>
                  <th>{t.macs.colApp}</th>
                  <th>{t.macs.colReplica}</th>
                  <th>{t.macs.colLastSeen}</th>
                  <th>
                    <span className="sr-only">{t.macs.revoke}</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {list.map((m) => (
                  <tr key={m.id} data-dim={m.revokedAt ? true : undefined}>
                    <td>
                      <div className="flex items-center gap-3">
                        {!m.revokedAt && <Led state={m.connected ? 'ok' : 'off'} />}
                        <div>
                          <b className="block font-semibold">{m.name}</b>
                          <span className="num text-xs text-ink-3" title={fmt(t.macs.valkeyUser, { user: m.valkeyUser })}>
                            {m.valkeyUser}
                          </span>
                        </div>
                      </div>
                    </td>
                    {admin && <td>{m.owner.name}</td>}
                    <td className="num">{m.macosVersion}</td>
                    <td>
                      <span className="num">{m.appVersion}</span>
                      {m.appOutdated && !m.revokedAt && <Readout className="ml-2">{t.macs.outdated}</Readout>}
                    </td>
                    <td>{m.revokedAt ? null : <ReplicaCell mac={m} />}</td>
                    <td>
                      {m.revokedAt ? (
                        <State led="err">{fmt(t.macs.revoked, { date: f.relative(m.revokedAt) })}</State>
                      ) : m.lastSeenAt ? (
                        f.relative(m.lastSeenAt)
                      ) : (
                        t.common.never
                      )}
                    </td>
                    <td className="text-right">
                      {!m.revokedAt && (
                        <Button size="sm" onClick={() => setRevoking(m)}>
                          {t.macs.revoke}
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </QueryView>
      {revoking && (
        <RevokeDialog
          mac={revoking}
          all={macs.data ?? []}
          admin={admin}
          onClose={() => setRevoking(null)}
          onDone={(name) => {
            setRevoking(null)
            setFlash(name)
          }}
        />
      )}
    </>
  )
}
