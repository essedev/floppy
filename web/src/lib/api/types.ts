/*
  The proposed contract between the admin web and the control plane API (FastAPI, later).
  Request and response shapes for every endpoint under /api/v1; the paths are in http.ts.
  The tables behind each endpoint are in docs/DATABASE_SCHEMA.md ("API endpoints and tables",
  "State machines"); the API speaks camelCase JSON and ISO 8601 UTC timestamps. The app's
  endpoints are at the end: the web never calls them, they are here so one file holds the API.
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

/** State of a pending request, polled by the waiting page until the link is opened. A
 * superseded request (a newer one for the same address) reads as expired. */
export type SignInStatus = 'pending' | 'completed' | 'expired' | 'locked'

/**
 * GET /auth/sign-in/{requestId}. Only the state: polling never yields a session. The session
 * goes to whoever opens the link or types the code, otherwise anyone who started a request with
 * someone else's address would be signed in when the victim clicks. A page that sees
 * `completed` without a session of its own was finished in another browser.
 */
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
  /** When the latest invitation link stops working (7 days); null unless invited. Past it, the
   * person cannot complete a first sign-in until an admin resends. */
  inviteExpiresAt: Iso | null
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

/** Where a disk is in its creation (juicefs format); a failed one is soft deleted and only
 * GET /disks/{id} still returns it. */
export type DiskState = 'creating' | 'ready' | 'failed'

/**
 * A disk's display name, which is its folder in /Volumes/Floppy (DATABASE_SCHEMA.md, "Disks on
 * a Mac"): ASCII letters, digits, spaces, dots, hyphens and underscores, starting and ending
 * with a letter or digit, at most 60. No accents: the Mac could receive them in another Unicode
 * normalization, and CapCut's paths would break. Unique case-insensitively, as macOS compares.
 */
export const DISK_NAME = /^[A-Za-z0-9]([A-Za-z0-9 ._-]{0,58}[A-Za-z0-9])?$/

/**
 * A disk: a JuiceFS volume, a prefix in the organization's bucket and a Valkey database. On
 * every Mac a team's disks are folders inside the one mounted disk, /Volumes/Floppy/<name>.
 * JuiceFS's own name (the object prefix) is derived by the server from `name` and never shown,
 * so it is not here; only the app's /device/config carries it.
 */
