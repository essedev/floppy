/*
  FAKE DATA. The seeded organization of the in-memory API: "Studio Esempio", the people,
  devices and disks of docs/concepts/concept-e-admin.html, with times relative to now so the
  page always looks live. Each disk has its own bucket and instance and each person an access
  per disk (DECISIONS #23, #24). Names and sizes are invented; block size, trash days and cache
  expiry are the real configuration (docs/ARCHITECTURE.md, DECISIONS #15).
*/

import type {
  AccessLevel,
  AuditEvent,
  CachePolicy,
  Disk,
  KeyWindowReason,
  MailLanguage,
  Person,
  ReplicaMode,
  StorageMode,
  StorageProvider,
  Versioning,
} from '../types'

/** A person as the fake stores it; deviceCount and isMe are computed on read. */
export interface PersonRow extends Omit<Person, 'deviceCount' | 'isMe'> {
  /** The token of the latest invitation link, while invited. */
  inviteToken: string | null
}

/** A device as the fake stores it. `live` ones keep sending heartbeats while the page is open. */
export interface DeviceRow {
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

/** A rotation of one disk's key; its progress is derived from the time since it started. */
export interface RotationRow {
  id: string
  newKeyId: string
  newKeyTail: string
  oldKeyTails: string[]
  startedAt: string
  devicesTotal: number
  /** Where it fails, from the pasted key (DENIED: the probe, BADCONFIG: juicefs config). */
  failStep: 'probe' | 'config' | null
  /** The new key became the disk's (juicefs config done). */
  applied: boolean
  /** key_rotation_failed was logged. */
  failLogged: boolean
}

/** A disk as the fake stores it: the API's fields plus its key, window and status. */
export interface DiskRow extends Disk {
  /** JuiceFS's name: derived from the name, never shown. */
  volumeName: string
  /** While creating: when the fake format ends, and whether it fails. */
  formatDoneAt: string | null
  formatFails: boolean
  /** Manual: the access key id the format holds (not a secret) and its tail. */
  keyId: string | null
  keyTail: string | null
  rotatedAt: string | null
  /** Manual: open exposures of the disk's key; the window is open while there is one. */
  exposures: KeyWindowReason[]
  rotation: RotationRow | null
  versioning: Versioning
  storageState: 'ok' | 'failing' | 'unknown'
  lastProbeAt: string | null
  latencyMs: number
}

/** One person on one disk. `appliedAt` null is owed until the disk's instance answers. */
export interface AccessRow {
  diskId: string
  personId: string
  access: AccessLevel
  appliedAt: string | null
}

/** A device's own key (managed). `deleteAt`: when the adapter deletes a retiring one. */
export interface DeviceKeyRow {
  id: string
  deviceId: string
  tail: string
  state: 'pending' | 'active' | 'retiring'
  activatedAt: string | null
  retiringAt: string | null
  deleteAt: string | null
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
  version: 3
  organizationId: string
  organizationName: string
  mailLanguage: MailLanguage
  mail: { host: string; port: number; username: string; sender: string; passwordSet: boolean }
  /** The default endpoint and region of new disks. */
  storage: { endpoint: string; region: string }
  storageMode: StorageMode
  storageProvider: StorageProvider | null
  controlPlaneKeyTail: string | null
  people: PersonRow[]
  devices: DeviceRow[]
  /** Live disks, `creating` included. */
  disks: DiskRow[]
  /** Disks whose creation failed: soft deleted, their names stay burned. */
  failedDisks: DiskRow[]
  access: AccessRow[]
  deviceKeys: DeviceKeyRow[]
  audit: AuditEvent[]
  signIns: SignInRow[]
  lastMailAt: string | null
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

const REGION = 'eu-south-mil'
const ENDPOINT = 's3.eu-south-mil.io.cloud.ovh.net'
const GARAGE = 'garage.studio.example:3900'

/** Where a disk appears on a device: a folder inside the one mounted disk. */
export const mountPathOf = (name: string) => `/Volumes/Floppy/${name}`

/** The instance the meta service gives a disk, as devices reach it. */
export const metaUrlOf = (volumeName: string) => `rediss://${volumeName}.meta.studio.example:6380/1`

/** The bucket the adapter creates for a disk (managed). */
export const managedBucketOf = (diskId: string) =>
  `floppy-${Array.from(diskId, (c) => c.charCodeAt(0).toString(16)).join('').padEnd(32, '0').slice(0, 32)}`

/** Which organization to seed: the full one in manual or managed mode, or one just created. */
export type Scenario = 'default' | 'managed' | 'empty'

/** A fresh fake database for a scenario. */
export function seed(scenario: Scenario, now = Date.now()): FakeDb {
  const ago = (ms: number) => new Date(now - ms).toISOString()
  const managed = scenario === 'managed'
  const giulia = { id: 'u-giulia', name: 'Giulia Ferri' }

  const base: FakeDb = {
    version: 3,
    organizationId: 'org-studio-esempio',
    organizationName: 'Studio Esempio',
    mailLanguage: 'it',
    mail: { host: 'smtp.studio.example', port: 587, username: 'floppy@studio.example', sender: 'Floppy · Studio Esempio', passwordSet: true },
    storage: managed ? { endpoint: GARAGE, region: 'garage' } : { endpoint: ENDPOINT, region: REGION },
    storageMode: managed ? 'managed' : 'manual',
    storageProvider: managed ? 'garage' : null,
    controlPlaneKeyTail: managed ? 'GK7Q2' : null,
    people: [],
    devices: [],
    disks: [],
    failedDisks: [],
    access: [],
    deviceKeys: [],
    audit: [],
    signIns: [],
    lastMailAt: ago(50 * MIN),
  }

  const person = (id: string, name: string | null, email: string, role: Person['role'], extra: Partial<PersonRow>): PersonRow => ({
    id, name, email, role, status: 'active', lastSignInAt: null, invitedAt: ago(11 * DAY), invitedBy: giulia.name, inviteExpiresAt: null, removedAt: null, inviteToken: null, ...extra,
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
    person('u-luca', null, 'luca@studio.example', 'member', { status: 'invited', invitedAt: ago(50 * MIN), inviteExpiresAt: new Date(now - 50 * MIN + INVITE_TTL).toISOString(), inviteToken: 'inv-luca-seed' }),
    person('u-chiara', null, 'chiara@studio.example', 'member', { status: 'invited', invitedAt: ago(9 * DAY), inviteExpiresAt: ago(2 * DAY), inviteToken: 'inv-chiara-seed' }),
    person('u-paolo', 'Paolo Greco', 'paolo@studio.example', 'member', { status: 'removed', lastSignInAt: ago(4 * DAY), removedAt }),
  ]

  const device = (id: string, name: string, ownerId: string, macosVersion: string, appVersion: string, seenAgo: number | null, extra: Partial<DeviceRow> = {}): DeviceRow => ({
    id, name, ownerId, valkeyUser: `floppy_d_${id}`, macosVersion, appVersion,
    lastSeenAt: seenAgo === null ? null : ago(seenAgo), live: false, replica: null, revokedAt: null, revokedBy: null, ...extra,
  })
  base.devices = [
    device('7f3a2c', 'MacBook Pro di Giulia', 'u-giulia', '27.0.1', '0.4.2', 20_000, { live: true, replica: { mode: 'replica', lagMs: 12 } }),
    device('b81e04', 'Mac Studio di Giulia', 'u-giulia', '26.6', '0.4.2', 12 * MIN),
    device('19c6d7', 'MacBook Air di Marco', 'u-marco', '27.0.1', '0.4.1', 35_000, { live: true, replica: { mode: 'master', lagMs: 3400 } }),
    device('e2047b', 'MacBook Pro di Sara', 'u-sara', '27.0', '0.4.2', 3 * HOUR),
    device('5d93f0', 'iMac di Sara', 'u-sara', '26.6', '0.4.0', 20 * HOUR),
    device('a4c71e', 'MacBook Pro di Elena', 'u-elena', '27.0.1', '0.4.2', 10_000, { live: true, replica: { mode: 'replica', lagMs: 8 } }),
    device('3be5a9', 'MacBook Air di Anna', 'u-anna', '27.0.1', '0.4.2', 2 * DAY + 3 * HOUR),
    device('c0f218', 'MacBook Pro di Paolo', 'u-paolo', '27.0', '0.4.1', 4 * DAY, { revokedAt: removedAt, revokedBy: giulia.name }),
  ]

  const disk = (
    id: string, name: string, volumeName: string, bucket: string, keyTail: string,
    used: number, files: number, trash: number, lastWrite: number, mounted: number, created: number, by: string, extra: Partial<DiskRow> = {},
  ): DiskRow => ({
    id, name, volumeName, mountPath: mountPathOf(name), state: 'ready', error: null, status: 'online',
    bucket: managed ? managedBucketOf(id) : bucket, region: base.storage.region, endpoint: base.storage.endpoint, metaUrl: metaUrlOf(volumeName),
    blockSizeKb: 1024, trashDays: 7, trashBytes: trash, usedBytes: used, fileCount: files, lastWriteAt: ago(lastWrite), mountedBy: mounted,
    cache: { ...DEFAULT_CACHE }, createdAt: ago(created), createdBy: by, formatDoneAt: null, formatFails: false,
    keyId: managed ? null : `AKFLOPPY${keyTail}`, keyTail: managed ? null : keyTail, rotatedAt: null, exposures: [], rotation: null,
    versioning: managed ? { state: 'unknown', oldVersionsDays: null } : { state: 'enabled', oldVersionsDays: 7 },
    storageState: 'ok', lastProbeAt: ago(2 * MIN), latencyMs: 4, ...extra,
  })
  base.disks = [
    disk('d-progetti', 'Progetti', 'progetti', 'studio-esempio-progetti', 'ZT0QK', 4.82e12, 38_214, 212e9, 3 * MIN, 3, 11 * DAY, giulia.name, {
      rotatedAt: managed ? null : ago(8 * DAY),
      exposures: managed ? [] : [{ id: 'x-paolo', kind: 'person_removed', label: 'Paolo Greco', at: removedAt }],
    }),
    disk('d-archivio', 'Archivio 2025', 'archivio-2025', 'studio-esempio-archivio-2025', '4RM8D', 2.1e12, 9_877, 18e9, 6 * DAY, 1, 5 * DAY, 'Anna Villa', {
      versioning: managed ? { state: 'unknown', oldVersionsDays: null } : { state: 'off', oldVersionsDays: null },
    }),
    // Its instance does not answer: access changes to it stay owed until it is back.
    disk('d-consegne', 'Consegne', 'consegne', 'studio-esempio-consegne', 'Q2V7N', 380e9, 1_204, 2e9, 26 * HOUR, 0, 3 * DAY, giulia.name, {
      status: 'unreachable', versioning: { state: 'unknown', oldVersionsDays: null }, lastProbeAt: ago(3 * DAY),
    }),
  ]

  const done = ago(DAY)
  const grant = (diskId: string, personId: string, access: AccessLevel, appliedAt: string | null = done): AccessRow => ({ diskId, personId, access, appliedAt })
  base.access = [
    grant('d-progetti', 'u-giulia', 'write'),
    grant('d-progetti', 'u-anna', 'write'),
    grant('d-progetti', 'u-marco', 'write'),
    grant('d-progetti', 'u-sara', 'write'),
    grant('d-progetti', 'u-elena', 'read'),
    grant('d-progetti', 'u-luca', 'read'),
    grant('d-archivio', 'u-anna', 'write'),
    grant('d-archivio', 'u-giulia', 'read'),
    grant('d-archivio', 'u-sara', 'read'),
    grant('d-consegne', 'u-giulia', 'write'),
    grant('d-consegne', 'u-elena', 'write'),
    grant('d-consegne', 'u-marco', 'read', null),
  ]

  if (managed) {
    const tails = ['K3V9A', 'M8Q2C', 'P4X7T', 'R2D6W', 'T9B3H', 'W5N8L', 'Z1F4S']
    base.deviceKeys = base.devices
      .filter((d) => !d.revokedAt)
      .map((d, i) => ({ id: `dk-${d.id}`, deviceId: d.id, tail: tails[i % tails.length] ?? 'K3V9A', state: 'active', activatedAt: ago(10 * DAY - i * 3 * HOUR), retiringAt: null, deleteAt: null }))
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
  const operator = { kind: 'operator' as const, id: null, label: null }
  const personTarget = (id: string) => ({ kind: 'person' as const, id, label: actor(id).label })
  const deviceTarget = (id: string) => ({ kind: 'device' as const, id, label: db.devices.find((m) => m.id === id)?.name ?? id })
  const diskTarget = (id: string) => ({ kind: 'disk' as const, id, label: db.disks.find((d) => d.id === id)?.name ?? id })
  const key = (label: string) => ({ kind: 'key' as const, id: `k-${label}`, label })
  const manual = db.storageMode === 'manual'
  const events: Omit<AuditEvent, 'id'>[] = [
    { at: ago(40 * MIN), actor: actor('u-giulia'), action: 'disk_access_changed', category: 'disks', target: diskTarget('d-consegne'), detail: { person: 'Marco Bassi', from: 'none', to: 'read' } },
    { at: ago(50 * MIN), actor: actor('u-giulia'), action: 'person_invited', category: 'people', target: { kind: 'person', id: 'u-luca', label: 'luca@studio.example' }, detail: { role: 'member' } },
    { at: ago(50 * MIN), actor: actor('u-giulia'), action: 'disk_access_changed', category: 'disks', target: diskTarget('d-progetti'), detail: { person: 'luca@studio.example', from: 'none', to: 'read' } },
    { at: ago(HOUR + 40 * MIN), actor: actor('u-elena'), action: 'device_registered', category: 'devices', target: deviceTarget('a4c71e'), detail: { appVersion: '0.4.2', macosVersion: '27.0.1' } },
    { at: ago(2 * HOUR), actor: actor('u-marco'), action: 'signed_in', category: 'access', target: personTarget('u-marco'), detail: { via: 'link', kind: 'web' } },
    { at: ago(2 * HOUR + 14 * MIN), actor: actor('u-giulia'), action: 'device_revoked', category: 'devices', target: deviceTarget('c0f218'), detail: { reason: 'member_removed' } },
    { at: ago(2 * HOUR + 14 * MIN), actor: actor('u-giulia'), action: 'person_removed', category: 'people', target: personTarget('u-paolo'), detail: { devices: 1 } },
    { at: ago(20 * HOUR), actor: system, action: 'device_seen_outdated', category: 'devices', target: deviceTarget('5d93f0'), detail: { appVersion: '0.4.0' } },
    { at: ago(3 * DAY), actor: actor('u-giulia'), action: 'disk_created', category: 'disks', target: diskTarget('d-consegne'), detail: { blockSize: '1 MB' } },
    { at: ago(5 * DAY), actor: actor('u-anna'), action: 'disk_created', category: 'disks', target: diskTarget('d-archivio'), detail: { blockSize: '1 MB' } },
    { at: ago(5 * DAY), actor: actor('u-anna'), action: 'disk_access_changed', category: 'disks', target: diskTarget('d-archivio'), detail: { person: 'Sara Conti', from: 'none', to: 'read' } },
    { at: ago(9 * DAY), actor: actor('u-giulia'), action: 'role_changed', category: 'people', target: personTarget('u-anna'), detail: { from: 'member', to: 'admin' } },
    { at: ago(9 * DAY + 2 * HOUR), actor: actor('u-giulia'), action: 'person_invited', category: 'people', target: { kind: 'person', id: 'u-chiara', label: 'chiara@studio.example' }, detail: { role: 'member' } },
    { at: ago(11 * DAY), actor: actor('u-giulia'), action: 'disk_created', category: 'disks', target: diskTarget('d-progetti'), detail: { blockSize: '1 MB' } },
    { at: ago(11 * DAY + 20 * MIN), actor: actor('u-giulia'), action: 'settings_updated', category: 'settings', target: { kind: 'organization', id: db.organizationId, label: db.organizationName }, detail: { fields: ['mail.host', 'mail.sender'] } },
    { at: ago(12 * DAY), actor: operator, action: 'organization_created', category: 'settings', target: { kind: 'organization', id: db.organizationId, label: db.organizationName }, detail: manual ? { storageMode: 'manual' } : { storageMode: 'managed', provider: db.storageProvider ?? '' } },
  ]
  if (manual) {
    events.push(
      { at: ago(8 * DAY), actor: actor('u-giulia'), action: 'key_window_closed', category: 'key', target: key('B7M2Q'), detail: { disk: 'Progetti', oldKeyTail: 'B7M2Q', reasons: 1 } },
      { at: ago(8 * DAY + 3 * MIN), actor: system, action: 'key_rotated', category: 'key', target: key('ZT0QK'), detail: { disk: 'Progetti' } },
      { at: ago(8 * DAY + 4 * MIN), actor: actor('u-giulia'), action: 'key_rotation_started', category: 'key', target: key('ZT0QK'), detail: { disk: 'Progetti', newKeyTail: 'ZT0QK', oldKeyTail: 'B7M2Q' } },
    )
    for (const d of db.disks) {
      events.push({ at: new Date(Date.parse(d.createdAt) - MIN).toISOString(), actor: actor(d.createdBy === 'Anna Villa' ? 'u-anna' : 'u-giulia'), action: 'key_created', category: 'key', target: key(d.keyTail ?? ''), detail: { holder: 'disk', accessKeyTail: d.keyTail ?? '', for: d.name } })
    }
  }
  for (const [i, p] of ['u-anna', 'u-marco', 'u-sara', 'u-elena', 'u-paolo'].entries()) {
    events.push({ at: ago(11 * DAY - i * HOUR), actor: actor('u-giulia'), action: 'person_invited', category: 'people', target: { kind: 'person', id: p, label: db.people.find((x) => x.id === p)?.email ?? p }, detail: { role: 'member' } })
    events.push({ at: ago(10 * DAY + 20 * HOUR - i * HOUR), actor: actor(p), action: 'invite_accepted', category: 'people', target: personTarget(p), detail: {} })
  }
  for (const [i, m] of db.devices.entries()) {
    if (m.id === 'a4c71e') continue
    events.push({ at: ago(10 * DAY - i * 3 * HOUR), actor: actor(m.ownerId), action: 'device_registered', category: 'devices', target: deviceTarget(m.id), detail: { appVersion: '0.4.0', macosVersion: m.macosVersion } })
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
