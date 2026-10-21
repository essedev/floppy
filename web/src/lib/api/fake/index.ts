/*
  FAKE API. An in-memory stand-in for the control plane (VITE_API=fake, the default of
  `pnpm dev`). It implements the same `Api` interface as http.ts with realistic latency, seeded
  data (seed.ts) and the rules of docs/DATABASE_SCHEMA.md ("Access per disk", "Storage modes",
  "State machines"): members see only their own devices; only admins invite, an invitation lasts
  7 days and gives no access to a disk it does not list; a new disk gives write to its creator
  and none to everyone else unless listed; a change of access is owed until the disk's instance
  takes it; in manual mode each disk brings its own bucket and key, probed first, and removing a
  person, revoking a device or lowering someone to none opens the key window of each disk they
  could see; in managed mode the adapter creates the bucket and each device has its own key,
  deleted at the provider on revocation; the last admin cannot be demoted; a sign-in answers the
  same for unknown addresses and polling never signs in; a rotation awaits the old key's deletion
  once the disk is configured, and closing it closes only that disk's exposures from before it
  started; the default endpoint is locked while a disk exists.

  State persists in localStorage so reloads keep it; `fakeControl` resets it, switches the
  scenario, makes every call fail like a network outage, and exposes the last email "sent".
  Deterministic triggers for error states:
    - an access key containing DENIED fails the probe write (AccessDenied), BADCONFIG fails a
      rotation's juicefs config;
    - a new disk whose name contains "fail" fails its format;
    - ?fake=empty seeds a new organization, ?fake=managed the managed one, ?fake=fail starts
      with the network down, ?fake=default goes back to the full manual one.
*/

import { ApiError, type Api } from '../client'
import {
  BUCKET_NAME,
  DISK_NAME,
  type AccessLevel,
  type AuditEvent,
  type AuditQuery,
  type Device,
  type Disk,
  type DiskAccess,
  type DiskKey,
  type Health,
  type KeyStatus,
  type KeyWindowReason,
  type MetadataHealth,
  type MyDisk,
  type Person,
  type Rotation,
  type Session,
  type Settings,
  type StepState,
  type StorageHealth,
  type Versioning,
} from '../types'
import {
  DEFAULT_CACHE,
  INVITE_TTL,
  LATEST_APP,
  managedBucketOf,
  metaUrlOf,
  mountPathOf,
  seed,
  type AccessRow,
  type DeviceRow,
  type DiskRow,
  type FakeDb,
  type PersonRow,
  type Scenario,
} from './seed'

const DB_KEY = 'floppy.fake.db.v4'
const SESSION_KEY = 'floppy.fake.session'
const FAILING_KEY = 'floppy.fake.failing'
const OUTBOX_KEY = 'floppy.fake.outbox'

const LINK_TTL = 15 * 60_000
const RESEND_AFTER = 60_000
const MAX_ATTEMPTS = 5
const CONNECTED_WITHIN = 2 * 60_000
const FORMAT_MS = 3_000
/** How long the fake takes to set a changed access on an instance and at the provider. */
const APPLY_MS = 2_500
/** How long the adapter takes to delete a revoked device's key. */
const DELETE_KEY_MS = 2_000

// Storage can be missing or throw (private windows): the fake then lives in memory only.
function read<T>(key: string): T | null {
  try {
    const raw = localStorage.getItem(key)
    return raw === null ? null : (JSON.parse(raw) as T)
  } catch {
    return null
  }
}
function write(key: string, value: unknown): void {
  try {
    if (value === null) localStorage.removeItem(key)
    else localStorage.setItem(key, JSON.stringify(value))
  } catch {
    // In-memory only: nothing else to do.
  }
}

/** The email the fake would have sent last, shown in the fake banner. */
export interface FakeMail {
  /** A sign-in link and code, or an invitation link. */
  kind: 'sign-in' | 'invite'
  to: string
  /** Empty for an invitation, which has only a link. */
  code: string
  token: string
  at: string
}

let db: FakeDb = read<FakeDb>(DB_KEY) ?? seed('default')
let failing = read<boolean>(FAILING_KEY) ?? false
const listeners = new Set<() => void>()

function bootFromUrl(): void {
  const param = new URLSearchParams(location.search).get('fake')
  if (param === 'empty' || param === 'default' || param === 'managed') {
    db = seed(param)
    // Persisted at once: the next load reads the stored database, not the URL.
    write(DB_KEY, db)
    write(SESSION_KEY, null)
    failing = false
  }
  if (param === 'fail') failing = true
  write(FAILING_KEY, failing)
}
bootFromUrl()

const save = () => write(DB_KEY, db)
const notify = () => listeners.forEach((fn) => fn())
const now = () => Date.now()
const iso = (t = now()) => new Date(t).toISOString()
const uid = (prefix: string) => `${prefix}-${Math.random().toString(36).slice(2, 10)}`
const tailOf = (accessKeyId: string) => accessKeyId.trim().slice(-5).toUpperCase()
const SYSTEM = { kind: 'system' as const, id: null, label: null }
const manual = () => db.storageMode === 'manual'

/** Waits like a network round trip, then fails if the fake network is down. */
async function latency(min = 220, max = 600): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, min + Math.random() * (max - min)))
  if (failing) throw new ApiError(0, 'network', 'Fake network is down')
}

// Sessions

function currentUser(): PersonRow | null {
  const id = read<string>(SESSION_KEY)
  const person = db.people.find((p) => p.id === id)
  return person && person.status === 'active' ? person : null
}
function requireUser(): PersonRow {
  const user = currentUser()
  if (!user) throw new ApiError(401, 'unauthorized', 'Not signed in')
  return user
}
function requireAdmin(): PersonRow {
  const user = requireUser()
  if (user.role !== 'admin') throw new ApiError(403, 'forbidden', 'Admins only')
  return user
}
const labelOf = (p: PersonRow) => p.name ?? p.email
const actorOf = (p: PersonRow) => ({ kind: 'person' as const, id: p.id, label: labelOf(p) })
const inviteLive = (p: PersonRow) => p.inviteExpiresAt !== null && Date.parse(p.inviteExpiresAt) > now()

