/*
  People (concept-e-admin, "Persone"): who can use the disks, invite with the disks they will
  see, role, removal. Removing someone with devices revokes them and, in manual storage, leaves
  the key reminder of every disk they saw until its key changes (DECISIONS #12, #24); the
  confirmation says in plain words what happens.
*/

import { ChevronDown, Ellipsis, Plus } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'
import { Button, IconButton } from '@/components/Button'
import { Avatar, Notice, SlotList, State } from '@/components/detail'
import { Dialog } from '@/components/Dialog'
import { Choice, Field, Input } from '@/components/Field'
import { AppIcon } from '@/components/marks'
import { Menu } from '@/components/Menu'
import { useErrorText } from '@/components/error-text'
import { EmptyState, PageHeader, QueryView, SkeletonLines, SkeletonRows } from '@/components/states'
import { fmt, rich, useI18n } from '@/i18n'
import type { AccessLevel, Person, Role } from '@/lib/api'
import { useFormat } from '@/lib/format'
import { useFlash, useNow } from '@/lib/hooks'
import { AccessList } from '@/features/access/AccessControl'
import { useCurrentSession } from '@/features/auth/hooks'
import { useDisks } from '@/features/disks/hooks'
import { KeyNotice } from '@/features/key/KeyNotice'
import { useKeyStatus } from '@/features/key/hooks'
import { useDevices } from '@/features/devices/hooks'
import { useChangeRole, useInvite, usePeople, useRemovePerson, useResendInvite } from './hooks'

const displayName = (p: Person) => p.name ?? p.email

function InviteDialog({ onClose, onSent }: { onClose(): void; onSent(email: string): void }) {
  const { t } = useI18n()
  const errorText = useErrorText()
  const invite = useInvite()
  const disks = useDisks()
  const [email, setEmail] = useState('')
  const [role, setRole] = useState<Role>('member')
  // Closed by default: a disk not listed stays hidden from the new person (DECISIONS #23).
  const [access, setAccess] = useState<Record<string, AccessLevel>>({})
  const [invalid, setInvalid] = useState(false)

  const submit = () => {
    if (!/^[^@\s]+@[^@\s]+\.[^@\s.]+$/.test(email.trim())) return setInvalid(true)
    setInvalid(false)
    const entries = Object.entries(access)
      .filter(([diskId, level]) => level !== 'none' && disks.data?.some((d) => d.id === diskId))
      .map(([diskId, level]) => ({ diskId, access: level }))
    invite.mutate({ email: email.trim(), role, disks: entries }, { onSuccess: (p) => onSent(p.email) })
  }

  return (
    <Dialog
      title={t.people.inviteTitle}
      onClose={onClose}
      onSubmit={submit}
      busy={invite.isPending}
      actions={
        <>
          <Button variant="ghost" onClick={onClose} disabled={invite.isPending}>
            {t.common.cancel}
          </Button>
          <Button type="submit" variant="ink" busy={invite.isPending}>
            {t.people.inviteSend}
          </Button>
        </>
      }
    >
      <Field label={t.people.inviteEmail} error={invalid ? t.auth.invalidEmail : invite.isError ? errorText(invite.error) : null}>
        {(id, describedBy) => (
          <Input id={id} type="email" autoFocus autoComplete="off" value={email} invalid={invalid || invite.isError} aria-describedby={describedBy} placeholder={t.auth.emailPlaceholder} onChange={(e) => setEmail(e.target.value)} />
        )}
      </Field>
      <div className="grid gap-1.5">
        <span className="text-[13px] font-semibold text-ink">{t.people.inviteRole}</span>
        <Choice<Role>
          label={t.people.inviteRole}
          value={role}
          onChange={setRole}
          options={[
            { value: 'member', title: t.roles.member, hint: t.people.memberHint },
            { value: 'admin', title: t.roles.admin, hint: t.people.adminHint },
          ]}
        />
      </div>
      <div className="grid gap-1.5">
        <span className="text-[13px] font-semibold text-ink">{t.people.inviteDisks}</span>
        <QueryView query={disks} loading={<SkeletonLines lines={2} />} isEmpty={(list) => list.length === 0} empty={<span className="text-[13px]">{t.people.inviteNoDisks}</span>}>
          {(list) => (
            <>
              <AccessList
                rows={list.map((d) => ({ id: d.id, label: d.name, sub: d.mountPath }))}
                value={access}
                onChange={setAccess}
                labelFor={(row) => fmt(t.access.changeTo, { disk: row.label })}
              />
              <span className="text-[13px]">{t.people.inviteDisksHint}</span>
            </>
          )}
        </QueryView>
      </div>
      <div className="flex items-center gap-3 text-sm">
        <AppIcon size={40} />
        <span>{t.people.inviteAppline}</span>
      </div>
    </Dialog>
  )
}

