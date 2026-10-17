/*
  FAKE API. An in-memory stand-in for the control plane, used while the backend does not
  exist (VITE_API=fake, the default of `pnpm dev`). It implements the same `Api` interface as
  http.ts with realistic latency, seeded data (seed.ts) and the rules the backend must keep:
  members see only their own Macs, only admins invite, removing a person revokes their Macs
  and opens the key window (revoking a Mac does too), the last admin cannot be demoted, a sign-in answers the same for unknown addresses.

  State persists in localStorage so reloads keep it; `fakeControl` resets it, switches the
  scenario, makes every call fail like a network outage, and exposes the last email "sent".
  Deterministic triggers for error states:
    - an access key containing DENIED makes the rotation's probe write fail (AccessDenied);
    - a storage probe on a bucket other than the organization's fails (NoSuchBucket);
    - ?fake=empty seeds a new organization, ?fake=fail starts with the network down,
      ?fake=default goes back to the full one.
*/

import { ApiError, type Api } from '../client'
import type { AuditEvent, AuditQuery, Disk, KeyStatus, Mac, Person, Rotation, Session, StepState } from '../types'
import { DEFAULT_CACHE, LATEST_APP, seed, type FakeDb, type MacRow, type PersonRow, type Scenario } from './seed'

const DB_KEY = 'floppy.fake.db.v2'
const SESSION_KEY = 'floppy.fake.session'
const FAILING_KEY = 'floppy.fake.failing'
const OUTBOX_KEY = 'floppy.fake.outbox'

const LINK_TTL = 15 * 60_000
const RESEND_AFTER = 60_000
const MAX_ATTEMPTS = 5
const CONNECTED_WITHIN = 2 * 60_000

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
const actorOf = (p: PersonRow) => ({ id: p.id, name: p.name ?? p.email })

function sessionOf(user: PersonRow): Session {
  return {
    user: { id: user.id, email: user.email, name: user.name ?? user.email },
    role: user.role,
    organization: {
      id: db.organizationId,
      name: db.organizationName,
      diskCount: db.disks.length,
      peopleCount: db.people.filter((p) => p.status !== 'removed').length,
      macCount: db.macs.filter((m) => !m.revokedAt).length,
    },
  }
}

function startSession(userId: string): Session {
  write(SESSION_KEY, userId)
  const person = db.people.find((p) => p.id === userId)
  if (!person) throw new ApiError(401, 'unauthorized', 'Unknown user')
  if (person.status === 'invited') {
    // First sign-in: the membership becomes active and the name comes from the address.
    person.status = 'active'
    const local = person.email.split('@')[0] ?? person.email
    person.name = local.charAt(0).toUpperCase() + local.slice(1)
  }
  person.lastSignInAt = iso()
  log({ actor: actorOf(person), action: 'signed_in', category: 'access', target: { kind: 'person', id: person.id, label: person.name ?? person.email }, detail: {} })
  save()
  return sessionOf(person)
}

// Rows to API shapes

function heartbeat(): void {
  // Live Macs keep reporting while the page is open, as the app's heartbeat would.
  for (const m of db.macs) if (m.live && !m.revokedAt) m.lastSeenAt = iso(now() - 5_000 - Math.random() * 20_000)
}

function toMac(m: MacRow): Mac {
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
    macCount: db.macs.filter((m) => m.ownerId === p.id && !m.revokedAt).length,
    isMe: p.id === me.id,
  }
}

function log(event: Omit<AuditEvent, 'id' | 'at'>): void {
  db.audit.unshift({ ...event, id: uid('e'), at: iso() })
}

// The key and its rotation

const PROBE_MS = 1200
const DISK_MS = 900
const MAC_MS = 600