function sessionOf(user: PersonRow): Session {
  return {
    user: { id: user.id, email: user.email, name: user.name ?? user.email },
    role: user.role,
    organization: {
      id: db.organizationId,
      name: db.organizationName,
      diskCount: db.disks.length,
      peopleCount: db.people.filter((p) => p.status !== 'removed').length,
      deviceCount: db.devices.filter((m) => !m.revokedAt).length,
    },
  }
}

/** Accepts a pending invitation: the person becomes active, named from the address. */
function accept(person: PersonRow): void {
  person.status = 'active'
  person.inviteExpiresAt = null
  person.inviteToken = null
  const local = person.email.split('@')[0] ?? person.email
  person.name = local.charAt(0).toUpperCase() + local.slice(1)
  log({ actor: actorOf(person), action: 'invite_accepted', category: 'people', target: { kind: 'person', id: person.id, label: person.name }, detail: {} })
}

function startSession(userId: string, via: 'link' | 'code' | 'invitation'): Session {
  write(SESSION_KEY, userId)
  const person = db.people.find((p) => p.id === userId)
  if (!person) throw new ApiError(401, 'unauthorized', 'Unknown user')
  if (person.status === 'invited') accept(person)
  person.lastSignInAt = iso()
  log({ actor: actorOf(person), action: 'signed_in', category: 'access', target: { kind: 'person', id: person.id, label: labelOf(person) }, detail: { via, kind: 'web' } })
  save()
  return sessionOf(person)
}

function sendInvite(person: PersonRow): void {
  person.inviteToken = uid('inv')
  person.invitedAt = iso()
  person.inviteExpiresAt = iso(now() + INVITE_TTL)
  write(OUTBOX_KEY, { kind: 'invite', to: person.email, code: '', token: person.inviteToken, at: iso() } satisfies FakeMail)
  db.lastMailAt = iso()
}

// Rows to API shapes

function heartbeat(): void {
  // Live devices keep reporting while the page is open, as the app's heartbeat would.
  for (const m of db.devices) if (m.live && !m.revokedAt) m.lastSeenAt = iso(now() - 5_000 - Math.random() * 20_000)
}

const isConnected = (m: DeviceRow) => !m.revokedAt && m.lastSeenAt !== null && now() - Date.parse(m.lastSeenAt) < CONNECTED_WITHIN

function toDevice(m: DeviceRow): Device {
  const owner = db.people.find((p) => p.id === m.ownerId)
  const connected = isConnected(m)
  return {
    id: m.id,
    name: m.name,
    state: m.revokedAt ? 'revoked' : m.lastSeenAt ? 'active' : 'pending',
    owner: { id: m.ownerId, name: owner ? labelOf(owner) : m.ownerId },
    valkeyUser: m.valkeyUser,
    macosVersion: m.macosVersion,
    appVersion: m.appVersion,
    appOutdated: m.appVersion !== LATEST_APP,
    lastSeenAt: m.lastSeenAt,
    connected,
    replica: connected ? m.replica : null,
    revokedAt: m.revokedAt,
    revokedBy: m.revokedBy,
  }
}

function toPerson(p: PersonRow, me: PersonRow): Person {
  const { inviteToken: _token, ...person } = p
  return {
    ...person,
    deviceCount: db.devices.filter((m) => m.ownerId === p.id && !m.revokedAt).length,
    isMe: p.id === me.id,
  }
}

function toDisk(d: DiskRow): Disk {
  return structuredClone({
    id: d.id, name: d.name, mountPath: d.mountPath, state: d.state, error: d.error, status: d.status,
    bucket: d.bucket, region: d.region, endpoint: d.endpoint, metaUrl: d.state === 'ready' ? d.metaUrl : null,
    blockSizeKb: d.blockSizeKb, trashDays: d.trashDays, trashBytes: d.trashBytes, usedBytes: d.usedBytes,
    fileCount: d.fileCount, lastWriteAt: d.lastWriteAt, mountedBy: d.mountedBy, cache: d.cache,
    createdAt: d.createdAt, createdBy: d.createdBy,
  })
}

function log(event: Omit<AuditEvent, 'id' | 'at'>): void {
  db.audit.unshift({ ...event, id: uid('e'), at: iso() })
}

// Access per disk

const activeDevicesOf = (personId: string) => db.devices.filter((m) => m.ownerId === personId && !m.revokedAt)
const accessRow = (diskId: string, personId: string) => db.access.find((a) => a.diskId === diskId && a.personId === personId)
const accessOf = (diskId: string, personId: string): AccessLevel => accessRow(diskId, personId)?.access ?? 'none'

/** Owed rows of reachable instances are applied once their time has come. */
function advanceAccess(): void {
  for (const a of db.access) {
    const disk = db.disks.find((d) => d.id === a.diskId)
    if (a.appliedAt === null && disk?.status === 'online' && disk.state === 'ready') a.appliedAt = iso(now() + APPLY_MS)
  }
}

/** Whether a person's devices have their access to a disk, or are still owed it. */
function applied(a: AccessRow | undefined): boolean {
  if (!a || activeDevicesOf(a.personId).length === 0) return true
  return a.appliedAt !== null && Date.parse(a.appliedAt) <= now()
}

/** Writes an access and logs it, as the server's put_access; nothing when it does not change. */
function putAccess(disk: DiskRow, person: PersonRow, access: AccessLevel, by: PersonRow): void {
  const row = accessRow(disk.id, person.id)
  const from = row?.access ?? 'none'
  if (from === access) return
  // An instance that does not answer leaves the change owed; a reachable one takes it shortly.
  const appliedAt = disk.status === 'online' && disk.state === 'ready' ? iso(now() + (activeDevicesOf(person.id).length > 0 ? APPLY_MS : 0)) : null
  if (row) Object.assign(row, { access, appliedAt })
  else db.access.push({ diskId: disk.id, personId: person.id, access, appliedAt })
  log({ actor: actorOf(by), action: 'disk_access_changed', category: 'disks', target: { kind: 'disk', id: disk.id, label: disk.name }, detail: { person: labelOf(person), from, to: access } })
}

