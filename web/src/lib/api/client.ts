/*
  The API as the features see it: one interface, two implementations (http.ts for the real
  control plane, fake/ for the in-memory stand-in). Features import `api` from '@/lib/api'
  and never know which one runs.
*/

import type {
  AccessLevel,
  AuditEvent,
  AuditQuery,
  CreateDiskRequest,
  Disk,
  DiskAccess,
  Health,
  InviteRequest,
  KeyStatus,
  Device,
  MailTestResponse,
  Page,
  Person,
  RemovePersonResponse,
  RevokeDeviceResponse,
  Rotation,
  Session,
  Settings,
  SignInStartResponse,
  SignInStatusResponse,
  StartRotationRequest,
  StorageProbeRequest,
  UpdateDiskRequest,
  UpdatePersonRequest,
  UpdateSettingsRequest,
  ErrorCode,
} from './types'

/** An error answer from the API, or a request that never got one (code 'network'). */
export class ApiError extends Error {
  readonly status: number
  readonly code: ErrorCode
  readonly details: Record<string, unknown>

  constructor(status: number, code: ErrorCode, message: string, details: Record<string, unknown> = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
  }
}

/** Whether a value is an ApiError with the given code. */
export const isApiError = (error: unknown, code?: ErrorCode): error is ApiError =>
  error instanceof ApiError && (code === undefined || error.code === code)

/** Every call the admin web makes. */
export interface Api {
  auth: {
    start(email: string): Promise<SignInStartResponse>
    /** The request's state only: polling never yields a session. */
    status(requestId: string): Promise<SignInStatusResponse>
    verifyCode(requestId: string, code: string): Promise<Session>
    openLink(token: string): Promise<Session>
    /** Accepts an invitation from its link and signs in. */
    acceptInvitation(token: string): Promise<Session>
    /** The current session, or null when signed out. */
    session(): Promise<Session | null>
    signOut(): Promise<void>
  }
  people: {
    list(): Promise<Person[]>
    invite(body: InviteRequest): Promise<Person>
    update(id: string, body: UpdatePersonRequest): Promise<Person>
    remove(id: string): Promise<RemovePersonResponse>
    resendInvite(id: string): Promise<Person>
  }
  devices: {
    /** Every device for an admin, only the caller's own for a member. */
    list(): Promise<Device[]>
    revoke(id: string): Promise<RevokeDeviceResponse>
  }
  disks: {
    /** Live disks, `creating` ones included. */
    list(): Promise<Disk[]>
    /** One disk, also after its creation failed (then soft deleted and out of the list). */
    get(id: string): Promise<Disk>
    create(body: CreateDiskRequest): Promise<Disk>
    update(id: string, body: UpdateDiskRequest): Promise<Disk>
    /** A test write on the disk's bucket; the result also lands in its storage status. */
    probe(id: string): Promise<void>
    /** Takes the disk off every device and every list; its instance, metadata and objects stay. */
    remove(id: string): Promise<void>
  }
  access: {
    /** Every live disk and person that is not removed, `none` included. */
    list(): Promise<DiskAccess[]>
    /** Sets a person's access to a disk, applied live to their devices. */
    set(diskId: string, personId: string, access: AccessLevel): Promise<DiskAccess>
  }
  key: {
    get(): Promise<KeyStatus>
    startRotation(body: StartRotationRequest): Promise<Rotation>
    getRotation(id: string): Promise<Rotation>
    close(id: string): Promise<KeyStatus>
  }
  audit: {
    list(query: AuditQuery): Promise<Page<AuditEvent>>
  }
  health: {
    get(): Promise<Health>
  }
  settings: {
    get(): Promise<Settings>
    update(body: UpdateSettingsRequest): Promise<Settings>
    testMail(): Promise<MailTestResponse>
    probeStorage(body: StorageProbeRequest): Promise<void>
  }
}
