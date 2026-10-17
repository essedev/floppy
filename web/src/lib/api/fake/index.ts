/*
  FAKE API. An in-memory stand-in for the control plane, used while the backend does not
  exist (VITE_API=fake, the default of `pnpm dev`). It implements the same `Api` interface as
  http.ts with realistic latency, seeded data (seed.ts) and the rules of docs/DATABASE_SCHEMA.md
  ("State machines"): members see only their own Macs; only admins invite, and an invitation
  lasts 7 days; removing a person revokes their Macs and opens the key window, as revoking a Mac
  does, but only if a Mac ever had the key; the last admin cannot be demoted; a sign-in answers
  the same for unknown addresses and polling never signs in; a rotation awaits the old key's
  deletion once every disk is configured, and closing it closes only the exposures that happened
  before it started; storage settings are locked while a disk exists; at most 15 disks.

  State persists in localStorage so reloads keep it; `fakeControl` resets it, switches the
  scenario, makes every call fail like a network outage, and exposes the last email "sent".
  Deterministic triggers for error states:
    - an access key containing DENIED makes the rotation's probe write fail (AccessDenied);
    - a storage probe on a bucket other than the organization's fails (NoSuchBucket);
    - a new disk whose name contains "fail" fails its format;
    - ?fake=empty seeds a new organization, ?fake=fail starts with the network down,
      ?fake=default goes back to the full one.
*/

import { ApiError, type Api } from '../client'
import { DISK_NAME, type AuditEvent, type AuditQuery, type Disk, type KeyStatus, type Device, type Person, type Rotation, type Session, type StepState } from '../types'
import { DEFAULT_CACHE, INVITE_TTL, LATEST_APP, mountPathOf, seed, type DiskRow, type FakeDb, type DeviceRow, type PersonRow, type Scenario } from './seed'

const DB_KEY = 'floppy.fake.db.v3'
const SESSION_KEY = 'floppy.fake.session'
const FAILING_KEY = 'floppy.fake.failing'
const OUTBOX_KEY = 'floppy.fake.outbox'

const LINK_TTL = 15 * 60_000
const RESEND_AFTER = 60_000
const MAX_ATTEMPTS = 5
const CONNECTED_WITHIN = 2 * 60_000
const FORMAT_MS = 3_000
/** Valkey's 16 databases, the first left empty (DATABASE_SCHEMA.md, open question 10). */
const DISK_LIMIT = 15

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
  to: string
  code: string
  token: string
  at: string
}

let db: FakeDb = read<FakeDb>(DB_KEY) ?? seed('default')
let failing = read<boolean>(FAILING_KEY) ?? false
const listeners = new Set<() => void>()

function bootFromUrl(): void {
  const param = new URLSearchParams(location.search).get('fake')
  if (param === 'empty' || param === 'default') {
    db = seed(param)
    write(SESSION_KEY, null)
  }
  if (param === 'fail') failing = true
  if (param === 'default' || param === 'empty') failing = false
  write(FAILING_KEY, failing)
}
bootFromUrl()

const save = () => write(DB_KEY, db)
const notify = () => listeners.forEach((fn) => fn())
const now = () => Date.now()
const iso = (t = now()) => new Date(t).toISOString()
const uid = (prefix: string) => `${prefix}-${Math.random().toString(36).slice(2, 10)}`
const SYSTEM = { kind: 'system' as const, id: null, label: null }

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
const actorOf = (p: PersonRow) => ({ kind: 'person' as const, id: p.id, label: p.name ?? p.email })
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

function startSession(userId: string, via: 'link' | 'code'): Session {
  write(SESSION_KEY, userId)
  const person = db.people.find((p) => p.id === userId)
  if (!person) throw new ApiError(401, 'unauthorized', 'Unknown user')
  if (person.status === 'invited') {
    // First sign-in accepts the invitation; the name comes from the address.
    person.status = 'active'
    person.inviteExpiresAt = null
    const local = person.email.split('@')[0] ?? person.email
    person.name = local.charAt(0).toUpperCase() + local.slice(1)
    log({ actor: actorOf(person), action: 'invite_accepted', category: 'people', target: { kind: 'person', id: person.id, label: person.name }, detail: {} })
  }
  person.lastSignInAt = iso()
  log({ actor: actorOf(person), action: 'signed_in', category: 'access', target: { kind: 'person', id: person.id, label: person.name ?? person.email }, detail: { via, kind: 'web' } })
  save()
  return sessionOf(person)
}