/** The disks one of the person's active devices has a user on: those it could read the key of. */
const disksSeenBy = (personId: string) =>
  activeDevicesOf(personId).length === 0 ? [] : db.disks.filter((d) => accessOf(d.id, personId) !== 'none')

function openExposure(disk: DiskRow, kind: KeyWindowReason['kind'], label: string): void {
  disk.exposures.push({ id: uid('x'), kind, label, at: iso() })
}

// Disks being created finish their fake format after a few seconds.
function advanceDisks(): void {
  let changed = false
  for (const d of [...db.disks]) {
    if (d.state !== 'creating' || !d.formatDoneAt || Date.parse(d.formatDoneAt) > now()) continue
    changed = true
    d.formatDoneAt = null
    if (d.formatFails) {
      d.state = 'failed'
      d.error = { code: 'format_failed', message: 'juicefs format: the bucket refused the new volume' }
      db.disks = db.disks.filter((x) => x.id !== d.id)
      db.failedDisks.push(d)
      db.access = db.access.filter((a) => a.diskId !== d.id)
    } else {
      d.state = 'ready'
      d.lastProbeAt = iso()
      d.storageState = 'ok'
      for (const a of db.access) if (a.diskId === d.id) a.appliedAt = iso(now() + APPLY_MS)
    }
  }
  if (changed) save()
}

// The adapter deletes revoked devices' keys shortly after.
function advanceDeviceKeys(): void {
  for (const k of [...db.deviceKeys]) {
    if (k.state !== 'retiring' || !k.deleteAt || Date.parse(k.deleteAt) > now()) continue
    db.deviceKeys = db.deviceKeys.filter((x) => x.id !== k.id)
    log({ actor: SYSTEM, action: 'key_deleted', category: 'key', target: { kind: 'key', id: k.id, label: k.tail }, detail: { holder: 'device', accessKeyTail: k.tail, by: 'adapter' } })
    save()
  }
}

function advance(): void {
  advanceDisks()
  advanceAccess()
  advanceDeviceKeys()
}

// Keys and rotations

const PROBE_MS = 1200
const CONFIG_MS = 1100
const DEVICE_MS = 700

function rotationOf(disk: DiskRow): Rotation | null {
  const r = disk.rotation
  if (!r) return null
  const elapsed = now() - Date.parse(r.startedAt)
  const base = { id: r.id, diskId: disk.id, diskName: disk.name, newKeyTail: r.newKeyTail, oldKeyTails: r.oldKeyTails, startedAt: r.startedAt }
  const probe: StepState = elapsed < PROBE_MS ? 'running' : r.failStep === 'probe' ? 'failed' : 'done'
  const config: StepState = probe !== 'done' ? 'pending' : elapsed < PROBE_MS + CONFIG_MS ? 'running' : r.failStep === 'config' ? 'failed' : 'done'
  if (probe === 'failed' || config === 'failed') {
    const step = probe === 'failed' ? 'probe' : 'config'
    const code = step === 'probe' ? 'AccessDenied' : 'juicefs config exited 1'
    if (!r.failLogged) {
      r.failLogged = true
      log({ actor: SYSTEM, action: 'key_rotation_failed', category: 'key', target: { kind: 'key', id: r.newKeyId, label: r.newKeyTail }, detail: { disk: disk.name, step, code } })
      save()
    }
    return {
      ...base, state: 'failed', probe, config, devices: { updated: 0, total: r.devicesTotal },
      error: { step, code, request: step === 'probe' ? `PUT /${disk.bucket}/floppy-probe-${r.id.slice(-4)}` : `juicefs config ${disk.volumeName}` },
    }
  }
  const configuredAt = PROBE_MS + CONFIG_MS
  const updated = Math.max(0, Math.min(r.devicesTotal, Math.floor((elapsed - configuredAt) / DEVICE_MS)))
  // The disk configured is enough: the devices are counted, not waited for.
  const configured = config === 'done'
  if (configured && !r.applied) {
    r.applied = true
    disk.keyTail = r.newKeyTail
    disk.keyId = r.newKeyId
    disk.rotatedAt = iso()
    log({ actor: SYSTEM, action: 'key_rotated', category: 'key', target: { kind: 'key', id: r.newKeyId, label: r.newKeyTail }, detail: { disk: disk.name } })
    save()
  }
  return { ...base, state: configured ? 'awaiting_old_key_deletion' : 'applying', probe, config, devices: { updated, total: r.devicesTotal }, error: null }
}

const VERSIONING_UNKNOWN: Versioning = { state: 'unknown', oldVersionsDays: null }

function diskKeyOf(d: DiskRow): DiskKey {
  const openedAt = d.exposures.map((x) => x.at).sort()[0]
  return {
    diskId: d.id,
    diskName: d.name,
    bucket: d.bucket,
    endpoint: d.endpoint,
    // The format holds the key once the disk is ready; while creating it is still pending.
    accessKeyTail: manual() && d.state === 'ready' ? d.keyTail : null,
    rotatedAt: d.rotatedAt,
    window: manual() && openedAt ? { openedAt, reasons: structuredClone(d.exposures) } : null,
    rotation: rotationOf(d),
    versioning: { ...d.versioning },
  }
}

