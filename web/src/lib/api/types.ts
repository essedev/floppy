/*
  The contract between the admin web and the control plane API (server/, FastAPI): request
  and response shapes for every endpoint under /api/v1; the paths are in http.ts. The source of
  truth is the server's Pydantic models; the tables behind each endpoint are in
  docs/DATABASE_SCHEMA.md ("API endpoints and tables", "State machines"). The API speaks
  camelCase JSON and ISO 8601 UTC timestamps. The app's endpoints are at the end: the web never
  calls them, they are here so one file holds the API.
*/

/** An ISO 8601 timestamp in UTC, as the API sends it. */
export type Iso = string

/** What a membership allows: admins manage everything, members only their own devices. */
export type Role = 'admin' | 'member'

/** Where the organization's buckets and keys come from (DECISIONS #24), fixed at creation. */
export type StorageMode = 'manual' | 'managed'

/** The provider adapter of managed storage; null in manual mode. */
export type StorageProvider = 'ovh' | 'aws' | 'minio' | 'garage'

// Session and sign-in

/** The organization as the session sees it, with the counts the sidebar shows. */
export interface OrganizationSummary {
  id: string
  name: string
  diskCount: number
  peopleCount: number
  deviceCount: number
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

/** POST /auth/magic-link and POST /auth/invitation: the token from the link in the email. */
export interface TokenRequest {
  token: string
}

// People

/** Membership state; a removed person stays listed, as soft delete does. */
export type PersonStatus = 'invited' | 'active' | 'removed'

/** A person in the organization: a user and their membership. The id is the user's. */
export interface Person {
  id: string
  /** Null until an invited person signs in for the first time. */
  name: string | null
  email: string
  role: Role
  status: PersonStatus
  /** Devices not revoked. */
  deviceCount: number
  lastSignInAt: Iso | null
  invitedAt: Iso | null
  invitedBy: string | null
  /** When the latest invitation link stops working (7 days); null unless invited. Past it, the
   * person cannot complete a first sign-in until an admin resends. */
  inviteExpiresAt: Iso | null
  removedAt: Iso | null
  isMe: boolean
}

/** POST /people. Only admins invite; the person has no access to any disk not listed. */
export interface InviteRequest {
  email: string
  role: Role
  disks?: { diskId: string; access: AccessLevel }[]
}

/** PATCH /people/{id}. */
export interface UpdatePersonRequest {
  role: Role
}

/** DELETE /people/{id}: the membership is removed and every device of the person with it. */
export interface RemovePersonResponse {
  person: Person
  revokedDevices: number
  /** The keys after the removal: in manual mode the windows of the disks they could see open. */
  key: KeyStatus
}

// Access per disk (DECISIONS #23)

/** What a person may do on a disk; their devices inherit it. A missing row means `none`. */
export type AccessLevel = 'none' | 'read' | 'write'

/**
 * GET /access and PUT /disks/{diskId}/access/{personId}: one person on one disk. `applied` is
 * false while a device's database user or (managed) its key's grant is still owed: the change
 * is recorded, the devices do not have it yet.
 */
export interface DiskAccess {
  diskId: string
  personId: string
  access: AccessLevel
  applied: boolean
}

/** GET /access/mine: a ready disk the signed-in person may read or write, for anyone. */
export interface MyDisk {
  id: string
  name: string
  access: Exclude<AccessLevel, 'none'>
}

/** PUT /disks/{diskId}/access/{personId}. Lowering to `none` in manual mode opens that disk's
 * key window when one of the person's devices could see it. */
export interface SetAccessRequest {
  access: AccessLevel
}

// Devices

/**
 * Where the device's JuiceFS reads its metadata from, as its last heartbeat says: its local
 * replica, the master (replica more than 2 s behind or restarting), or offline (master
 * unreachable, the disk is read only). See docs/ARCHITECTURE.md, "Shared metadata".
 */
export type ReplicaMode = 'replica' | 'master' | 'offline'

/** A device (today a Mac): one Valkey user on every disk it may see (DECISIONS #7, #23). */
export interface Device {
  id: string
  name: string
  /** `pending` from the app's sign-in until it registers. */
  state: 'pending' | 'active' | 'revoked'
  owner: { id: string; name: string }
  /** The Valkey ACL user, the same name on every disk's instance. */
  valkeyUser: string
  macosVersion: string
  appVersion: string
  appOutdated: boolean
  lastSeenAt: Iso | null
  /** A heartbeat in the last 2 minutes. */
  connected: boolean
  /** From the last heartbeat; null when the device has never reported one or is not connected. */
  replica: { mode: ReplicaMode; lagMs: number | null } | null
  revokedAt: Iso | null
  revokedBy: string | null
}

/** POST /devices/{id}/revoke: its users deleted on every instance, their connections killed. */
export interface RevokeDeviceResponse {
  device: Device
  /** The keys after the revocation; null for a member, who does not see them. */
  key: KeyStatus | null
}

// Disks

/**
 * The cache option every device mounting the disk gets: only the expiry. There is no size cap
 * per disk; each device computes its own from its free space (docs/ARCHITECTURE.md,
 * "Configuration").
 */
export interface CachePolicy {
  /** --cache-expire: days after which an unused block is freed. Default 30. */
  expireDays: number
}

/** Where a disk is in its creation (instance, bucket, juicefs format); a failed one is soft
 * deleted and only GET /disks/{id} still returns it. */
export type DiskState = 'creating' | 'ready' | 'failed'

/**
 * A disk's display name, which is its folder in /Volumes/Floppy (DATABASE_SCHEMA.md, "Disks on
 * a device"): ASCII letters, digits, spaces, dots, hyphens and underscores, starting and ending
 * with a letter or digit, at most 60. No accents: the Mac could receive them in another Unicode
 * normalization, and CapCut's paths would break. Unique case-insensitively, as macOS compares.
 */
export const DISK_NAME = /^[A-Za-z0-9]([A-Za-z0-9 ._-]{0,58}[A-Za-z0-9])?$/

/** An S3 bucket name: 3-63 lowercase letters, digits, dots and hyphens. */
export const BUCKET_NAME = /^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$/

/**
 * A disk: a JuiceFS volume with its own Valkey instance and its own bucket (DECISIONS #23). On
 * every device a team's disks are folders inside the one mounted disk, /Volumes/Floppy/<name>.
 * JuiceFS's own name is derived by the server from `name` and never shown, so it is not here;
 * only the app's /device/config carries it.
 */
export interface Disk {
  id: string
  /** The name people see, also the folder in /Volumes/Floppy; never renamed, CapCut keeps
   * full paths. */
  name: string
  /** Where the disk appears on a device: /Volumes/Floppy/<name>. */
  mountPath: string
  state: DiskState
  /** Why the creation failed, when it did; never contains a secret. */
  error: { code: string; message: string } | null
  /** Reachability of its instance, as the control plane last measured it. */
  status: 'online' | 'unreachable'
  bucket: string
  region: string
  endpoint: string
  /** The disk's instance and database as devices reach it (rediss://host:port/1, no
   * credentials); null until the meta service gave it one. */
  metaUrl: string | null
  /** Fixed at creation: 1 MB (DECISIONS #15). */
  blockSizeKb: number
  trashDays: number
  trashBytes: number
  usedBytes: number
  fileCount: number
  lastWriteAt: Iso | null
  /** Devices connected with this disk mounted. */
  mountedBy: number
  cache: CachePolicy
  createdAt: Iso
  createdBy: string
}

/** A disk's own bucket and key, made by the admin at the provider (manual mode). Endpoint and
 * region default to the organization's. */
export interface DiskStorageRequest {
  endpoint?: string
  region?: string
  bucket: string
  accessKeyId: string
  secretKey: string
}

/**
 * POST /disks: answers at once with the disk `creating`; the instance, the bucket (managed) and
 * juicefs format run after, and GET /disks/{id} reports `ready` or `failed`. In manual mode
 * `storage` is required and probed first (`probe_failed`, `bucket_taken`); in managed mode it is
 * refused. The creator gets `write` unless `access` lists them; everyone else not listed none.
 */
export interface CreateDiskRequest {
  /** Matches DISK_NAME; the server derives JuiceFS's name from it, resolving collisions. */
  name: string
  trashDays: number
  storage?: DiskStorageRequest
  access?: { personId: string; access: AccessLevel }[]
}

/** PATCH /disks/{id}: trash days through juicefs config, the cache policy at the next mount. */
export interface UpdateDiskRequest {
  trashDays?: number
  cache?: CachePolicy
}

// Storage keys and their rotation (DECISIONS #18, #24)

/** Why a disk's key window is open: someone who could read its key lost access to it. */
export interface KeyWindowReason {
  id: string
  kind: 'person_removed' | 'device_revoked' | 'access_removed'
  label: string
  at: Iso
}

/** Open from a disk's first exposure until a rotation of its key closes it. */
export interface KeyWindow {
  openedAt: Iso
  reasons: KeyWindowReason[]
}

/** A bucket's versioning as the last probe read it (recommended in manual mode). */
export interface Versioning {
  state: 'enabled' | 'suspended' | 'off' | 'unknown'
  oldVersionsDays: number | null
}

/** Progress of one part of a rotation. */
export type StepState = 'pending' | 'running' | 'done' | 'failed'

/**
 * A rotation of one disk's key (manual): probe write, juicefs config, devices picking up the
 * new key. It is `awaiting_old_key_deletion` as soon as the disk is configured: the devices
 * count is shown, not waited for, or a device switched off would block it forever. Closing it
 * closes only the exposures that happened before `startedAt`; a later one stays open, since the
 * device may have read the new key from its replica.
 */
export interface Rotation {
  id: string
  diskId: string
  diskName: string
  state: 'applying' | 'awaiting_old_key_deletion' | 'failed'
  newKeyTail: string
  /** Every key of the disk to delete at the provider: the one replaced and those of the
   * rotations it superseded before they were closed. */
  oldKeyTails: string[]
  probe: StepState
  config: StepState
  /** Informative: connected devices seeing the disk when it started, and those on the new key. */
  devices: { updated: number; total: number }
  /** The provider's answer when the probe write fails (e.g. AccessDenied), or JuiceFS's when
   * the config does. */
  error: { step: 'probe' | 'config'; code: string; request: string } | null
  startedAt: Iso
}

/**
 * One disk's storage: its bucket, and in manual mode the key its format holds, with the window
 * and the rotation. In managed mode `accessKeyTail` is null and there is no window.
 */
export interface DiskKey {
  diskId: string
  diskName: string
  bucket: string
  endpoint: string
  accessKeyTail: string | null
  rotatedAt: Iso | null
  window: KeyWindow | null
  /** A rotation applying, awaiting the old key's deletion, or failed. */
  rotation: Rotation | null
  versioning: Versioning
}

/** A device's own key (managed): live, or `retiring` while its deletion at the provider is owed. */
export interface DeviceKey {
  id: string
  deviceId: string
  /** "MacBook Pro di Giulia (Giulia Ferri)". */
  owner: string
  accessKeyTail: string
  state: 'pending' | 'active' | 'retiring'
  activatedAt: Iso | null
  retiringAt: Iso | null
}

/** GET /key: the organization's storage keys, each disk's (manual) or each device's (managed). */
export interface KeyStatus {
  storageMode: StorageMode
  storageProvider: StorageProvider | null
  /** Managed: the control plane's own key, owner of every bucket it creates. */
  controlPlaneKeyTail: string | null
  disks: DiskKey[]
  keys: DeviceKey[]
}

/** POST /key/rotations (manual). The secret is sent once and never returned. Starting one while
 * another of the same disk is awaiting the old key's deletion supersedes it. */
export interface StartRotationRequest {
  diskId: string
  accessKeyId: string
  secretKey: string
}

/** POST /key/rotations/{id}/close: the admin confirms the old key is deleted. */
export interface CloseRotationRequest {
  oldKeyDeleted: true
}

// Audit log

/** Filter groups of the audit log. */
export type AuditCategory = 'access' | 'people' | 'devices' | 'key' | 'disks' | 'settings'

/** Every action the control plane records (server/src/floppy_server/audit/service.py). */
export type AuditAction =
  | 'signed_in'
  | 'sign_in_locked'
  | 'person_invited'
  | 'invite_resent'
  | 'invite_accepted'
  | 'role_changed'
  | 'person_removed'
  | 'device_registered'
  | 'device_revoked'
  | 'device_signed_out'
  | 'device_seen_outdated'
  | 'organization_created'
  | 'key_created'
  | 'key_deleted'
  | 'key_rotation_started'
  | 'key_rotated'
  | 'key_rotation_failed'
  | 'key_window_closed'
  | 'disk_created'
  | 'disk_updated'
  | 'disk_access_changed'
  | 'disk_deleted'
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
  target: { kind: 'person' | 'device' | 'disk' | 'key' | 'organization'; id: string; label: string }
  /** Small facts from an allowlist per action, e.g. { from: 'member', to: 'admin' },
   * { trashDays: [7, 14] }, { fields: ['organizationName'] }; never a secret. */
  detail: Record<string, string | number | (string | number)[]>
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

// Health, as the control plane sees it (not as each device does)

/** A disk's instance as last pinged, or the worst of every disk's. */
export interface MetadataHealth {
  state: 'ok' | 'down'
  latencyMs: number | null
  checkedAt: Iso | null
}

/** A bucket's last test write and its versioning, or the worst of every disk's; `unknown`
 * until probed, or when the disks disagree. */
export interface StorageHealth {
  state: 'ok' | 'failing' | 'unknown'
  lastProbeAt: Iso | null
  versioning: Versioning
}

/** GET /health: `metadata` and `storage` summarize the ready disks in `disks`. */
export interface Health {
  metadata: MetadataHealth
  storage: StorageHealth
  mail: { state: 'ok' | 'unconfigured' | 'failing'; lastSentAt: Iso | null }
  disks: { id: string; name: string; metadata: MetadataHealth; storage: StorageHealth }[]
}

// Settings

/** The language of the mails the control plane sends. */
export type MailLanguage = 'it' | 'en'

/** GET /settings. The SMTP password is write-only: only whether one is set comes back. */
export interface Settings {
  organizationName: string
  mailLanguage: MailLanguage
  mail: { host: string; port: number; username: string; sender: string; passwordSet: boolean }
  /** The default endpoint and region of new disks. */
  storage: { endpoint: string; region: string }
  /** True while a live disk exists: the defaults cannot change any more. */
  storageLocked: boolean
  /** Set when the organization was created, shown only. */
  storageMode: StorageMode
  storageProvider: StorageProvider | null
}

/** PATCH /settings. Omitted fields stay as they are; an empty password keeps the old one, an
 * empty host returns to the instance's server; a storage change while a live disk exists fails
 * with `storage_in_use`. */
export interface UpdateSettingsRequest {
  organizationName?: string
  mailLanguage?: MailLanguage
  mail?: { host: string; port: number; username: string; sender: string; password?: string }
  storage?: { endpoint: string; region: string }
}

/** POST /settings/storage/probe (manual): a test write with a bucket and its key before
 * creating a disk with them. Nothing is stored. */
export interface StorageProbeRequest {
  endpoint: string
  region: string
  bucket: string
  accessKeyId: string
  secretKey: string
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
  | 'validation'
  | 'invalid_code'
  | 'request_expired'
  | 'request_locked'
  | 'link_invalid'
  | 'rate_limited'
  | 'already_member'
  | 'last_admin'
  | 'cannot_remove_self'
  /** A transition the current state does not allow (409). */
  | 'invalid_state'
  /** A live disk has the same name, compared case-insensitively. */
  | 'disk_name_taken'
  /** Another disk uses this bucket: each disk has its own (409, details.field = 'bucket'). */
  | 'bucket_taken'
  | 'storage_in_use'
  /** A test write failed (422, details.code and details.request). */
  | 'probe_failed'
  | 'rotation_in_progress'
  /** JuiceFS refused a juicefs config (502). */
  | 'format_failed'
  /** The storage adapter's call to the provider failed (502). */
  | 'provider_failed'
  /** The SMTP server refused the mail (503). */
  | 'mail_failed'

// The app's API (Floppy.app on each device). Types and paths only: the web never calls these.
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

/** The new device, `pending` until it registers, and its token (shown once, kept in the
 * Keychain). */
export interface AppTokenResponse {
  deviceId: string
  deviceToken: string
}

/** POST /device/register: the bootstrap; called again to reissue the credentials. */
export interface DeviceRegisterRequest {
  appVersion: string
  macosVersion: string
}

/** A disk the device may see, as the app mounts it; `read` means a --read-only mount. */
export interface DeviceDiskConfig {
  id: string
  name: string
  /** JuiceFS's name, `^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$`. */
  volumeName: string
  access: 'read' | 'write'
  /** The disk's instance and database, without credentials: the app adds its user and password. */
  metaUrl: string
  cacheExpireDays: number
  mountPath: string
}

/** Returned once: the Valkey user and password (the same on every disk's instance) and, in
 * managed mode, the device's own S3 key (null in manual mode: the key is in each disk's format). */
export interface DeviceRegisterResponse {
  valkeyUsername: string
  valkeyPassword: string
  storageMode: StorageMode
  s3: { accessKeyId: string; secretKey: string } | null
  organization: { id: string; name: string }
  user: { email: string; name: string }
  disks: DeviceDiskConfig[]
}

/** GET /device/config: what the app needs to mount, no secret. */
export interface DeviceConfig {
  storageMode: StorageMode
  /** Managed: the tail of the device's own key, to check the one in the Keychain is current. */
  s3AccessKeyTail: string | null
  organization: { id: string; name: string }
  user: { email: string; name: string }
  disks: DeviceDiskConfig[]
}

/** POST /device/heartbeat, every 30 to 60 seconds. */
export interface DeviceHeartbeatRequest {
  appVersion: string
  macosVersion: string
  replica: { mode: ReplicaMode; lagMs: number | null } | null
  /** The key its mounts use: counts a rotation's devices. */
  s3AccessKeyTail: string | null
  mountedDiskIds: string[]
}

/** The heartbeat's answer: on `revoked` the app stops every disk, clears cache and replicas,
 * and deletes its token; `disks` is what the device may see now (remount read-only, unmount,
 * or fetch the config of a new one). */
export interface DeviceHeartbeatResponse {
  state: 'active' | 'revoked'
  disks: { id: string; access: 'read' | 'write' }[]
}

// POST /device/sign-out: "Esci" in the app. The device is revoked (`signed_out`); no key
// window, since its owner keeps access. No body, answers 204.