// Rows to API shapes

function heartbeat(): void {
  // Live Macs keep reporting while the page is open, as the app's heartbeat would.
  for (const m of db.devices) if (m.live && !m.revokedAt) m.lastSeenAt = iso(now() - 5_000 - Math.random() * 20_000)
}

function toDevice(m: DeviceRow): Device {
  const owner = db.people.find((p) => p.id === m.ownerId)
  const connected = !m.revokedAt && m.lastSeenAt !== null && now() - Date.parse(m.lastSeenAt) < CONNECTED_WITHIN
  return {
    id: m.id,
    name: m.name,
    owner: { id: m.ownerId, name: owner?.name ?? owner?.email ?? m.ownerId },
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
  return {
    ...p,
    deviceCount: db.devices.filter((m) => m.ownerId === p.id && !m.revokedAt).length,
    isMe: p.id === me.id,
  }
}

function toDisk(d: DiskRow): Disk {
  const { formatDoneAt: _done, formatFails: _fails, volumeName: _volume, ...disk } = d
  return structuredClone(disk)
}

function log(event: Omit<AuditEvent, 'id' | 'at'>): void {
  db.audit.unshift({ ...event, id: uid('e'), at: iso() })
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
    } else {
      d.state = 'ready'
    }
  }
  if (changed) save()
}

// The key and its rotation

const PROBE_MS = 1200
const DISK_MS = 900
const MAC_MS = 700

function rotationOf(): Rotation | null {
  const r = db.key.rotation
  if (!r) return null
  const elapsed = now() - Date.parse(r.startedAt)
  const disksAtStart = db.disks.filter((d) => d.state === 'ready')
  const probe: StepState = elapsed < PROBE_MS ? 'running' : r.failCode ? 'failed' : 'done'
  if (probe === 'failed') {
    if (!r.failLogged) {
      r.failLogged = true
      log({ actor: SYSTEM, action: 'key_rotation_failed', category: 'key', target: { kind: 'key', id: r.id, label: r.newKeyTail }, detail: { step: 'probe', code: r.failCode ?? 'AccessDenied' } })
      save()
    }
    return {
      id: r.id, state: 'failed', newKeyTail: r.newKeyTail, oldKeyTails: r.oldKeyTails, probe,
      disks: disksAtStart.map((d) => ({ id: d.id, name: d.name, state: 'pending' })),
      devices: { updated: 0, total: r.devicesTotal },
      error: { step: 'probe', code: r.failCode ?? 'AccessDenied', request: `PUT /${db.storage.bucket}/floppy-probe-${r.id.slice(-4)}` },
      startedAt: r.startedAt,
    }
  }
  const disks = disksAtStart.map((d, i) => {
    const start = PROBE_MS + i * DISK_MS
    const state: StepState = elapsed < start ? 'pending' : elapsed < start + DISK_MS ? 'running' : 'done'
    return { id: d.id, name: d.name, state }
  })
  const macsStart = PROBE_MS + disks.length * DISK_MS
  const updated = Math.max(0, Math.min(r.devicesTotal, Math.floor((elapsed - macsStart) / MAC_MS)))
  // Every disk configured is enough: the Macs are counted, not waited for.
  const configured = disks.every((d) => d.state === 'done') && elapsed >= macsStart
  if (configured && !r.applied) {
    r.applied = true
    db.key.accessKeyTail = r.newKeyTail
    db.key.rotatedAt = iso()
    log({ actor: SYSTEM, action: 'key_rotated', category: 'key', target: { kind: 'key', id: r.id, label: r.newKeyTail }, detail: { disks: disks.length } })
    save()
  }
  return {
    id: r.id, state: configured ? 'awaiting_old_key_deletion' : 'applying', newKeyTail: r.newKeyTail, oldKeyTails: r.oldKeyTails,
    probe, disks, devices: { updated, total: r.devicesTotal }, error: null, startedAt: r.startedAt,
  }
}

function keyStatus(): KeyStatus {
  const rotation = rotationOf()
  const exposures = db.key.exposures
  const openedAt = exposures.map((x) => x.at).sort()[0]
  return {
    bucket: db.storage.bucket,
    accessKeyTail: db.key.accessKeyTail,
    rotatedAt: db.key.rotatedAt,
    window: openedAt ? { openedAt, reasons: structuredClone(exposures) } : null,
    rotation,
  }
}