function RemoveDialog({ person, onClose, onDone }: { person: Person; onClose(): void; onDone(name: string): void }) {
  const { t } = useI18n()
  const errorText = useErrorText()
  const remove = useRemovePerson()
  const devices = useDevices()
  const mode = useKeyStatus().data?.storageMode
  const theirs = devices.data?.filter((m) => m.owner.id === person.id && !m.revokedAt) ?? []
  const invited = person.status === 'invited'
  const name = displayName(person)
  const deviceNames = rich(theirs.map((_, i) => `{m${i}}`).join(t.people.and), Object.fromEntries(theirs.map((m, i) => [`m${i}`, <b key={m.id} className="font-semibold text-ink">{m.name}</b>])))

  return (
    <Dialog
      title={invited ? fmt(t.people.cancelInviteTitle, { email: person.email }) : fmt(t.people.removeTitle, { name })}
      onClose={onClose}
      busy={remove.isPending}
      actions={
        <>
          <Button variant="ghost" onClick={onClose} disabled={remove.isPending}>
            {t.common.cancel}
          </Button>
          <Button variant="danger" busy={remove.isPending} onClick={() => remove.mutate(person.id, { onSuccess: () => onDone(name) })}>
            {invited ? t.people.cancelInvite : fmt(t.people.removeConfirm, { name: person.name?.split(' ')[0] ?? name })}
          </Button>
        </>
      }
    >
      {invited ? (
        <p>{t.people.cancelInviteBody}</p>
      ) : person.deviceCount > 0 ? (
        <SlotList
          items={[
            rich(t.people.removeDevices, { devices: deviceNames }),
            t.people.removeCache,
            ...(mode ? [mode === 'managed' ? t.people.removeKeyManaged : t.people.removeKeyManual] : []),
          ]}
        />
      ) : (
        <SlotList items={[t.people.removeNoDevices]} />
      )}
      {remove.isError && (
        <div className="notice grid gap-1 px-4 py-3" role="alert">
          <State led="err">{t.common.failed}</State>
          <span>{errorText(remove.error)}</span>
        </div>
      )}
    </Dialog>
  )
}

function PersonRow({ person, onRemove, onFlash }: { person: Person; onRemove(): void; onFlash(text: string, error?: boolean): void }) {
  const { t } = useI18n()
  const f = useFormat()
  const errorText = useErrorText()
  const changeRole = useChangeRole()
  const resend = useResendInvite()
  const removed = person.status === 'removed'
  const name = displayName(person)

  const now = useNow(60_000)
  // Past its 7 days an invitation no longer lets the person in: the admin resends it.
  const expired = person.status === 'invited' && person.inviteExpiresAt !== null && Date.parse(person.inviteExpiresAt) <= now
  const status =
    person.status === 'active' ? (
      <State led="ok">{t.people.active}</State>
    ) : person.status === 'invited' ? (
      <State led="off">{expired ? t.people.inviteExpired : t.people.invited}</State>
    ) : (
      <State led="err">{t.people.removed}</State>
    )
  const last =
    person.status === 'removed' && person.removedAt
      ? fmt(t.people.removedOn, { date: f.relative(person.removedAt) })
      : expired && person.inviteExpiresAt
        ? fmt(t.people.expiredOn, { date: f.relative(person.inviteExpiresAt) })
      : person.status === 'invited' && person.invitedAt
        ? fmt(t.people.invitedOn, { date: f.relative(person.invitedAt) })
        : person.lastSignInAt
          ? f.relative(person.lastSignInAt)
          : t.common.never

  const setRole = (role: Role) => {
    if (role === person.role) return
    changeRole.mutate(
      { id: person.id, role },
      {
        onSuccess: () => onFlash(fmt(t.people.roleChanged, { name, role: t.roles[role].toLowerCase() })),
        onError: (error) => onFlash(errorText(error), true),
      },
    )
  }

  return (
    <tr data-dim={removed || undefined}>
      <td>
        <Link to={`/persone/${encodeURIComponent(person.id)}`} aria-label={fmt(t.people.open, { name })} className="flex min-w-0 items-center gap-3 text-ink no-underline">
          <Avatar name={name} />
          <div className="min-w-0">
            <b className="block font-semibold hover:underline">
              {name}
              {person.isMe && <span className="font-normal text-ink-3"> ({t.common.you})</span>}
            </b>
            {person.name && <span className="text-[13px] text-ink-3">{person.email}</span>}
          </div>
        </Link>
      </td>
      <td>
        {removed ? (
          <span className="px-2">{t.roles[person.role]}</span>
        ) : (
          <Menu
            align="left"
            items={(['member', 'admin'] as const).map((role) => ({ label: t.roles[role], checked: person.role === role, onSelect: () => setRole(role) }))}
            trigger={(props) => (
              <button
                type="button"
                {...props}
                aria-label={fmt(t.people.changeRole, { name })}
                aria-busy={changeRole.isPending || undefined}
                className="inline-flex h-[30px] items-center gap-1 rounded-lg border-0 bg-transparent px-2 text-sm hover:bg-sunken"
              >
                {t.roles[person.role]}
                <ChevronDown size={15} aria-hidden />
              </button>
            )}
          />
        )}
      </td>
      <td>{status}</td>
      <td className="num text-right">{person.deviceCount}</td>
      <td>{last}</td>
      <td className="text-right">
        {!removed && (
          <Menu
            items={[
              ...(person.status === 'invited'
                ? [
                    {
                      label: t.people.resend,
                      onSelect: () =>
                        resend.mutate(person.id, {
                          onSuccess: () => onFlash(fmt(t.people.resent, { email: person.email })),
                          onError: (error) => onFlash(errorText(error), true),
                        }),
                    },
                    { label: t.people.cancelInvite, destructive: true, onSelect: onRemove },
                  ]
                : [{ label: t.people.remove, destructive: true, disabled: person.isMe, hint: person.isMe ? t.people.removeSelf : undefined, onSelect: onRemove }]),
            ]}
            trigger={(props) => (
              <IconButton {...props} label={fmt(t.common.more, { name })}>
                <Ellipsis size={18} aria-hidden />
              </IconButton>
            )}
          />
        )}
      </td>
    </tr>
  )
}