function keyStatus(): KeyStatus {
  advance()
  return {
    storageMode: db.storageMode,
    storageProvider: db.storageProvider,
    controlPlaneKeyTail: db.controlPlaneKeyTail,
    disks: db.disks.map(diskKeyOf),
    keys: db.deviceKeys.map((k) => {
      const m = db.devices.find((x) => x.id === k.deviceId)
      const owner = db.people.find((p) => p.id === m?.ownerId)
      return {
        id: k.id, deviceId: k.deviceId, owner: `${m?.name ?? k.deviceId} (${owner ? labelOf(owner) : ''})`,
        accessKeyTail: k.tail, state: k.state, activatedAt: k.activatedAt, retiringAt: k.retiringAt,
      }
    }),
  }
}

function revokeDeviceRow(m: DeviceRow, by: PersonRow, reason: 'admin' | 'owner' | 'member_removed'): void {
  m.revokedAt = iso()
  m.revokedBy = labelOf(by)
  m.live = false
  m.replica = null
  for (const k of db.deviceKeys) {
    if (k.deviceId === m.id && k.state !== 'retiring') Object.assign(k, { state: 'retiring', retiringAt: iso(), deleteAt: iso(now() + DELETE_KEY_MS) })
  }
  log({ actor: actorOf(by), action: 'device_revoked', category: 'devices', target: { kind: 'device', id: m.id, label: m.name }, detail: { reason } })
}

function activeAdmins(): PersonRow[] {
  return db.people.filter((p) => p.role === 'admin' && p.status === 'active')
}

function filterAudit(q: AuditQuery): AuditEvent[] {
  const since = now() - q.sinceDays * 24 * 3_600_000
  const needle = q.q?.trim().toLowerCase()
  return db.audit.filter((e) =>
    Date.parse(e.at) >= since &&
    (!q.category || e.category === q.category) &&
    (!needle || e.target.label.toLowerCase().includes(needle) || (e.actor.label?.toLowerCase().includes(needle) ?? false)),
  )
}

const validEmail = (s: string) => /^[^@\s]+@[^@\s]+\.[^@\s.]+$/.test(s)
const validKey = (s: string) => /^\S{8,}$/.test(s)
/** JuiceFS's volume name pattern. */
const VOLUME_NAME = /^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$/

/**
 * JuiceFS's name for a new disk, derived once from its display name as the server does:
 * lowercased, every run of other characters a hyphen, then -2, -3... while taken. Names of
 * failed and deleted disks stay taken forever.
 */
function deriveVolumeName(name: string, taken: Set<string>): string {
  const base = name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 58).replace(/-+$/, '')
  if (VOLUME_NAME.test(base) && !taken.has(base)) return base
  for (let n = 2; ; n += 1) {
    const candidate = `${base || 'disk'}-${n}`
    if (VOLUME_NAME.test(candidate) && !taken.has(candidate)) return candidate
  }
}

/** A probe write with a pasted key: DENIED in the key id is refused like a real AccessDenied. */
function probeWith(bucket: string, accessKeyId: string): void {
  if (accessKeyId.toUpperCase().includes('DENIED')) {
    throw new ApiError(422, 'probe_failed', 'The test write failed', { code: 'AccessDenied', request: `PUT /${bucket}/floppy-probe-${uid('p').slice(-4)}` })
  }
}

const settingsOf = (): Settings => ({
  organizationName: db.organizationName,
  mailLanguage: db.mailLanguage,
  mail: { ...db.mail },
  storage: { ...db.storage },
  storageLocked: db.disks.length > 0,
  storageMode: db.storageMode,
  storageProvider: db.storageProvider,
})

function healthOf(d: DiskRow): Health['disks'][number] {
  return {
    id: d.id,
    name: d.name,
    metadata: { state: d.status === 'online' ? 'ok' : 'down', latencyMs: d.status === 'online' ? d.latencyMs + Math.round(Math.random() * 3) : null, checkedAt: iso(now() - 18_000) },
    storage: { state: d.storageState, lastProbeAt: d.lastProbeAt, versioning: { ...d.versioning } },
  }
}

/** The worst state and the highest latency; versioning only when every disk agrees (as the server). */
function summary(disks: Health['disks']): { metadata: MetadataHealth; storage: StorageHealth } {
  const latencies = disks.flatMap((d) => (d.metadata.latencyMs === null ? [] : [d.metadata.latencyMs]))
  const checked = disks.flatMap((d) => (d.metadata.checkedAt ? [d.metadata.checkedAt] : [])).sort()
  const probed = disks.flatMap((d) => (d.storage.lastProbeAt ? [d.storage.lastProbeAt] : [])).sort()
  const states = new Set(disks.map((d) => d.storage.state))
  const versionings = new Set(disks.map((d) => `${d.storage.versioning.state}|${d.storage.versioning.oldVersionsDays ?? ''}`))
  const first = disks[0]?.storage.versioning
  return {
    metadata: { state: disks.some((d) => d.metadata.state === 'down') ? 'down' : 'ok', latencyMs: latencies.length ? Math.max(...latencies) : null, checkedAt: checked[0] ?? null },
    storage: {
      state: states.has('failing') ? 'failing' : states.size === 1 && states.has('ok') ? 'ok' : 'unknown',
      lastProbeAt: probed[0] ?? null,
      versioning: versionings.size === 1 && first ? { ...first } : { ...VERSIONING_UNKNOWN },
    },
  }
}