function openExposure(kind: 'person_removed' | 'mac_revoked', label: string): void {
  db.key.exposures.push({ id: uid('x'), kind, label, at: iso() })
}

function revokeDeviceRow(m: DeviceRow, by: PersonRow, reason: 'admin' | 'owner' | 'member_removed'): void {
  m.revokedAt = iso()
  m.revokedBy = by.name ?? by.email
  m.live = false
  m.replica = null
  log({ actor: actorOf(by), action: 'mac_revoked', category: 'macs', target: { kind: 'mac', id: m.id, label: m.name }, detail: { reason } })
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

const validEmail = (s: string) => /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(s)
/** JuiceFS's volume name pattern, which is also the object prefix. */
const VOLUME_NAME = /^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$/

/**
 * JuiceFS's name for a new disk, derived once from its display name (DATABASE_SCHEMA.md,
 * volumes.volume_name): lowercased, every other character a hyphen. The schema asks for "a
 * short suffix" when the result is taken without fixing its form: the fake appends -2, -3...
 * Names of failed and deleted disks stay taken forever, their objects are still there.
 */
function deriveVolumeName(name: string, taken: Set<string>): string {
  const base = name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 58).replace(/-+$/, '')
  if (VOLUME_NAME.test(base) && !taken.has(base)) return base
  for (let n = 2; ; n += 1) {
    const candidate = `${base || 'disk'}-${n}`
    if (VOLUME_NAME.test(candidate) && !taken.has(candidate)) return candidate
  }
}