export interface Disk {
  id: string
  /** The name people see, also the folder in /Volumes/Floppy; never renamed, CapCut keeps
   * full paths. */
  name: string
  /** Where the disk appears on a Mac: /Volumes/Floppy/<name>. */
  mountPath: string
  state: DiskState
  /** Why the creation failed, when it did; never contains a secret. */
  error: { code: string; message: string } | null
  /** Reachability of its metadata, as the control plane last measured it. */
  status: 'online' | 'unreachable'
  bucket: string
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

/**
 * POST /disks: answers at once with the disk `creating`; juicefs format runs after, and GET
 * /disks/{id} reports `ready` or `failed`. At most 15 disks per organization (Valkey's 16
 * databases, the first left empty): `disk_limit_reached`.
 */
export interface CreateDiskRequest {
  /** Matches DISK_NAME; the server derives JuiceFS's name from it, resolving collisions. */
  name: string
  trashDays: number
}

/** PATCH /disks/{id}: trash days through juicefs config, the cache policy at the next mount. */
export interface UpdateDiskRequest {
  trashDays?: number
  cache?: CachePolicy
}

// The S3 key and its rotation

/** Why the key window is open: someone who had the key no longer has access (a key exposure). */
export interface KeyWindowReason {
  id: string
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

/**
 * A key rotation: probe write, juicefs config on each disk, Macs picking up the new key. It is
 * `awaiting_old_key_deletion` as soon as every disk is configured: the Macs count is shown, not
 * waited for, or a Mac switched off would block it forever. Closing it closes only the
 * exposures that happened before `startedAt`; a later one stays open, since the revoked Mac may
 * have read the new key from its replica.
 */
export interface Rotation {
  id: string
  state: 'applying' | 'awaiting_old_key_deletion' | 'failed'
  newKeyTail: string
  /** Every key to delete at the provider: the one this rotation replaces and those of the
   * rotations it superseded before they were closed. */
  oldKeyTails: string[]
  probe: StepState
  disks: { id: string; name: string; state: StepState }[]
  /** Informative: connected Macs when it started, and those already on the new key. */
  macs: { updated: number; total: number }
  /** The provider's answer when the probe write fails (e.g. AccessDenied), or JuiceFS's when
   * a disk fails (step 'disk'). */
  error: { step: 'probe' | 'disk'; code: string; request: string } | null
  startedAt: Iso
}

/** POST /key/rotations. The secret is sent once and never returned. Starting one while
 * another is awaiting the old key's deletion supersedes it. */
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
  | 'sign_in_locked'
  | 'invite_accepted'
  | 'mac_registered'
  | 'mac_signed_out'
  | 'key_rotation_started'
  | 'key_rotation_failed'
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
  /** A person, the control plane itself (`system`: a heartbeat, the reconciler, a rotation's
   * worker) or whoever runs the hosted service (`operator`). Labels are as they were then. */
  actor: { kind: 'person'; id: string; label: string } | { kind: 'system' | 'operator'; id: null; label: string | null }
  action: AuditAction
  category: AuditCategory
  target: { kind: 'person' | 'mac' | 'disk' | 'key' | 'organization'; id: string; label: string }
  /** Small facts from an allowlist per action, e.g. { from: 'member', to: 'admin' },
   * { appVersion: '0.4.0' }, { fields: ['name'] }; never a secret. */
  detail: Record<string, string | number | string[]>
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
  /** True while a live disk exists: its format holds the bucket, so storage cannot change. */
  storageLocked: boolean
}

/** PATCH /settings. Omitted fields stay as they are; an empty password keeps the old one; a
 * storage change while a live disk exists fails with `storage_in_use`. */
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
  /** A live disk has the same name, compared case-insensitively. */
  | 'disk_name_taken'
  | 'disk_limit_reached'
  | 'storage_in_use'
  | 'probe_failed'
  | 'rotation_in_progress'
  | 'validation'

// The app's API (Floppy.app on each Mac). Types and paths only: the web never calls these.
// Flows and tables in docs/DATABASE_SCHEMA.md ("sign_in_requests", "devices", "API endpoints
// and tables"). After /auth/app/token every call carries the device token as a Bearer header.

/**
 * POST /auth/app/sign-in: the app asks for a link and a code, with PKCE (S256) and the
 * loopback port it listens on. Answers like the web's sign-in. The link in the mail, opened in
 * any browser, redirects to `redirectUri` with a one-time authorization code (60 seconds)
 * instead of opening a web session.
 */
export interface AppSignInRequest {
  email: string
  codeChallenge: string
  /** http://127.0.0.1:<port>/... only. */
  redirectUri: string
  /** "MacBook Pro di Giulia", shown on the page and in the mail. */
  deviceName: string
}

/**
 * POST /auth/app/token: the verifier with either the authorization code from the loopback or
 * the 6-digit code typed in the app; the request is found by S256(verifier). Single use.
 */
export interface AppTokenRequest {
  codeVerifier: string
  authorizationCode?: string
  code?: string
}

/** The new Mac, `pending` until it registers, and its token (shown once, kept in the Keychain). */
export interface AppTokenResponse {
  deviceId: string
  deviceToken: string
}

/** POST /device/register: creates the Mac's Valkey user; called again to reissue it. */
export interface DeviceRegisterRequest {
  appVersion: string
  macosVersion: string
}

/** The Mac's Valkey credentials, returned once (only a SHA-256 of the password is kept). */
export interface DeviceRegisterResponse {
  valkeyUsername: string
  valkeyPassword: string
  valkeyUrl: string
}

/** GET /device/config: what the app needs to mount; no secret, the S3 key is in the format. */
export interface DeviceConfig {
  valkeyUrl: string
  /** volumeName is JuiceFS's name, `^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$`, the object prefix. */
  disks: { id: string; name: string; volumeName: string; metaDb: number; cacheExpireDays: number; mountPath: string }[]
}

/** POST /device/heartbeat, every 30 to 60 seconds. */
export interface DeviceHeartbeatRequest {
  appVersion: string
  macosVersion: string
  replica: { mode: ReplicaMode; lagMs: number | null } | null
  /** The key its mounts use, read from the format on its replica: counts a rotation's Macs. */
  s3AccessKeyTail: string | null
  mountedDiskIds: string[]
}

/** The heartbeat's answer: on `revoked` the app stops the disk, clears cache and replica, and
 * deletes its token. */
export interface DeviceHeartbeatResponse {
  state: 'active' | 'revoked'
}

// POST /device/sign-out: "Esci" in the app. The Mac is revoked (`signed_out`); no key window,
// since its owner keeps access. No body, answers 204.
