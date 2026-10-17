/*
  FAKE DATA. The seeded organization of the in-memory API: "Studio Esempio", the people,
  Macs and disks of docs/concepts/concept-e-admin.html, with times relative to now so the page
  always looks live. Names and sizes are invented; block size, trash days and cache expiry
  are the real configuration (docs/ARCHITECTURE.md, DECISIONS #15).
*/

import type { AuditEvent, CachePolicy, Disk, KeyWindowReason, Person, ReplicaMode } from '../types'

/** A person as the fake stores it; macCount and isMe are computed on read. */
export type PersonRow = Omit<Person, 'macCount' | 'isMe'>

/** A Mac as the fake stores it. `live` Macs keep sending heartbeats while the page is open. */
export interface MacRow {
  id: string
  name: string
  ownerId: string
  valkeyUser: string
  macosVersion: string
  appVersion: string
  lastSeenAt: string | null
  live: boolean
  replica: { mode: ReplicaMode; lagMs: number | null } | null
  revokedAt: string | null
  revokedBy: string | null
}

/** A disk as the fake stores it: the API's shape plus when its format "finishes". */
export interface DiskRow extends Disk {
  /** While creating: when the fake format ends, and whether it fails. */
  formatDoneAt: string | null
  formatFails: boolean
}

/** A rotation as the fake stores it; its progress is derived from the time since it started. */
export interface RotationRow {
  id: string
  newKeyTail: string
  oldKeyTails: string[]
  startedAt: string
  macsTotal: number
  failCode: string | null
  /** The new key became the active one (every disk configured). */
  applied: boolean
  /** key_rotation_failed was logged. */
  failLogged: boolean
}

/** A pending sign-in request with its link token and code. */
export interface SignInRow {
  id: string
  email: string
  userId: string | null
  code: string
  token: string
  expiresAt: string
  resendAt: string
  attempts: number
  completed: boolean
  superseded: boolean
}

/** The whole fake database, persisted in localStorage. */
export interface FakeDb {
  version: 2
  organizationId: string
  organizationName: string
  mail: { host: string; port: number; username: string; sender: string; passwordSet: boolean }
  storage: { endpoint: string; region: string; bucket: string }
  people: PersonRow[]
  macs: MacRow[]
  /** Live disks, `creating` included. */
  disks: DiskRow[]
  /** Disks whose creation failed: soft deleted, their name in the bucket and database stay burned. */
  failedDisks: DiskRow[]
  key: {
    accessKeyTail: string
    rotatedAt: string | null
    /** Open exposures; the window is open while there is one. */
    exposures: KeyWindowReason[]
    rotation: RotationRow | null
  }
  audit: AuditEvent[]
  signIns: SignInRow[]
  lastMailAt: string | null
  lastStorageProbeAt: string | null
}

/** The app version the fake considers current. */
export const LATEST_APP = '0.4.2'

const MIN = 60_000
const HOUR = 60 * MIN
const DAY = 24 * HOUR

/** How long an invitation link lasts. */
export const INVITE_TTL = 7 * DAY

/** The fake's default cache policy, the real default of docs/ARCHITECTURE.md. */
export const DEFAULT_CACHE: CachePolicy = { expireDays: 30 }

const BUCKET = 'studio-esempio-floppy'
const REGION = 'eu-south-mil'
const ENDPOINT = 's3.eu-south-mil.io.cloud.ovh.net'

/** Where a disk appears on a Mac: a folder inside the one mounted disk. */
export const mountPathOf = (name: string) => `/Volumes/Floppy/${name}`

/** Which organization to seed: the full one, or one just created. */
export type Scenario = 'default' | 'empty'