const settingsOf = () => ({
  organizationName: db.organizationName,
  mail: { ...db.mail },
  storage: { ...db.storage },
  storageLocked: db.disks.length > 0,
})

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
      write(OUTBOX_KEY, { to: address, code: row.code, token: row.token, at: iso() } satisfies FakeMail)
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
          log({ actor: SYSTEM, action: 'sign_in_locked', category: 'access', target: { kind: 'person', id: person.id, label: person.name ?? person.email }, detail: {} })
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
    async invite({ email, role }) {
      await latency()
      const me = requireAdmin()
      const address = email.trim().toLowerCase()
      if (!validEmail(address)) throw new ApiError(422, 'validation', 'Invalid email')
      let person = db.people.find((p) => p.email === address && p.status !== 'removed')
      if (person) throw new ApiError(409, 'already_member', 'Already in the organization')
      // A removed person invited again gets a new membership; the old row keeps its history.
      person = {
        id: uid('u'), name: null, email: address, role, status: 'invited', lastSignInAt: null,
        invitedAt: iso(), invitedBy: me.name, inviteExpiresAt: iso(now() + INVITE_TTL), removedAt: null,
      }
      db.people.push(person)
      log({ actor: actorOf(me), action: 'person_invited', category: 'people', target: { kind: 'person', id: person.id, label: address }, detail: { role } })
      db.lastMailAt = iso()
      save()
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
      person.role = role
      log({ actor: actorOf(me), action: 'role_changed', category: 'people', target: { kind: 'person', id, label: person.name ?? person.email }, detail: { from, to: role } })
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
      const macs = db.devices.filter((m) => m.ownerId === id && !m.revokedAt)
      macs.forEach((m) => revokeDeviceRow(m, me, 'member_removed'))
      person.status = 'removed'
      person.removedAt = iso()
      person.inviteExpiresAt = null
      // Only a Mac ever held the key (it lives in the metadata): no Mac, no window. One
      // exposure for the person, not one per Mac.
      if (macs.length > 0) openExposure('person_removed', person.name ?? person.email)
      log({ actor: actorOf(me), action: 'person_removed', category: 'people', target: { kind: 'person', id, label: person.name ?? person.email }, detail: { macs: macs.length } })
      save()
      return { person: toPerson(person, me), revokedDevices: macs.length, key: keyStatus() }
    },
    async resendInvite(id) {
      await latency()
      const me = requireAdmin()
      const person = db.people.find((p) => p.id === id && p.status === 'invited')
      if (!person) throw new ApiError(404, 'not_found', 'No such invite')
      person.invitedAt = iso()
      person.inviteExpiresAt = iso(now() + INVITE_TTL)
      log({ actor: actorOf(me), action: 'invite_resent', category: 'people', target: { kind: 'person', id, label: person.email }, detail: { role: person.role } })
      db.lastMailAt = iso()
      save()
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
      revokeDeviceRow(m, me, m.ownerId === me.id && me.role !== 'admin' ? 'owner' : 'admin')
      openExposure('mac_revoked', m.name)
      save()
      return { device: toDevice(m), key: keyStatus() }
    },
  },

  disks: {
    async list() {
      await latency()
      requireAdmin()
      advanceDisks()
      return db.disks.map(toDisk)
    },
    async get(id) {
      await latency(150, 300)
      requireAdmin()
      advanceDisks()
      const disk = db.disks.find((d) => d.id === id) ?? db.failedDisks.find((d) => d.id === id)
      if (!disk) throw new ApiError(404, 'not_found', 'No such disk')
      return toDisk(disk)
    },
    async create({ name, trashDays }) {
      await latency(400, 700)
      const me = requireAdmin()
      advanceDisks()
      const clean = name.trim()
      if (!DISK_NAME.test(clean) || !Number.isInteger(trashDays) || trashDays < 0 || trashDays > 365) {
        throw new ApiError(422, 'validation', 'Invalid disk')
      }
      if (db.disks.length >= DISK_LIMIT) throw new ApiError(409, 'disk_limit_reached', 'At most 15 disks')
      // Names are unique among live disks; the name in the bucket and the database number are
      // burned forever, failed and deleted disks included.
      const all = [...db.disks, ...db.failedDisks]
      // macOS compares folder names case-insensitively: "Progetti" and "progetti" collide.
      if (db.disks.some((d) => d.name.toLowerCase() === clean.toLowerCase())) {
        throw new ApiError(409, 'disk_name_taken', 'Name taken')
      }
      const volumeName = deriveVolumeName(clean, new Set(all.map((d) => d.volumeName)))
      const disk: DiskRow = {
        id: uid('d'), name: clean, volumeName, mountPath: mountPathOf(clean), state: 'creating', error: null, status: 'online',
        bucket: db.storage.bucket, region: db.storage.region, endpoint: db.storage.endpoint,
        metaDb: Math.max(0, ...all.map((d) => d.metaDb)) + 1, blockSizeKb: 1024, trashDays, trashBytes: 0, usedBytes: 0, fileCount: 0,
        lastWriteAt: null, mountedBy: 0, cache: { ...DEFAULT_CACHE }, createdAt: iso(), createdBy: me.name ?? me.email,
        formatDoneAt: iso(now() + FORMAT_MS), formatFails: /fail/i.test(clean),
      }
      db.disks.push(disk)
      log({ actor: actorOf(me), action: 'disk_created', category: 'disks', target: { kind: 'disk', id: disk.id, label: disk.name }, detail: { blockSize: '1 MB' } })
      save()
      return toDisk(disk)
    },
    async update(id, body) {
      await latency()
      const me = requireAdmin()
      const disk = db.disks.find((d) => d.id === id && d.state === 'ready')
      if (!disk) throw new ApiError(404, 'not_found', 'No such disk')
      const fields: string[] = []
      if (body.trashDays !== undefined && body.trashDays !== disk.trashDays) {
        disk.trashDays = body.trashDays
        fields.push('trashDays')
      }
      if (body.cache && body.cache.expireDays !== disk.cache.expireDays) {
        disk.cache = { expireDays: body.cache.expireDays }
        fields.push('cacheExpireDays')
      }
      log({ actor: actorOf(me), action: 'disk_updated', category: 'disks', target: { kind: 'disk', id, label: disk.name }, detail: { fields } })
      save()
      return toDisk(disk)
    },
  },

  key: {
    async get() {
      await latency()
      requireAdmin()
      return keyStatus()
    },
    async startRotation({ accessKeyId, secretKey }) {
      await latency()
      const me = requireAdmin()
      if (accessKeyId.trim().length < 8 || secretKey.trim().length < 8) throw new ApiError(422, 'validation', 'Key too short')
      const current = rotationOf()
      if (current?.state === 'applying') throw new ApiError(409, 'rotation_in_progress', 'A rotation is running')
      heartbeat()
      // A rotation awaiting the old key's deletion is superseded: its keys stay to delete.
      const oldKeyTails =
        current?.state === 'awaiting_old_key_deletion' ? [...current.oldKeyTails, current.newKeyTail]
        : current?.state === 'failed' ? current.oldKeyTails
        : [db.key.accessKeyTail]
      const newKeyTail = accessKeyId.trim().slice(-5).toUpperCase()
      db.key.rotation = {
        id: uid('rot'), newKeyTail, oldKeyTails, startedAt: iso(),
        devicesTotal: db.devices.map(toDevice).filter((m) => m.connected).length,
        failCode: accessKeyId.toUpperCase().includes('DENIED') ? 'AccessDenied' : null, applied: false, failLogged: false,
      }
      log({ actor: actorOf(me), action: 'key_rotation_started', category: 'key', target: { kind: 'key', id: db.key.rotation.id, label: newKeyTail }, detail: { newKeyTail, oldKeyTail: oldKeyTails.join(', ') } })
      save()
      const rotation = rotationOf()
      if (!rotation) throw new ApiError(500, 'server', 'Rotation lost')
      return rotation
    },
    async getRotation(id) {
      await latency(120, 260)
      requireAdmin()
      const rotation = rotationOf()
      if (!rotation || rotation.id !== id) throw new ApiError(404, 'not_found', 'No such rotation')
      return rotation
    },
    async close(id) {
      await latency()
      const me = requireAdmin()
      const rotation = rotationOf()
      if (!rotation || rotation.id !== id || rotation.state !== 'awaiting_old_key_deletion') throw new ApiError(409, 'validation', 'Nothing to close')
      // Only what happened before the rotation started: a Mac revoked during it may have read
      // the new key from its replica.
      const closed = db.key.exposures.filter((x) => x.at < rotation.startedAt)
      db.key.exposures = db.key.exposures.filter((x) => x.at >= rotation.startedAt)
      db.key.rotation = null
      log({ actor: actorOf(me), action: 'key_window_closed', category: 'key', target: { kind: 'key', id, label: rotation.newKeyTail }, detail: { oldKeyTail: rotation.oldKeyTails.join(', '), reasons: closed.length } })
      save()
      return keyStatus()
    },
  },

  audit: {
    async list(q) {
      await latency(300, 700)
      requireAdmin()
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
      return {
        metadata: { state: 'ok', latencyMs: 3 + Math.round(Math.random() * 3), checkedAt: iso(now() - 18_000) },
        storage: { state: 'ok', lastProbeAt: db.lastStorageProbeAt },
        mail: { state: db.mail.host ? 'ok' : 'unconfigured', lastSentAt: db.lastMailAt },
      }
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
      if (body.storage) {
        const changed = (['endpoint', 'region', 'bucket'] as const).some((k) => body.storage?.[k].trim() !== db.storage[k])
        // A disk's format holds the bucket: storage cannot move under a live disk.
        if (changed && db.disks.length > 0) throw new ApiError(409, 'storage_in_use', 'A disk exists')
        if (changed) {
          db.storage = { endpoint: body.storage.endpoint.trim(), region: body.storage.region.trim(), bucket: body.storage.bucket.trim() }
          fields.push('storage')
        }
      }
      if (body.organizationName !== undefined && body.organizationName.trim() !== db.organizationName) {
        if (!body.organizationName.trim()) throw new ApiError(422, 'validation', 'Name required')
        db.organizationName = body.organizationName.trim()
        fields.push('name')
      }
      if (body.mail) {
        const { password, ...mail } = body.mail
        const changed = (Object.keys(mail) as (keyof typeof mail)[]).some((k) => mail[k] !== db.mail[k]) || !!password
        db.mail = { ...mail, passwordSet: db.mail.passwordSet || !!password }
        if (changed) fields.push('smtp')
      }
      log({ actor: actorOf(me), action: 'settings_updated', category: 'settings', target: { kind: 'organization', id: db.organizationId, label: db.organizationName }, detail: { fields } })
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
    async probeStorage({ bucket, region }) {
      await latency(700, 1300)
      requireAdmin()
      if (bucket.trim() !== db.storage.bucket) {
        throw new ApiError(422, 'probe_failed', 'Probe failed', { code: 'NoSuchBucket', request: region.trim() || 'no region' })
      }
      db.lastStorageProbeAt = iso()
      save()
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
