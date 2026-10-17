/*
  The proposed contract between the admin web and the control plane API (FastAPI, later).
  Request and response shapes for every endpoint under /api/v1; the paths are in http.ts.
  Field names follow the tables in docs/architecture.html (organizations, users, memberships,
  volumes, devices, audit_events, sign_in_requests); the API speaks camelCase JSON and ISO 8601
  UTC timestamps. Until docs/DATABASE_SCHEMA.md exists this file is the proposal to review.
*/

/** An ISO 8601 timestamp in UTC, as the API sends it. */
export type Iso = string

/** What a membership allows: admins manage everything, members only their own Macs. */
export type Role = 'admin' | 'member'

// Session and sign-in

/** The organization as the session sees it, with the counts the sidebar shows. */
export interface OrganizationSummary {
  id: string
  name: string
  diskCount: number
  peopleCount: number
  macCount: number
}

/** The signed-in person, their role and their organization. */
export interface Session {
  user: { id: string; email: string; name: string }
  role: Role
  organization: OrganizationSummary
}

/** POST /auth/sign-in: asks for a magic link and a 6-digit code by email. */
export interface SignInStartRequest {
  email: string
}

/**
 * The pending sign-in request. The answer is the same whether or not the address belongs to
 * a team, so the page never reveals who is a member; the email says it instead.
 */
export interface SignInStartResponse {
  requestId: string
  email: string
  expiresAt: Iso
  /** When "send another link" becomes available. */
  resendAt: Iso
}

/** State of a pending request, polled by the waiting page until the link is opened. */
export type SignInStatus = 'pending' | 'completed' | 'expired' | 'locked'

/** GET /auth/sign-in/{requestId}. */
export interface SignInStatusResponse {
  status: SignInStatus
}

/** POST /auth/sign-in/{requestId}/code: the fallback for mail read on another device. */
export interface SignInCodeRequest {
  code: string
}

/** POST /auth/magic-link: the token from the link in the email, single use, 15 minutes. */
export interface MagicLinkRequest {
  token: string
}

// People

/** Membership state; a removed person stays listed, as soft delete does. */
export type PersonStatus = 'invited' | 'active' | 'removed'

/** A person in the organization: a user and their membership. */
export interface Person {
  id: string
  /** Null until an invited person signs in for the first time. */
  name: string | null
  email: string
  role: Role
  status: PersonStatus
  /** Macs not revoked. */
  macCount: number
  lastSignInAt: Iso | null
  invitedAt: Iso | null
  invitedBy: string | null
  removedAt: Iso | null
  isMe: boolean
}

/** POST /people. Only admins invite. */
export interface InviteRequest {
  email: string
  role: Role
}

/** PATCH /people/{id}. */
export interface UpdatePersonRequest {
  role: Role
}

/** DELETE /people/{id}: the membership is revoked and every Mac of the person with it. */
export interface RemovePersonResponse {
  person: Person
  revokedMacs: number
  /** The key status after the removal: its window is open now. */
  key: KeyStatus
}

// Macs

/**
 * Where the Mac's JuiceFS reads its metadata from, as its last heartbeat says: its local
 * replica, the master (replica more than 2 s behind or restarting), or offline (master
 * unreachable, the disk is read only). See docs/ARCHITECTURE.md, "Shared metadata".
 */
export type ReplicaMode = 'replica' | 'master' | 'offline'

/** A Mac (a device): one Valkey user each, revoked alone (DECISIONS #7). */
export interface Mac {
  id: string
  name: string
  owner: { id: string; name: string }
  /** The Valkey ACL user, floppy_d_<id>. */
  valkeyUser: string
  macosVersion: string
  appVersion: string
  appOutdated: boolean
  lastSeenAt: Iso | null
  /** A heartbeat in the last 2 minutes. */
  connected: boolean
  /** From the last heartbeat; null when the Mac has never reported one or is not connected. */
  replica: { mode: ReplicaMode; lagMs: number | null } | null
  revokedAt: Iso | null
  revokedBy: string | null
}

/** POST /macs/{id}/revoke: ACL DELUSER and CLIENT KILL on the central Valkey. */
export interface RevokeMacResponse {
  mac: Mac
  key: KeyStatus
}

// Disks

/**
 * The cache option every Mac mounting the disk gets: only the expiry. There is no size cap
 * per disk; each Mac computes its own from its free space (docs/ARCHITECTURE.md, "Configuration").
 */
export interface CachePolicy {
  /** --cache-expire: days after which an unused block is freed. Default 30. */
  expireDays: number
}

/** A disk: a JuiceFS volume, a prefix in the organization's bucket and a Valkey database. */
export interface Disk {
  id: string
  /** VOLUME_NAME, shown in the Finder; never renamed, CapCut keeps full paths. */
  name: string
  status: 'online' | 'unreachable'
  bucket: string
  prefix: string
  region: string
  endpoint: string
  /** Valkey database number holding this volume's metadata. */
  metaDb: number
  /** Fixed at creation: 1 MB (DECISIONS #15). */
  blockSizeKb: number
  trashDays: number
  trashBytes: number
  usedBytes: number
  fileCount: number
  lastWriteAt: Iso | null
  /** Macs connected with this disk mounted. */
  mountedBy: number
  cache: CachePolicy
  createdAt: Iso
  createdBy: string
}