/** A fresh fake database for a scenario. */
export function seed(scenario: Scenario, now = Date.now()): FakeDb {
  const ago = (ms: number) => new Date(now - ms).toISOString()
  const giulia = { id: 'u-giulia', name: 'Giulia Ferri' }

  const base: FakeDb = {
    version: 2,
    organizationId: 'org-studio-esempio',
    organizationName: 'Studio Esempio',
    mail: { host: 'smtp.studio.example', port: 587, username: 'floppy@studio.example', sender: 'Floppy · Studio Esempio', passwordSet: true },
    storage: { endpoint: ENDPOINT, region: REGION, bucket: BUCKET },
    people: [],
    macs: [],
    disks: [],
    failedDisks: [],
    key: { accessKeyTail: 'ZT0QK', rotatedAt: null, exposures: [], rotation: null },
    audit: [],
    signIns: [],
    lastMailAt: ago(50 * MIN),
    lastStorageProbeAt: ago(2 * MIN),
  }

  const person = (id: string, name: string | null, email: string, role: Person['role'], extra: Partial<PersonRow>): PersonRow => ({
    id, name, email, role, status: 'active', lastSignInAt: null, invitedAt: ago(11 * DAY), invitedBy: giulia.name, inviteExpiresAt: null, removedAt: null, ...extra,
  })

  if (scenario === 'empty') {
    base.people = [person(giulia.id, giulia.name, 'giulia@studio.example', 'admin', { lastSignInAt: ago(MIN), invitedAt: null, invitedBy: null })]
    base.lastMailAt = null
    return base
  }

  const removedAt = ago(2 * HOUR + 14 * MIN)
  base.people = [
    person(giulia.id, giulia.name, 'giulia@studio.example', 'admin', { lastSignInAt: ago(MIN), invitedAt: null, invitedBy: null }),
    person('u-anna', 'Anna Villa', 'anna@studio.example', 'admin', { lastSignInAt: ago(2 * DAY + 3 * HOUR) }),
    person('u-marco', 'Marco Bassi', 'marco@studio.example', 'member', { lastSignInAt: ago(2 * HOUR) }),
    person('u-sara', 'Sara Conti', 'sara@studio.example', 'member', { lastSignInAt: ago(3 * HOUR) }),
    person('u-elena', 'Elena Riva', 'elena@studio.example', 'member', { lastSignInAt: ago(HOUR + 40 * MIN) }),
    person('u-luca', null, 'luca@studio.example', 'member', { status: 'invited', invitedAt: ago(50 * MIN), inviteExpiresAt: new Date(now - 50 * MIN + INVITE_TTL).toISOString() }),
    person('u-chiara', null, 'chiara@studio.example', 'member', { status: 'invited', invitedAt: ago(9 * DAY), inviteExpiresAt: ago(2 * DAY) }),
    person('u-paolo', 'Paolo Greco', 'paolo@studio.example', 'member', { status: 'removed', lastSignInAt: ago(4 * DAY), removedAt }),
  ]

  const mac = (id: string, name: string, ownerId: string, macosVersion: string, appVersion: string, seenAgo: number | null, extra: Partial<MacRow> = {}): MacRow => ({
    id, name, ownerId, valkeyUser: `floppy_d_${id}`, macosVersion, appVersion,
    lastSeenAt: seenAgo === null ? null : ago(seenAgo), live: false, replica: null, revokedAt: null, revokedBy: null, ...extra,
  })
  base.macs = [
    mac('7f3a2c', 'MacBook Pro di Giulia', 'u-giulia', '27.0.1', '0.4.2', 20_000, { live: true, replica: { mode: 'replica', lagMs: 12 } }),
    mac('b81e04', 'Mac Studio di Giulia', 'u-giulia', '26.6', '0.4.2', 12 * MIN),
    mac('19c6d7', 'MacBook Air di Marco', 'u-marco', '27.0.1', '0.4.1', 35_000, { live: true, replica: { mode: 'master', lagMs: 3400 } }),
    mac('e2047b', 'MacBook Pro di Sara', 'u-sara', '27.0', '0.4.2', 3 * HOUR),
    mac('5d93f0', 'iMac di Sara', 'u-sara', '26.6', '0.4.0', 20 * HOUR),
    mac('a4c71e', 'MacBook Pro di Elena', 'u-elena', '27.0.1', '0.4.2', 10_000, { live: true, replica: { mode: 'replica', lagMs: 8 } }),
    mac('3be5a9', 'MacBook Air di Anna', 'u-anna', '27.0.1', '0.4.2', 2 * DAY + 3 * HOUR),
    mac('c0f218', 'MacBook Pro di Paolo', 'u-paolo', '27.0', '0.4.1', 4 * DAY, { revokedAt: removedAt, revokedBy: giulia.name }),
  ]

  const disk = (id: string, name: string, volumeName: string, metaDb: number, used: number, files: number, trash: number, lastWrite: number, mounted: number, created: number, by: string): DiskRow => ({
    id, name, volumeName, mountPath: mountPathOf(name), state: 'ready', error: null, status: 'online', bucket: BUCKET, region: REGION, endpoint: ENDPOINT,
    metaDb, blockSizeKb: 1024, trashDays: 7, trashBytes: trash, usedBytes: used, fileCount: files, lastWriteAt: ago(lastWrite), mountedBy: mounted,
    cache: { ...DEFAULT_CACHE }, createdAt: ago(created), createdBy: by, formatDoneAt: null, formatFails: false,
  })
  base.disks = [
    disk('d-progetti', 'Progetti', 'progetti', 1, 4.82e12, 38_214, 212e9, 3 * MIN, 3, 11 * DAY, giulia.name),
    disk('d-archivio', 'Archivio 2025', 'archivio-2025', 2, 2.1e12, 9_877, 18e9, 6 * DAY, 1, 5 * DAY, 'Anna Villa'),
  ]

  base.key = {
    accessKeyTail: 'ZT0QK',
    rotatedAt: ago(8 * DAY),
    exposures: [{ id: 'x-paolo', kind: 'person_removed', label: 'Paolo Greco', at: removedAt }],
    rotation: null,
  }

  base.audit = seedAudit(now, base)
  return base
}