/** The people page. */
export function PeoplePage() {
  const { t } = useI18n()
  const session = useCurrentSession()
  const people = usePeople()
  const [inviting, setInviting] = useState(false)
  const [removing, setRemoving] = useState<Person | null>(null)
  const [flash, setFlash] = useFlash<{ text: string; error: boolean }>()

  return (
    <>
      <PageHeader
        title={t.people.title}
        lead={fmt(t.people.lead, { org: session.organization.name })}
        actions={
          <Button variant="ink" onClick={() => setInviting(true)}>
            <Plus size={17} aria-hidden />
            {t.people.invite}
          </Button>
        }
      />
      <KeyNotice urgent />
      {flash && <Notice className="mb-3.5" state={<State led={flash.error ? 'err' : 'ok'}>{flash.error ? t.common.failed : t.common.done}</State>} text={flash.text} />}
      <QueryView
        query={people}
        loading={
          <table className="tbl">
            <tbody>
              <SkeletonRows widths={[180, 70, 90, 20, 90, 20]} />
            </tbody>
          </table>
        }
        isEmpty={(list) => list.filter((p) => p.status !== 'removed').length <= 1}
        empty={<EmptyState title={t.people.onlyYouTitle} body={t.people.onlyYouBody} action={<Button variant="ink" onClick={() => setInviting(true)}><Plus size={17} aria-hidden />{t.people.invite}</Button>} />}
      >
        {(list) => (
          <div className="relative overflow-x-auto">
            <table className="tbl">
              <thead>
                <tr>
                  <th>{t.people.colPerson}</th>
                  <th>{t.people.colRole}</th>
                  <th>{t.people.colStatus}</th>
                  <th className="text-right">{t.people.colDevices}</th>
                  <th>{t.people.colLastSignIn}</th>
                  <th>
                    <span className="sr-only">{t.common.edit}</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {list.map((p) => (
                  <PersonRow key={p.id} person={p} onRemove={() => setRemoving(p)} onFlash={(text, error = false) => setFlash({ text, error })} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </QueryView>
      {inviting && (
        <InviteDialog
          onClose={() => setInviting(false)}
          onSent={(email) => {
            setInviting(false)
            setFlash({ text: fmt(t.people.inviteSent, { email }), error: false })
          }}
        />
      )}
      {removing && (
        <RemoveDialog
          person={removing}
          onClose={() => setRemoving(null)}
          onDone={(name) => {
            setRemoving(null)
            setFlash({ text: fmt(t.people.removedDone, { name }), error: false })
          }}
        />
      )}
    </>
  )
}