/** The fake implementation of the API. */
export const fakeApi: Api = {
  auth: {
    async start(email) {
      await latency()
      const address = email.trim().toLowerCase()
      if (!validEmail(address)) throw new ApiError(422, 'validation', 'Invalid email')
      const person = db.people.find((p) => p.email === address && p.status !== 'removed')
      // An invited person whose invitation expired cannot sign in until a resend.
      const eligible = person && (person.status === 'active' || inviteLive(person)) ? person : null
      for (const r of db.signIns) if (r.email === address && !r.completed) r.superseded = true
      const row = {
        id: uid('sr'), email: address, userId: eligible?.id ?? null,
        code: String(Math.floor(100_000 + Math.random() * 900_000)), token: uid('tok'),
        expiresAt: iso(now() + LINK_TTL), resendAt: iso(now() + RESEND_AFTER), attempts: 0, completed: false, superseded: false,
      }
      db.signIns.push(row)
      save()
      // Unknown addresses get a mail that says so; the fake shows it like any other.
      write(OUTBOX_KEY, { kind: 'sign-in', to: address, code: row.code, token: row.token, at: iso() } satisfies FakeMail)
      notify()
      return { requestId: row.id, email: address, expiresAt: row.expiresAt, resendAt: row.resendAt }
    },
    async status(requestId) {
      await latency(120, 260)
      // Reads the state only: polling never signs anyone in.
      const row = db.signIns.find((r) => r.id === requestId)
      if (!row) throw new ApiError(404, 'not_found', 'No such request')
      if (row.completed) return { status: 'completed' }
      if (row.attempts >= MAX_ATTEMPTS) return { status: 'locked' }
      if (row.superseded || Date.parse(row.expiresAt) < now()) return { status: 'expired' }
      return { status: 'pending' }
    },
    async verifyCode(requestId, code) {
      await latency()
      const row = db.signIns.find((r) => r.id === requestId)
      if (!row || row.superseded || row.completed || Date.parse(row.expiresAt) < now()) throw new ApiError(410, 'request_expired', 'Expired')
      if (row.attempts >= MAX_ATTEMPTS) throw new ApiError(429, 'request_locked', 'Too many attempts')
      if (code.replace(/\D/g, '') !== row.code || !row.userId) {
        row.attempts += 1
        const left = MAX_ATTEMPTS - row.attempts
        const person = db.people.find((p) => p.id === row.userId)
        if (left <= 0 && person) {
          log({ actor: SYSTEM, action: 'sign_in_locked', category: 'access', target: { kind: 'person', id: person.id, label: labelOf(person) }, detail: {} })
        }
        save()
        if (left <= 0) throw new ApiError(429, 'request_locked', 'Too many attempts')
        throw new ApiError(422, 'invalid_code', 'Wrong code', { attemptsLeft: left })
      }
      row.completed = true
      return startSession(row.userId, 'code')
    },
    async openLink(token) {
      await latency()
      const row = db.signIns.find((r) => r.token === token)
      if (!row || !row.userId || row.completed || row.superseded || row.attempts >= MAX_ATTEMPTS || Date.parse(row.expiresAt) < now()) {
        throw new ApiError(410, 'link_invalid', 'Link expired or used')
      }
      row.completed = true
      return startSession(row.userId, 'link')
    },
    async acceptInvitation(token) {
      await latency()
      const person = db.people.find((p) => p.inviteToken === token && p.status === 'invited')
      if (!token || !person || !inviteLive(person)) throw new ApiError(410, 'link_invalid', 'Invitation expired or used')
      return startSession(person.id, 'invitation')
    },
    async session() {
      await latency(150, 350)
      const user = currentUser()
      return user ? sessionOf(user) : null
    },
    async signOut() {
      await latency(120, 250)
      write(SESSION_KEY, null)
    },
  },

  people: {
    async list() {
      await latency()
      const me = requireAdmin()
      const order = { active: 0, invited: 1, removed: 2 }
      return db.people.map((p) => toPerson(p, me)).sort((a, b) => order[a.status] - order[b.status])
    },
    async invite({ email, role, disks = [] }) {
      await latency()
      const me = requireAdmin()
      const address = email.trim().toLowerCase()
      if (!validEmail(address)) throw new ApiError(422, 'validation', 'Invalid email')
      if (db.people.some((p) => p.email === address && p.status !== 'removed')) throw new ApiError(409, 'already_member', 'Already in the organization')
      const targets = disks.map((entry) => {
        const disk = db.disks.find((d) => d.id === entry.diskId)
        if (!disk) throw new ApiError(422, 'validation', 'No such disk', { field: 'disks', diskId: entry.diskId })
        return { disk, access: entry.access }
      })
      // A removed person invited again gets a new membership, with no access; the old row keeps
      // its history.
      const person: PersonRow = {
        id: uid('u'), name: null, email: address, role, status: 'invited', lastSignInAt: null,
        invitedAt: iso(), invitedBy: labelOf(me), inviteExpiresAt: null, removedAt: null, inviteToken: null,
      }
      db.people.push(person)
      for (const { disk, access } of targets) putAccess(disk, person, access, me)
      sendInvite(person)
      log({ actor: actorOf(me), action: 'person_invited', category: 'people', target: { kind: 'person', id: person.id, label: address }, detail: { role } })
      save()
      notify()
      return toPerson(person, me)
    },
    async update(id, { role }) {
      await latency()
      const me = requireAdmin()
      const person = db.people.find((p) => p.id === id && p.status !== 'removed')
      if (!person) throw new ApiError(404, 'not_found', 'No such person')
      if (person.role === 'admin' && role === 'member' && person.status === 'active' && activeAdmins().length === 1) {
        throw new ApiError(409, 'last_admin', 'The organization needs an admin')
      }
      const from = person.role
      if (from !== role) {
        person.role = role
        log({ actor: actorOf(me), action: 'role_changed', category: 'people', target: { kind: 'person', id, label: labelOf(person) }, detail: { from, to: role } })
      }
      save()
      return toPerson(person, me)
    },
    async remove(id) {
      await latency(400, 800)
      const me = requireAdmin()
      const person = db.people.find((p) => p.id === id && p.status !== 'removed')
      if (!person) throw new ApiError(404, 'not_found', 'No such person')
      if (person.id === me.id) throw new ApiError(409, 'cannot_remove_self', 'Cannot remove yourself')
      if (person.role === 'admin' && person.status === 'active' && activeAdmins().length === 1) {
        throw new ApiError(409, 'last_admin', 'The organization needs an admin')
      }
      // Read before the revocation: the disks their devices had a user on, whose key they could read.
      if (manual()) for (const disk of disksSeenBy(id)) openExposure(disk, 'person_removed', labelOf(person))
      const devices = activeDevicesOf(id)
      devices.forEach((m) => revokeDeviceRow(m, me, 'member_removed'))
      person.status = 'removed'
      person.removedAt = iso()
      person.inviteExpiresAt = null
      person.inviteToken = null
      log({ actor: actorOf(me), action: 'person_removed', category: 'people', target: { kind: 'person', id, label: labelOf(person) }, detail: { devices: devices.length } })
      save()
      return { person: toPerson(person, me), revokedDevices: devices.length, key: keyStatus() }
    },
    async resendInvite(id) {
      await latency()
      const me = requireAdmin()
      const person = db.people.find((p) => p.id === id && p.status !== 'removed')
      if (!person) throw new ApiError(404, 'not_found', 'No such person')
      if (person.status !== 'invited') throw new ApiError(409, 'invalid_state', 'Only an invited person can get a new invitation')
      sendInvite(person)
      log({ actor: actorOf(me), action: 'invite_resent', category: 'people', target: { kind: 'person', id, label: person.email }, detail: { role: person.role } })
      save()
      notify()
      return toPerson(person, me)
    },
  },

  devices: {
    async list() {
      await latency()
      const me = requireUser()
      heartbeat()
      const rows = me.role === 'admin' ? db.devices : db.devices.filter((m) => m.ownerId === me.id)
      return rows.map(toDevice).sort((a, b) => Number(!!a.revokedAt) - Number(!!b.revokedAt) || Number(b.connected) - Number(a.connected))
    },
    async revoke(id) {
      await latency(400, 800)
      const me = requireUser()
      const m = db.devices.find((x) => x.id === id && !x.revokedAt)
      if (!m || (me.role !== 'admin' && m.ownerId !== me.id)) throw new ApiError(404, 'not_found', 'No such device')
      if (manual()) for (const disk of db.disks.filter((d) => accessOf(d.id, m.ownerId) !== 'none')) openExposure(disk, 'device_revoked', m.name)
      revokeDeviceRow(m, me, me.role === 'admin' && m.ownerId !== me.id ? 'admin' : 'owner')
      save()
      return { device: toDevice(m), key: me.role === 'admin' ? keyStatus() : null }
    },
  },

  disks: {
    async list() {
      await latency()
      requireAdmin()
      advance()
      return db.disks.map(toDisk)
    },
    async get(id) {
      await latency(150, 300)
      requireAdmin()
      advance()
      const disk = db.disks.find((d) => d.id === id) ?? db.failedDisks.find((d) => d.id === id)
      if (!disk) throw new ApiError(404, 'not_found', 'No such disk')
      return toDisk(disk)
    },
    async create({ name, trashDays, storage, access = [] }) {
      await latency(400, 700)
      const me = requireAdmin()
      advance()
      const clean = name.trim()
      if (!DISK_NAME.test(clean) || !Number.isInteger(trashDays) || trashDays < 0 || trashDays > 365) {
        throw new ApiError(422, 'validation', 'Invalid disk')
      }
      if (!manual() && storage) throw new ApiError(422, 'validation', 'The provider creates each disk’s bucket: send no storage', { field: 'storage' })
      if (manual() && !storage) throw new ApiError(422, 'validation', 'Each disk has its own bucket and key: paste them', { field: 'storage' })
      if (storage && (!BUCKET_NAME.test(storage.bucket) || !validKey(storage.accessKeyId) || !validKey(storage.secretKey))) {
        throw new ApiError(422, 'validation', 'Invalid request')
      }
      const endpoint = storage?.endpoint?.trim() || db.storage.endpoint
      const region = storage?.region?.trim() || db.storage.region
      // The pasted key is probed before anything is recorded.
      if (storage) probeWith(storage.bucket, storage.accessKeyId)
      // macOS compares folder names case-insensitively: "Progetti" and "progetti" collide.
      if (db.disks.some((d) => d.name.toLowerCase() === clean.toLowerCase())) throw new ApiError(409, 'disk_name_taken', 'Name already in use')
      const id = uid('d')
      const bucket = storage?.bucket ?? managedBucketOf(id)
      if (db.disks.some((d) => d.endpoint === endpoint && d.bucket === bucket)) throw new ApiError(409, 'bucket_taken', 'Another disk uses this bucket', { field: 'bucket' })
      if (storage && db.disks.some((d) => d.keyId === storage.accessKeyId.trim())) throw new ApiError(422, 'validation', 'This key already serves another disk', { field: 'accessKeyId' })
      const people = access.map((entry) => {
        const person = db.people.find((p) => p.id === entry.personId && p.status !== 'removed')
        if (!person) throw new ApiError(422, 'validation', 'No such person', { field: 'access', personId: entry.personId })
        return { person, access: entry.access }
      })
      if (!people.some((e) => e.person.id === me.id)) people.push({ person: me, access: 'write' })
      const all = [...db.disks, ...db.failedDisks]
      const volumeName = deriveVolumeName(clean, new Set(all.map((d) => d.volumeName)))
      const keyId = storage ? storage.accessKeyId.trim() : null
      const disk: DiskRow = {
        id, name: clean, volumeName, mountPath: mountPathOf(clean), state: 'creating', error: null, status: 'online',
        bucket, region, endpoint, metaUrl: metaUrlOf(volumeName), blockSizeKb: 1024, trashDays, trashBytes: 0, usedBytes: 0, fileCount: 0,
        lastWriteAt: null, mountedBy: 0, cache: { ...DEFAULT_CACHE }, createdAt: iso(), createdBy: labelOf(me),
        formatDoneAt: iso(now() + FORMAT_MS), formatFails: /fail/i.test(clean),
        keyId, keyTail: keyId ? tailOf(keyId) : null, rotatedAt: null, exposures: [], rotation: null,
        versioning: { ...VERSIONING_UNKNOWN }, storageState: 'unknown', lastProbeAt: null, latencyMs: 3,
      }
      db.disks.push(disk)
      log({ actor: actorOf(me), action: 'disk_created', category: 'disks', target: { kind: 'disk', id, label: clean }, detail: { blockSize: '1 MB' } })
      if (keyId) log({ actor: actorOf(me), action: 'key_created', category: 'key', target: { kind: 'key', id: uid('k'), label: tailOf(keyId) }, detail: { holder: 'disk', accessKeyTail: tailOf(keyId), for: clean } })
      for (const e of people) putAccess(disk, e.person, e.access, me)
      save()
      return toDisk(disk)
    },
    async update(id, body) {
      await latency()
      const me = requireAdmin()
      const disk = db.disks.find((d) => d.id === id)
      if (!disk) throw new ApiError(404, 'not_found', 'No such disk')
      if (disk.state !== 'ready') throw new ApiError(409, 'invalid_state', 'The disk is not ready')
      const detail: Record<string, number[]> = {}
      if (body.trashDays !== undefined && body.trashDays !== disk.trashDays) {
        detail.trashDays = [disk.trashDays, body.trashDays]
        disk.trashDays = body.trashDays
      }
      if (body.cache && body.cache.expireDays !== disk.cache.expireDays) {
        detail.cacheExpireDays = [disk.cache.expireDays, body.cache.expireDays]
        disk.cache = { expireDays: body.cache.expireDays }
      }
      if (Object.keys(detail).length > 0) log({ actor: actorOf(me), action: 'disk_updated', category: 'disks', target: { kind: 'disk', id, label: disk.name }, detail })
      save()
      return toDisk(disk)
    },
    async probe(id) {
      await latency(700, 1300)
      requireAdmin()
      const disk = db.disks.find((d) => d.id === id)
      if (!disk) throw new ApiError(404, 'not_found', 'No such disk')
      if (disk.state !== 'ready' && !disk.keyId) throw new ApiError(409, 'invalid_state', 'The disk has no key to probe with')
      disk.lastProbeAt = iso()
      disk.storageState = 'ok'
      save()
    },
    async remove(id) {
      await latency(300, 600)
      const me = requireAdmin()
      const disk = db.disks.find((d) => d.id === id)
      if (!disk) throw new ApiError(404, 'not_found', 'No such disk')
      if (disk.state === 'creating') throw new ApiError(409, 'invalid_state', 'The disk is still being created')
      // Soft deleted, like a failed one: its name is free again, its prefix and bucket are not.
      db.disks = db.disks.filter((d) => d.id !== id)
      db.failedDisks.push(disk)
      log({ actor: actorOf(me), action: 'disk_deleted', category: 'disks', target: { kind: 'disk', id, label: disk.name }, detail: { bucket: disk.bucket } })
      save()
    },
  },

  access: {
    async list() {
      await latency()
      requireAdmin()
      advance()
      const people = db.people.filter((p) => p.status !== 'removed')
      return db.disks.flatMap((d) =>
        people.map((p): DiskAccess => {
          const row = accessRow(d.id, p.id)
          return { diskId: d.id, personId: p.id, access: row?.access ?? 'none', applied: applied(row) }
        }),
      )
    },
    async set(diskId, personId, access) {
      await latency(250, 500)
      const me = requireAdmin()
      advance()
      const disk = db.disks.find((d) => d.id === diskId)
      if (!disk) throw new ApiError(404, 'not_found', 'No such disk')
      const person = db.people.find((p) => p.id === personId && p.status !== 'removed')
      if (!person) throw new ApiError(404, 'not_found', 'No such person')
      // Read before the change: a device that had a user on the instance could read the key.
      if (access === 'none' && manual() && disksSeenBy(person.id).some((d) => d.id === disk.id)) openExposure(disk, 'access_removed', labelOf(person))
      putAccess(disk, person, access, me)
      save()
      return { diskId, personId, access, applied: applied(accessRow(diskId, personId)) }
    },
    async mine() {
      await latency()
      const me = requireUser()
      advance()
      return db.disks.flatMap((d): MyDisk[] => {
        const access = accessOf(d.id, me.id)
        return d.state === 'ready' && access !== 'none' ? [{ id: d.id, name: d.name, access }] : []
      })
    },
  },

  key: {
    async get() {
      await latency()
      requireAdmin()
      return keyStatus()
    },
    async startRotation({ diskId, accessKeyId, secretKey }) {
      await latency()
      const me = requireAdmin()
      if (!manual()) throw new ApiError(409, 'invalid_state', 'Managed storage gives each device its own key: nothing to rotate')
      if (!validKey(accessKeyId) || !validKey(secretKey)) throw new ApiError(422, 'validation', 'Invalid request')
      const disk = db.disks.find((d) => d.id === diskId)
      if (!disk) throw new ApiError(404, 'not_found', 'No such disk')
      if (disk.state !== 'ready') throw new ApiError(409, 'invalid_state', 'The disk is not ready')
      const current = rotationOf(disk)
      if (current?.state === 'applying') throw new ApiError(409, 'rotation_in_progress', 'A rotation is already running')
      const keyId = accessKeyId.trim()
      if (db.disks.some((d) => d.keyId === keyId)) throw new ApiError(422, 'validation', 'This key is already in use', { field: 'accessKeyId' })
      heartbeat()
      // A rotation awaiting the old key's deletion is superseded: its keys stay to delete.
      const oldKeyTails =
        current?.state === 'awaiting_old_key_deletion' ? [...current.oldKeyTails, current.newKeyTail]
        : current?.state === 'failed' ? current.oldKeyTails
        : [disk.keyTail ?? '']
      const newKeyTail = tailOf(keyId)
      const upper = keyId.toUpperCase()
      const seeing = db.devices.filter((m) => isConnected(m) && accessOf(disk.id, m.ownerId) !== 'none')
      disk.rotation = {
        id: uid('rot'), newKeyId: keyId, newKeyTail, oldKeyTails, startedAt: iso(), devicesTotal: seeing.length,
        failStep: upper.includes('DENIED') ? 'probe' : upper.includes('BADCONFIG') ? 'config' : null, applied: false, failLogged: false,
      }
      log({ actor: actorOf(me), action: 'key_rotation_started', category: 'key', target: { kind: 'key', id: disk.rotation.id, label: newKeyTail }, detail: { disk: disk.name, newKeyTail, oldKeyTail: oldKeyTails.join(', ') } })
      save()
      const rotation = rotationOf(disk)
      if (!rotation) throw new ApiError(500, 'server', 'Rotation lost')
      return rotation
    },
    async getRotation(id) {
      await latency(120, 260)
      requireAdmin()
      const disk = db.disks.find((d) => d.rotation?.id === id)
      const rotation = disk ? rotationOf(disk) : null
      if (!rotation) throw new ApiError(404, 'not_found', 'No such rotation')
      return rotation
    },
    async close(id) {
      await latency()
      const me = requireAdmin()
      const disk = db.disks.find((d) => d.rotation?.id === id)
      const rotation = disk ? rotationOf(disk) : null
      if (!disk || !rotation) throw new ApiError(404, 'not_found', 'No such rotation')
      if (rotation.state !== 'awaiting_old_key_deletion') throw new ApiError(409, 'invalid_state', 'Nothing to close')
      // Only what happened before the rotation started: a device revoked during it may have
      // read the new key from its replica.
      const closed = disk.exposures.filter((x) => x.at < rotation.startedAt)
      disk.exposures = disk.exposures.filter((x) => x.at >= rotation.startedAt)
      disk.rotation = null
      const tail = rotation.oldKeyTails.join(', ')
      log({ actor: actorOf(me), action: 'key_window_closed', category: 'key', target: { kind: 'key', id, label: tail }, detail: { disk: disk.name, oldKeyTail: tail, reasons: closed.length } })
      save()
      return keyStatus()
    },
  },

  audit: {
    async list(q) {
      await latency(300, 700)
      requireAdmin()
      advance()
      const all = filterAudit(q)
      const start = q.cursor ? Number(q.cursor) : 0
      const limit = q.limit ?? 20
      const items = all.slice(start, start + limit)
      return { items, nextCursor: start + limit < all.length ? String(start + limit) : null }
    },
  },

  health: {
    async get() {
      await latency()
      requireAdmin()
      advance()
      const disks = db.disks.filter((d) => d.state === 'ready').map(healthOf)
      return { ...summary(disks), mail: { state: db.mail.host ? 'ok' : 'unconfigured', lastSentAt: db.lastMailAt }, disks }
    },
  },

  settings: {
    async get() {
      await latency()
      requireAdmin()
      return settingsOf()
    },
    async update(body) {
      await latency(400, 800)
      const me = requireAdmin()
      const fields: string[] = []
      if (body.organizationName !== undefined && body.organizationName.trim() !== db.organizationName) {
        if (!body.organizationName.trim()) throw new ApiError(422, 'validation', 'Invalid request')
        db.organizationName = body.organizationName.trim()
        fields.push('organizationName')
      }
      if (body.mailLanguage && body.mailLanguage !== db.mailLanguage) {
        db.mailLanguage = body.mailLanguage
        fields.push('mailLanguage')
      }
      if (body.mail) {
        const { password, ...mail } = body.mail
        if (mail.host && (!mail.sender || mail.port < 1 || mail.port > 65535)) throw new ApiError(422, 'validation', 'A mail server needs a port and a sender', { field: 'mail' })
        for (const k of ['host', 'port', 'username', 'sender'] as const) if (mail[k] !== db.mail[k]) fields.push(`mail.${k}`)
        if (password) fields.push('mail.password')
        db.mail = { ...mail, passwordSet: db.mail.passwordSet || !!password }
      }
      if (body.storage) {
        const next = { endpoint: body.storage.endpoint.trim(), region: body.storage.region.trim() }
        if (!next.endpoint) throw new ApiError(422, 'validation', 'Invalid request')
        if (next.endpoint !== db.storage.endpoint || next.region !== db.storage.region) {
          // The defaults are frozen once a disk exists.
          if (db.disks.length > 0) throw new ApiError(409, 'storage_in_use', 'Storage cannot change while disks exist', { disks: db.disks.length })
          db.storage = next
          fields.push('storage')
        }
      }
      if (fields.length > 0) log({ actor: actorOf(me), action: 'settings_updated', category: 'settings', target: { kind: 'organization', id: db.organizationId, label: db.organizationName }, detail: { fields } })
      save()
      return settingsOf()
    },
    async testMail() {
      await latency(600, 1100)
      const me = requireAdmin()
      db.lastMailAt = iso()
      save()
      return { sentTo: me.email }
    },
    async probeStorage({ bucket, accessKeyId, secretKey }) {
      await latency(700, 1300)
      requireAdmin()
      if (!manual()) throw new ApiError(409, 'invalid_state', 'The provider creates each disk’s bucket: there is nothing to probe')
      if (!bucket.trim() || !validKey(accessKeyId) || !validKey(secretKey)) throw new ApiError(422, 'validation', 'Invalid request')
      probeWith(bucket.trim(), accessKeyId)
    },
  },
}

/** Controls of the fake, for the banner that marks it. */
export const fakeControl = {
  /** The last email the fake "sent". */
  outbox: (): FakeMail | null => read<FakeMail>(OUTBOX_KEY),
  isFailing: () => failing,
  setFailing(value: boolean) {
    failing = value
    write(FAILING_KEY, value)
    notify()
  },
  /** Back to a fresh organization; signs out. */
  reset(scenario: Scenario = 'default') {
    db = seed(scenario)
    failing = false
    write(FAILING_KEY, false)
    write(SESSION_KEY, null)
    write(OUTBOX_KEY, null)
    save()
    notify()
  },
  subscribe(fn: () => void): () => void {
    listeners.add(fn)
    return () => listeners.delete(fn)
  },
}