/** POST /disks: juicefs format on a new prefix and a new Valkey database. */
export interface CreateDiskRequest {
  name: string
  prefix: string
  trashDays: number
}

/** PATCH /disks/{id}: trash days through juicefs config, the cache policy at the next mount. */
export interface UpdateDiskRequest {
  trashDays?: number
  cache?: CachePolicy
}

// The S3 key and its rotation

/** Why the key window is open: someone who had the key no longer has access. */
export interface KeyWindowReason {
  kind: 'person_removed' | 'mac_revoked'
  label: string
  at: Iso
}

/** The organization's S3 key (DECISIONS #4) and the risk window after a removal or revocation. */
export interface KeyStatus {
  /** The organization's bucket, which the key must be scoped to. */
  bucket: string
  /** The last characters of the access key in use. */
  accessKeyTail: string
  rotatedAt: Iso | null
  /** Open from the first removal or revocation until the old key is deleted at the provider. */
  window: { openedAt: Iso; reasons: KeyWindowReason[] } | null
  /** A rotation in progress, or applied and waiting for the old key's deletion. */
  rotation: Rotation | null
}

/** Progress of one part of a rotation. */
export type StepState = 'pending' | 'running' | 'done' | 'failed'

/** A key rotation: probe write, juicefs config on each disk, Macs picking up the new key. */
export interface Rotation {
  id: string
  state: 'applying' | 'awaiting_old_key_deletion' | 'failed'
  newKeyTail: string
  oldKeyTail: string
  probe: StepState
  disks: { id: string; name: string; state: StepState }[]
  macs: { updated: number; total: number }
  /** The provider's answer when the probe write fails, e.g. AccessDenied. */
  error: { code: string; request: string } | null
  startedAt: Iso
}

/** POST /key/rotations. The secret is sent once and never returned. */
export interface StartRotationRequest {
  accessKeyId: string
  secretKey: string
}

/** POST /key/rotations/{id}/close: the admin confirms the old key is deleted. */
export interface CloseRotationRequest {
  oldKeyDeleted: true
}

// Audit log

/** Filter groups of the audit log. */
export type AuditCategory = 'access' | 'people' | 'macs' | 'key' | 'disks' | 'settings'

/** Every action the control plane records. */
export type AuditAction =
  | 'signed_in'
  | 'mac_registered'
  | 'mac_seen_outdated'
  | 'person_invited'
  | 'invite_resent'
  | 'person_removed'
  | 'role_changed'
  | 'mac_revoked'
  | 'key_rotated'
  | 'key_window_closed'
  | 'disk_created'
  | 'disk_updated'
  | 'settings_updated'

/** One audit_events row, append only. */
export interface AuditEvent {
  id: string
  at: Iso
  /** Null for the system itself (e.g. a heartbeat). */
  actor: { id: string; name: string } | null
  action: AuditAction
  category: AuditCategory
  target: { kind: 'person' | 'mac' | 'disk' | 'key' | 'organization'; id: string; label: string }
  /** Small extra facts, e.g. { role: 'member' } or { appVersion: '0.4.0' }. */
  detail: Record<string, string | number>
}

/** GET /audit query string. */
export interface AuditQuery {
  category?: AuditCategory
  /** Matches the actor's or the target's name. */
  q?: string
  sinceDays: 7 | 30 | 90
  cursor?: string
  limit?: number
}

/** A page of a cursor-paginated list. */
export interface Page<T> {
  items: T[]
  nextCursor: string | null
}

// Health, as the control plane sees it (not as each Mac does)

/** GET /health. */
export interface Health {
  metadata: { state: 'ok' | 'down'; latencyMs: number | null; checkedAt: Iso }
  storage: { state: 'ok' | 'failing'; lastProbeAt: Iso | null }
  mail: { state: 'ok' | 'unconfigured' | 'failing'; lastSentAt: Iso | null }
}

// Settings

/** GET /settings. The SMTP password is write-only: only whether one is set comes back. */
export interface Settings {
  organizationName: string
  mail: { host: string; port: number; username: string; sender: string; passwordSet: boolean }
  storage: { endpoint: string; region: string; bucket: string }
}

/** PATCH /settings. Omitted fields stay as they are; an empty password keeps the old one. */
export interface UpdateSettingsRequest {
  organizationName?: string
  mail?: { host: string; port: number; username: string; sender: string; password?: string }
  storage?: { endpoint: string; region: string; bucket: string }
}

/** POST /settings/storage/probe: a test write with the organization's key, nothing changes. */
export interface StorageProbeRequest {
  endpoint: string
  region: string
  bucket: string
}

/** POST /settings/mail/test answer. */
export interface MailTestResponse {
  sentTo: string
}

// Errors

/**
 * Error codes the API returns in { code, message, details }. The UI shows its own text for
 * each code; message is for logs.
 */
export type ErrorCode =
  | 'unauthorized'
  | 'forbidden'
  | 'not_found'
  | 'network'
  | 'server'
  | 'invalid_code'
  | 'request_expired'
  | 'request_locked'
  | 'link_invalid'
  | 'already_member'
  | 'last_admin'
  | 'cannot_remove_self'
  | 'disk_name_taken'
  | 'probe_failed'
  | 'rotation_in_progress'
  | 'validation'
