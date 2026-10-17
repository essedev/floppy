/*
  The API as the features see it: one interface, two implementations (http.ts for the real
  control plane, fake/ for the in-memory stand-in). Features import `api` from '@/lib/api'
  and never know which one runs.
*/

import type {
  AuditEvent,
  AuditQuery,
  CreateDiskRequest,
  Disk,
  Health,
  InviteRequest,
  KeyStatus,
  Mac,
  MailTestResponse,
  Page,
  Person,
  RemovePersonResponse,
  RevokeMacResponse,
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
    status(requestId: string): Promise<SignInStatusResponse>
    verifyCode(requestId: string, code: string): Promise<Session>
    openLink(token: string): Promise<Session>
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
  macs: {
    /** Every Mac for an admin, only the caller's own for a member. */
    list(): Promise<Mac[]>
    revoke(id: string): Promise<RevokeMacResponse>
  }
  disks: {
    list(): Promise<Disk[]>
    create(body: CreateDiskRequest): Promise<Disk>
    update(id: string, body: UpdateDiskRequest): Promise<Disk>
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