function rotationOf(): Rotation | null {
  const r = db.key.rotation
  if (!r) return null
  const elapsed = now() - Date.parse(r.startedAt)
  const probe: StepState = elapsed < PROBE_MS ? 'running' : r.failCode ? 'failed' : 'done'
  if (probe === 'failed') {
    return {
      id: r.id, state: 'failed', newKeyTail: r.newKeyTail, oldKeyTail: r.oldKeyTail, probe,
      disks: db.disks.map((d) => ({ id: d.id, name: d.name, state: 'pending' })),
      macs: { updated: 0, total: r.macsTotal },
      error: { code: r.failCode ?? 'AccessDenied', request: `PUT /${db.storage.bucket}/floppy-probe-${r.id.slice(-4)}` },
      startedAt: r.startedAt,
    }
  }
  const disks = db.disks.map((d, i) => {
    const start = PROBE_MS + i * DISK_MS
    const state: StepState = elapsed < start ? 'pending' : elapsed < start + DISK_MS ? 'running' : 'done'
    return { id: d.id, name: d.name, state }
  })
  const macsStart = PROBE_MS + db.disks.length * DISK_MS
  const updated = Math.max(0, Math.min(r.macsTotal, Math.floor((elapsed - macsStart) / MAC_MS)))
  const finished = disks.every((d) => d.state === 'done') && updated >= r.macsTotal
  if (finished && !r.applied) {
    r.applied = true
    db.key.accessKeyTail = r.newKeyTail
    db.key.rotatedAt = iso()
    const user = currentUser()
    log({ actor: user ? actorOf(user) : null, action: 'key_rotated', category: 'key', target: { kind: 'key', id: 'key', label: r.newKeyTail }, detail: { disks: db.disks.length } })
    save()
  }
  return {
    id: r.id, state: finished ? 'awaiting_old_key_deletion' : 'applying', newKeyTail: r.newKeyTail, oldKeyTail: r.oldKeyTail,
    probe, disks, macs: { updated, total: r.macsTotal }, error: null, startedAt: r.startedAt,
  }
}

function keyStatus(): KeyStatus {
  const rotation = rotationOf()
  return { bucket: db.storage.bucket, accessKeyTail: db.key.accessKeyTail, rotatedAt: db.key.rotatedAt, window: db.key.window, rotation }
}

function openWindow(reason: { kind: 'person_removed' | 'mac_revoked'; label: string }): void {
  const at = iso()
  if (db.key.window) db.key.window.reasons.push({ ...reason, at })
  else db.key.window = { openedAt: at, reasons: [{ ...reason, at }] }
}

function revokeMacRow(m: MacRow, by: PersonRow): void {
  m.revokedAt = iso()
  m.revokedBy = by.name ?? by.email
  m.live = false
  m.replica = null
  log({ actor: actorOf(by), action: 'mac_revoked', category: 'macs', target: { kind: 'mac', id: m.id, label: m.name }, detail: {} })
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
    (!needle || e.target.label.toLowerCase().includes(needle) || (e.actor?.name.toLowerCase().includes(needle) ?? false)),
  )
}

const slugOk = (s: string) => /^[a-z0-9][a-z0-9-]*$/.test(s)

