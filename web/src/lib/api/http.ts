/*
  The real client: JSON over fetch to the control plane under /api/v1, same origin, with the
  session in an HttpOnly cookie the API sets after a magic link or a code. Not exercised until
  the backend exists; the paths here are the proposed endpoints.
*/

import { ApiError, type Api } from './client'
import type { AuditQuery, ErrorCode, Session } from './types'

const BASE = import.meta.env.VITE_API_URL ?? '/api/v1'

type Method = 'GET' | 'POST' | 'PATCH' | 'DELETE'

async function request<T>(method: Method, path: string, body?: unknown): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${BASE}${path}`, {
      method,
      credentials: 'include',
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch (cause) {
    throw new ApiError(0, 'network', cause instanceof Error ? cause.message : 'Network error')
  }
  if (response.status === 204) return undefined as T
  const payload: unknown = await response.json().catch(() => null)
  if (!response.ok) {
    const error = (payload ?? {}) as { code?: ErrorCode; message?: string; details?: Record<string, unknown> }
    throw new ApiError(
      response.status,
      error.code ?? (response.status >= 500 ? 'server' : 'validation'),
      error.message ?? response.statusText,
      error.details,
    )
  }
  return payload as T
}

const query = (q: AuditQuery): string => {
  const params = new URLSearchParams({ sinceDays: String(q.sinceDays) })
  if (q.category) params.set('category', q.category)
  if (q.q) params.set('q', q.q)
  if (q.cursor) params.set('cursor', q.cursor)
  if (q.limit) params.set('limit', String(q.limit))
  return params.toString()
}

const id = encodeURIComponent

/** The HTTP implementation of the API. */
export const httpApi: Api = {
  auth: {
    start: (email) => request('POST', '/auth/sign-in', { email }),
    status: (requestId) => request('GET', `/auth/sign-in/${id(requestId)}`),
    verifyCode: (requestId, code) => request('POST', `/auth/sign-in/${id(requestId)}/code`, { code }),
    openLink: (token) => request('POST', '/auth/magic-link', { token }),
    session: async () => {
      try {
        return await request<Session>('GET', '/session')
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) return null
        throw error
      }
    },
    signOut: () => request('DELETE', '/session'),
  },
  people: {
    list: () => request('GET', '/people'),
    invite: (body) => request('POST', '/people', body),
    update: (personId, body) => request('PATCH', `/people/${id(personId)}`, body),
    remove: (personId) => request('DELETE', `/people/${id(personId)}`),
    resendInvite: (personId) => request('POST', `/people/${id(personId)}/resend`),
  },
  devices: {
    list: () => request('GET', '/devices'),
    revoke: (deviceId) => request('POST', `/devices/${id(deviceId)}/revoke`),
  },
  disks: {
    list: () => request('GET', '/disks'),
    get: (diskId) => request('GET', `/disks/${id(diskId)}`),
    create: (body) => request('POST', '/disks', body),
    update: (diskId, body) => request('PATCH', `/disks/${id(diskId)}`, body),
  },
  key: {
    get: () => request('GET', '/key'),
    startRotation: (body) => request('POST', '/key/rotations', body),
    getRotation: (rotationId) => request('GET', `/key/rotations/${id(rotationId)}`),
    close: (rotationId) => request('POST', `/key/rotations/${id(rotationId)}/close`, { oldKeyDeleted: true }),
  },
  audit: {
    list: (q) => request('GET', `/audit?${query(q)}`),
  },
  health: {
    get: () => request('GET', '/health'),
  },
  settings: {
    get: () => request('GET', '/settings'),
    update: (body) => request('PATCH', '/settings', body),
    testMail: () => request('POST', '/settings/mail/test'),
    probeStorage: (body) => request('POST', '/settings/storage/probe', body),
  },
}

/** The app's endpoints, for reference: the web never calls them (types at the end of types.ts). */
export const APP_PATHS = {
  signIn: 'POST /auth/app/sign-in',
  token: 'POST /auth/app/token',
  register: 'POST /device/register',
  config: 'GET /device/config',
  heartbeat: 'POST /device/heartbeat',
  signOut: 'POST /device/sign-out',
} as const
