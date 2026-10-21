/*
  Devices (concept-e-admin, "Mac"; DECISIONS #7, #12, #23): one Valkey user per device on every
  disk it sees, its last contact and how its replica keeps up; revoking one leaves the others
  alone. Members see only their own, as "I miei dispositivi", and can revoke a lost one
  themselves.
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
import type { Device } from '@/lib/api'
import { useFormat } from '@/lib/format'
import { useFlash, useNow } from '@/lib/hooks'
import { useCurrentSession } from '@/features/auth/hooks'
import { KeyNotice } from '@/features/key/KeyNotice'
import { useKeyStatus } from '@/features/key/hooks'
import { useDevices, useRevokeDevice } from './hooks'

function ReplicaCell({ device }: { device: Device }) {
  const { t } = useI18n()
  const f = useFormat()
  if (!device.replica) return <span className="text-ink-3">{t.devices.notConnected}</span>
  const { mode, lagMs } = device.replica
  const lag = lagMs === null ? '' : f.latency(lagMs)
  const text = mode === 'replica' ? fmt(t.devices.replica, { lag }) : mode === 'master' ? fmt(t.devices.master, { lag }) : t.devices.offline
  const help = mode === 'replica' ? t.devices.replicaHelp : mode === 'master' ? t.devices.masterHelp : t.devices.offlineHelp
  return (
    <span title={help}>
      <Readout>{text}</Readout>
      <span className="sr-only">. {help}</span>
    </span>
  )
}

function RevokeDialog({ device, all, admin, onClose, onDone }: { device: Device; all: Device[]; admin: boolean; onClose(): void; onDone(name: string): void }) {
  const { t } = useI18n()
  const errorText = useErrorText()
  const revoke = useRevokeDevice()
  // Admins only: members do not see the keys.
  const mode = useKeyStatus(admin).data?.storageMode
  const other = all.find((m) => m.owner.id === device.owner.id && m.id !== device.id && !m.revokedAt)
  return (
    <Dialog
      title={fmt(t.devices.revokeTitle, { name: device.name })}
      onClose={onClose}
      busy={revoke.isPending}
      actions={
        <>
          <Button variant="ghost" onClick={onClose} disabled={revoke.isPending}>
            {t.common.cancel}
          </Button>
          <Button variant="danger" busy={revoke.isPending} onClick={() => revoke.mutate(device.id, { onSuccess: () => onDone(device.name) })}>
            {t.devices.revokeConfirm}
          </Button>
        </>
      }
    >
      <p>{t.devices.revokeBody}</p>
      {other && <p>{rich(t.devices.revokeOther, { owner: device.owner.name, other: <b className="font-semibold text-ink">{other.name}</b> })}</p>}
      <p>{!admin ? t.devices.revokeKeyMember : mode === 'managed' ? t.devices.revokeKeyManaged : t.devices.revokeKeyAdmin}</p>
      {revoke.isError && (
        <div className="notice grid gap-1 px-4 py-3" role="alert">
          <State led="err">{t.common.failed}</State>
          <span>{errorText(revoke.error)}</span>
        </div>
      )}
    </Dialog>
  )
}

/** The devices page, for admins and members. */
export function DevicesPage() {
  const { t } = useI18n()
  const f = useFormat()
  const session = useCurrentSession()
  const admin = session.role === 'admin'
  const devices = useDevices()
  useNow(30_000)
  const [revoking, setRevoking] = useState<Device | null>(null)
  const [flash, setFlash] = useFlash<string>()
  const cols = admin ? [190, 110, 50, 50, 110, 80, 60] : [190, 50, 50, 110, 80, 60]

  return (
    <>
      <PageHeader
        title={admin ? t.devices.titleAdmin : t.devices.titleMember}
        lead={admin ? t.devices.leadAdmin : fmt(t.devices.leadMember, { org: session.organization.name })}
      />
      {admin && <KeyNotice />}
      {flash && <Notice className="mb-3.5" state={<State led="ok">{t.devices.revokedState}</State>} text={fmt(t.devices.revokedDone, { name: flash })} />}
      <QueryView
        query={devices}
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
              title={t.devices.emptyTitle}
              body={t.devices.emptyBody}
              action={
                <Link to="/persone" className="inline-flex h-10 items-center gap-2 rounded-[11px] bg-ink px-4 text-sm font-semibold text-surface no-underline hover:opacity-90">
                  <Users size={17} aria-hidden />
                  {t.devices.emptyAction}
                </Link>
              }
            />
          ) : (
            <EmptyState
              art={<Faceplate state="off" />}
              title={t.devices.emptyMemberTitle}
              body={t.devices.emptyMemberBody}
              action={
                <Link to="/" className="inline-flex h-10 items-center rounded-[11px] bg-ink px-4 text-sm font-semibold text-surface no-underline hover:opacity-90">
                  {t.devices.emptyMemberAction}
                </Link>
              }
            />
          )
        }
      >
        {(list) => (
          <div className="relative overflow-x-auto">
            <table className="tbl">
              <thead>
                <tr>
                  <th>{t.devices.colDevice}</th>
                  {admin && <th>{t.devices.colOwner}</th>}
                  <th>{t.devices.colMacos}</th>
                  <th>{t.devices.colApp}</th>
                  <th>{t.devices.colReplica}</th>
                  <th>{t.devices.colLastSeen}</th>
                  <th>
                    <span className="sr-only">{t.devices.revoke}</span>
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
                          <span className="num text-xs text-ink-3" title={fmt(t.devices.valkeyUser, { user: m.valkeyUser })}>
                            {m.valkeyUser}
                          </span>
                        </div>
                      </div>
                    </td>
                    {admin && <td>{m.owner.name}</td>}
                    <td className="num">{m.macosVersion}</td>
                    <td>
                      <span className="num">{m.appVersion}</span>
                      {m.appOutdated && !m.revokedAt && <Readout className="ml-2">{t.devices.outdated}</Readout>}
                    </td>
                    <td>{m.revokedAt ? null : <ReplicaCell device={m} />}</td>
                    <td>
                      {m.revokedAt ? (
                        <State led="err">{fmt(t.devices.revoked, { date: f.relative(m.revokedAt) })}</State>
                      ) : m.lastSeenAt ? (
                        f.relative(m.lastSeenAt)
                      ) : m.state === 'pending' ? (
                        <Readout>{t.devices.pending}</Readout>
                      ) : (
                        t.common.never
                      )}
                    </td>
                    <td className="text-right">
                      {!m.revokedAt && (
                        <Button size="sm" onClick={() => setRevoking(m)}>
                          {t.devices.revoke}
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
          device={revoking}
          all={devices.data ?? []}
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