/** The fake implementation of the API. */
export const fakeApi: Api = {
  auth: {
    async start(email) {
      await latency()
      const address = email.trim().toLowerCase()
      if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(address)) throw new ApiError(422, 'validation', 'Invalid email')
      const person = db.people.find((p) => p.email === address && p.status !== 'removed')
      const row = {
        id: uid('sr'), email: address, userId: person?.id ?? null,
        code: String(Math.floor(100_000 + Math.random() * 900_000)), token: uid('tok'),
        expiresAt: iso(now() + LINK_TTL), resendAt: iso(now() + RESEND_AFTER), attempts: 0, completed: false,
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
      const row = db.signIns.find((r) => r.id === requestId)
      if (!row) throw new ApiError(404, 'not_found', 'No such request')
      if (row.completed) return { status: 'completed' }
      if (row.attempts >= MAX_ATTEMPTS) return { status: 'locked' }
      if (Date.parse(row.expiresAt) < now()) return { status: 'expired' }
      return { status: 'pending' }
    },
    async verifyCode(requestId, code) {
      await latency()
      const row = db.signIns.find((r) => r.id === requestId)
      if (!row || Date.parse(row.expiresAt) < now()) throw new ApiError(410, 'request_expired', 'Expired')
      if (row.attempts >= MAX_ATTEMPTS) throw new ApiError(429, 'request_locked', 'Too many attempts')
      if (row.completed) throw new ApiError(410, 'request_expired', 'Already used')
      if (code.replace(/\D/g, '') !== row.code || !row.userId) {
        row.attempts += 1
        save()
        const left = MAX_ATTEMPTS - row.attempts
        if (left <= 0) throw new ApiError(429, 'request_locked', 'Too many attempts')
        throw new ApiError(422, 'invalid_code', 'Wrong code', { attemptsLeft: left })
      }
      row.completed = true
      return startSession(row.userId)
    },
    async openLink(token) {
      await latency()
      const row = db.signIns.find((r) => r.token === token)
      if (!row || !row.userId || row.completed || row.attempts >= MAX_ATTEMPTS || Date.parse(row.expiresAt) < now()) {
        throw new ApiError(410, 'link_invalid', 'Link expired or used')
      }
      row.completed = true
      return startSession(row.userId)
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
      if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(address)) throw new ApiError(422, 'validation', 'Invalid email')
      let person = db.people.find((p) => p.email === address)
      if (person && person.status !== 'removed') throw new ApiError(409, 'already_member', 'Already in the organization')
      if (person) Object.assign(person, { status: 'invited', role, invitedAt: iso(), invitedBy: me.name, removedAt: null })
      else {
        person = { id: uid('u'), name: null, email: address, role, status: 'invited', lastSignInAt: null, invitedAt: iso(), invitedBy: me.name, removedAt: null }
        db.people.push(person)
      }
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
      person.role = role
      log({ actor: actorOf(me), action: 'role_changed', category: 'people', target: { kind: 'person', id, label: person.name ?? person.email }, detail: { role } })
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
      const macs = db.macs.filter((m) => m.ownerId === id && !m.revokedAt)
      macs.forEach((m) => revokeMacRow(m, me))
      person.status = 'removed'
      person.removedAt = iso()
      // Only a Mac ever held the key (it lives in the metadata): no Mac, no window.
      if (macs.length > 0) openWindow({ kind: 'person_removed', label: person.name ?? person.email })
      log({ actor: actorOf(me), action: 'person_removed', category: 'people', target: { kind: 'person', id, label: person.name ?? person.email }, detail: { macs: macs.length } })
      save()
      return { person: toPerson(person, me), revokedMacs: macs.length, key: keyStatus() }
    },
    async resendInvite(id) {
      await latency()
      const me = requireAdmin()
      const person = db.people.find((p) => p.id === id && p.status === 'invited')
      if (!person) throw new ApiError(404, 'not_found', 'No such invite')
      person.invitedAt = iso()
      log({ actor: actorOf(me), action: 'invite_resent', category: 'people', target: { kind: 'person', id, label: person.email }, detail: {} })
      db.lastMailAt = iso()
      save()
      return toPerson(person, me)
    },
  },

  macs: {
    async list() {
      await latency()
      const me = requireUser()
      heartbeat()
      const rows = me.role === 'admin' ? db.macs : db.macs.filter((m) => m.ownerId === me.id)
      return rows.map(toMac).sort((a, b) => Number(!!a.revokedAt) - Number(!!b.revokedAt) || Number(b.connected) - Number(a.connected))
    },
    async revoke(id) {
      await latency(400, 800)
      const me = requireUser()
      const m = db.macs.find((x) => x.id === id && !x.revokedAt)
      if (!m || (me.role !== 'admin' && m.ownerId !== me.id)) throw new ApiError(404, 'not_found', 'No such Mac')
      revokeMacRow(m, me)
      openWindow({ kind: 'mac_revoked', label: m.name })
      save()
      return { mac: toMac(m), key: keyStatus() }
    },
  },

  disks: {
    async list() {
      await latency()
      requireAdmin()
      return structuredClone(db.disks)
    },
    async create({ name, prefix, trashDays }) {
      await latency(500, 900)
      const me = requireAdmin()
      const clean = name.trim()
      if (!clean || !slugOk(prefix) || trashDays < 0) throw new ApiError(422, 'validation', 'Invalid disk')
      if (db.disks.some((d) => d.name.toLowerCase() === clean.toLowerCase() || d.prefix === prefix)) {
        throw new ApiError(409, 'disk_name_taken', 'Name or prefix taken')
      }
      const disk: Disk = {
        id: uid('d'), name: clean, status: 'online', bucket: db.storage.bucket, prefix, region: db.storage.region, endpoint: db.storage.endpoint,
        metaDb: Math.max(0, ...db.disks.map((d) => d.metaDb)) + 1, blockSizeKb: 1024, trashDays, trashBytes: 0, usedBytes: 0, fileCount: 0,
        lastWriteAt: null, mountedBy: 0, cache: { ...DEFAULT_CACHE }, createdAt: iso(), createdBy: me.name ?? me.email,
      }
      db.disks.push(disk)
      log({ actor: actorOf(me), action: 'disk_created', category: 'disks', target: { kind: 'disk', id: disk.id, label: disk.name }, detail: { blockSize: '1 MB' } })
      save()
      return structuredClone(disk)
    },
    async update(id, body) {
      await latency()
      const me = requireAdmin()
      const disk = db.disks.find((d) => d.id === id)
      if (!disk) throw new ApiError(404, 'not_found', 'No such disk')
      if (body.trashDays !== undefined) disk.trashDays = body.trashDays
      if (body.cache) disk.cache = { expireDays: body.cache.expireDays }
      log({ actor: actorOf(me), action: 'disk_updated', category: 'disks', target: { kind: 'disk', id, label: disk.name }, detail: {} })
      save()
      return structuredClone(disk)
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
      requireAdmin()
      if (accessKeyId.trim().length < 8 || secretKey.trim().length < 8) throw new ApiError(422, 'validation', 'Key too short')
      const current = rotationOf()
      if (current && current.state === 'applying') throw new ApiError(409, 'rotation_in_progress', 'A rotation is running')
      heartbeat()
      db.key.rotation = {
        id: uid('rot'), newKeyTail: accessKeyId.trim().slice(-5).toUpperCase(), oldKeyTail: db.key.accessKeyTail, startedAt: iso(),
        macsTotal: db.macs.map(toMac).filter((m) => m.connected).length,
        failCode: accessKeyId.toUpperCase().includes('DENIED') ? 'AccessDenied' : null, applied: false,
      }
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
      db.key.rotation = null
      db.key.window = null
      log({ actor: actorOf(me), action: 'key_window_closed', category: 'key', target: { kind: 'key', id: 'key', label: rotation.oldKeyTail }, detail: {} })
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
      return { organizationName: db.organizationName, mail: { ...db.mail }, storage: { ...db.storage } }
    },
    async update(body) {
      await latency(400, 800)
      const me = requireAdmin()
      if (body.organizationName !== undefined) {
        if (!body.organizationName.trim()) throw new ApiError(422, 'validation', 'Name required')
        db.organizationName = body.organizationName.trim()
      }
      if (body.mail) {
        const { password, ...mail } = body.mail
        db.mail = { ...mail, passwordSet: db.mail.passwordSet || !!password }
      }
      if (body.storage) db.storage = { ...body.storage }
      log({ actor: actorOf(me), action: 'settings_updated', category: 'settings', target: { kind: 'organization', id: db.organizationId, label: db.organizationName }, detail: {} })
      save()
      return { organizationName: db.organizationName, mail: { ...db.mail }, storage: { ...db.storage } }
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