function seedAudit(now: number, db: FakeDb): AuditEvent[] {
  const ago = (ms: number) => new Date(now - ms).toISOString()
  const actor = (id: string) => {
    const p = db.people.find((x) => x.id === id)
    return { kind: 'person' as const, id, label: p?.name ?? p?.email ?? id }
  }
  const system = { kind: 'system' as const, id: null, label: null }
  const personTarget = (id: string) => ({ kind: 'person' as const, id, label: actor(id).label })
  const macTarget = (id: string) => ({ kind: 'mac' as const, id, label: db.macs.find((m) => m.id === id)?.name ?? id })
  const key = (label: string) => ({ kind: 'key' as const, id: `k-${label}`, label })
  const events: Omit<AuditEvent, 'id'>[] = [
    { at: ago(50 * MIN), actor: actor('u-giulia'), action: 'person_invited', category: 'people', target: { kind: 'person', id: 'u-luca', label: 'luca@studio.example' }, detail: { role: 'member' } },
    { at: ago(HOUR + 40 * MIN), actor: actor('u-elena'), action: 'mac_registered', category: 'macs', target: macTarget('a4c71e'), detail: { appVersion: '0.4.2' } },
    { at: ago(2 * HOUR), actor: actor('u-marco'), action: 'signed_in', category: 'access', target: personTarget('u-marco'), detail: { via: 'link', kind: 'web' } },
    { at: ago(2 * HOUR + 14 * MIN), actor: actor('u-giulia'), action: 'mac_revoked', category: 'macs', target: macTarget('c0f218'), detail: { reason: 'member_removed' } },
    { at: ago(2 * HOUR + 14 * MIN), actor: actor('u-giulia'), action: 'person_removed', category: 'people', target: personTarget('u-paolo'), detail: { macs: 1 } },
    { at: ago(20 * HOUR), actor: system, action: 'mac_seen_outdated', category: 'macs', target: macTarget('5d93f0'), detail: { appVersion: '0.4.0' } },
    { at: ago(5 * DAY), actor: actor('u-anna'), action: 'disk_created', category: 'disks', target: { kind: 'disk', id: 'd-archivio', label: 'Archivio 2025' }, detail: { blockSize: '1 MB' } },
    { at: ago(8 * DAY), actor: actor('u-giulia'), action: 'key_window_closed', category: 'key', target: key('ZT0QK'), detail: { oldKeyTail: 'B7M2Q' } },
    { at: ago(8 * DAY + 3 * MIN), actor: system, action: 'key_rotated', category: 'key', target: key('ZT0QK'), detail: { disks: 1 } },
    { at: ago(8 * DAY + 4 * MIN), actor: actor('u-giulia'), action: 'key_rotation_started', category: 'key', target: key('ZT0QK'), detail: { newKeyTail: 'ZT0QK', oldKeyTail: 'B7M2Q' } },
    { at: ago(9 * DAY), actor: actor('u-giulia'), action: 'role_changed', category: 'people', target: personTarget('u-anna'), detail: { from: 'member', to: 'admin' } },
    { at: ago(9 * DAY + 2 * HOUR), actor: actor('u-giulia'), action: 'person_invited', category: 'people', target: { kind: 'person', id: 'u-chiara', label: 'chiara@studio.example' }, detail: { role: 'member' } },
    { at: ago(11 * DAY), actor: actor('u-giulia'), action: 'disk_created', category: 'disks', target: { kind: 'disk', id: 'd-progetti', label: 'Progetti' }, detail: { blockSize: '1 MB' } },
    { at: ago(11 * DAY + 20 * MIN), actor: actor('u-giulia'), action: 'settings_updated', category: 'settings', target: { kind: 'organization', id: db.organizationId, label: db.organizationName }, detail: { fields: ['smtp'] } },
  ]
  for (const [i, p] of ['u-anna', 'u-marco', 'u-sara', 'u-elena', 'u-paolo'].entries()) {
    events.push({ at: ago(11 * DAY - i * HOUR), actor: actor('u-giulia'), action: 'person_invited', category: 'people', target: { kind: 'person', id: p, label: db.people.find((x) => x.id === p)?.email ?? p }, detail: { role: 'member' } })
    events.push({ at: ago(10 * DAY + 20 * HOUR - i * HOUR), actor: actor(p), action: 'invite_accepted', category: 'people', target: personTarget(p), detail: {} })
  }
  for (const [i, m] of db.macs.entries()) {
    if (m.id === 'a4c71e') continue
    events.push({ at: ago(10 * DAY - i * 3 * HOUR), actor: actor(m.ownerId), action: 'mac_registered', category: 'macs', target: macTarget(m.id), detail: { appVersion: '0.4.0' } })
  }
  // Older sign-ins, so the log has pages and the 90-day filter means something.
  const active = ['u-giulia', 'u-anna', 'u-marco', 'u-sara', 'u-elena']
  for (let day = 3; day < 80; day += 2) {
    const who = active[day % active.length] ?? 'u-giulia'
    events.push({ at: ago(day * DAY + (day % 7) * HOUR), actor: actor(who), action: 'signed_in', category: 'access', target: personTarget(who), detail: { via: 'link', kind: 'web' } })
  }
  return events
    .sort((a, b) => b.at.localeCompare(a.at))
    .map((e, i) => ({ ...e, id: `e-${String(i + 1).padStart(4, '0')}` }))
}
